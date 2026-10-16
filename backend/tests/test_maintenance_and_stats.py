"""One-time radar clean-up, the AI / status-source flags on items, and the stats endpoint."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as db_module, maintenance, models
from app.main import app


def _item(db, name, status, history_notes, mode=None):
    item = models.Item(source="github", external_id=f"o/{name}", name=name, owner="o", url="https://x",
                       description="d", topics=[], raw={}, status=status)
    db.add(item)
    db.flush()
    for note in history_notes:
        db.add(models.StatusHistory(item_id=item.id, status=status, note=note))
    if mode:
        db.add(models.ItemAnalysisState(item_id=item.id, provenance={"mode": mode}))
    db.flush()
    db.expire(item, ["status_history"])
    return item


def test_reset_only_touches_keyword_only_auto_suggestions(db_session):
    auto_keyword = _item(db_session, "auto-keyword", models.Status.TRIAL, ["Discovered", "Auto-suggested (x)"], "heuristic")
    auto_no_state = _item(db_session, "auto-nostate", models.Status.ASSESS, ["Auto-suggested (x)"])
    auto_ai = _item(db_session, "auto-ai", models.Status.ASSESS, ["Auto-suggested (x)"], "ai")
    by_you = _item(db_session, "by-you", models.Status.ADOPT, ["Auto-suggested (x)", "Changed from UI"], "heuristic")
    already_new = _item(db_session, "new", models.Status.NEW, ["Discovered"], "heuristic")

    assert maintenance.reset_heuristic_auto_statuses(db_session) == 2

    assert auto_keyword.status == models.Status.NEW and auto_no_state.status == models.Status.NEW
    assert auto_ai.status == models.Status.ASSESS  # AI-backed suggestion stays
    assert by_you.status == models.Status.ADOPT  # a person decided this
    assert already_new.status == models.Status.NEW
    assert db_session.query(models.StatusHistory).filter_by(item_id=auto_keyword.id).all()[-1].note == maintenance.RESET_NOTE


def test_apply_pending_runs_once(db_session):
    _item(db_session, "a", models.Status.TRIAL, ["Auto-suggested (x)"], "heuristic")

    assert maintenance.apply_pending(db_session) == {maintenance.RESET_HEURISTIC_STATUS: 1,
                                                     maintenance.CAP_AUTO_STATUS: 0,
                                                     maintenance.ARCHIVE_LOW_QUALITY: 1,
                                                     maintenance.SPLIT_FINGERPRINTS: 0,
                                                     maintenance.MARK_HEURISTIC_OPPORTUNITIES: 0,
                                                     maintenance.ARXIV_DEFAULT_QUERIES: 0,
                                                     maintenance.PURGE_ORPHANS: 0}
    assert maintenance.apply_pending(db_session) == {}  # recorded, never repeated


def test_auto_trial_and_adopt_are_capped_at_assess_but_yours_are_kept(db_session):
    auto_trial = _item(db_session, "auto-trial", models.Status.TRIAL, ["Discovered", "Auto-suggested (x)"], "ai")
    auto_adopt = _item(db_session, "auto-adopt", models.Status.ADOPT, ["Auto-suggested (x)"], "ai")
    yours = _item(db_session, "yours", models.Status.TRIAL, ["Auto-suggested (x)", "Changed from UI"], "ai")
    assess = _item(db_session, "assess", models.Status.ASSESS, ["Auto-suggested (x)"], "ai")

    assert maintenance.cap_auto_statuses_at_assess(db_session) == 2

    assert auto_trial.status == models.Status.ASSESS and auto_adopt.status == models.Status.ASSESS
    assert yours.status == models.Status.TRIAL  # you chose this
    assert assess.status == models.Status.ASSESS


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    db_module.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override():
        session = Session()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[db_module.get_db] = override
    seed = Session()
    project = models.Project(name="Читалка", slug="chitalka", keywords=[], description="d")
    seed.add(project)
    seed.flush()
    ai = _item(seed, "ai-item", models.Status.ASSESS, ["Discovered", "Auto-suggested (x)"], "ai")
    plain = _item(seed, "plain-item", models.Status.NEW, ["Discovered"], "heuristic")
    mine = _item(seed, "mine", models.Status.TRIAL, ["Discovered", "Changed from UI"], "heuristic")
    for item, score in ((ai, 0.9), (plain, 0.8), (mine, 0.7)):
        seed.add(models.ItemProjectRelevance(item_id=item.id, project_id=project.id, score=score, rationale="r"))
    seed.commit()
    seed.close()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_items_report_ai_analysis_and_who_set_the_status(client):
    items = {i["name"]: i for i in client.get("/api/discover?limit=10").json()}
    assert items["ai-item"]["ai_analyzed"] is True and items["plain-item"]["ai_analyzed"] is False
    assert items["ai-item"]["status_source"] == "auto" and items["mine"]["status_source"] == "you"


def test_ai_only_filters_discover_and_relevant(client):
    assert [i["name"] for i in client.get("/api/discover?ai_only=true").json()] == ["ai-item"]
    relevant = client.get("/api/relevant?ai_only=true").json()
    assert [i["name"] for i in relevant] == ["ai-item"]
    assert relevant[0]["matched_relevance"]["project_name"] == "Читалка"  # a name, not a slug
    assert len(client.get("/api/relevant").json()) == 3


def test_stats_summarise_ai_coverage(client):
    stats = client.get("/api/stats").json()
    assert stats["items"] == 3 and stats["ai_analyzed"] == 1 and stats["projects"] == 1
    assert stats["last_run"] is None and stats["votes"] == 0
