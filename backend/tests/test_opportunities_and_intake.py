"""Opportunities persist between runs (same signal = same opportunity, fading, dismissed stays
dismissed) and Add URL puts a hand-picked repository or model on the radar."""

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as db_module, digest, intake, models, opportunities
from app.ai_analysis import AIContext
from app.main import app
from tests.fakes import Web, gh_repo, hf_model
from app.utils import utcnow

NOW = utcnow()


class NoteGateway:
    """Returns the opportunity notes it is told to, whatever the request."""

    def __init__(self):
        self.namespace = "ns-opps"
        self.notes = []
        self.calls = 0

    def profiles(self):
        return {"routing_version": "v1"}

    def generate(self, body, key):
        self.calls += 1
        return {"id": f"r{self.calls}", "status": "completed", "provider": "fake", "model": "fake",
                "output": [{"type": "json", "data": {"opportunities": self.notes}}]}


def _setup(db, n_items=6):
    project = models.Project(name="Chitalka", slug="chitalka", keywords=["ocr"], description="d")
    other = models.Project(name="Softbody Demo", slug="softbody", keywords=["physics"], description="d")
    db.add_all([project, other])
    db.flush()
    items = []
    for i in range(n_items):
        item = models.Item(source="github", external_id=f"o/r{i}", name=f"r{i}", owner="o", url="u", description="d",
                           topics=[], momentum_score=0.5, llm_summary=f"summary {i}", last_seen_at=NOW)
        db.add(item)
        db.flush()
        for p in (project, other):
            db.add(models.ItemProjectRelevance(item_id=item.id, project_id=p.id, score=0.8, rationale="r"))
        items.append(item)
    db.flush()
    return [project, other], items


def _run(db, projects, gateway, notes):
    gateway.notes = notes
    # every run needs a different request or the content-addressed cache would answer without a call
    for item in db.query(models.Item).all():
        item.llm_summary = f"summary at {gateway.calls}"
    opportunities.synthesize_opportunities(db, projects, context=AIContext(client=gateway, max_calls=1))
    db.flush()


def _note(title, ids, slug="chitalka"):
    return {"project_slug": slug, "title": title, "body": f"body of {title}", "item_ids": ids}


def test_a_new_note_becomes_an_opportunity_with_a_first_seen_date(db_session):
    projects, items = _setup(db_session)
    gateway = NoteGateway()

    _run(db_session, projects, gateway, [_note("Local OCR", [items[0].id, items[1].id, items[2].id])])

    (opp,) = db_session.query(models.Opportunity).all()
    assert opp.state == "active" and opp.times_seen == 1 and opp.origin == "ai"
    assert opp.first_seen_at is not None and opp.trend[0]["items"] == 3


def test_the_same_signal_next_time_is_the_same_opportunity_with_grown_evidence(db_session):
    projects, items = _setup(db_session)
    gateway = NoteGateway()
    ids = [i.id for i in items]
    _run(db_session, projects, gateway, [_note("Local OCR", ids[:3])])
    first = db_session.query(models.Opportunity).one()
    first_seen = first.first_seen_at

    _run(db_session, projects, gateway, [_note("Local OCR is maturing", ids[1:5])])  # 2 of 3 shared

    (opp,) = db_session.query(models.Opportunity).all()
    assert opp.id == first.id and opp.times_seen == 2 and opp.first_seen_at == first_seen
    assert opp.title == "Local OCR is maturing" and len(opp.item_ids) == 4
    assert [t["items"] for t in opp.trend] == [3, 4]


def test_a_note_about_other_items_or_another_project_is_a_new_opportunity(db_session):
    projects, items = _setup(db_session)
    gateway = NoteGateway()
    ids = [i.id for i in items]
    _run(db_session, projects, gateway, [_note("Local OCR", ids[:2])])

    _run(db_session, projects, gateway, [_note("Local OCR", ids[:2], slug="softbody"), _note("Voice", ids[4:6])])

    assert db_session.query(models.Opportunity).count() == 3


