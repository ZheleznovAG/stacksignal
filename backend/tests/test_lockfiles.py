"""Lockfiles only fill resolved_version for existing declarations, and only when one registry version is unambiguous."""

import pytest

from app import lockfiles, profile_import


def _write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _resolved(root):
    return {(d["manifest_path"], d["package_key"]): d["resolved_version"]
            for d in profile_import.scan_folder(str(root))["dependencies"]}


def test_npm_workspace_member_uses_hoisted_root_lock(tmp_path):
    _write(tmp_path, "package.json", '{"dependencies":{"react":"^18.2"},"workspaces":["packages/*"]}')
    _write(tmp_path, "packages/web/package.json", '{"dependencies":{"vite":"^5"},"devDependencies":{"left":"1.x"}}')
    _write(tmp_path, "package-lock.json", """{"lockfileVersion":3,"packages":{
        "":{},"packages/web":{},
        "node_modules/react":{"version":"18.3.1"},
        "node_modules/vite":{"version":"5.4.2"},
        "packages/web/node_modules/left":{"version":"1.0.4"}}}""")
    resolved = _resolved(tmp_path)
    assert resolved[("package.json", "react")] == "18.3.1"
    assert resolved[("packages/web/package.json", "vite")] == "5.4.2"
    assert resolved[("packages/web/package.json", "left")] == "1.0.4"


def test_npm_linked_or_out_of_range_versions_stay_unresolved(tmp_path):
    _write(tmp_path, "package.json", '{"dependencies":{"local":"^1","react":"^17"}}')
    _write(tmp_path, "package-lock.json", """{"lockfileVersion":3,"packages":{"":{},
        "node_modules/local":{"link":true,"resolved":"libs/local"},
        "node_modules/react":{"version":"18.3.1"}}}""")
    resolved = _resolved(tmp_path)
    assert resolved[("package.json", "local")] == ""
    assert resolved[("package.json", "react")] == ""


def test_pnpm_importers_strip_peer_context(tmp_path):
    _write(tmp_path, "apps/site/package.json", '{"dependencies":{"next":"^14"},"devDependencies":{"vitest":"~1.6"}}')
    _write(tmp_path, "pnpm-lock.yaml", """lockfileVersion: '9.0'
importers:
  apps/site:
    dependencies:
      next:
        specifier: ^14
        version: 14.2.5(react-dom@18.3.1(react@18.3.1))(react@18.3.1)
    devDependencies:
      vitest:
        specifier: ~1.6
        version: 1.6.0
""")
    resolved = _resolved(tmp_path)
    assert resolved[("apps/site/package.json", "next")] == "14.2.5"
    assert resolved[("apps/site/package.json", "vitest")] == "1.6.0"


def test_pnpm_yaml_aliases_are_refused():
    with pytest.raises(ValueError):
        lockfiles.parse("pnpm-lock.yaml", "lockfileVersion: '9.0'\na: &x {b: 1}\nc: *x\n")


def test_uv_lock_skips_the_project_and_leaves_platform_splits_unresolved(tmp_path):
    _write(tmp_path, "pyproject.toml", '[project]\nname="app"\ndependencies=["fastapi>=0.115","numpy>=1.26","app-sdk"]\n')
    _write(tmp_path, "uv.lock", """version = 1
[[package]]
name = "app"
version = "0.1.0"
source = { editable = "." }

[[package]]
name = "app-sdk"
version = "0.2.0"
source = { path = "../sdk" }

[[package]]
name = "fastapi"
version = "0.141.1"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "numpy"
version = "2.0.2"
source = { registry = "https://pypi.org/simple" }

[[package]]
name = "numpy"
version = "2.5.3"
source = { registry = "https://pypi.org/simple" }
""")
    resolved = _resolved(tmp_path)
    assert resolved[("pyproject.toml", "fastapi")] == "0.141.1"
    assert resolved[("pyproject.toml", "numpy")] == ""
    assert resolved[("pyproject.toml", "app-sdk")] == ""


