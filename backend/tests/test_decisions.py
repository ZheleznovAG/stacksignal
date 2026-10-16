"""Decisions per project: the same item can be Trial for one project and Rejected for another,
every change is remembered with its reason, the project radar has its own inbox, and a decided
item is not nagged about in the digest for that project."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as db_module, decisions, digest, models
from app.main import app
from app.utils import utcnow


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
    seed = Session()
    chitalka = models.Project(name="Chitalka", slug="chitalka", description="d", keywords=["ocr"])
    softbody = models.Project(name="Softbody Demo", slug="softbody", description="d", keywords=["physics"])
    seed.add_all([chitalka, softbody])
    seed.flush()
    for name, score in (("ocr-lib", 0.9), ("other-lib", 0.8), ("weak-lib", 0.2)):
        item = models.Item(source="github", external_id=f"o/{name}", name=name, owner="o", url="u",
                           description="d", topics=[], last_seen_at=utcnow())
        seed.add(item)
        seed.flush()
        seed.add(models.ItemProjectRelevance(item_id=item.id, project_id=chitalka.id, score=score, rationale="why"))
    seed.commit()
    seed.close()
    yield TestClient(app), Session
    app.dependency_overrides.clear()


def _id(client, name):
    return next(i["id"] for i in client.get("/api/discover?limit=50").json() if i["name"] == name)


def test_one_item_can_have_different_decisions_for_different_projects(api):
    client, _ = api
    item_id = _id(client, "ocr-lib")

    client.put(f"/api/items/{item_id}/decisions/chitalka", json={"status": "trial", "goal": "20 images on a phone"})
    result = client.put(f"/api/items/{item_id}/decisions/softbody", json={"status": "ignore", "verdict": "no use"}).json()

    by_project = {d["project_slug"]: d for d in result["decisions"]}
    assert by_project["chitalka"]["status"] == "trial" and by_project["chitalka"]["goal"] == "20 images on a phone"
    assert by_project["softbody"]["status"] == "ignore" and by_project["softbody"]["verdict"] == "no use"
    assert result["status"] == "new"  # the global status is a separate thing


def test_changing_a_decision_keeps_goal_and_records_history_with_the_reason(api):
    client, _ = api
    item_id = _id(client, "ocr-lib")
    client.put(f"/api/items/{item_id}/decisions/chitalka", json={"status": "trial", "goal": "measure speed"})

    result = client.put(f"/api/items/{item_id}/decisions/chitalka",
                        json={"status": "ignore", "verdict": "too slow on Android"}).json()

    (decision,) = result["decisions"]
    assert decision["status"] == "ignore" and decision["goal"] == "measure speed"  # goal kept
    history = [h for h in result["status_history"] if h["project_slug"] == "chitalka"]
    assert [h["status"] for h in history] == ["trial", "ignore"]
    assert "too slow on Android" in history[-1]["note"]


def test_a_decision_can_be_cleared(api):
    client, _ = api
    item_id = _id(client, "ocr-lib")
    client.put(f"/api/items/{item_id}/decisions/chitalka", json={"status": "watch"})

    result = client.delete(f"/api/items/{item_id}/decisions/chitalka").json()

    assert result["decisions"] == []
    assert result["status_history"][-1]["note"] == "Decision cleared"


def test_invalid_input_is_rejected(api):
    client, _ = api
    item_id = _id(client, "ocr-lib")
    assert client.put(f"/api/items/{item_id}/decisions/chitalka", json={"status": "great"}).status_code == 400
    assert client.put(f"/api/items/{item_id}/decisions/chitalka", json={"status": "new"}).status_code == 400
    assert client.put(f"/api/items/{item_id}/decisions/nope", json={"status": "watch"}).status_code == 404
    assert client.put("/api/items/9999/decisions/chitalka", json={"status": "watch"}).status_code == 404


def test_project_radar_groups_decisions_and_has_an_inbox_of_undecided_relevant_items(api):
    client, _ = api
    client.put(f"/api/items/{_id(client, 'ocr-lib')}/decisions/chitalka", json={"status": "trial"})

    board = client.get("/api/radar?project_slug=chitalka").json()

    assert [i["name"] for i in board["trial"]] == ["ocr-lib"]
    assert board["trial"][0]["decision"]["status"] == "trial"
    assert [i["name"] for i in board["new"]] == ["other-lib"]  # relevant, undecided; weak-lib is below 0.6
    assert client.get("/api/radar?project_slug=softbody").json()["trial"] == []
    assert client.get("/api/radar?project_slug=nope").status_code == 404
    assert "new" in client.get("/api/radar").json()  # the overall radar still works


def test_deleting_a_project_removes_its_decisions(api):
    client, Session = api
    client.put(f"/api/items/{_id(client, 'ocr-lib')}/decisions/chitalka", json={"status": "watch"})
    project_id = next(p["id"] for p in client.get("/api/projects").json() if p["slug"] == "chitalka")

    client.delete(f"/api/projects/{project_id}")

    session = Session()
    assert session.query(models.ItemProjectDecision).count() == 0


def test_the_digest_does_not_nag_about_what_is_decided_for_that_project(api):
    client, Session = api
    decided, undecided = _id(client, "ocr-lib"), _id(client, "other-lib")
    client.put(f"/api/items/{decided}/decisions/chitalka", json={"status": "trial"})

    session = Session()
    ids = {entry["item_id"] for entry in digest.select_items(session, min_relevance=0.6, limit=10)}
    assert undecided in ids and decided not in ids


def test_the_inbox_only_offers_ai_read_items_once_the_ai_has_read_anything(api):
    client, Session = api
    session = Session()
    other = session.query(models.Item).filter_by(name="other-lib").one()
    session.add(models.ItemAnalysisState(item_id=other.id, provenance={"mode": "ai"}))
    session.commit()

    board = client.get("/api/radar?project_slug=chitalka").json()

    assert [i["name"] for i in board["new"]] == ["other-lib"]  # ocr-lib is only a keyword match


def test_decisions_and_their_reasons_calibrate_the_ai(api):
    from app import feedback
    client, Session = api
    rejected, trying, undecided = _id(client, "ocr-lib"), _id(client, "other-lib"), _id(client, "weak-lib")
    client.put(f"/api/items/{rejected}/decisions/chitalka", json={"status": "ignore", "verdict": "too slow on Android"})
    client.put(f"/api/items/{trying}/decisions/chitalka", json={"status": "trial", "goal": "20 photos"})
    client.put(f"/api/items/{undecided}/decisions/softbody", json={"status": "watch"})  # watch is not a strong signal

    session = Session()
    by_item = {e["item"]: e for e in feedback.examples(session)}

    assert by_item["ocr-lib"]["verdict"] == "rejected" and by_item["ocr-lib"]["note"] == "too slow on Android"
    assert by_item["other-lib"]["verdict"] == "trying" and by_item["other-lib"]["note"] == "20 photos"
    assert "weak-lib" not in by_item