def test_an_opportunity_that_stops_coming_back_fades_but_is_never_deleted(db_session):
    projects, items = _setup(db_session)
    gateway = NoteGateway()
    _run(db_session, projects, gateway, [_note("Local OCR", [items[0].id, items[1].id])])
    opp = db_session.query(models.Opportunity).one()
    opp.last_seen_at = NOW - datetime.timedelta(days=opportunities.FADE_AFTER_DAYS + 1)

    _run(db_session, projects, gateway, [_note("Something else", [items[4].id, items[5].id])])

    assert db_session.get(models.Opportunity, opp.id).state == "faded"
    assert db_session.query(models.Opportunity).count() == 2


def test_a_faded_one_that_returns_is_active_again_but_a_dismissed_one_stays_dismissed(db_session):
    projects, items = _setup(db_session)
    gateway = NoteGateway()
    a, b = [i.id for i in items[:2]], [i.id for i in items[3:5]]
    _run(db_session, projects, gateway, [_note("A", a), _note("B", b)])
    first, second = db_session.query(models.Opportunity).order_by(models.Opportunity.id).all()
    first.state, second.state = "faded", "dismissed"

    _run(db_session, projects, gateway, [_note("A again", a), _note("B again", b)])

    assert first.state == "active" and second.state == "dismissed"
    assert second.title == "B again" and second.times_seen == 2  # evidence keeps updating quietly


def test_when_the_ai_is_down_existing_opportunities_are_left_alone(db_session):
    projects, items = _setup(db_session)
    gateway = NoteGateway()
    _run(db_session, projects, gateway, [_note("Local OCR", [items[0].id, items[1].id])])
    before = db_session.query(models.Opportunity).one().body

    opportunities.synthesize_opportunities(db_session, projects, context=AIContext(client=None, max_calls=0))

    assert [o.body for o in db_session.query(models.Opportunity).filter_by(project_slug="chitalka")] == [before]
    assert db_session.query(models.Opportunity).filter_by(origin="heuristic").count() == 1  # only where none existed


def test_keyword_placeholders_give_way_once_the_ai_writes_real_notes(db_session):
    projects, items = _setup(db_session)
    db_session.add(models.Opportunity(title="6 relevant items for Chitalka", body="x", project_slug="chitalka",
                                      item_ids=[items[0].id], origin="heuristic"))
    db_session.flush()

    _run(db_session, projects, NoteGateway(), [_note("Local OCR", [items[2].id, items[3].id])])

    states = {o.title: o.state for o in db_session.query(models.Opportunity)}
    assert states == {"6 relevant items for Chitalka": "faded", "Local OCR": "active"}


def test_the_migration_marks_old_keyword_opportunities(db_session):
    from app import maintenance
    db_session.add_all([models.Opportunity(title="6 relevant items for Chitalka", body="x", project_slug="s", item_ids=[]),
                        models.Opportunity(title="Local OCR is ready", body="x", project_slug="s", item_ids=[])])
    db_session.flush()

    assert maintenance.mark_heuristic_opportunities(db_session) == 1
    assert {o.title: o.origin for o in db_session.query(models.Opportunity)} == {
        "6 relevant items for Chitalka": "heuristic", "Local OCR is ready": "ai"}


def test_the_digest_only_carries_active_unsent_opportunities_and_marks_them(db_session):
    projects, items = _setup(db_session)
    gateway = NoteGateway()
    _run(db_session, projects, gateway, [_note("Keep", [items[0].id, items[1].id]),
                                         _note("Drop", [items[3].id, items[4].id])])
    keep, drop = db_session.query(models.Opportunity).order_by(models.Opportunity.id).all()
    drop.state = "dismissed"

    assert [o["title"] for o in digest.select_opportunities(db_session)] == ["Keep"]

    keep.digested_at = NOW
    assert digest.select_opportunities(db_session) == []


# ---- HTTP: states and Add URL ----------------------------------------------------------------

@pytest.fixture()
def api(monkeypatch):
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
    _, items = _setup(seed, 3)
    seed.add(models.Opportunity(title="T", body="b", project_slug="chitalka", item_ids=[items[0].id, 9999],
                                first_seen_at=NOW, last_seen_at=NOW, trend=[{"at": "x", "items": 1}]))
    seed.commit()
    seed.close()
    yield TestClient(app), Session
    app.dependency_overrides.clear()