@pytest.mark.parametrize("spec, locked, expected", [
    ("^24.1", "24.1", "24.1"),          # two-part Python versions with Poetry caret
    ("~=1.4", "1.6.2", "1.6.2"),        # PEP 440 compatible release, not Poetry tilde
    ("~1.4", "1.6.2", ""),              # Poetry tilde: >=1.4,<1.5
    (">=2.0", "2.1.0rc1", "2.1.0rc1"),  # a locked prerelease is still what is installed
    ("^1.2", "2.0.0", ""),
])
def test_poetry_lock_matches_python_constraints(tmp_path, spec, locked, expected):
    _write(tmp_path, "pyproject.toml", f'[tool.poetry.dependencies]\npython="^3.12"\nlib="{spec}"\n')
    _write(tmp_path, "poetry.lock", f'[[package]]\nname = "lib"\nversion = "{locked}"\n')
    assert _resolved(tmp_path)[("pyproject.toml", "lib")] == expected


def test_cargo_lock_uses_only_registry_sources(tmp_path):
    _write(tmp_path, "Cargo.toml", '[dependencies]\nserde="1.0"\nmine={git="https://example.test/mine",version="0.3"}\n')
    _write(tmp_path, "Cargo.lock", """version = 3
[[package]]
name = "serde"
version = "1.0.210"
source = "registry+https://github.com/rust-lang/crates.io-index"

[[package]]
name = "mine"
version = "0.3.1"
source = "git+https://example.test/mine#abc"
""")
    resolved = _resolved(tmp_path)
    assert resolved[("Cargo.toml", "serde")] == "1.0.210"
    assert resolved[("Cargo.toml", "mine")] == ""


def test_nuget_lock_requires_one_direct_resolution_across_frameworks(tmp_path):
    _write(tmp_path, "Game.csproj", '<Project><ItemGroup><PackageReference Include="Newtonsoft.Json" Version="13.0.1"/>'
                                    '<PackageReference Include="Serilog" Version="[3.0,4.0)"/></ItemGroup></Project>')
    _write(tmp_path, "packages.lock.json", """{"version":1,"dependencies":{
        "net8.0":{"Newtonsoft.Json":{"type":"Direct","requested":"[13.0.1, )","resolved":"13.0.3"},
                  "Serilog":{"type":"Direct","resolved":"3.1.1"}},
        "net6.0":{"Newtonsoft.Json":{"type":"Direct","resolved":"13.0.3"},
                  "Serilog":{"type":"Direct","resolved":"3.0.1"}}}}""")
    resolved = _resolved(tmp_path)
    assert resolved[("Game.csproj", "newtonsoft.json")] == "13.0.3"
    assert resolved[("Game.csproj", "serilog")] == ""  # differs per framework


def test_unity_packages_lock_reads_direct_registry_packages(tmp_path):
    _write(tmp_path, "Unity/Packages/manifest.json",
           '{"dependencies":{"com.unity.burst":"1.8.17","com.local.tool":"file:../Tool"}}')
    _write(tmp_path, "Unity/Packages/packages-lock.json", """{"dependencies":{
        "com.unity.burst":{"version":"1.8.17","depth":0,"source":"registry"},
        "com.local.tool":{"version":"file:../Tool","depth":0,"source":"local"}}}""")
    resolved = _resolved(tmp_path)
    assert resolved[("Unity/Packages/manifest.json", "com.unity.burst")] == "1.8.17"
    assert resolved[("Unity/Packages/manifest.json", "com.local.tool")] == ""


def test_nearest_lock_wins_and_damaged_locks_keep_declarations(tmp_path):
    _write(tmp_path, "package-lock.json", '{"lockfileVersion":3,"packages":{"":{},"node_modules/react":{"version":"18.0.0"}}}')
    _write(tmp_path, "web/package.json", '{"dependencies":{"react":"^18"}}')
    _write(tmp_path, "web/package-lock.json", '{"lockfileVersion":3,"packages":{"":{},"node_modules/react":{"version":"18.3.1"}}}')
    _write(tmp_path, "api/package.json", '{"dependencies":{"react":"^18"}}')
    _write(tmp_path, "api/package-lock.json", "{not json")
    scan = profile_import.scan_folder(str(tmp_path))
    resolved = {(d["manifest_path"], d["package_key"]): d["resolved_version"] for d in scan["dependencies"]}
    assert resolved[("web/package.json", "react")] == "18.3.1"
    # api's own lock is unreadable; the root lock does not list api as a workspace member.
    assert resolved[("api/package.json", "react")] == ""
    assert "api/package-lock.json" not in scan["files"]
