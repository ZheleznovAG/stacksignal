"""Read locked registry versions without running a package manager or resolving a graph.

Only enrich existing declarations. Ambiguous platform/framework resolutions,
local/git dependencies and unsupported constraints stay unresolved.
"""

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath

import yaml
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from .dependencies import package_key

FILENAMES = {"package-lock.json", "pnpm-lock.yaml", "uv.lock", "poetry.lock", "cargo.lock",
             "packages.lock.json", "packages-lock.json"}
MAX_FILE_BYTES = 5_000_000
_ECOSYSTEM = {"package-lock.json": "npm", "pnpm-lock.yaml": "npm", "uv.lock": "pypi", "poetry.lock": "pypi",
              "cargo.lock": "crates", "packages.lock.json": "nuget", "packages-lock.json": "unity"}
_SEMVER = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


@dataclass
class Lockfile:
    path: PurePosixPath
    data: dict

    @property
    def ecosystem(self):
        return _ECOSYSTEM[self.path.name.lower()]


def parse(path: str, text: str) -> Lockfile:
    name = PurePosixPath(path).name.lower()
    if name == "pnpm-lock.yaml":
        # safe_load has no object constructors; aliases are unnecessary in a
        # generated lockfile and refusing them bounds pathological YAML graphs.
        if any(isinstance(token, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken)) for token in yaml.scan(text)):
            raise ValueError("YAML aliases are not supported in lockfiles")
        data = yaml.safe_load(text)
    elif name.endswith(".lock"):
        data = tomllib.loads(text)
    else:
        data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Lockfile must be a mapping")
    return Lockfile(PurePosixPath(path), data)


def _numeric(value: str):
    match = re.fullmatch(r"v?(\d+)(?:\.(\d+))?(?:\.(\d+))?", value)
    return tuple(int(p or 0) for p in match.groups()) if match else None


def _matches(version: str, spec: str, ecosystem: str) -> bool:
    spec = spec.split(";", 1)[0].strip()  # Python marker selection is deliberately not inferred
    # Poetry's caret/tilde are SemVer-style; "~=" is PEP 440's compatible release.
    if ecosystem == "pypi" and not (spec.startswith("^") or (spec.startswith("~") and not spec.startswith("~="))):
        try:
            # A locked prerelease satisfying the constraint is still the resolved version.
            return SpecifierSet(spec).contains(Version(version), prereleases=True)
        except (InvalidVersion, InvalidSpecifier):
            return False
    if not (_SEMVER.fullmatch(version) or (ecosystem == "pypi" and _numeric(version))):
        return False
    if not spec or spec == "*":
        return True
    if ecosystem == "nuget" and spec.startswith(("[", "(")):
        if "," not in spec:
            return spec == f"[{version}]"
        left, right = (part.strip() for part in spec[1:-1].split(",", 1))
        bounds = [(">=" if spec[0] == "[" else ">") + left] if left else []
        if right:
            bounds.append(("<=" if spec[-1] == "]" else "<") + right)
        spec = " ".join(bounds)
    # Conservative SemVer ranges. Unsupported tags, URLs, aliases and prerelease
    # ranges cannot manufacture a resolved registry version.
    if "-" in version.split("+", 1)[0]:
        return spec.lstrip("=") == version
    current = _numeric(version.split("+", 1)[0])
    for alternative in spec.split("||"):
        tokens = re.findall(r"(?:>=|<=|>|<|=|\^|~)?\s*v?[0-9xX*]+(?:\.[0-9xX*]+){0,2}", alternative)
        if not tokens or re.sub(r"[\s,]", "", "".join(tokens)) != re.sub(r"[\s,]", "", alternative):
            continue
        accepted = True
        for token in tokens:
            match = re.fullmatch(r"(>=|<=|>|<|=|\^|~)?\s*(v?[0-9xX*]+(?:\.[0-9xX*]+){0,2})", token)
            operator, raw = match.groups()
            parts = raw.lstrip("v").split(".")
            fixed = [int(p) for p in parts if p.lower() not in {"x", "*"}]
            lower = tuple((fixed + [0, 0, 0])[:3])
            if operator in {">=", "<=", ">", "<", "="}:
                accepted &= {">=": current >= lower, "<=": current <= lower, ">": current > lower,
                             "<": current < lower, "=": current == lower}[operator]
            elif operator in {"^", "~"} or (ecosystem == "crates" and operator is None and len(fixed) == len(parts)):
                index = (0 if len(fixed) < 2 else 1) if operator == "~" else next(
                    (i for i, p in enumerate(lower) if p), max(len(fixed) - 1, 0))
                upper = tuple(lower[i] + 1 if i == index else lower[i] if i < index else 0 for i in range(3))
                accepted &= lower <= current < upper
            elif ecosystem == "nuget" and len(fixed) == len(parts):
                accepted &= current >= lower  # bare NuGet versions are minimums
            else:
                accepted &= current[:len(fixed)] == tuple(fixed)
        if accepted:
            return True
    return False


