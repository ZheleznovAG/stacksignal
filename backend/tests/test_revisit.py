"""What changed since you decided: snapshots at decision time, releases, real shifts and new alternatives that
share a capability, the overview for rejected/adopted decisions, the digest section and the API."""

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as db_module, decisions, digest, models, revisit
from app.main import app
from app.utils import utcnow

NOW = utcnow()
DAY = datetime.timedelta(days=1)


def _world(db):
    project = models.Project(name="Chitalka", slug="chitalka", description="d", keywords=["ocr"])
    db.add(project)
    ocr = models.Capability(name="local ocr", slug="local-ocr")
    voice = models.Capability(name="speech", slug="speech")
    db.add_all([ocr, voice])
    db.flush()
    return project, ocr, voice


def _item(db, name, capabilities=(), first_seen_days_ago=60, momentum=0.5, maturity=0.5, **fields):
    item = models.Item(source="github", external_id=f"o/{name}", name=name, owner="o", url=f"https://github.com/o/{name}",
                       description="d", topics=[], momentum_score=momentum, maturity_score=maturity, stars=10, forks=0,
                       likes=0, downloads=0, first_seen_at=NOW - first_seen_days_ago * DAY, last_seen_at=NOW, **fields)
    db.add(item)
    db.flush()
    for capability in capabilities:
        db.add(models.ItemCapability(item_id=item.id, capability_id=capability.id, confidence=0.7))
    db.flush()
    db.expire(item, ["capability_links", "releases"])
    return item


def _relevance(db, item, project, score):
    db.add(models.ItemProjectRelevance(item_id=item.id, project_id=project.id, score=score, rationale="fits"))
    db.flush()


def _decide(db, item, project, status="ignore", days_ago=30, **fields):
    decision = decisions.set_decision(db, item, project, status, **fields)
    decision.decided_at = NOW - days_ago * DAY
    db.flush()
    return decision


# ---- the snapshot ------------------------------------------------------------------------------------------

def test_a_decision_remembers_the_item_as_it_was_and_only_changes_that_when_the_status_changes(db_session):
    project, ocr, _ = _world(db_session)
    item = _item(db_session, "tess", [ocr], momentum=0.4)
    db_session.add(models.ItemRelease(item_id=item.id, tag="v1.0", published_at=NOW - 5 * DAY))
    db_session.flush()
    db_session.expire(item, ["releases"])

    decision = decisions.set_decision(db_session, item, project, "ignore", verdict="too slow", criteria="20 fps",
                                      revisit_when="a GPU build appears")
    assert decision.snapshot["momentum"] == 0.4 and decision.snapshot["release"] == "v1.0"
    assert decision.snapshot["capabilities"] == ["local ocr"] and decision.decided_at is not None
    assert decision.criteria == "20 fps" and decision.revisit_when == "a GPU build appears"

    item.momentum_score = 0.9
    first = decision.decided_at
    decisions.set_decision(db_session, item, project, "ignore", verdict="still too slow")   # only the note changed
    assert decision.snapshot["momentum"] == 0.4 and decision.decided_at == first and decision.verdict == "still too slow"

    decisions.set_decision(db_session, item, project, "trial")                                # a real change of mind
    assert decision.snapshot["momentum"] == 0.9 and decision.criteria == "20 fps"             # kept what was not touched


# ---- what changed --------------------------------------------------------------------------------------------------

def test_releases_after_the_decision_count_and_older_ones_or_prereleases_do_not(db_session):
    project, ocr, _ = _world(db_session)
    item = _item(db_session, "tess", [ocr])
    decision = _decide(db_session, item, project)
    db_session.add_all([models.ItemRelease(item_id=item.id, tag="v0.9", published_at=NOW - 90 * DAY),
                        models.ItemRelease(item_id=item.id, tag="v1.0", published_at=NOW - 10 * DAY),
                        models.ItemRelease(item_id=item.id, tag="v1.1-rc1", prerelease=True, published_at=NOW - 2 * DAY)])
    db_session.flush()
    db_session.expire(item, ["releases"])

    changes = revisit.changes_since(db_session, decision)

    assert [r["tag"] for r in changes["releases"]] == ["v1.0"] and changes["release_count"] == 1


