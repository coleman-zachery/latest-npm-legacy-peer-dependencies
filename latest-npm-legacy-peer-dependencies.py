import bisect
from datetime import datetime, timezone, timedelta
import re
import json
import subprocess
from packaging.version import parse

# ------------------------------
# Utilities
# ------------------------------

def get_dependencies_list():
    with open("package.json", "r") as file: package = json.load(file)
    return [dependency for key in package for dependency in package.get(key, {}) if "dependencies" in key.lower()]

def run_shell(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True).stdout.strip()

def get_sorted_versions(dependency):
    output = json.loads(run_shell(f"npm info {dependency} versions --json") or "[]")
    pattern = r"\d+\.\d+\.\d+(?:-0)?"
    versions = list(set([version for version in output if re.fullmatch(pattern, version)]))
    return sorted(versions, key=parse, reverse=True)

def get_next_version(current_version, versions):
    current_tuple = semver_to_tuple(current_version)[1]
    for version in versions:
        if semver_to_tuple(version)[1] < current_tuple: return version
    return None

def get_peerDependencies(dep, version):
    peers = json.loads(run_shell(f"npm info {dep}@{version} peerDependencies --json") or "{}")
    meta = json.loads(run_shell(f"npm info {dep}@{version} peerDependenciesMeta --json") or "{}")
    return {peer: version for peer, version in peers.items() if not meta.get(peer, {}).get("optional", False)}

def dependency_is_stale(dependency, years=1):
    time_info = [(version, timestamp) for version, timestamp in json.loads(run_shell(f"npm info {dependency} time --json") or "{}").items() if version != "modified"]
    latest_timestamp = max(timestamp for _, timestamp in time_info).replace("Z", "+00:00")
    ts = datetime.fromisoformat(latest_timestamp)
    now = datetime.now(timezone.utc)
    return (now - ts) > timedelta(days=365 * years)

# ------------------------------
# Parse semver
# ------------------------------

def semver_to_tuple(semver):
    if semver == "": return None
    semver = re.sub(r"[-+].*$", "", semver).replace("*", "x")
    match = re.match(r"^(\^|~|>=|<=|>|<|=)", semver)
    symbol, version = (match.group(), semver[match.end():]) if match else ("=", semver)
    parts = version.split(".")
    parts += ["x"] * (3 - len(parts))
    parts = [int(p) if p.isdigit() else None for p in parts[:3]]
    return symbol, parts

def tuple_to_semver(symbol, parts):
    version = ".".join(str(p) if p is not None else "x" for p in parts)
    return f"{symbol}{version}"

def _semver_range(input):
    symbol, parts = semver_to_tuple(input) if type(input) is str else input
    major, minor, patch = parts
    if major is None: return [0, 0, 0], [None, None, None]

    def _increment_version(parts):
        major, minor, patch = parts
        if patch is not None: return [major, minor, patch + 1]
        if minor is not None: return [major, minor + 1, 0]
        if major is not None: return [major + 1, 0, 0]
        return [None, None, None]

    if symbol == "<": return [0, 0, 0], parts
    if symbol == "<=": return [0, 0, 0], _increment_version(parts)
    if symbol == "=": return parts, _increment_version(parts)
    if symbol == ">=": return parts, [None, None, None]
    if symbol == ">": return _increment_version(parts), [None, None, None]
    if symbol == "~": return parts, [major, 1 + (minor or 0), 0]
    if symbol == "^": return _semver_range(("=" if minor == 0 else "~", parts)) if major == 0 else (parts, [major + 1, 0, 0])

def semver_range(input):
    min_version, max_version = _semver_range(input)
    def _nones_to_zeros(parts): return [p if p is not None else 0 for p in parts]
    def _nones_to_inf(parts): return "inf" if all(p is None for p in parts) else parts
    return _nones_to_zeros(min_version), _nones_to_inf(max_version)

def range_intersection(range1, range2):
    min1, max1 = range1
    min2, max2 = range2
    new_min = max(min1, min2)
    new_max = min(max1, max2) if max1 != "inf" and max2 != "inf" else max1 if max2 == "inf" else max2
    if new_max != "inf" and new_min >= new_max: return None
    return new_min, new_max

def range_union(range1, range2):
    if range_intersection(range1, range2) is None and range1[1] != range2[0] and range2[1] != range1[0]: return None
    min1, max1 = range1
    min2, max2 = range2
    new_min = min(min1, min2)
    new_max = max(max1, max2) if max1 != "inf" and max2 != "inf" else "inf"
    return new_min, new_max

def is_version_in_range(version, range_):
    min_version, max_version = range_
    if version < min_version: return False
    if max_version != "inf" and version >= max_version: return False
    return True

def semver_range_to_string(range_):
    min_version, max_version = range_
    min_str = tuple_to_semver(">=", min_version)
    if max_version == "inf": return min_str
    max_str = tuple_to_semver("<", max_version)
    return f"{min_str} {max_str}"

def check_version_compatibility(version, compatible_versions):
    version_tuple = semver_to_tuple(version)[1]
    greater_than = True
    for item in compatible_versions.split(" || "):
        semvers = [semver_to_tuple(semver) for semver in (item.strip() + " ").split(" ")[:2]]
        ranges = [semver_range(semver) for semver in semvers if semver is not None]
        range_ = range_intersection(ranges[0], ranges[1]) if len(ranges) == 2 else ranges[0]
        if is_version_in_range(version_tuple, range_): return True, None
        if version_tuple <= range_[0]: greater_than = False
    return False, greater_than