def _candidates(lock: Lockfile, dep: dict) -> set[str]:
    data, name = lock.data, dep["package_key"]
    directory = PurePosixPath(dep["manifest_path"]).parent
    importer = directory.relative_to(lock.path.parent).as_posix()
    filename = lock.path.name.lower()
    if filename == "package-lock.json":
        if data.get("lockfileVersion", 1) not in (1, 2, 3):
            return set()
        packages = data.get("packages")
        if isinstance(packages, dict):
            prefix = "" if importer == "." else importer
            if prefix not in packages:
                return set()  # not a member of this workspace
            for folder in [directory, *directory.parents]:
                if not folder.is_relative_to(lock.path.parent):
                    break
                rel = folder.relative_to(lock.path.parent).as_posix()
                key = ("" if rel == "." else rel + "/") + "node_modules/" + name
                if key in packages:
                    row = packages[key]
                    return {str(row.get("version", ""))} if not row.get("link") else set()
            return set()
        row = data.get("dependencies", {}).get(name, {}) if importer == "." else {}
        return {str(row.get("version", ""))}
    if filename == "pnpm-lock.yaml":
        major = str(data.get("lockfileVersion", "")).split(".")[0]
        if major not in {"5", "6", "9"}:
            return set()
        project = data.get("importers", {}).get(importer, {}) if "importers" in data else data if importer == "." else {}
        section = {"dev": "devDependencies", "optional": "optionalDependencies"}.get(dep["scope"], "dependencies")
        row = project.get(section, {}).get(name, {})
        value = str(row.get("version", "")) if isinstance(row, dict) else str(row)
        # Peer contexts decorate a locked version but do not change it.
        return {re.split(r"[(_]", value, maxsplit=1)[0]}
    if filename in {"uv.lock", "poetry.lock", "cargo.lock"}:
        versions = set()
        for row in data.get("package", []):
            if package_key(dep["ecosystem"], row.get("name", "")) != name:
                continue
            source = row.get("source")
            if filename == "cargo.lock" and not str(source or "").startswith("registry+"):
                continue
            if isinstance(source, dict) and (any(k in source for k in ("git", "editable", "directory", "path", "virtual"))
                                             or source.get("type") in {"git", "directory", "file", "url"}):
                continue
            versions.add(str(row.get("version", "")))
        return versions
    if filename == "packages.lock.json":
        if importer != ".":
            return set()
        return {str(row.get("resolved", "")) for framework in data.get("dependencies", {}).values()
                for key, row in framework.items() if package_key("nuget", key) == name and row.get("type") == "Direct"}
    if filename == "packages-lock.json":
        if importer != "." or lock.path.parent.name != "Packages":
            return set()
        row = data.get("dependencies", {}).get(name, {})
        return {str(row.get("version", ""))} if row.get("source") == "registry" and row.get("depth") == 0 else set()
    return set()


def apply(dependencies: list[dict], locks: list[Lockfile]) -> None:
    for dep in dependencies:
        directory = PurePosixPath(dep["manifest_path"]).parent
        relevant = [lock for lock in locks if lock.ecosystem == dep["ecosystem"]
                    and directory.is_relative_to(lock.path.parent)]
        if not relevant:
            continue
        nearest = max(len(lock.path.parent.parts) for lock in relevant)
        versions = set()
        for lock in relevant:
            if len(lock.path.parent.parts) != nearest:
                continue
            try:
                versions |= {v for v in _candidates(lock, dep) if v and _matches(v, dep["declared_spec"], dep["ecosystem"])}
            except (ValueError, TypeError, KeyError, AttributeError):
                continue  # damaged/unsupported rows must not invalidate the manifest scan
        if len(versions) == 1:
            dep["resolved_version"] = versions.pop()