def test_only_a_real_shift_in_momentum_or_maturity_is_reported(db_session):
    project, ocr, _ = _world(db_session)
    item = _item(db_session, "tess", [ocr], momentum=0.4, maturity=0.5)
    decision = _decide(db_session, item, project)
    item.momentum_score, item.maturity_score = 0.45, 0.7   # +0.05 is noise, +0.2 is not

    assert revisit.changes_since(db_session, decision)["shifts"] == {"maturity": {"before": 0.5, "now": 0.7}}


def test_new_alternatives_share_a_capability_are_strongly_relevant_and_newer_than_the_decision(db_session):
    project, ocr, voice = _world(db_session)
    rejected = _item(db_session, "tess", [ocr])
    decision = _decide(db_session, rejected, project, days_ago=30)
    good = _item(db_session, "fast-ocr", [ocr], first_seen_days_ago=5)
    old = _item(db_session, "old-ocr", [ocr], first_seen_days_ago=60)                     # existed before the decision
    weak = _item(db_session, "weak-ocr", [ocr], first_seen_days_ago=5)                    # not relevant enough
    other = _item(db_session, "tts", [voice], first_seen_days_ago=5)                      # another capability
    dropped = _item(db_session, "dropped-ocr", [ocr], first_seen_days_ago=5, status=models.Status.IGNORE)
    archived = _item(db_session, "gone-ocr", [ocr], first_seen_days_ago=5, archived=True)
    decided = _item(db_session, "decided-ocr", [ocr], first_seen_days_ago=5)
    _decide(db_session, decided, project, "watch")
    for item, score in ((good, 0.9), (old, 0.9), (weak, 0.3), (other, 0.9), (dropped, 0.9), (archived, 0.9), (decided, 0.9)):
        _relevance(db_session, item, project, score)

    alternatives = revisit.changes_since(db_session, decision)["alternatives"]

    assert [a["name"] for a in alternatives] == ["fast-ocr"]
    assert alternatives[0]["shared_capabilities"] == ["local ocr"] and alternatives[0]["score"] == 0.9


def test_the_overview_leaves_out_unjudged_and_dormant_and_puts_notable_first(db_session):
    project, ocr, _ = _world(db_session)
    quiet = _decide(db_session, _item(db_session, "quiet", [ocr]), project)
    noisy_item = _item(db_session, "noisy", [ocr])
    _decide(db_session, noisy_item, project)
    db_session.add(models.ItemRelease(item_id=noisy_item.id, tag="v2", published_at=NOW - DAY))
    _decide(db_session, _item(db_session, "just-watching", [ocr]), project, "watch")   # not a judgment yet
    _decide(db_session, _item(db_session, "used", [ocr]), project, "adopt")
    db_session.flush()
    db_session.expire(noisy_item, ["releases"])

    entries = revisit.overview(db_session)

    assert [e["item_name"] for e in entries] == ["noisy", "quiet", "used"]   # notable first, then revisit before update
    assert [e["kind"] for e in entries] == ["revisit", "revisit", "update"]
    assert [e["item_name"] for e in revisit.overview(db_session, only_notable=True)] == ["noisy"]
    project.tier = "dormant"
    assert revisit.overview(db_session) == [] and quiet is not None


# ---- digest ------------------------------------------------------------------------------------------------------------

