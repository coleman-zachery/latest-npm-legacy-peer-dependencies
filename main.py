from datetime import datetime, timezone, timedelta
import os
import re
import json
import subprocess
from packaging.version import parse










# ------------------------------
# I/O
# ------------------------------

def get_dependencies_list():
    with open("package.json", "r") as file:
        package = json.load(file)
        dependencies_list = []
        for key, value in package.items():
            if "dependencies" in key.lower():
                dependencies_list.extend(list(value))
        return dependencies_list





def write_package_versions(package):
    with open("package-versions.json", "w") as file:
        package_versions = {}
        for dependency, dependency_info in package.items():
            package_versions[dependency] = dependency_info["version"]
        json.dump(package_versions, file, indent=4)





def write_package_peerDependencies(package):
    with open("package-peerDependencies.json", "w") as file:
        package_peerDependencies = {}
        for dependency, dependency_info in package.items():
            package_peerDependencies[dependency] = {}
            for key, value in dependency_info.items():
                if key != "versions":
                    package_peerDependencies[dependency][key] = value
        json.dump(package_peerDependencies, file, indent=4)





def print_added_peerDependencies(package):
    added_peerDependencies = []
    dependencies = get_dependencies_list()
    for dependency in package:
        if dependency in dependencies: continue
        added_peerDependencies.append(dependency)
    print(f"\nadded peerDependencies: {added_peerDependencies}")





def print_stale_dependencies(package):
    stale_dependencies = []
    for dependency, dependency_info in package.items():
        if dependency_info["stale"]:
            stale_dependencies.append(dependency)
    print(f"\nstale dependencies found: {stale_dependencies}")





def overwrite_package():
    overwrite = input('\nEnter "yes" to overwrite package.json: ')
    overwrite = overwrite.strip().lower() == "yes"
    if overwrite:
        with open("package-versions.json", "r") as file:
            package_versions = json.load(file)
        with open("package.json", "r") as file:
            package_json = json.load(file)
        updated_dependencies = []
        for key in package_json:
            if "dependencies" not in key.lower(): continue
            for dependency, version in package_versions.items():
                if dependency in package_json[key]:
                    package_json[key][dependency] = f"^{version}"
                    updated_dependencies.append(dependency)
        for dependency, version in package_versions.items():
            if dependency in updated_dependencies: continue
            package_json["dependencies"][dependency] = f"^{version}"
        with open("package.json", "w") as file:
            json.dump(package_json, file, indent=4)
        print("package.json has been updated with versions from package-versions.json.")










# ------------------------------
# npm info shell
# ------------------------------

def json_npm_shell(dependency, action, default="{}"):
    output = subprocess.run(f"npm info {dependency} {action} --json", shell=True, capture_output=True, text=True).stdout.strip()
    return json.loads(output or default)





def npm_cache(dependency, action, default="{}"):
    NPM_CACHE_FILE = ".npm_cache.json"
    command = f"{dependency} {action}"
    cache, data = {}, None
    if os.path.exists(NPM_CACHE_FILE):
        with open(NPM_CACHE_FILE, "r") as file:
            cache = json.load(file)
        if command in cache: return cache[command]
    data = json_npm_shell(dependency, action, default=default)
    cache[command] = data
    with open(NPM_CACHE_FILE, "w") as file:
        json.dump(cache, file, indent=4)
    return data





def get_versions(dependency):
    print(f"[{dependency}]: versions", end=" ", flush=True)
    versions_output = npm_cache(dependency, "versions", "[]")
    pattern = r"\d+\.\d+\.\d+(?:-0)?"
    filtered_versions = list(set([version for version in versions_output if re.fullmatch(pattern, version)]))
    versions = sorted(filtered_versions, key=parse, reverse=True)
    print(f"({len(versions)})", end=" ", flush=True)
    return versions





def get_peerDependencies(dependency, version, mute=False):
    if mute == False: print(f"(latest version: {version}) peerDependencies", end=" ", flush=True)
    peerDependencies_output = npm_cache(f"{dependency}@{version}", "peerDependencies")
    peerDependenciesMeta_output = npm_cache(f"{dependency}@{version}", "peerDependenciesMeta")
    peerDependencies = {}
    for peer, semver_requirements in peerDependencies_output.items():
        if peerDependenciesMeta_output.get(peer, {}).get("optional", False): continue
        peerDependencies[peer] = semver_requirements
    if mute == False: print(f"({len(peerDependencies)})", flush=True)
    return peerDependencies





def is_dependency_stale(dependency, years=1):
    time_output = npm_cache(dependency, "time")
    filtered_time = [(version, timestamp) for version, timestamp in time_output.items() if version != "modified"]
    latest_timestamp = max(timestamp for _, timestamp in filtered_time).replace("Z", "+00:00")
    then = datetime.fromisoformat(latest_timestamp)
    now = datetime.now(timezone.utc)
    is_stale = (now - then) > timedelta(days=365 * years)
    return is_stale










