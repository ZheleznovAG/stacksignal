import sqlite3

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from .config import DATABASE_URL, ROOT_DIR

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, _record):
    """WAL lets the dashboard read while a collection run writes; busy_timeout makes a briefly
    locked database wait instead of failing."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


@event.listens_for(Engine, "connect")
def _sqlite_foreign_keys(dbapi_connection, _record):
    """SQLite ignores FOREIGN KEY declarations unless each connection turns them on. Every SQLite
    engine gets it -- the tests' in-memory ones too, so a delete that would leave dangling rows fails
    there first. Alembic's env.py turns it off again for migrations (table rebuilds need that)."""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Bring the database up to the latest schema. A brand new database gets every table from
    the baseline Alembic revision; an existing one is fast-forwarded from wherever it is. Schema
    changes from here on are Alembic revisions (`alembic revision --autogenerate`), not an
    ever-growing hand-rolled column list."""
    from alembic import command
    from alembic.config import Config

    from . import models  # noqa: F401  (ensure models are registered, for `alembic check` / autogenerate)

    backend_dir = ROOT_DIR / "backend"
    cfg = Config(str(backend_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_dir / "alembic"))
    command.upgrade(cfg, "head")
