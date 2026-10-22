"""A consistent copy of the database, safe to take while StackSignal is running.

The database runs in WAL mode: recent writes live in stacksignal.db-wal until a checkpoint moves
them into stacksignal.db, so copying the .db file alone can miss them or catch a half-written page.
SQLite's online backup API copies what a reader sees at one moment, WAL included.

    python stacksignal.py backup               # -> configured data/backups/
    python stacksignal.py backup path/copy.db   # -> that new file

Restore: stop StackSignal, replace the configured data/stacksignal.db with the copy, delete any
stacksignal.db-wal / -shm next to it, start again.
"""

import datetime
from contextlib import closing
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app import config
from app.storage import database_path


def backup(target: Path, *, source_path: Path | None = None) -> Path:
    source_path = (source_path or database_path()).resolve()
    target = target.expanduser().resolve()
    if not source_path.is_file():
        raise ValueError("The configured database does not exist; start StackSignal first.")
    if source_path == target:
        raise ValueError("The backup target must differ from the source database.")
    if target.exists():
        raise FileExistsError("Backup target already exists; choose a new filename.")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Reserve a new target without ever overwriting a previous backup.
    target.open("xb").close()
    try:
        with closing(sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(target)) as copy:
                source.backup(copy)
                if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("The backup failed its integrity check.")
    except Exception:
        target.unlink(missing_ok=True)  # Only the new file reserved by this call.
        raise
    return target


if __name__ == "__main__":
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else config.DATA_DIR / "backups" / f"stacksignal-{stamp}.db"
    try:
        done = backup(target)
    except (ValueError, OSError, sqlite3.Error) as error:
        raise SystemExit(str(error))
    print(f"Backed up to {done} ({done.stat().st_size // 1024} KB)")
