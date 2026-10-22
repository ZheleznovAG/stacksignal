import importlib.util
from pathlib import Path
import sqlite3

import pytest

from app import config
from app.storage import database_path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/backup.py"
spec = importlib.util.spec_from_file_location("backup_script", SCRIPT)
backup_script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup_script)


def test_backup_uses_database_override_and_includes_committed_wal_writes(tmp_path, monkeypatch):
    source, target = tmp_path / "custom.db", tmp_path / "snapshot.db"
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{source}")
    connection = sqlite3.connect(source)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("CREATE TABLE preserved (note TEXT)")
        connection.execute("INSERT INTO preserved VALUES ('committed WAL row')")
        connection.commit()
        assert database_path() == source
        backup_script.backup(target)
        with sqlite3.connect(target) as snapshot:
            assert snapshot.execute("SELECT note FROM preserved").fetchall() == [("committed WAL row",)]
    finally:
        connection.close()


def test_backup_refuses_source_and_existing_targets(tmp_path, monkeypatch):
    source, target = tmp_path / "source.db", tmp_path / "existing.db"
    with sqlite3.connect(source) as connection:
        connection.execute("CREATE TABLE kept (id INTEGER)")
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{source}")
    with pytest.raises(ValueError, match="differ"):
        backup_script.backup(source)
    target.write_bytes(b"keep this existing backup")
    with pytest.raises(FileExistsError):
        backup_script.backup(target)
    assert target.read_bytes() == b"keep this existing backup"


@pytest.mark.parametrize("url", ["sqlite:///:memory:", "postgresql://localhost/example", "not-a-url", "sqlite:///file:example?mode=memory"])
def test_operational_tools_refuse_non_file_sqlite_databases(monkeypatch, url):
    monkeypatch.setattr(config, "DATABASE_URL", url)
    with pytest.raises(ValueError):
        database_path()