# ------------------------------
# Test Recursive Add
# ------------------------------

def add_dependency_to_package(package, dependency, include_stale_dependencies):
    if dependency not in package:
        print(f" Fetching versions for {dependency}...")
        versions = get_sorted_versions(dependency)
        version = versions[0]
        peerDependencies = get_peerDependencies(dependency, version)
        package[dependency] = {
            "versions": versions,
            "version": version,
            "peerDependencies": peerDependencies,
            "stale": False if dependency in include_stale_dependencies or len(peerDependencies) == 0 else dependency_is_stale(dependency),
        }
    return package


def downgrade_peer(peer_version, peer_versions, compatible_versions, peer, dependency, dependency_version):
    compatible, greater_than = check_version_compatibility(peer_version, compatible_versions)
    if compatible: return peer_version
    if greater_than:
        idx = bisect.bisect_left(peer_versions, peer_version) - 1
        lo, hi = 0, idx
        best = None
        while lo <= hi:
            mid = (lo + hi) // 2
            candidate = peer_versions[mid]
            is_compatible, _ = check_version_compatibility(candidate, compatible_versions)
            if is_compatible:
                best = candidate
                lo = mid + 1
            else:
                hi = mid - 1
        if best is not None: return best
    raise Exception(f"Conflict detected: {peer}@{peer_version} has no lower version satisfying {compatible_versions} from {dependency}@{dependency_version}")


def downgrade_dependency(package, dependency, include_stale_dependencies):
    if dependency not in package:
        print(f"added dependency: {dependency}")
        package = add_dependency_to_package(package, dependency, include_stale_dependencies)
    if dependency not in include_stale_dependencies and package[dependency]["stale"]: return package
    previous_version = package[dependency]["version"] + ""
    print(f"  checking peerDependencies of {dependency}@{previous_version}...")
    for version in package[dependency]["versions"]:
        if version > previous_version: continue
        peerDependencies = get_peerDependencies(dependency, version)
        for peer, compatible_versions in peerDependencies.items():
            if peer not in package:
                package = add_dependency_to_package(package, peer, include_stale_dependencies=include_stale_dependencies)
                package = downgrade_dependency(package, peer, include_stale_dependencies=include_stale_dependencies)
            if peer not in include_stale_dependencies and package[peer]["stale"]: continue
            peer_version = package[peer]["version"]
            compatible, greater_than = check_version_compatibility(peer_version, compatible_versions)
            if compatible: continue
            if greater_than:
                if peer_version := downgrade_peer(peer_version, package[peer]["versions"], compatible_versions, peer, dependency, version):
                    print(f"-- downgraded peer: '{peer}' version {package[peer]["version"]} to {peer_version}")
                    package[peer]["version"] = peer_version
                else:
                    raise Exception(f"Conflict detected: {peer}@{peer_version} not in {compatible_versions} (from {dependency}) and no lower version satisifies the requirement")
            else: break
        else:
            if previous_version != version: print(f"-- downgraded dependency: '{dependency}' version {previous_version} to {version}")
            package[dependency]["version"] = version
            package[dependency]["peerDependencies"] = peerDependencies
            break
    if previous_version != package[dependency]["version"]:
        for peer, _ in package[dependency]["peerDependencies"].items():
            package = downgrade_dependency(package, peer, include_stale_dependencies=include_stale_dependencies)
    return package


def verify_all_versions(packages):
    problems = []
    for pkg, info in packages.items():
        version = info["version"]
        peers = info.get("peerDependencies", {})
        for peer_name, peer_constraint in peers.items():
            if peer_name not in packages:
                problems.append(f"{pkg}@{version} requires {peer_name}@{peer_constraint}, but {peer_name} is not installed")
                continue
            peer_version = packages[peer_name]["version"]
            compatible, _ = check_version_compatibility(peer_version, peer_constraint)
            if not compatible: problems.append(f"{pkg}@{version} requires {peer_name}@{peer_constraint}, but found {peer_name}@{peer_version}")
    return (True, None) if not problems else (False, problems)

# ------------------------------
# Main
# ------------------------------

def main():
    include_stale_dependencies = ["react-table", "@testing-library/react-hooks"]

    package = {}
    for dependency in get_dependencies_list():
        package = downgrade_dependency(package, dependency, include_stale_dependencies=include_stale_dependencies)
    for dependency in package:
        package = downgrade_dependency(package, dependency, include_stale_dependencies=include_stale_dependencies)

    with open("package-versions.json", "w") as f:
        package_without_versions_keys = {k: {key: val for key, val in v.items() if key != "versions"} for k, v in package.items()}
        json.dump(package_without_versions_keys, f, indent=4)

    with open("package-export.json", "w") as f:
        package_export = {dependency: package[dependency]["version"] for dependency in get_dependencies_list()}
        json.dump(package_export, f, indent=4)

    print(f"\nstale packages found: {[dependency for dependency in package if package[dependency]["stale"]]}")

    print(verify_all_versions(package))

if __name__ == "__main__":
    main()
