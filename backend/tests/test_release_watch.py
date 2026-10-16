"""Release watch: new releases of the GitHub repositories you track are found, stored once, shown on the
item, the radar and in the digest, and untracked repositories are never asked about."""

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import config, db as db_module, digest, models, pipeline, release_watch
from app.collectors import github
from app.main import app
from tests.fakes import Web, gh_release, gh_repo
from app.utils import utcnow

NOW = utcnow()


def _item(db, full_name="o/tool", status=models.Status.ASSESS, source="github", **fields):
    item = models.Item(source=source, external_id=full_name, name=full_name.split("/")[1], owner="o", url=f"https://github.com/{full_name}",
                       description="d", topics=[], status=status, last_seen_at=NOW, **fields)
    db.add(item)
    db.flush()
    if status != models.Status.NEW:  # a status you set yourself leaves this trace
        db.add(models.StatusHistory(item_id=item.id, status=status, note="Changed from UI"))
        db.flush()
        db.expire(item, ["status_history"])
    return item


def test_the_collector_returns_published_releases_and_skips_drafts():
    web = Web()
    web.gh_releases["o/tool"] = [gh_release("v2.0.0", "Big one", days_ago=1), gh_release("v2.1.0-rc1", prerelease=True),
                                 gh_release("v9", draft=True), gh_release("v1.9.0", days_ago=30)]

    releases = github.get_releases("o/tool", transport=web.transport)

    assert [r["tag"] for r in releases] == ["v2.0.0", "v2.1.0-rc1", "v1.9.0"]
    assert releases[0]["name"] == "Big one" and releases[1]["prerelease"] and releases[0]["notes"]
    assert github.get_releases("o/none", transport=web.transport) == []


def test_new_releases_of_tracked_repositories_are_stored_once(db_session):
    item = _item(db_session)
    web = Web()
    web.gh_releases["o/tool"] = [gh_release("v2.0.0", days_ago=1), gh_release("v1.9.0", days_ago=30)]

    first = release_watch.check(db_session, transport=web.transport)
    again = release_watch.check(db_session, transport=web.transport)

    assert first["new_releases"] == 2 and first["checked"] == 1 and again["new_releases"] == 0
    assert {r.tag for r in item.releases} == {"v2.0.0", "v1.9.0"} and item.releases_checked_at
    web.gh_releases["o/tool"].insert(0, gh_release("v2.1.0", days_ago=0))
    assert release_watch.check(db_session, transport=web.transport)["new_releases"] == 1  # only the new one


def test_untracked_repositories_and_other_sources_are_not_asked_about(db_session):
    _item(db_session, "o/ignored-by-you", status=models.Status.NEW)             # nobody cares about it
    _item(db_session, "someone/model", source="huggingface")                      # not GitHub
    web = Web()

    stats = release_watch.check(db_session, transport=web.transport)

    assert stats["tracked"] == 0 and not any("/releases" in r for r in web.requests)


def test_the_budget_goes_to_the_repositories_checked_longest_ago(db_session):
    old = _item(db_session, "o/old")
    recent = _item(db_session, "o/recent")
    old.releases_checked_at = NOW - datetime.timedelta(days=9)
    recent.releases_checked_at = NOW - datetime.timedelta(days=1)
    never = _item(db_session, "o/never")
    web = Web()

    stats = release_watch.check(db_session, limit=2, transport=web.transport)

    assert stats["checked"] == 2 and never.releases_checked_at and old.releases_checked_at > NOW - datetime.timedelta(minutes=5)
    assert recent.releases_checked_at < NOW - datetime.timedelta(hours=12)  # left for the next run


def test_a_rate_limit_stops_the_release_watch_but_not_the_run(db_session):
    _item(db_session, "o/one")
    web = Web()
    web.github_rate_limited = True

    stats = release_watch.check(db_session, transport=web.transport)
    assert stats["rate_limited"] and stats["checked"] == 0

    run = pipeline.run_collection(db_session, transport=web.transport)  # a whole run carries on regardless
    assert run.finished_at is not None


def test_a_collection_run_looks_for_releases(db_session):
    item = _item(db_session, "o/tool")
    web = Web()
    web.gh_repos["o/tool"] = gh_repo("o/tool", 500)
    web.gh_releases["o/tool"] = [gh_release("v3.0.0", days_ago=2)]

    pipeline.run_collection(db_session, transport=web.transport)

    assert [r.tag for r in item.releases] == ["v3.0.0"]


def test_recent_lists_only_fresh_releases_of_items_you_have_not_dropped(db_session):
    kept = _item(db_session, "o/kept")
    dropped = _item(db_session, "o/dropped", status=models.Status.IGNORE)
    for item in (kept, dropped):
        db_session.add(models.ItemRelease(item_id=item.id, tag="v1", published_at=NOW - datetime.timedelta(days=2)))
    db_session.add(models.ItemRelease(item_id=kept.id, tag="v0.1", published_at=NOW - datetime.timedelta(days=90)))
    db_session.add(models.ItemRelease(item_id=kept.id, tag="v1.1-beta", prerelease=True, published_at=NOW - datetime.timedelta(days=1)))
    db_session.flush()

    assert [r["tag"] for r in release_watch.recent(db_session)] == ["v1.1-beta", "v1"]
    assert [r["tag"] for r in release_watch.recent(db_session, include_prereleases=False)] == ["v1"]