def test_the_api_lists_states_and_resolves_the_evidence(api):
    client, _ = api
    (opp,) = client.get("/api/opportunities").json()
    assert opp["state"] == "active" and [e["name"] for e in opp["evidence"]] == ["r0"]  # unknown id is skipped
    assert opp["evidence_added"] == 1

    assert client.post(f"/api/opportunities/{opp['id']}/state", json={"state": "dismissed"}).json()["state"] == "dismissed"
    assert client.get("/api/opportunities").json() == []
    assert len(client.get("/api/opportunities?state=dismissed").json()) == 1
    assert len(client.get("/api/opportunities?state=all").json()) == 1
    assert client.post(f"/api/opportunities/{opp['id']}/state", json={"state": "faded"}).status_code == 400
    assert client.post("/api/opportunities/999/state", json={"state": "done"}).status_code == 404


@pytest.mark.parametrize("url, expected", [
    ("https://github.com/owner/repo", ("github", "owner/repo")),
    ("github.com/owner/repo/tree/main/src", ("github", "owner/repo")),
    ("https://www.github.com/owner/repo.git", ("github", "owner/repo")),
    ("https://huggingface.co/openai/whisper-large-v3", ("huggingface", "openai/whisper-large-v3")),
    ("https://huggingface.co/openai/whisper-large-v3/tree/main", ("huggingface", "openai/whisper-large-v3")),
    ("https://huggingface.co/gpt2", ("huggingface", "gpt2")),
])
def test_urls_are_understood(url, expected):
    assert intake.parse_url(url) == expected


@pytest.mark.parametrize("url, code", [
    ("", "invalid_url"), ("https://github.com/owner", "invalid_url"), ("ftp://github.com/a/b", "invalid_url"),
    ("https://example.com/a/b", "unsupported_url"), ("https://huggingface.co/datasets/a/b", "unsupported_url"),
    ("https://huggingface.co/spaces/a/b", "unsupported_url"), ("https://evilgithub.com/a/b", "unsupported_url"),
])
def test_other_urls_are_refused(url, code):
    with pytest.raises(intake.IntakeError) as error:
        intake.parse_url(url)
    assert error.value.code == code


def test_adding_a_repository_puts_it_on_the_radar_even_below_the_quality_gate(db_session):
    _setup(db_session, 0)
    web = Web()
    web.gh_repos["me/tiny"] = gh_repo("me/tiny", 3, description="soft body ocr physics")

    item, created = intake.add_url(db_session, "https://github.com/me/tiny", analyze=False, transport=web.transport)

    assert created and item.stars == 3 and item.archived is False
    assert item.status == models.Status.WATCH
    assert any(h.note == "Added by you from a URL" for h in item.status_history)
    assert db_session.query(models.ItemSnapshot).filter_by(item_id=item.id).count() == 1


def test_adding_a_model_and_adding_a_known_item_again(db_session):
    _setup(db_session, 0)
    web = Web()
    web.hf_models["me/model"] = hf_model("me/model", 5, 10)
    item, created = intake.add_url(db_session, "https://huggingface.co/me/model", analyze=False, transport=web.transport)
    item.status = models.Status.TRIAL  # the user has moved on since

    again, created_again = intake.add_url(db_session, "https://huggingface.co/me/model", analyze=False,
                                          transport=web.transport)

    assert created and not created_again and again.id == item.id
    assert again.status == models.Status.TRIAL  # never demoted by adding it again


def test_unknown_and_unreachable_sources_are_reported(db_session):
    web = Web()
    with pytest.raises(intake.IntakeError) as missing:
        intake.add_url(db_session, "https://github.com/no/such", analyze=False, transport=web.transport)
    assert missing.value.code == "not_found"
    web.github_rate_limited = True
    with pytest.raises(intake.IntakeError) as limited:
        intake.add_url(db_session, "https://github.com/no/such", analyze=False, transport=web.transport)
    assert limited.value.code == "fetch_failed"


def test_the_api_refuses_bad_urls_with_a_reason(api):
    client, _ = api
    assert client.post("/api/items/add-url", json={"url": "https://example.com/x/y"}).json()["detail"] == "unsupported_url"
    assert client.post("/api/items/add-url", json={"url": "nonsense"}).status_code == 400
