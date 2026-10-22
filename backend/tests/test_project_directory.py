"""Directory discovery stays read-only; reviewed imports are atomic and do not duplicate profiles."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as db_module, models, project_directory
from app.main import app


def project_folder(root, name):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "README.md").write_text(
        f"# {name}\n\nA local application that turns photographed pages into useful learning material.\n", encoding="utf-8")
    (folder / "requirements.txt").write_text("fastapi>=0.115\n", encoding="utf-8")
    (folder / ".env").write_text("API_KEY=PRIVATE_SECRET", encoding="utf-8")
    return folder


@pytest.fixture()
def api():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db_module.Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False)

    def override():
        with factory() as session:
            yield session

    app.dependency_overrides[db_module.get_db] = override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        engine.dispose()


def test_scan_detects_projects_and_existing_paths_without_saving(api, tmp_path):
    new = project_folder(tmp_path, "New App")
    existing = project_folder(tmp_path, "Existing App")
    (tmp_path / "EmptyFolder").mkdir()
    project_folder(tmp_path, "node_modules")
    project_folder(tmp_path, ".hidden")
    saved = api.post("/api/projects", json={"name": "Transcription Demo", "description": "Keep this profile"}).json()
    api.post(f"/api/projects/{saved['id']}/apply-scan", json={"path": str(existing), "stack": ["Keep this stack"]})

    response = api.post("/api/projects/scan-directory", json={"path": str(tmp_path)})
    assert response.status_code == 200
    data = response.json()
    assert {p["name"] for p in data["projects"]} == {"New App", "Existing App"}
    candidates = {p["name"]: p for p in data["projects"]}
    assert candidates["Existing App"]["existing_project"]["id"] == saved["id"]
    assert candidates["New App"]["existing_project"] is None
    assert candidates["New App"]["path"] == str(new)
    assert candidates["New App"]["proposal"]["stack"] == ["fastapi"]
    assert candidates["New App"]["proposal"]["keywords"] == []
    assert candidates["New App"]["proposal"]["description"].startswith("A local application")
    assert "PRIVATE_SECRET" not in repr(data)
    assert data["skipped"] == [{"name": "EmptyFolder", "reason": "no_project_files"}]
    assert not data["truncated"]
    projects = api.get("/api/projects").json()
    assert len(projects) == 1 and projects[0]["description"] == "Keep this profile"
    assert projects[0]["stack"] == ["Keep this stack"]


def test_scan_matches_legacy_profiles_by_slug(api, tmp_path):
    project_folder(tmp_path, "SpriteLab")
    saved = api.post("/api/projects", json={"name": "Sprite-Lab"}).json()
    data = api.post("/api/projects/scan-directory", json={"path": str(tmp_path)}).json()
    assert data["projects"][0]["existing_project"]["id"] == saved["id"]


def test_import_saves_only_reviewed_profiles_and_rechecks_duplicates(api, tmp_path):
    project_folder(tmp_path, "Wanted")
    project_folder(tmp_path, "Leave Alone")
    data = api.post("/api/projects/scan-directory", json={"path": str(tmp_path)}).json()
    found = next(p for p in data["projects"] if p["name"] == "Wanted")
    body = {"path": str(tmp_path), "projects": [{
        "path": found["path"], "name": "Reviewed name", "description": "My corrected description",
        "keywords": ["handwriting OCR"], "needs": ["Offline OCR"], "stack": found["proposal"]["stack"],
        "packages": found["dependencies"], "kind": "lab", "lifecycle_status": "watch", "discovery_enabled": False,
    }]}
    response = api.post("/api/projects/import-directory", json=body)
    assert response.status_code == 200
    data = response.json()
    assert len(data["created"]) == 1 and data["skipped"] == []
    saved = data["created"][0]
    assert saved["name"] == "Reviewed name" and saved["description"] == "My corrected description"
    assert saved["keywords"] == ["handwriting OCR"] and saved["needs"] == ["Offline OCR"]
    assert saved["kind"] == "lab" and saved["lifecycle_status"] == "watch" and not saved["discovery_enabled"]
    assert saved["stack_path"] == found["path"] and saved["stack_scanned_at"]
    assert saved["stack"] == ["fastapi"]
    assert saved["dependencies"][0]["declared_spec"] == ">=0.115"
    body["projects"][0].update(name="Changed after import", description="Do not overwrite")
    repeated = api.post("/api/projects/import-directory", json=body).json()
    assert repeated["created"] == [] and repeated["skipped"][0]["project_id"] == saved["id"]
    projects = api.get("/api/projects").json()
    assert len(projects) == 1 and projects[0]["description"] == "My corrected description"


def test_import_detects_a_profile_created_after_scan(api, tmp_path):
    path = project_folder(tmp_path, "Demo")
    api.post("/api/projects/scan-directory", json={"path": str(tmp_path)})
    saved = api.post("/api/projects", json={"name": "Demo", "description": "Created elsewhere"}).json()
    data = api.post("/api/projects/import-directory", json={"path": str(tmp_path), "projects": [
        {"path": str(path), "name": "Demo", "description": "Do not replace"}]}).json()
    assert data["created"] == [] and data["skipped"][0]["project_id"] == saved["id"]
    assert api.get("/api/projects").json()[0]["description"] == "Created elsewhere"


def test_import_rejects_outside_and_nested_paths_before_saving(api, tmp_path):
    root = tmp_path / "Projects"
    first = project_folder(root, "First")
    outside = project_folder(tmp_path, "Outside")
    nested = project_folder(first, "Nested")
    for invalid in [outside, nested, root / "Missing"]:
        response = api.post("/api/projects/import-directory", json={"path": str(root), "projects": [
            {"path": str(first), "name": "First"}, {"path": str(invalid), "name": "Invalid"}]})
        assert response.status_code == 400
        assert api.get("/api/projects").json() == []


def test_import_rolls_back_the_whole_batch_on_invalid_policy(api, tmp_path):
    first = project_folder(tmp_path, "First")
    second = project_folder(tmp_path, "Second")
    response = api.post("/api/projects/import-directory", json={"path": str(tmp_path), "projects": [
        {"path": str(first), "name": "First"},
        {"path": str(second), "name": "Second", "lifecycle_status": "invalid"}]})
    assert response.status_code == 400
    assert api.get("/api/projects").json() == []


def test_scan_is_bounded_and_supports_default_directory(api, tmp_path, monkeypatch):
    monkeypatch.setattr(project_directory, "MAX_FOLDERS", 1)
    monkeypatch.setattr(project_directory, "default_path", lambda: str(tmp_path))
    project_folder(tmp_path, "First")
    project_folder(tmp_path, "Second")
    assert api.get("/api/projects/scan-directory").json()["path"] == str(tmp_path)
    data = api.post("/api/projects/scan-directory", json={}).json()
    assert data["truncated"] and len(data["projects"]) == 1 and data["limit"] == 1
    assert api.post("/api/projects/scan-directory", json={"path": str(tmp_path / "Missing")}).status_code == 400


def test_scan_does_not_follow_linked_project_folders(api, tmp_path):
    root = tmp_path / "Projects"
    root.mkdir()
    outside = project_folder(tmp_path, "Outside")
    link = root / "Linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks is not permitted on this machine")
    data = api.post("/api/projects/scan-directory", json={"path": str(root)}).json()
    assert data["projects"] == []
    response = api.post("/api/projects/import-directory", json={"path": str(root), "projects": [
        {"path": str(link), "name": "Linked"}]})
    assert response.status_code == 400
