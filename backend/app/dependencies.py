"""Package identities shared by profile scans and registry lookups."""

import re


def package_key(ecosystem: str, name: str) -> str:
    name = name.strip()
    if ecosystem == "pypi":
        return re.sub(r"[-_.]+", "-", name).lower()
    if ecosystem in {"npm", "nuget"}:
        return name.lower()
    return name
