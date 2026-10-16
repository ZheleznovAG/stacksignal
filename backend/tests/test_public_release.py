"""Publication must include new source, refuse credentials, and omit private state/history."""
import hashlib
import importlib.util
import subprocess
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "public_release.py"
spec = importlib.util.spec_from_file_location("public_release", SCRIPT)
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


@pytest.fixture()
def repository(tmp_path, monkeypatch):
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    for name in release.ROOT_FILES:
        (tmp_path / name).write_text("", encoding="utf-8")
    (tmp_path / ".gitignore").write_text("/data/\n.env\n/dist/\n", encoding="utf-8")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend/app.py").write_text("print('example')\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    monkeypatch.setattr(release, "ROOT", tmp_path)
    monkeypatch.setattr(release.make_archive, "ROOT", tmp_path)
    return tmp_path


def test_archive_contains_current_source_without_history_or_private_state(repository):
    (repository / "backend/new.py").write_text("# uncommitted new source\n", encoding="utf-8")
    (repository / "backend/windows.py").write_bytes(b"# windows checkout\r\n")
    (repository / "data").mkdir()
    (repository / "data/private.json").write_text('{"private":true}', encoding="utf-8")
    (repository / ".env").write_text("AI_API_KEY=local-only-value-123456\n", encoding="utf-8")
    (repository / "private-notes.md").write_text("not public", encoding="utf-8")
    target = repository / "dist/public.zip"
    result = release.write_public_zip(target, release.source_contents())
    with zipfile.ZipFile(target) as archive:
        names = archive.namelist()
        assert "StackSignal/backend/new.py" in names
        assert archive.read("StackSignal/backend/windows.py") == b"# windows checkout\n"
        assert not any("/.git/" in p or "/data/" in p or p.endswith("/.env") for p in names)
        assert not any("private-notes" in p or "history.bundle" in p for p in names)
        assert archive.getinfo("StackSignal/run.sh").external_attr >> 16 == 0o100755
    assert result["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert target.with_suffix(".zip.sha256").exists()
    assert target.with_suffix(".zip.manifest.json").exists()
    with pytest.raises(release.make_archive.ArchiveError, match="already exists"):
        release.write_public_zip(target, {})


@pytest.mark.parametrize("value,rule", [
    ("ghp_" + "A" * 36, "github-token"),
    ("sk-ant-" + "a" * 40, "provider-key"),
    ("123456789:" + "A" * 35, "telegram-token"),
    ("local-only-value-123456", "local-credential-value"),
])
def test_credentials_are_refused_without_echoing_the_value(repository, value, rule):
    (repository / ".env").write_text("AI_API_KEY=local-only-value-123456\n", encoding="utf-8")
    (repository / "backend/leak.py").write_text(value, encoding="utf-8")
    with pytest.raises(release.make_archive.ArchiveError) as error:
        release.source_contents()
    assert rule in str(error.value) and "backend/leak.py" in str(error.value)
    assert value not in str(error.value)


def test_forced_tracking_of_runtime_data_fails_the_public_check(repository):
    (repository / "data").mkdir()
    (repository / "data/fake.db").write_bytes(b"synthetic database")
    subprocess.run(["git", "-C", str(repository), "add", "-f", "data/fake.db"], check=True)
    with pytest.raises(release.make_archive.ArchiveError, match="Private file tracked"):
        release.source_contents()


def test_history_audit_finds_a_removed_token_without_printing_it(repository):
    value = "ghp_" + "B" * 36
    path = repository / "backend/old.py"
    path.write_text(value, encoding="utf-8")
    git = ["git", "-C", str(repository)]
    subprocess.run([*git, "add", "."], check=True)
    identity = ["-c", "user.name=Example", "-c", "user.email=example@example.invalid"]
    subprocess.run([*git, *identity, "commit", "--quiet", "-m", "Synthetic fixture"], check=True)
    path.unlink()
    subprocess.run([*git, "add", "-u"], check=True)
    subprocess.run([*git, *identity, "commit", "--quiet", "-m", "Remove fixture"], check=True)
    report = release.audit_history()
    assert any("github-token" in r["rules"] for r in report["credential_or_private_path_findings"])
    assert value not in str(report)


@pytest.mark.parametrize("path", ["../backend/x.py", "backend/../x.py", "/backend/x.py",
                                  "backend\\x.py", "backend/data/private.py"])
def test_paths_cannot_escape_the_public_source_tree(path):
    assert not release.is_public(path)
