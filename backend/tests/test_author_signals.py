"""User engagement has a sign: rejection is neither endorsement nor a reason to watch an author."""

import pytest

from app import authors, decisions, models


@pytest.fixture()
def signal_case(db_session):
    project = models.Project(name="P", slug="p")
    author = models.Author(login="neo", login_key="neo", status="candidate")
    item = models.Item(source="github", external_id="neo/tool", name="tool", owner="neo", url="u")
    db_session.add_all([project, author, item])
    db_session.flush()
    return db_session, project, author, item


@pytest.mark.parametrize("status,kind", [
    ("ignore", "negative"), ("hold", "neutral"), ("watch", "neutral"), ("assess", "neutral"),
    ("trial", "positive"), ("adopt", "positive"),
])
def test_only_positive_decisions_raise_priority_and_promote_candidates(signal_case, status, kind):
    db, project, author, item = signal_case
    before = authors.priority(author, authors.author_stats(db)["neo"])
    decisions.set_decision(db, item, project, status)
    stats = authors.author_stats(db)["neo"]
    assert stats[kind] == stats["engaged"] == 1
    assert authors.priority(author, stats) == before + (kind == "positive")
    authors.prepare_watch(db)
    assert author.status == ("watch" if kind == "positive" else "candidate")


def test_latest_vote_and_clear_replace_historical_endorsement(signal_case):
    db, project, author, item = signal_case
    before = authors.priority(author, authors.author_stats(db)["neo"])
    for verdict, positive, negative in [(1, 1, 0), (-1, 0, 1), (0, 0, 0)]:
        db.add(models.ItemFeedback(item_id=item.id, kind="vote", project_slug=project.slug, verdict=verdict))
        db.flush()
        stats = authors.author_stats(db)["neo"]
        assert (stats["positive"], stats["negative"], stats["engaged"]) == (positive, negative, positive + negative)
        assert authors.priority(author, stats) == before + positive


def test_rejection_overrides_an_old_like_only_in_its_own_context(signal_case):
    db, project, author, item = signal_case
    other = models.Project(name="Other", slug="other")
    db.add(other)
    db.add(models.ItemFeedback(item_id=item.id, kind="vote", project_slug=project.slug, verdict=1))
    db.flush()
    decisions.set_decision(db, item, project, "ignore")
    assert authors.author_stats(db)["neo"]["positive"] == 0
    decisions.set_decision(db, item, other, "adopt")
    stats = authors.author_stats(db)["neo"]
    assert (stats["positive"], stats["negative"], stats["engaged"]) == (1, 1, 1)
    decisions.clear_decision(db, item, other)
    assert authors.author_stats(db)["neo"]["positive"] == 0


def test_notes_and_unknown_global_status_do_not_endorse_an_author(signal_case):
    db, project, author, item = signal_case
    item.status = models.Status.TRIAL  # no history establishing a user action
    db.add(models.ItemFeedback(item_id=item.id, kind="note", note="Investigate later"))
    db.flush()
    stats = authors.author_stats(db)["neo"]
    assert (stats["positive"], stats["neutral"]) == (0, 1)
    authors.prepare_watch(db)
    assert author.status == "candidate"


def test_context_history_cannot_turn_an_automatic_global_status_into_an_endorsement(signal_case):
    db, project, author, item = signal_case
    item.status = models.Status.TRIAL
    db.add(models.StatusHistory(item_id=item.id, status="trial", note="Auto-suggested by old AI"))
    db.flush()
    decisions.set_decision(db, item, project, "ignore")
    stats = authors.author_stats(db)["neo"]
    assert (stats["positive"], stats["negative"]) == (0, 1)
    authors.prepare_watch(db)
    assert author.status == "candidate"


def test_endorsement_is_counted_once_per_item(signal_case):
    db, project, author, item = signal_case
    item.status = models.Status.ADOPT
    db.add(models.StatusHistory(item_id=item.id, status="adopt", note="Changed from UI"))
    db.add(models.ItemFeedback(item_id=item.id, kind="vote", project_slug=project.slug, verdict=1))
    decisions.set_decision(db, item, project, "trial")
    assert authors.author_stats(db)["neo"]["positive"] == 1
