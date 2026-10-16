"""GitHub searches read several pages (spaced out to respect the search limit), and queries that keep
finding nothing new rest longer and longer so the per-run budget goes to ones that pay off."""

import datetime

from app import config, discovery, models, pipeline
from app.collectors import github
from tests.fakes import Web, gh_repo
from app.utils import utcnow

NOW = utcnow()


def test_a_search_reads_further_pages_while_the_pages_are_full():
    web = Web()
    web.gh_queries["physics"] = [gh_repo("a/one", 900), gh_repo("a/two", 800)]
    web.gh_queries_page2["physics"] = [gh_repo("a/three", 700), gh_repo("a/four", 600)]

    found = github.search_repos("physics", per_page=2, pages=3, transport=web.transport)

    assert [r["external_id"] for r in found] == ["a/one", "a/two", "a/three", "a/four"]
    assert sum("page=" in r for r in web.requests) == 3  # page 3 came back empty, so it stopped there


def test_a_short_page_ends_the_search_early():
    web = Web()
    web.gh_queries["physics"] = [gh_repo("a/one", 900)]

    found = github.search_repos("physics", per_page=30, pages=3, transport=web.transport)

    assert len(found) == 1 and len(web.requests) == 1


def test_searches_are_spaced_out(monkeypatch):
    waited = []
    monkeypatch.setattr(config, "GITHUB_SEARCH_PAUSE", 5.0)
    monkeypatch.setattr(github.time, "sleep", lambda seconds: waited.append(seconds))
    monkeypatch.setattr(github, "_last_search", github.time.monotonic())  # a search "just happened"
    web = Web()

    github.search_repos("physics", pages=1, transport=web.transport)

    assert len(waited) == 1 and 0 < waited[0] <= 5.0


def test_the_rest_grows_with_every_empty_run():
    assert [discovery.backoff_days(n) for n in range(0, 9)] == [0, 0, 0, 0, 2, 4, 8, 14, 14]


def _query(db, text, *, streak, last_run_days_ago):
    query = models.SearchQuery(project_slug="p", source="github", text=text, origin="auto", empty_streak=streak,
                               last_run_at=NOW - datetime.timedelta(days=last_run_days_ago))
    db.add(query)
    db.flush()
    return query


def test_a_resting_query_is_skipped_until_its_rest_is_over(db_session):
    db_session.add(models.Project(name="P", slug="p", description="d", keywords=["k"]))
    fresh = _query(db_session, "fresh", streak=0, last_run_days_ago=1)
    resting = _query(db_session, "resting", streak=5, last_run_days_ago=1)      # rests 4 days
    rested = _query(db_session, "rested", streak=5, last_run_days_ago=5)        # rest is over

    due = discovery.due_queries(db_session, github_limit=10, hf_limit=10)

    assert resting not in due and {fresh, rested} <= set(due)


def test_the_budget_goes_to_queries_that_are_not_resting(db_session):
    db_session.add(models.Project(name="P", slug="p", description="d", keywords=["k"]))
    for i in range(3):
        _query(db_session, f"resting {i}", streak=6, last_run_days_ago=3)       # oldest, but resting
    live = _query(db_session, "live", streak=0, last_run_days_ago=1)

    due = discovery.due_queries(db_session, github_limit=1, hf_limit=0)

    assert due == [live]


def test_a_run_updates_how_productive_each_query_was(db_session):
    db_session.add(models.Project(name="Softbody Demo", slug="softbody", description="d", keywords=["soft-body physics"]))
    db_session.flush()
    web = Web()
    web.gh_queries["soft-body physics"] = [gh_repo("b/niche", 20)]
    web.gh_queries["position based dynamics"] = []
    db_session.add_all([
        models.SearchQuery(project_slug="softbody", source="github", text="soft-body physics", origin="user"),
        models.SearchQuery(project_slug="softbody", source="github", text="position based dynamics", origin="user")])
    db_session.flush()

    pipeline.run_collection(db_session, transport=web.transport)
    pipeline.run_collection(db_session, transport=web.transport)

    rows = {q.text: q for q in db_session.query(models.SearchQuery).filter_by(source="github")}
    assert rows["soft-body physics"].total_new == 1 and rows["soft-body physics"].empty_streak == 1  # new once, then known
    assert rows["soft-body physics"].runs == 2
    assert rows["position based dynamics"].empty_streak == 2 and rows["position based dynamics"].total_new == 0
