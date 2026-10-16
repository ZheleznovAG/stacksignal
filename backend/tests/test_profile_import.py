"""Importing a project's profile from its folder: only whitelisted manifests and the README are read,
the stack ends up in what the AI is told about the project, and applying is a separate, reviewed step."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import config, db as db_module, enrichment, models, profile_import
from app.ai_analysis import AIContext
from app.main import app


def _write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def folder(tmp_path):
    _write(tmp_path, "README.md", "# Demo\n\n![badge](x.png)\n\nA small tool that turns photographed pages into flashcards "
                                   "for language learners.\n\n## Roadmap\n- OCR of handwriting\n")
    _write(tmp_path, "backend/requirements.txt", "fastapi>=0.115\nuvicorn[standard]>=0.32  # server\n-r other.txt\n"
                                                 "git+https://x/y.git\nSQLAlchemy==2.0\n")
    _write(tmp_path, "pyproject.toml", '[project]\nname="d"\ndependencies=["httpx[socks]>=0.27","pydantic>=2"]\n'
                                       '[project.optional-dependencies]\ndev=["pytest>=8"]\n')
    _write(tmp_path, "web/package.json", '{"dependencies":{"react":"^18"},"devDependencies":{"vite":"^5"}}')
    _write(tmp_path, "app/build.gradle.kts", 'dependencies {\n implementation("io.ktor:ktor-client-core:2.3")\n'
                                             ' implementation("androidx.room:room-runtime:2.6")\n}\n')
    _write(tmp_path, "Game/Game.csproj", '<Project><ItemGroup><PackageReference Include="Newtonsoft.Json" Version="13"/>'
                                         '</ItemGroup></Project>')
    _write(tmp_path, "Unity/Packages/manifest.json", '{"dependencies":{"com.unity.burst":"1.8"}}')
    _write(tmp_path, "Dockerfile", "FROM python:3.12-slim\nFROM scratch\n")
    _write(tmp_path, ".env", "TELEGRAM_BOT_TOKEN=" + "123456789:" + "S" * 35 + "\n")
    _write(tmp_path, "node_modules/leftpad/package.json", '{"dependencies":{"should-be-skipped":"1"}}')
    _write(tmp_path, "a/b/c/d/requirements.txt", "too-deep==1\n")
    _write(tmp_path, "src/main.py", "SECRET_IN_SOURCE = 'never read'\n")
    return tmp_path


def _names(scan):
    return {d["name"] for d in scan["dependencies"]}


def test_the_scan_reads_the_stack_from_the_usual_manifests(folder):
    scan = profile_import.scan_folder(str(folder))

    assert {"fastapi", "uvicorn", "SQLAlchemy", "httpx", "pydantic", "pytest", "react", "vite", "ktor-client-core",
            "room-runtime", "Newtonsoft.Json", "com.unity.burst", "python"} - {"python"} <= _names(scan)
    assert "should-be-skipped" not in _names(scan) and "too-deep" not in _names(scan)
    assert "-r" not in _names(scan) and not any("git+" in n for n in _names(scan))
    assert next(d for d in scan["dependencies"] if d["name"] == "fastapi")["file"] == "backend/requirements.txt"
    assert "photographed pages" in scan["readme"] and "badge" not in scan["readme"]


def test_only_manifests_and_the_readme_are_ever_read(folder):
    scan = profile_import.scan_folder(str(folder))

    assert ".env" not in scan["files"] and "src/main.py" not in scan["files"]
    assert not any("node_modules" in f for f in scan["files"])
    assert "SECRET" not in repr(scan)


def test_scan_preserves_coordinates_specs_scopes_and_manifest_paths(folder):
    scan = profile_import.scan_folder(str(folder))
    deps = {(d["ecosystem"], d["package_key"]): d for d in scan["dependencies"]}
    assert deps["pypi", "fastapi"]["declared_spec"] == ">=0.115"
    assert deps["pypi", "fastapi"]["manifest_path"] == "backend/requirements.txt"
    assert deps["pypi", "sqlalchemy"]["declared_spec"] == "==2.0"
    assert deps["pypi", "pytest"]["scope"] == "dev"
    assert deps["npm", "react"]["declared_spec"] == "^18"
    assert deps["npm", "vite"]["scope"] == "dev"
    assert deps["maven", "io.ktor:ktor-client-core"]["declared_spec"] == "2.3"
    assert deps["maven", "io.ktor:ktor-client-core"]["name"] == "ktor-client-core"
    assert deps["nuget", "newtonsoft.json"]["declared_spec"] == "13"
    assert deps["docker", "python"]["declared_spec"] == "3.12-slim"
    assert all(d["resolved_version"] == "" for d in scan["dependencies"])


def test_scan_keeps_different_ecosystems_manifests_and_scopes(tmp_path):
    _write(tmp_path, "requirements.txt", "Foo_Bar>=1\nFoo_Bar>=1\n")
    _write(tmp_path, "requirements-dev.txt", "Foo_Bar==2\n")
    _write(tmp_path, "package.json", '{"dependencies":{"foo-bar":"^3"},"devDependencies":{"foo-bar":"^4"}}')
    _write(tmp_path, "sub/requirements.txt", "foo.bar>=5\n")
    deps = profile_import.scan_folder(str(tmp_path))["dependencies"]
    assert len(deps) == 5
    assert {d["declared_spec"] for d in deps} == {">=1", "==2", "^3", "^4", ">=5"}
    assert {d["package_key"] for d in deps} == {"foo-bar"}


def test_additional_manifests_preserve_versions_and_full_package_keys(tmp_path):
    _write(tmp_path, "go.mod", "module example.com/app\nrequire (\n golang.org/x/sync v0.12.0\n"
                                 " example.com/dep v1.2.3 // indirect\n)\nexclude example.com/unused v1.0.0\n")
    _write(tmp_path, "Cargo.toml", '[dependencies]\nserde={version="1.0",features=["derive"]}\n'
                                     'alias={package="real-crate",version="~2"}\n[dev-dependencies]\nmock="3"\n')
    _write(tmp_path, "libs.versions.toml", '[versions]\nktor="3.0.0"\n[libraries]\n'
                                           'ktor={module="io.ktor:ktor-server-core", version.ref="ktor"}\n')
    _write(tmp_path, "Dockerfile", "FROM registry.example:5000/team/image:2 AS base\nFROM base AS build\nFROM scratch\n")
    _write(tmp_path, "Demo.csproj", '<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003">'
                                      '<ItemGroup><PackageReference Include="Foo"><Version>1.2.3</Version>'
                                      '</PackageReference></ItemGroup></Project>')
    deps = {(d["ecosystem"], d["package_key"]): d for d in profile_import.scan_folder(str(tmp_path))["dependencies"]}
    assert deps["go", "golang.org/x/sync"]["declared_spec"] == "v0.12.0"
    assert deps["go", "example.com/dep"]["scope"] == "indirect"
    assert ("go", "example.com/unused") not in deps
    assert deps["crates", "serde"]["declared_spec"] == "1.0"
    assert deps["crates", "real-crate"]["declared_spec"] == "~2"
    assert deps["crates", "mock"]["scope"] == "dev"
    assert deps["maven", "io.ktor:ktor-server-core"]["declared_spec"] == "3.0.0"
    assert deps["docker", "registry.example:5000/team/image"]["declared_spec"] == "2"
    assert not any(key in {"base", "scratch"} for ecosystem, key in deps)
    assert deps["nuget", "foo"]["declared_spec"] == "1.2.3"


def test_a_broken_manifest_does_not_fail_the_scan(folder):
    _write(folder, "web2/package.json", "{ not json")
    _write(folder, "x/pyproject.toml", "[[[")

    assert "fastapi" in _names(profile_import.scan_folder(str(folder)))


@pytest.mark.parametrize("path", ["", "does/not/exist"])
def test_a_path_that_is_not_a_folder_is_refused(path):
    with pytest.raises(profile_import.ScanError) as error:
        profile_import.scan_folder(path)
    assert error.value.code == "not_a_folder"


def test_the_file_count_and_size_are_bounded(tmp_path):
    for i in range(profile_import.MAX_FILES + 10):
        _write(tmp_path, f"d{i}/requirements.txt", f"pkg{i}\n")
    _write(tmp_path, "huge/requirements.txt", "big\n" * 100_000)

    scan = profile_import.scan_folder(str(tmp_path))

    assert len(scan["files"]) <= profile_import.MAX_FILES and "big" not in _names(scan)


def test_the_heuristic_proposal_gives_the_stack_and_a_missing_description_but_no_keywords(folder):
    project = models.Project(name="Demo", slug="demo", description="", keywords=["flashcards"])
    scan = profile_import.scan_folder(str(folder))

    proposal = profile_import.heuristic_proposal(scan, project)

    assert "fastapi" in proposal["stack"] and proposal["keywords"] == []  # needs are not the libraries you use
    assert proposal["description"].startswith("A small tool that turns photographed pages")
    described = models.Project(name="D", slug="d", description="already written", keywords=[])
    assert profile_import.heuristic_proposal(scan, described)["description"] == ""


def test_readme_description_skips_fenced_commands_navigation_and_status_metadata():
    text = ("# Demo\n\n[Documentation and getting started in another language](docs/README.md)\n\n"
            "```powershell\nkeytool -genkeypair -keystore release.keystore -alias release\n```\n\n"
            "**Дата:** 15 октября 2026 г.\n**Статус:** текущий локальный снимок\n\n"
            "A local application for turning photographed pages into useful learning material.\n")
    assert profile_import._first_paragraph(text) == (
        "A local application for turning photographed pages into useful learning material.")


class ProposalGateway:
    namespace = "ns-profile"
    calls = 0

    def profiles(self):
        return {"routing_version": "v1"}

    def generate(self, body, key):
        self.calls += 1
        data = {"description": "Превращает фото страниц в карточки.", "keywords": ["handwriting OCR"],
                "stack": ["FastAPI", "SQLite"], "needs": ["распознавание рукописного текста"]}
        return {"id": "r1", "status": "completed", "provider": "fake", "model": "fake",
                "output": [{"type": "json", "data": data}]}


def test_the_ai_proposal_adds_keywords_and_needs(folder, db_session):
    project = models.Project(name="Demo", slug="demo", description="", keywords=[])
    db_session.add(project)
    db_session.flush()

    proposal = profile_import.ai_proposal(db_session, profile_import.scan_folder(str(folder)), project,
                                          context=AIContext(client=ProposalGateway(), max_calls=1))

    assert proposal["mode"] == "ai" and proposal["needs"] == ["распознавание рукописного текста"]
    assert proposal["keywords"] == ["handwriting OCR"]


def test_the_stack_reaches_the_ai_and_only_changes_that_projects_fingerprint():
    scanned = models.Project(name="A", slug="a", description="d", keywords=["k"], stack=["FastAPI"], needs=["OCR"])
    plain = models.Project(name="B", slug="b", description="d", keywords=["k"], stack=[], needs=[])
    before = enrichment.project_fingerprint(plain)

    payload = {p["slug"]: p for p in enrichment._projects_payload([scanned, plain])}

    assert payload["a"]["stack"] == ["FastAPI"] and payload["a"]["needs"] == ["OCR"]
    assert "stack" not in payload["b"] and "needs" not in payload["b"]
    plain.stack = None
    assert enrichment.project_fingerprint(plain) == before  # a project never scanned keeps its fingerprint
    assert enrichment.project_fingerprint(scanned) != enrichment.project_fingerprint(
        models.Project(name="A", slug="a", description="d", keywords=["k"], stack=[], needs=["OCR"]))


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db_module.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[db_module.get_db] = override
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_scan_and_apply_through_the_api(api, folder):
    project = api.post("/api/projects", json={"name": "Demo", "description": "", "keywords": ["flashcards"]}).json()

    scan = api.post(f"/api/projects/{project['id']}/scan", json={"path": str(folder)}).json()
    assert "fastapi" in scan["proposal"]["stack"] and scan["ai_error"] == ""
    assert api.get("/api/projects").json()[0]["stack"] == []  # scanning alone saves nothing

    applied = api.post(f"/api/projects/{project['id']}/apply-scan", json={
        "path": scan["path"], "stack": scan["proposal"]["stack"][:3] + ["  ", "fastapi"], "needs": ["OCR of handwriting"],
        "keywords": ["flashcards", "handwriting OCR"]}).json()

    assert applied["stack"] == scan["proposal"]["stack"][:3] + (["fastapi"] if "fastapi" not in scan["proposal"]["stack"][:3] else [])
    assert applied["needs"] == ["OCR of handwriting"] and applied["stack_scanned_at"]
    assert applied["keywords"] == ["flashcards", "handwriting OCR"] and applied["description"] == ""


def test_the_api_reports_bad_input(api, tmp_path):
    project = api.post("/api/projects", json={"name": "Demo"}).json()
    assert api.post(f"/api/projects/{project['id']}/scan", json={"path": str(tmp_path / "nope")}).json()["detail"] == "not_a_folder"
    assert api.post("/api/projects/999/scan", json={"path": str(tmp_path)}).status_code == 404
    assert api.post("/api/projects/999/apply-scan", json={"path": "x"}).status_code == 404


def test_apply_scan_persists_and_replaces_structured_dependencies(api, folder):
    project = api.post("/api/projects", json={"name": "Demo"}).json()
    url = f"/api/projects/{project['id']}"
    scan = api.post(url + "/scan", json={"path": str(folder)}).json()
    body = {"path": scan["path"], "stack": ["FastAPI"], "packages": scan["dependencies"]}
    for _ in range(2):
        response = api.post(url + "/apply-scan", json=body)
        assert response.status_code == 200
        deps = response.json()["dependencies"]
        assert len(deps) == len(scan["dependencies"])
        fastapi = next(d for d in deps if d["package_key"] == "fastapi")
        assert fastapi["declared_spec"] == ">=0.115" and fastapi["resolved_version"] == ""
        assert fastapi["manifest_path"] == "backend/requirements.txt"
        assert any(d["package_key"] == "io.ktor:ktor-client-core" for d in deps)
    body["packages"] = [{"name": "React", "ecosystem": "npm"}]  # old clients still work
    result = api.post(url + "/apply-scan", json=body).json()
    assert len(result["dependencies"]) == 1
    assert result["dependencies"][0]["package_key"] == "react"
    assert result["dependencies"][0]["declared_spec"] == ""
    assert api.delete(url).status_code == 200  # dependency rows cascade with the project