def test_the_digest_carries_a_stable_release_once(db_session):
    item = _item(db_session, "o/tool", status=models.Status.TRIAL)
    db_session.add_all([
        models.ItemRelease(item_id=item.id, tag="v2.0.0", name="Version 2", url="https://github.com/o/tool/releases/tag/v2.0.0",
                           notes="Rewrote the parser.", published_at=NOW - datetime.timedelta(days=1)),
        models.ItemRelease(item_id=item.id, tag="v2.1.0-rc1", prerelease=True, published_at=NOW - datetime.timedelta(hours=5)),
        models.ItemRelease(item_id=item.id, tag="v1.0.0", published_at=NOW - datetime.timedelta(days=20))])
    db_session.flush()

    built = digest.build_digest(db_session, lang="en")
    text = "\n".join(built.messages)

    assert [r["tag"] for r in built.releases] == ["v2.0.0"]  # no pre-release, nothing older than a week
    assert "New releases of what you track" in text and "tool v2.0.0" in text and "Rewrote the parser." in text
    assert not built.empty

    db_session.add(models.DigestLog(ok=True, release_keys=[r["key"] for r in built.releases]))
    db_session.flush()
    assert digest.select_releases(db_session) == []


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
    item = _item(seed, "o/tool")
    seed.add_all([models.ItemRelease(item_id=item.id, tag="v1.2.0", published_at=NOW - datetime.timedelta(days=3)),
                  models.ItemRelease(item_id=item.id, tag="v1.3.0-rc1", prerelease=True, published_at=NOW - datetime.timedelta(hours=2))])
    seed.commit()
    seed.close()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_the_api_lists_releases_and_items_show_the_latest_stable_one(api):
    releases = api.get("/api/releases").json()
    assert [r["tag"] for r in releases] == ["v1.3.0-rc1", "v1.2.0"] and releases[0]["prerelease"] is True

    (item,) = api.get("/api/discover").json()
    assert item["latest_release"]["tag"] == "v1.2.0" and item["latest_release"]["prerelease"] is False


def test_only_what_you_did_something_about_is_tracked(db_session):
    by_you = _item(db_session, "o/by-you")
    suggested = _item(db_session, "o/suggested")
    db_session.add(models.StatusHistory(item_id=suggested.id, status=models.Status.ASSESS, note="Auto-suggested (momentum=0.5)"))
    db_session.flush()
    db_session.expire(suggested, ["status_history"])
    voted = _item(db_session, "o/voted", status=models.Status.NEW)
    db_session.add(models.ItemFeedback(item_id=voted.id, kind="vote", project_slug="p", verdict=1))
    decided = _item(db_session, "o/decided", status=models.Status.NEW)
    project = models.Project(name="P", slug="p", description="d", keywords=["k"])
    db_session.add(project)
    db_session.flush()
    db_session.add(models.ItemProjectDecision(item_id=decided.id, project_id=project.id, status=models.Status.TRIAL))
    db_session.flush()
    for item in (by_you, suggested, voted, decided):
        db_session.expire(item)

    assert [release_watch.user_engaged(i) for i in (by_you, suggested, voted, decided)] == [True, False, True, True]
    web = Web()
    release_watch.check(db_session, transport=web.transport)
    asked = {r.split("/repos/")[1].split("/releases")[0] for r in web.requests if "/releases" in r}
    assert asked == {"o/by-you", "o/voted", "o/decided"}


def test_nightly_builds_and_pre_release_tags_are_not_news(db_session):
    item = _item(db_session)
    web = Web()
    web.gh_releases["o/tool"] = [gh_release("build-20260919-220131"), gh_release("nightly-2026-09-19"), gh_release("v2.0.0a3"),
                                 gh_release("v2.0.0-beta.2"), gh_release("v2.0.0rc1"), gh_release("v1.9.0")]

    release_watch.check(db_session, transport=web.transport)

    stored = {r.tag: r.prerelease for r in item.releases}
    assert set(stored) == {"v2.0.0a3", "v2.0.0-beta.2", "v2.0.0rc1", "v1.9.0"}  # the two nightly builds are dropped
    assert stored == {"v2.0.0a3": True, "v2.0.0-beta.2": True, "v2.0.0rc1": True, "v1.9.0": False}


def test_an_item_with_only_a_project_decision_is_never_archived_or_forgotten(db_session):
    from app import quality
    item = _item(db_session, "o/decided", status=models.Status.NEW)
    project = models.Project(name="P", slug="p", description="d", keywords=["k"])
    db_session.add(project)
    db_session.flush()
    assert quality.has_engagement(item, False) is False

    db_session.add(models.ItemProjectDecision(item_id=item.id, project_id=project.id, status=models.Status.TRIAL))
    db_session.flush()
    db_session.expire(item, ["decisions"])

    assert quality.has_engagement(item, False) is True  # so it is refreshed and survives the archive step