# ------------------------------
# semver
# ------------------------------

def range_intersection(range1, range2):
    min1, max1 = range1
    min2, max2 = range2
    min_ = max(min1, min2)
    max_ = max2 if max1 == "inf" else max1 if max2 == "inf" else min(max1, max2)
    if max_ != "inf" and min_ >= max_: return None
    return min_, max_





def check_version_compatibility(semver_version, semver_requirements):

    def _semver_to_tuple(semver):
        if semver == "": return None
        semver = re.sub(r"[-+].*$", "", semver).replace("*", "x")
        match = re.match(r"^(\^|~|>=|<=|>|<|=)", semver)
        symbol, version = (match.group(), semver[match.end():]) if match else ("=", semver)
        parts = version.split(".")
        parts += ["x"] * (3 - len(parts))
        parts = [int(p) if p.isdigit() else None for p in parts]
        return symbol, parts

    def _get_range(semver):
        def _semver_range(semver):
            symbol, parts = _semver_to_tuple(semver) if type(semver) is str else semver
            major, minor, patch = parts
            if major is None: return [0, 0, 0], [None, None, None]
            if symbol == "^":
                if major > 0: return parts, [major + 1, 0, 0]
                if minor > 0: return parts, [0, 1 + (minor or 0), 0]
                return parts, [0, 0, patch + 1]
            if symbol == "<": return [0, 0, 0], parts
            if symbol == "~": return parts, [major, 1 + (minor or 0), 0]
            if symbol == ">=": return parts, [None, None, None]
            def _increment_version(parts):
                major, minor, patch = parts
                if patch is not None: return [major, minor, patch + 1]
                if minor is not None: return [major, minor + 1, 0]
                return [major + 1, 0, 0]
            if symbol == "<=": return [0, 0, 0], _increment_version(parts)
            if symbol == "=": return parts, _increment_version(parts)
            if symbol == ">": return _increment_version(parts), [None, None, None]
        min_, max_ = _semver_range(semver)
        def _nones_to_zeros(parts): return [0 if p is None else p for p in parts]
        def _nones_to_inf(parts): return "inf" if all(p is None for p in parts) else parts
        return _nones_to_zeros(min_), _nones_to_inf(max_)

    def _is_version_in_range(version_parts, range_):
        min_, max_ = range_
        if version_parts < min_: return False
        if max_ != "inf" and version_parts >= max_: return False
        return True

    _, version_parts = _semver_to_tuple(semver_version)
    greater_than = True
    for semver_requirement in semver_requirements.split(" || "):
        semver_tuples = [_semver_to_tuple(semver) for semver in (semver_requirement.strip() + " ").split(" ")[:2]]
        ranges = [_get_range(semver_tuple) for semver_tuple in semver_tuples if semver_tuple is not None]
        range_ = range_intersection(ranges[0], ranges[1]) if len(ranges) == 2 else ranges[0]
        if _is_version_in_range(version_parts, range_): return True, None
        if version_parts <= range_[0]: greater_than = False
    return False, greater_than










# ------------------------------
# un-used
# ------------------------------

def range_union(range1, range2):
    if range_intersection(range1, range2) is None and range1[1] != range2[0] and range2[1] != range1[0]: return None
    min1, max1 = range1
    min2, max2 = range2
    new_min = min(min1, min2)
    new_max = max(max1, max2) if max1 != "inf" and max2 != "inf" else "inf"
    return new_min, new_max





def semver_range_to_string(range_):
    def parts_to_string(parts): return ".".join(str(p) if p is not None else "x" for p in parts)
    min_parts, max_parts = range_
    min_str = f">={parts_to_string(min_parts)}"
    if max_parts == "inf": return min_str
    max_str = f"<{parts_to_string(max_parts)}"
    return f"{min_str} {max_str}"










# ------------------------------
# package logic
# ------------------------------

def add_recursive_dependency_to_package(package, dependency, required_by="<root>", include_stale_dependencies=[]):
    if dependency in package:
        if required_by not in package[dependency]["required_by"]: package[dependency]["required_by"].append(required_by)
    else:
        versions = get_versions(dependency)
        version = versions[0]
        peerDependencies = get_peerDependencies(dependency, version)
        stale = False if dependency in include_stale_dependencies or len(peerDependencies) == 0 else is_dependency_stale(dependency)
        package[dependency] = {
            "versions": versions,
            "version": version,
            "peerDependencies": peerDependencies,
            "required_by": [required_by],
            "stale": stale,
        }
        for peer in peerDependencies:
            package = add_recursive_dependency_to_package(package, peer, required_by=dependency, include_stale_dependencies=include_stale_dependencies)
    return package





