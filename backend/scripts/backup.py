"""A consistent copy of the database, safe to take while StackSignal is running.

The database runs in WAL mode: recent writes live in stacksignal.db-wal until a checkpoint moves
them into stacksignal.db, so copying the .db file alone can miss them or catch a half-written page.
SQLite's online backup API copies what a reader sees at one moment, WAL included.

    .venv\\Scripts\\python.exe backend\\scripts\\backup.py            # -> configured data/backups/
    .venv\\Scripts\\python.exe backend\\scripts\\backup.py D:\\copy.db  # -> that file

Restore: stop StackSignal, replace the configured data/stacksignal.db with the copy, delete any
stacksignal.db-wal / -shm next to it, start again.
"""

import datetime
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
from app import config

SOURCE = config.DB_PATH


def backup(target: Path) -> Path:
    if not SOURCE.exists():
        raise SystemExit(f"No database at {SOURCE}")
    target.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(SOURCE.resolve().as_uri() + "?mode=ro", uri=True)
    copy = sqlite3.connect(target)
    try:
        source.backup(copy)
        if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise SystemExit(f"The copy at {target} failed its integrity check")
    finally:
        copy.close()
        source.close()
    return target


if __name__ == "__main__":
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else config.DATA_DIR / "backups" / f"stacksignal-{stamp}.db"
    done = backup(target)
    print(f"Backed up to {done} ({done.stat().st_size // 1024} KB)")