def test_the_digest_raises_a_revisit_once_and_again_only_with_new_evidence(db_session):
    project, ocr, _ = _world(db_session)
    item = _item(db_session, "tess", [ocr])
    _decide(db_session, item, project, verdict="too slow on Android", revisit_when="a GPU build appears")
    alt = _item(db_session, "fast-ocr", [ocr], first_seen_days_ago=3)
    _relevance(db_session, alt, project, 0.88)

    built = digest.build_digest(db_session, lang="en")
    text = "\n".join(built.messages)

    assert [e["item_name"] for e in built.revisit] == ["tess"] and not built.empty
    assert "Worth another look" in text and "too slow on Android" in text and "new alternative: fast-ocr (88%)" in text
    assert "revisit when: a GPU build appears" in text

    db_session.add(models.DigestLog(ok=True, revisit_keys=[e["key"] for e in built.revisit]))
    db_session.flush()
    assert digest.select_revisit(db_session) == []                                   # the same evidence is not repeated

    another = _item(db_session, "cuda-ocr", [ocr], first_seen_days_ago=1)
    _relevance(db_session, another, project, 0.91)
    assert [e["item_name"] for e in digest.select_revisit(db_session)] == ["tess"]   # new evidence: raised again


def test_only_rejected_or_paused_decisions_reach_the_digest(db_session):
    project, ocr, _ = _world(db_session)
    item = _item(db_session, "used", [ocr])
    _decide(db_session, item, project, "adopt")
    db_session.add(models.ItemRelease(item_id=item.id, tag="v3", published_at=NOW - DAY))
    db_session.flush()
    db_session.expire(item, ["releases"])

    assert digest.select_revisit(db_session) == []  # a release of what you use is news for the release section instead


def test_a_written_revisit_condition_reaches_the_ai_as_calibration(db_session):
    from app import feedback
    project, ocr, _ = _world(db_session)
    item = _item(db_session, "tess", [ocr])
    _decide(db_session, item, project, verdict="too slow", revisit_when="a GPU build appears")

    (example,) = [e for e in feedback.examples(db_session) if e["item"] == "tess"]

    assert example["verdict"] == "rejected" and example["revisit_when"] == "a GPU build appears"


# ---- API ------------------------------------------------------------------------------------------------------------------

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
    project, ocr, _ = _world(seed)
    item = _item(seed, "tess", [ocr])
    seed.add(models.ItemRelease(item_id=item.id, tag="v1", published_at=NOW + DAY))  # after any decision made now
    seed.commit()
    seed.close()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_a_decision_carries_criteria_and_the_revisit_condition_and_the_overview_is_served(api):
    item_id = api.get("/api/discover").json()[0]["id"]

    result = api.put(f"/api/items/{item_id}/decisions/chitalka", json={
        "status": "ignore", "goal": "measure fps", "criteria": "20 fps on a mid phone", "verdict": "8 fps",
        "revisit_when": "a GPU build appears"}).json()

    (decision,) = result["decisions"]
    assert decision["criteria"] == "20 fps on a mid phone" and decision["revisit_when"] == "a GPU build appears"
    assert "success: 20 fps on a mid phone" in [h["note"] for h in result["status_history"]][-1]

    (entry,) = api.get("/api/revisit").json()
    assert entry["item_name"] == "tess" and entry["kind"] == "revisit" and entry["notable"] is True
    assert entry["changes"]["release_count"] == 1 and entry["revisit_when"] == "a GPU build appears"
    assert len(api.get("/api/revisit?notable=true").json()) == 1


def test_a_projects_needs_can_be_edited_and_left_alone(api):
    project = api.get("/api/projects").json()[0]
    changed = api.put(f"/api/projects/{project['id']}", json={
        "name": "Chitalka", "keywords": ["ocr"], "needs": ["schema migrations", " ", "schema migrations", "retrieval evaluation"]}).json()
    assert changed["needs"] == ["schema migrations", "retrieval evaluation"]

    kept = api.put(f"/api/projects/{project['id']}", json={"name": "Chitalka", "keywords": ["ocr"]}).json()
    assert kept["needs"] == ["schema migrations", "retrieval evaluation"]   # an edit that says nothing keeps them
