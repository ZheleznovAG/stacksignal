"""File-backed SQLite location shared by operational tools."""
from pathlib import Path

from sqlalchemy.engine import make_url

from . import config


def database_path() -> Path:
    try:
        url = make_url(config.DATABASE_URL)
    except Exception as error:
        raise ValueError("Invalid database URL.") from error
    if (url.get_backend_name() != "sqlite" or not url.database or url.database == ":memory:"
            or url.database.startswith("file:") or url.query.get("mode") == "memory"):
        raise ValueError("This command requires a file-backed SQLite database URL.")
    return Path(url.database).expanduser().resolve()
