"""User feedback: votes outrank the analysis (and survive re-analysis), notes, calibration
examples for the model, chat memory and the HTTP API."""

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import chat, db as db_module, enrichment, feedback, models, opportunities, pipeline
from app.ai_analysis import AIContext
from app.enrichment import Enrichment
from app.main import app
from app.utils import utcnow


def _project(db, slug="chitalka", name="Читалка"):
    project = models.Project(name=name, slug=slug, keywords=["ocr"], description="d")
    db.add(project)
    db.flush()
    return project


def _item(db, name="whisper", status=models.Status.NEW, relevance=None, summary="Fast local speech recognition"):
    item = models.Item(source="github", external_id=f"o/{name}", name=name, owner="o", url=f"https://x/{name}",
                       description=summary, llm_summary=summary, topics=[], momentum_score=0.5, raw={}, status=status)
    db.add(item)
    db.flush()
    for project, score in (relevance or {}).items():
        db.add(models.ItemProjectRelevance(item_id=item.id, project_id=project.id, score=score,
                                           base_score=score, rationale="model says so"))
    db.flush()
    return item


def _row(db, item, project):
    return db.query(models.ItemProjectRelevance).filter_by(item_id=item.id, project_id=project.id).one_or_none()


def test_latest_vote_wins_and_cleared_votes_disappear(db_session):
    project = _project(db_session)
    item = _item(db_session, relevance={project: 0.5})

    feedback.record_vote(db_session, item, "chitalka", 1)
    feedback.record_vote(db_session, item, "chitalka", -1)
    assert feedback.current_votes(item) == {"chitalka": -1}
    feedback.record_vote(db_session, item, "chitalka", 0)
    assert feedback.current_votes(item) == {}


def test_useful_raises_the_score_and_is_idempotent(db_session):
    project = _project(db_session)
    item = _item(db_session, relevance={project: 0.3})

    feedback.record_vote(db_session, item, "chitalka", 1)
    feedback.apply_votes(db_session, item)  # applying again must not stack prefixes

    row = _row(db_session, item, project)
    assert row.score == feedback.BOOST_SCORE and row.base_score == 0.3
    assert row.rationale == feedback.PREFIX_USEFUL + "model says so"


def test_not_relevant_drops_the_item_out_of_every_threshold(db_session):
    project = _project(db_session)
    item = _item(db_session, relevance={project: 0.8})

    feedback.record_vote(db_session, item, "chitalka", -1)

    row = _row(db_session, item, project)
    assert row.score == feedback.SUPPRESS_SCORE < 0.1  # below /api/relevant's 0.1 floor
    assert row.rationale.startswith(feedback.PREFIX_NOT_RELEVANT)


def test_useful_vote_creates_a_row_the_analysis_never_produced(db_session):
    project = _project(db_session)
    item = _item(db_session)

    feedback.record_vote(db_session, item, "chitalka", 1)

    row = _row(db_session, item, project)
    assert row.score == feedback.BOOST_SCORE and row.base_score == 0.0


def test_clearing_a_vote_restores_the_analysis_score(db_session):
    project = _project(db_session)
    item = _item(db_session, relevance={project: 0.4})

    feedback.record_vote(db_session, item, "chitalka", -1)
    feedback.record_vote(db_session, item, "chitalka", 0)

    row = _row(db_session, item, project)
    assert row.score == 0.4 and row.rationale == "model says so"


def test_clearing_removes_a_row_that_only_existed_because_of_the_vote(db_session):
    project = _project(db_session)
    item = _item(db_session)

    feedback.record_vote(db_session, item, "chitalka", 1)
    feedback.record_vote(db_session, item, "chitalka", 0)

    assert _row(db_session, item, project) is None


def test_votes_survive_reanalysis(db_session):
    chitalka, softbody = _project(db_session), _project(db_session, "softbody", "Softbody Demo")
    item = _item(db_session, relevance={chitalka: 0.3, softbody: 0.8})
    feedback.record_vote(db_session, item, "chitalka", 1)
    feedback.record_vote(db_session, item, "softbody", -1)

    # The next analysis disagrees with both votes.
    fresh = Enrichment(summary="s", why_interesting="w", relevance={
        "chitalka": {"score": 0.2, "rationale": "weak"}, "softbody": {"score": 0.7, "rationale": "strong"}})
    pipeline._apply_result(db_session, item, [chitalka, softbody], fresh)

    assert _row(db_session, item, chitalka).score == feedback.BOOST_SCORE
    assert _row(db_session, item, softbody).score == feedback.SUPPRESS_SCORE
    assert _row(db_session, item, chitalka).base_score == 0.2  # the new analysis is remembered underneath


def test_useful_moves_a_new_item_to_assess_but_leaves_other_statuses(db_session):
    _project(db_session)
    fresh = _item(db_session, "fresh")
    adopted = _item(db_session, "adopted", status=models.Status.ADOPT)

    feedback.record_vote(db_session, fresh, "chitalka", 1)
    feedback.record_vote(db_session, adopted, "chitalka", 1)

    assert fresh.status == models.Status.ASSESS and adopted.status == models.Status.ADOPT
    history = db_session.query(models.StatusHistory).filter_by(item_id=fresh.id).one()
    assert "Marked useful for chitalka" in history.note


