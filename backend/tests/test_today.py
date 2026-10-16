"""The home screen endpoint: which new items it surfaces, the per-project inbox, and the health line
that flags a run in which the AI read nothing although it should have."""

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as db_module, models
from app.main import app
from app.routers import today as today_module
from app.utils import utcnow

NOW = utcnow()


@pytest.fixture()
def api():
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
    yield TestClient(app), Session
    app.dependency_overrides.clear()


def _project(db, slug, tier="active"):
    project = models.Project(name=slug.title(), slug=slug, description="d", keywords=["k"], tier=tier)
    db.add(project)
    db.flush()
    return project


def _item(db, name, *, days_old=1, ai=True, status=models.Status.NEW, relevance=()):
    item = models.Item(source="github", external_id=f"o/{name}", name=name, owner="o", url=f"https://x/{name}",
                       description="d", topics=[], raw={}, status=status,
                       first_seen_at=NOW - datetime.timedelta(days=days_old))
    db.add(item)
    db.flush()
    db.add(models.ItemAnalysisState(item_id=item.id, provenance={"mode": "ai" if ai else "heuristic"}))
    for project, score in relevance:
        db.add(models.ItemProjectRelevance(item_id=item.id, project_id=project.id, score=score, rationale="because"))
    db.flush()
    return item


def test_fresh_shows_only_new_ai_read_relevant_undecided_items_for_attended_projects(api):
    client, Session = api
    db = Session()
    active, asleep = _project(db, "chitalka"), _project(db, "softbody", tier="dormant")
    _item(db, "keeper", relevance=[(active, 0.8)])
    _item(db, "keyword-only", ai=False, relevance=[(active, 0.9)])
    _item(db, "old-news", days_old=30, relevance=[(active, 0.9)])
    _item(db, "dormant-only", relevance=[(asleep, 0.9)])
    _item(db, "ignored", status=models.Status.IGNORE, relevance=[(active, 0.9)])
    _item(db, "weak", relevance=[(active, 0.3)])
    decided = _item(db, "decided", relevance=[(active, 0.9)])
    db.add(models.ItemProjectDecision(item_id=decided.id, project_id=active.id, status="trial"))
    db.commit()

    fresh = client.get("/api/today").json()["fresh"]

    assert [i["name"] for i in fresh] == ["keeper"]
    assert fresh[0]["matched_relevance"]["project_slug"] == "chitalka"


def test_inbox_counts_undecided_items_per_attended_project(api):
    client, Session = api
    db = Session()
    active, asleep = _project(db, "chitalka"), _project(db, "softbody", tier="dormant")
    for index in range(4):
        _item(db, f"s{index}", days_old=40, relevance=[(active, 0.9 - index * 0.05)])
    _item(db, "j", relevance=[(asleep, 0.9)])
    db.commit()

    inbox = client.get("/api/today").json()["inbox"]

    assert [row["project_slug"] for row in inbox] == ["chitalka"]
    assert inbox[0]["count"] == 4 and not inbox[0]["capped"]
    assert [entry["name"] for entry in inbox[0]["top"]] == ["s0", "s1", "s2"]


def _run(db, enrich_stage):
    db.add(models.CollectionRun(sources=["github"], status="completed", finished_at=NOW,
                                stages=[{"name": "collect", "status": "ok", "duration_s": 1.0}, enrich_stage]))
    db.commit()


def test_health_flags_a_run_where_the_ai_read_nothing_although_it_should_have(api, monkeypatch):
    client, Session = api
    monkeypatch.setattr(today_module, "LLM_ENABLED", True)
    _run(Session(), {"name": "enrich", "status": "ok", "duration_s": 0.4, "ai": 0, "heuristic": 42, "ai_errors": 0})

    health = client.get("/api/today").json()["health"]

    assert health["ai_silent"] is True and health["ai_read_last_run"] == 0
    assert health["last_run"]["stages"][1]["heuristic"] == 42


def test_health_is_quiet_when_the_ai_read_items_or_is_not_configured(api, monkeypatch):
    client, Session = api
    _run(Session(), {"name": "enrich", "status": "ok", "duration_s": 9.0, "ai": 0, "heuristic": 42, "ai_errors": 0})
    assert client.get("/api/today").json()["health"]["ai_silent"] is False  # keyword mode on purpose

    monkeypatch.setattr(today_module, "LLM_ENABLED", True)
    _run(Session(), {"name": "enrich", "status": "ok", "duration_s": 9.0, "ai": 18, "heuristic": 42, "ai_errors": 0})
    assert client.get("/api/today").json()["health"]["ai_silent"] is False


def test_health_endpoint_does_not_call_a_gateway_when_ai_is_off(api):
    client, _ = api
    assert client.get("/api/health").json()["ai_gateway"] is None


def test_stats_count_live_ai_analyses_from_gateway_and_local_agents(api):
    client, Session = api
    with Session() as db:
        for provider in ("chatgpt-web", "claude-code"):
            item = _item(db, provider)
            db.get(models.ItemAnalysisState, item.id).provenance = {"mode": "ai", "provider": provider}
        _item(db, "keyword-only", ai=False)
        unseen = _item(db, "not-yet-analyzed", ai=False)
        db.delete(db.get(models.ItemAnalysisState, unseen.id))
        _item(db, "archived-ai").archived = True
        db.commit()

    stats = client.get("/api/stats").json()

    assert stats["items"] == 4
    assert stats["ai_analyzed"] == 2
    assert stats["archived"] == 1


def test_overall_radar_can_be_capped_per_status_with_true_counts(api):
    client, Session = api
    db = Session()
    for index in range(3):
        _item(db, f"n{index}", days_old=40)
    archived = _item(db, "gone", days_old=40)
    archived.archived = True
    db.commit()

    body = client.get("/api/radar?per_status=2").json()

    assert len(body["groups"]["new"]) == 2 and body["counts"]["new"] == 3
    assert body["counts"]["adopt"] == 0 and body["groups"]["adopt"] == []
    assert isinstance(client.get("/api/radar").json()["new"], list)  # the old shape is unchanged
