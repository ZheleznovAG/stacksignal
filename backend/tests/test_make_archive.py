"""Regression tests for the repository exporter without invoking Git or creating a full bundle."""

import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "make_archive.py"
spec = importlib.util.spec_from_file_location("make_archive", SCRIPT)
make_archive = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(make_archive)


def test_stale_projectpack_directory_is_always_excluded():
    assert make_archive.is_secret("projectpack-deadbeef/stacksignal.db")
    assert make_archive.is_secret("projectpack-deadbeef/nested/source.py")


def test_normal_source_and_env_example_are_not_accidentally_excluded():
    assert not make_archive.is_secret("backend/app/chat.py")
    assert not make_archive.is_secret(".env.example")


def test_private_credentials_are_checked_without_being_exported(tmp_path, monkeypatch):
    project, private = tmp_path / "project", tmp_path / "private"
    project.mkdir()
    private.mkdir()
    (project / ".env").write_text('STACKSIGNAL_PRIVATE_DIR="../private"\n', encoding="utf-8")
    (private / ".env").write_text("AI_API_KEY=private-placeholder\n", encoding="utf-8")
    monkeypatch.setattr(make_archive, "ROOT", project)
    monkeypatch.delenv("STACKSIGNAL_PRIVATE_DIR", raising=False)
    values = make_archive.secret_values()
    assert values == [b"private-placeholder"]
    with pytest.raises(make_archive.ArchiveError, match="Possible credential") as error:
        make_archive.check_bytes("example.py", b"private-placeholder", values)
    assert "private-placeholder" not in str(error.value)
    with pytest.raises(make_archive.ArchiveError, match="Separate private storage"):
        make_archive.write_zip(tmp_path / "transfer.zip", include_data=True)
    assert not (tmp_path / "transfer.zip").exists()