def test_invalid_votes_and_notes_are_rejected(db_session):
    _project(db_session)
    item = _item(db_session)
    with pytest.raises(ValueError):
        feedback.record_vote(db_session, item, "no-such-project", 1)
    with pytest.raises(ValueError):
        feedback.record_vote(db_session, item, "chitalka", 5)
    with pytest.raises(ValueError):
        feedback.record_note(db_session, item, "   ")
    with pytest.raises(ValueError):
        feedback.record_note(db_session, item, "x" * (feedback.NOTE_MAX_LENGTH + 1))
    assert item.feedback == []


def test_examples_are_newest_first_capped_and_ignore_superseded_votes(db_session):
    _project(db_session)
    items = [_item(db_session, f"item{i}") for i in range(8)]
    for item in items:
        feedback.record_vote(db_session, item, "chitalka", 1)
    feedback.record_vote(db_session, items[0], "chitalka", -1)  # supersedes its earlier "useful"
    feedback.record_vote(db_session, items[1], "chitalka", 0)  # cleared -> not an example at all
    feedback.record_note(db_session, items[7], "great for hint generation")

    result = feedback.examples(db_session)

    useful = [e for e in result if e["verdict"] == "useful"]
    assert len(useful) == feedback.MAX_EXAMPLES_PER_SIDE
    assert useful[0]["item"] == "item7" and useful[0]["note"] == "great for hint generation"
    assert "item1" not in [e["item"] for e in result] and "item0" in [
        e["item"] for e in result if e["verdict"] == "not_relevant"]


class _Gateway:
    def __init__(self):
        self.namespace = "ns-fb"
        self.payload = None

    def profiles(self):
        return {"routing_version": "v1"}

    def generate(self, body, key):
        self.payload = json.loads(body["messages"][1]["content"][0]["text"])
        return {"id": "resp_fb", "status": "completed", "provider": "fake", "model": "fake",
                "output": [{"type": "json", "data": {"results": []}}]}


def test_recent_votes_are_sent_to_the_model_as_calibration(db_session):
    project = _project(db_session)
    liked, target = _item(db_session, "liked"), _item(db_session, "target")
    feedback.record_vote(db_session, liked, "chitalka", 1)
    gateway = _Gateway()

    enrichment.enrich_batch([target], [project], context=AIContext(client=gateway, max_calls=1))

    assert gateway.payload["user_feedback"] == [
        {"project": "chitalka", "verdict": "useful", "item": "liked", "summary": "Fast local speech recognition"}]


def test_a_not_relevant_item_no_longer_feeds_opportunities(db_session):
    project = _project(db_session)
    item = _item(db_session, relevance={project: 0.9})
    assert opportunities._heuristic(db_session, [project])  # visible before the vote

    feedback.record_vote(db_session, item, "chitalka", -1)

    assert opportunities._heuristic(db_session, [project]) == []


def test_chat_sees_votes_and_notes_and_can_search_them(db_session):
    project = _project(db_session)
    plain = _item(db_session, "plain", summary="An unremarkable library")
    _item(db_session, "other", summary="Something else entirely")
    feedback.record_vote(db_session, plain, "chitalka", -1)
    feedback.record_note(db_session, plain, "rejected: needs a GPU we do not have")

    entry = chat._catalogue_entry(plain, chat.utcnow())
    assert entry["your_votes"] == {"chitalka": "not_relevant"}
    assert entry["your_notes"] == ["rejected: needs a GPU we do not have"]
    assert chat._retrieve(db_session, "which one needed a GPU?")[0].name == "plain"


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
    project = _project(seed)
    item = _item(seed, relevance={project: 0.3})
    seed.commit()
    item_id = item.id
    seed.close()
    yield TestClient(app), item_id  # no `with`: the lifespan (real DB, scheduler) must not run
    app.dependency_overrides.clear()


def test_api_vote_boosts_relevance_and_reports_it(api):
    client, item_id = api
    response = client.post(f"/api/items/{item_id}/feedback", json={"project_slug": "chitalka", "verdict": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["feedback"] == {"chitalka": 1} and body["status"] == "assess"
    assert body["relevance"][0]["score"] == feedback.BOOST_SCORE


def test_api_note_is_stored_and_listed_in_the_detail_view(api):
    client, item_id = api
    assert client.post(f"/api/items/{item_id}/feedback", json={"note": " revisit in Q4 "}).status_code == 200

    detail = client.get(f"/api/items/{item_id}").json()
    assert detail["notes_count"] == 1 and detail["notes"][0]["note"] == "revisit in Q4"


@pytest.mark.parametrize("payload", [
    {},  # neither
    {"note": "x", "verdict": 1, "project_slug": "chitalka"},  # both
    {"verdict": 1},  # vote without a project
    {"project_slug": "chitalka"},  # vote without a verdict
    {"project_slug": "chitalka", "verdict": 7},
    {"project_slug": "nope", "verdict": 1},
    {"note": "   "},
])
def test_api_rejects_malformed_feedback(api, payload):
    client, item_id = api
    assert client.post(f"/api/items/{item_id}/feedback", json=payload).status_code == 400
    assert client.get(f"/api/items/{item_id}").json()["notes_count"] == 0


def test_api_unknown_item_is_404(api):
    client, _ = api
    assert client.post("/api/items/99999/feedback", json={"note": "x"}).status_code == 404
