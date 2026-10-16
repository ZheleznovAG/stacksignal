"""Manual decisions become a conservative local preference signal for unseen candidates."""

import math

from app import chat, decisions, feedback, models, preference_learning
from app.routers import radar as radar_router
from app.utils import utcnow

NOW = utcnow()


def _unit(x, y):
    norm = math.sqrt(x * x + y * y)
    return [x / norm, y / norm]


def _item(db, name, vector, *, momentum=0.1):
    item = models.Item(source="github", external_id=f"o/{name}", name=name, owner="o", url=f"https://x/{name}",
                       description=f"{name} toolkit", topics=[], momentum_score=momentum,
                       first_seen_at=NOW, last_seen_at=NOW)
    db.add(item)
    db.flush()
    db.add(models.ItemEmbedding(item_id=item.id, vector=vector, signature=name, computed_at=NOW))
    return item


def _project(db, slug="stacksignal"):
    project = models.Project(name=slug.title(), slug=slug, description="d", keywords=[])
    db.add(project)
    db.flush()
    return project


def _decision(db, item, project, status):
    db.add(models.ItemProjectDecision(item_id=item.id, project_id=project.id, status=status,
                                      decided_at=NOW, created_at=NOW, updated_at=NOW, snapshot={}))
    db.flush()


def _balanced_profile(db):
    project = _project(db)
    positives = [_item(db, "kept-a", _unit(1, 0)), _item(db, "kept-b", _unit(0.95, 0.05))]
    negatives = [_item(db, "rejected-a", _unit(0, 1)), _item(db, "rejected-b", _unit(0.05, 0.95))]
    for item in positives:
        _decision(db, item, project, models.Status.ADOPT)
    for item in negatives:
        _decision(db, item, project, models.Status.IGNORE)
    return project, positives, negatives


def test_profile_stays_off_until_it_has_balanced_evidence(db_session):
    project = _project(db_session)
    positive = _item(db_session, "one-like", _unit(1, 0))
    negative = _item(db_session, "one-dislike", _unit(0, 1))
    candidate = _item(db_session, "candidate", _unit(0.9, 0.1))
    _decision(db_session, positive, project, models.Status.ADOPT)
    _decision(db_session, negative, project, models.Status.IGNORE)

    assert preference_learning.preference_ranking(db_session, [project.id], [candidate.id]) == []
    assert preference_learning.profile_stats(db_session, [project.id])["active"] is False


def test_profile_ranks_unseen_positive_like_items_and_never_boosts_training_examples(db_session):
    project, positives, _negatives = _balanced_profile(db_session)
    strong = _item(db_session, "strong-new", _unit(0.98, 0.20))
    weaker = _item(db_session, "weaker-new", _unit(0.80, 0.60))

    ranked = preference_learning.preference_ranking(
        db_session, [project.id], [positives[0].id, strong.id, weaker.id]
    )

    assert ranked == [strong.id, weaker.id]
    stats = preference_learning.profile_stats(db_session, [project.id])
    assert stats == {"positive": 2, "negative": 2, "judged": 4,
                     "embedded_positive": 2, "embedded_negative": 2,
                     "needed_positive": 0, "needed_negative": 0, "active": True}



def test_watch_assess_and_hold_are_neutral_training_states(db_session):
    project = _project(db_session)
    watch = _item(db_session, "watch", _unit(1, 0))
    assess = _item(db_session, "assess", _unit(1, 0))
    hold = _item(db_session, "hold", _unit(0, 1))
    trial = _item(db_session, "trial", _unit(1, 0))
    ignored = _item(db_session, "ignored", _unit(0, 1))
    _decision(db_session, watch, project, models.Status.WATCH)
    _decision(db_session, assess, project, models.Status.ASSESS)
    _decision(db_session, hold, project, models.Status.HOLD)
    _decision(db_session, trial, project, models.Status.TRIAL)
    _decision(db_session, ignored, project, models.Status.IGNORE)

    judgments = preference_learning.project_judgments(db_session, [project.id])

    assert judgments == {trial.id: 1, ignored.id: -1}

def test_latest_vote_clear_blocks_old_vote_and_decision_overrides_vote(db_session):
    project = _project(db_session)
    item = _item(db_session, "x", _unit(1, 0))
    db_session.add_all([
        models.ItemFeedback(item_id=item.id, kind="vote", project_slug=project.slug, verdict=1),
        models.ItemFeedback(item_id=item.id, kind="vote", project_slug=project.slug, verdict=0),
    ])
    db_session.flush()
    assert item.id not in preference_learning.project_judgments(db_session, [project.id])

    db_session.add(models.ItemFeedback(item_id=item.id, kind="vote", project_slug=project.slug, verdict=1))
    _decision(db_session, item, project, models.Status.IGNORE)
    assert preference_learning.project_judgments(db_session, [project.id])[item.id] == -1


def test_chat_rrf_gets_a_weak_preference_ranking_only_inside_the_existing_pool(db_session, monkeypatch):
    project, _positives, _negatives = _balanced_profile(db_session)
    strong = _item(db_session, "strong-new", _unit(0.98, 0.20))
    outside = _item(db_session, "outside", _unit(1, 0))
    outside.archived = True
    for i in range(20):
        _item(db_session, f"filler-{i}", _unit(0.7, 0.7), momentum=0.01)
    db_session.flush()

    monkeypatch.setattr(chat.config, "CHAT_FTS_MIN_ITEMS", 5)
    monkeypatch.setattr(chat.search_index, "search", lambda db, stems: [strong.id])
    signals = chat._retrieval_signals(db_session, {"tool"}, {project.id}, False, {}, {})

    assert signals is not None
    assert signals["preference"][0] == strong.id
    assert outside.id not in signals["preference"]  # taste cannot inject a non-candidate into the query


def test_radar_uses_preference_as_a_second_vote_not_as_a_relevance_replacement(db_session):
    project, _positives, _negatives = _balanced_profile(db_session)
    strong = _item(db_session, "strong-new", _unit(0.98, 0.20), momentum=0.1)
    weaker = _item(db_session, "weaker-new", _unit(0.80, 0.60), momentum=0.9)
    db_session.add_all([
        models.ItemProjectRelevance(item_id=strong.id, project_id=project.id, score=0.8, rationale="r"),
        models.ItemProjectRelevance(item_id=weaker.id, project_id=project.id, score=0.8, rationale="r"),
    ])
    db_session.flush()

    rows = decisions.undecided_for(db_session, project)

    assert [row.item_id for row in rows[:2]] == [strong.id, weaker.id]


def test_preference_pilot_reports_readiness_and_removes_judged_candidates(db_session):
    project = _project(db_session)
    first = _item(db_session, "first", _unit(1, 0))
    second = _item(db_session, "second", _unit(0.9, 0.1))
    db_session.add_all([
        models.ItemProjectRelevance(item_id=first.id, project_id=project.id, score=0.9, rationale="fit"),
        models.ItemProjectRelevance(item_id=second.id, project_id=project.id, score=0.8, rationale="fit"),
    ])
    db_session.flush()

    before = radar_router.preference_pilot(project.slug, limit=8, db=db_session)
    assert before["profile"]["needed_positive"] == 2
    assert before["profile"]["needed_negative"] == 2
    assert [item["name"] for item in before["candidates"]] == ["first", "second"]

    feedback.record_vote(db_session, first, project.slug, 1)
    db_session.flush()
    after = radar_router.preference_pilot(project.slug, limit=8, db=db_session)
    assert after["profile"]["positive"] == 1
    assert [item["name"] for item in after["candidates"]] == ["second"]