def check_package_problems(package):
    problems = {
        "greater_than": {},
        "else": {},
    }
    stop = False
    for dependency, dependency_info in package.items():
        if dependency_info["stale"]: continue
        dependency_version = dependency_info["version"]
        required_by = dependency_info["required_by"]
        for peer in required_by:
            if peer == "<root>": continue
            if package[peer]["stale"]: continue
            dependency_requirements = package[peer]["peerDependencies"][dependency]
            compatible, greater_than = check_version_compatibility(dependency_version, dependency_requirements)
            if compatible: continue
            stop = True
            problems["greater_than" if greater_than else "else"][peer] = dependency_requirements
        if stop: return dependency, dependency_version, problems
    return None





def resolve_package_problems(package, package_problems, include_stale_dependencies=[]):

    def _update_dependency_version(package, dependency, version, peerDependencies=None, include_stale_dependencies=[]):
        previous_peerDependencies = package[dependency]["peerDependencies"]
        new_peerDependencies = peerDependencies or get_peerDependencies(dependency, version, mute=True)
        package[dependency]["version"] = version
        package[dependency]["peerDependencies"] = new_peerDependencies
        stale = False if dependency in include_stale_dependencies or len(new_peerDependencies) == 0 else is_dependency_stale(dependency)
        package[dependency]["stale"] = stale
        for p in previous_peerDependencies:
            if p not in new_peerDependencies:
                if dependency in package[p]["required_by"]:
                    package[p]["required_by"].remove(dependency)
        for p in new_peerDependencies:
            if p not in previous_peerDependencies:
                if dependency not in package[p]["required_by"]:
                    package[p]["required_by"].append(dependency)
        return package

    dependency, dependency_version, problems = package_problems

    # downgrade dependency to meet dependency_requirements
    if len(problems["greater_than"]) > 0:
        satisfied_peers = None
        for peer, dependency_requirements in problems["greater_than"].items():
            peers = []
            for version in package[dependency]["versions"]:
                if version > dependency_version: continue
                if check_version_compatibility(version, dependency_requirements)[1]:
                    if peer not in peers: peers.append(peer)
                    continue
                break
            package = _update_dependency_version(package, dependency, version, peerDependencies=None, include_stale_dependencies=include_stale_dependencies)
            if len(peers) > 0:
                satisfied_peers = peers
        print(f"\n[{dependency}]: downgraded dependency version {dependency_version} --> {version}")
        for peer in satisfied_peers:
            print(f"-- satisfied {peer}@{package[peer]["version"]} peerDependency: {dependency}@{problems["greater_than"][peer]}")

    def _find_compatible_version(peer, dependency, dependency_version, package):
        versions = package[peer]["versions"]
        lo, hi = 0, len(versions) - 1
        result = None
        while lo <= hi:
            mid = (lo + hi) // 2
            version = versions[mid]
            temp_peerDependencies = get_peerDependencies(peer, version, mute=True)
            dependency_requirements = temp_peerDependencies.get(dependency)
            if dependency_requirements is None:
                hi = mid - 1
                continue
            compatible, greater_than = check_version_compatibility(dependency_version, dependency_requirements)
            if compatible:
                result = version
                hi = mid - 1
            else:
                if greater_than: hi = mid - 1
                else: lo = mid + 1
        return result

    # downgrade peer to meet current dependency version
    dependency_version = package[dependency]["version"]
    for peer, _ in problems["else"].items():
        peer_version = package[peer]["version"]
        peer_requirements = package[peer]["peerDependencies"][dependency]
        version = _find_compatible_version(peer, dependency, dependency_version, package)
        if version:
            print(f"\n[{peer}]: downgraded dependency version {peer_version} --> {version} (for {dependency}@{dependency_version}, previous peerDependency: {dependency}@{peer_requirements})")
            temp_peerDependencies = get_peerDependencies(peer, version, mute=True)
            package = _update_dependency_version(package, peer, version, peerDependencies=temp_peerDependencies, include_stale_dependencies=include_stale_dependencies)
            print(f"-- satisfied {peer}@{version} peerDependency: {dependency}@{package[peer]["peerDependencies"][dependency]}")

    return package










# ------------------------------
# Main
# ------------------------------

def main():
    include_stale_dependencies = []
    package = {}
    print("adding dependencies to package...")
    for dependency in get_dependencies_list():
        package = add_recursive_dependency_to_package(package, dependency, required_by="<root>", include_stale_dependencies=include_stale_dependencies)
    while package_problems := check_package_problems(package):
        package = resolve_package_problems(package, package_problems, include_stale_dependencies=include_stale_dependencies)
    write_package_peerDependencies(package)
    write_package_versions(package)
    print_added_peerDependencies(package)
    print_stale_dependencies(package)
    overwrite_package()

if __name__ == "__main__":
    main()
