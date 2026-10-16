"""Chat candidates through the FTS5 index: it narrows a big catalogue, follows the data without anyone
updating it, ignores archived items, works for Russian questions, and falls back when unavailable."""

import datetime

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app import chat, config, models, search_index
from app.db import Base
from app.utils import utcnow

NOW = utcnow()


def _item(db, name, description, **fields):
    item = models.Item(source="github", external_id=f"o/{name}", name=name, owner="o", url="u", description=description,
                       topics=[], momentum_score=fields.pop("momentum_score", 0.1), last_seen_at=NOW,
                       first_seen_at=fields.pop("first_seen_at", NOW - datetime.timedelta(days=90)), **fields)
    db.add(item)
    db.flush()
    return item


def _catalogue(db, n=60):
    items = [_item(db, f"filler{i}", f"generic utility number {i}") for i in range(n)]
    target = _item(db, "whisperx", "speech recognition with word timestamps and diarization")
    return items, target


def test_the_index_finds_matches_and_ranks_the_name_higher(db_session):
    _, target = _catalogue(db_session)
    other = _item(db_session, "misc", "mentions whisperx once in passing")

    ids = search_index.search(db_session, {"whisp"})

    assert ids[0] == target.id and other.id in ids  # prefix match; the name outranks a passing mention


def test_the_index_follows_the_data_without_being_told(db_session):
    _, target = _catalogue(db_session)
    assert search_index.search(db_session, {"diariz"}) == [target.id]

    target.llm_summary = "now also does quantum error correction"
    late = _item(db_session, "newcomer", "quantum annealing toolkit")

    assert set(search_index.search(db_session, {"quantum"})) == {target.id, late.id}


def test_archived_items_are_not_indexed(db_session):
    _, target = _catalogue(db_session)
    target.archived = True

    assert search_index.search(db_session, {"diariz"}) == []


def test_a_query_without_usable_terms_or_an_unavailable_index_gives_none(db_session, monkeypatch):
    _catalogue(db_session, 3)
    assert search_index.search(db_session, set()) is None
    monkeypatch.setattr(search_index, "_ensure_tables", lambda db: False)
    assert search_index.search(db_session, {"whisp"}) is None


def test_a_big_catalogue_is_narrowed_before_scoring(db_session, monkeypatch):
    monkeypatch.setattr(config, "CHAT_FTS_MIN_ITEMS", 10)
    items, target = _catalogue(db_session, 80)

    found = chat._retrieve(db_session, "which tool does speech recognition with diarization?")

    assert found[0].id == target.id
    assert len(chat._candidates(db_session, {"diariz"}, set(), False, {})) <= 45  # 1 match + the fastest 40, not all 81


def test_a_small_catalogue_still_scores_everything(db_session, monkeypatch):
    monkeypatch.setattr(config, "CHAT_FTS_MIN_ITEMS", 500)
    items, target = _catalogue(db_session, 20)

    assert len(chat._candidates(db_session, {"diariz"}, set(), False, {})) == len(items) + 1


def test_a_russian_question_reaches_english_items_through_the_glossary(db_session, monkeypatch):
    monkeypatch.setattr(config, "CHAT_FTS_MIN_ITEMS", 10)
    _, target = _catalogue(db_session, 60)
    target.llm_summary = "Speech recognition (transcription) with speaker diarization"

    found = chat._retrieve(db_session, "что есть для распознавания речи?")

    assert found and found[0].id == target.id


def test_items_of_a_mentioned_project_are_always_candidates(db_session, monkeypatch):
    monkeypatch.setattr(config, "CHAT_FTS_MIN_ITEMS", 10)
    project = models.Project(name="Chitalka", slug="chitalka", description="d", keywords=["ocr"])
    db_session.add(project)
    items, _ = _catalogue(db_session, 60)
    db_session.flush()
    db_session.add(models.ItemProjectRelevance(item_id=items[5].id, project_id=project.id, score=0.9, rationale="r"))
    db_session.flush()

    candidates = chat._candidates(db_session, {"nomatchword"}, {project.id}, False, {})

    assert items[5] in candidates


def test_large_catalogue_rrf_rewards_keyword_and_semantic_consensus(db_session, monkeypatch):
    monkeypatch.setattr(config, "CHAT_FTS_MIN_ITEMS", 10)
    _, target = _catalogue(db_session, 60)
    semantic_only = _item(db_session, "semantic-only", "unrelated words")
    db_session.flush()

    # Both are semantic hits, but target is also the best BM25 hit. RRF should reward agreement
    # between independent retrieval channels instead of comparing BM25 and cosine raw values.
    monkeypatch.setattr(chat.embeddings, "semantic_scores",
                        lambda db, q: {semantic_only.id: 0.99, target.id: 0.70})

    found = chat._retrieve(db_session, "whisperx")

    assert found[0].id == target.id


def test_refresh_does_not_commit_the_callers_transaction(db_session):
    item = _item(db_session, "transient", "caller can roll this back")
    assert search_index.refresh(db_session)
    assert search_index.search(db_session, {"transient"}) == [item.id]

    db_session.rollback()

    assert db_session.query(models.Item).count() == 0
    assert search_index.search(db_session, {"transient"}) == []


def test_index_transaction_is_separate_from_file_database_writes(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fts.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as writer:
        item = _item(writer, "persisted", "initial description")
        writer.commit()
        item.description = "uncommitted change"
        writer.flush()  # hold a real SQLite writer lock while the cache builds
        assert search_index.search(writer, {"uncommitted"}) == [item.id]
        with Session(engine) as reader:
            assert reader.query(models.Item).one().description == "initial description"
            assert search_index.search(reader, {"uncommitted"}) == []
        writer.rollback()
        assert search_index.search(writer, {"uncommitted"}) == []
        assert search_index.search(writer, {"initial"}) == [item.id]
    with engine.connect() as conn:
        assert conn.execute(text("SELECT name FROM sqlite_master WHERE name LIKE 'items_fts%'" )).all() == []
    engine.dispose()
