"""A full-text index (SQLite FTS5, BM25) over items, used by the chat to pick candidates quickly.

Without it the chat scores every item in Python, which is fine for hundreds of items and slow for
thousands. The index only *narrows* the candidates (best BM25 matches + items relevant to a mentioned
project + the newest ones); the chat's own ranking still decides the order. It is rebuilt lazily: a small
signature of the data is stored beside it and compared on use, so nothing has to remember to update it.
If FTS5 is unavailable, everything silently falls back to scoring all items.
The derived index lives in its own in-memory SQLite database, one per application engine.
It can be rebuilt after restart and never owns the caller's data transaction.
"""

import re
import threading
import weakref
from contextlib import contextmanager
from dataclasses import dataclass, field

from sqlalchemy import Engine, create_engine, func, text
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.pool import StaticPool

from . import models

_WORD = re.compile(r"[\w]+", re.UNICODE)


@dataclass
class _Index:
    engine: Engine
    lock: object = field(default_factory=threading.RLock)


_INDEXES: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()
_INDEXES_LOCK = threading.Lock()


@contextmanager
def _index_session(db: Session):
    db.flush()
    source_engine = db.get_bind().engine
    # A flushed SQLite write transaction can contain data that will still be rolled back.
    # Give that snapshot a disposable index; readers must never reuse its uncommitted text.
    private = (source_engine.dialect.name == "sqlite"
               and db.connection().connection.driver_connection.in_transaction)
    with _INDEXES_LOCK:
        index = None if private else _INDEXES.get(source_engine)
        if index is None:
            index = _Index(create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}))
            if not private:
                _INDEXES[source_engine] = index
                weakref.finalize(source_engine, index.engine.dispose)
    # StaticPool shares a connection. Refresh and search must observe the same index snapshot.
    try:
        with index.lock, Session(index.engine) as session, session.begin():
            yield session
    finally:
        if private:
            index.engine.dispose()


def item_text(item: models.Item) -> str:
    """The text that represents an item for both the FTS index and its embedding, so the two
    always agree on what an item "is about"."""
    from . import feedback
    parts = [item.name, item.description or "", " ".join(item.topics or []), item.llm_summary or "", item.why_interesting or "",
             " ".join(link.capability.name for link in item.capability_links),
             " ".join(n.note for n in feedback.notes(item))]
    return " ".join(p for p in parts if p)


def _signature(db: Session) -> str:
    items = db.query(func.count(models.Item.id), func.max(models.Item.last_enriched_at),
                     func.max(models.Item.last_seen_at)).filter(models.Item.archived.is_(False)).one()
    feedback_rows = db.query(func.max(models.ItemFeedback.id)).scalar()
    capabilities = db.query(func.count(models.ItemCapability.id)).scalar()
    return f"{items[0]}|{items[1]}|{items[2]}|{feedback_rows}|{capabilities}"


def _ensure_tables(db: Session) -> bool:
    try:
        db.execute(text("CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(name, body, tokenize='unicode61 remove_diacritics 2')"))
        db.execute(text("CREATE TABLE IF NOT EXISTS items_fts_meta (id INTEGER PRIMARY KEY CHECK (id = 1), signature TEXT)"))
        return True
    except Exception:  # SQLite built without FTS5
        return False


def refresh(db: Session) -> bool:
    """Update the derived cache in its own transaction; never commit or roll back caller data."""
    with _index_session(db) as index_db:
        return _refresh(db, index_db)


def _refresh(db: Session, index_db: Session) -> bool:
    if not _ensure_tables(index_db):
        return False
    signature = _signature(db)
    row = index_db.execute(text("SELECT signature FROM items_fts_meta WHERE id = 1")).first()
    if row is not None and row[0] == signature:
        return True
    index_db.execute(text("DELETE FROM items_fts"))
    rows = [{"id": item.id, "name": item.name, "body": item_text(item)}
            for item in db.query(models.Item).options(
                selectinload(models.Item.capability_links).joinedload(models.ItemCapability.capability),
                selectinload(models.Item.feedback)).filter(models.Item.archived.is_(False)).all()]
    if rows:
        index_db.execute(text("INSERT INTO items_fts(rowid, name, body) VALUES (:id, :name, :body)"), rows)
    index_db.execute(text("INSERT INTO items_fts_meta(id, signature) VALUES (1, :s) "
                          "ON CONFLICT(id) DO UPDATE SET signature = :s"), {"s": signature})
    return True


def _match_expression(stems: set[str]) -> str:
    terms = sorted({m.group(0) for stem in stems for m in _WORD.finditer(stem)})
    return " OR ".join(f'"{term}"*' for term in terms[:40])


def search(db: Session, stems: set[str], limit: int = 300) -> list[int] | None:
    """Ids of the best BM25 matches for the query stems (prefix matching), best first; None if the
    index cannot be used. Names count double."""
    expression = _match_expression(stems)
    if not expression:
        return None
    try:
        with _index_session(db) as index_db:
            if not _refresh(db, index_db):
                return None
            rows = index_db.execute(text("SELECT rowid FROM items_fts WHERE items_fts MATCH :q "
                                         "ORDER BY bm25(items_fts, 4.0, 1.0) LIMIT :n"),
                                     {"q": expression, "n": limit}).all()
    except Exception:
        return None
    return [row[0] for row in rows]
