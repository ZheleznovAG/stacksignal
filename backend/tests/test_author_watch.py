"""Watched authors: their repositories are checked about once a week, remembered as metadata, and only a
few enter the radar (new ones first, then older ones that earned stars). An author's yield orders the
checks, and an owner who keeps showing up in relevant finds is suggested as a candidate."""

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import authors, config, db as db_module, models, pipeline
from app.main import app
from app.utils import utcnow
from tests.fakes import Web, gh_repo

NOW = utcnow()


def _ago(days):
    return (NOW - datetime.timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _author(db, login, status="watch", **fields):
    author = models.Author(login=login, login_key=login.lower(), status=status, kind="user",
                           followed=fields.pop("followed", False), starred_repos=fields.pop("starred_repos", 0),
                           added_manually=fields.pop("added_manually", True), **fields)
    db.add(author)
    db.commit()
    return author


def _profile():
    """One author's profile: what should and should not reach the radar on a first check."""
    return [
        gh_repo("neo/fresh", stars=0, created_at=_ago(10), pushed_at=_ago(1)),           # new: yes, even at 0 stars
        gh_repo("neo/classic", stars=500, created_at=_ago(1500), pushed_at=_ago(30)),    # earned stars, active: yes
        gh_repo("neo/hobby", stars=5, created_at=_ago(1500), pushed_at=_ago(30)),        # old and unnoticed: no
        gh_repo("neo/fork", stars=0, created_at=_ago(5), pushed_at=_ago(5), fork=True),  # a fork: no
        gh_repo("neo/retired", stars=900, created_at=_ago(1500), archived=True),         # archived: no
        gh_repo("neo/abandoned", stars=900, created_at=_ago(2000), pushed_at=_ago(1200)),  # stale: no
    ]


def _item(db, external_id):
    return db.query(models.Item).filter_by(external_id=external_id).one_or_none()


def test_a_first_check_remembers_every_repository_and_promotes_new_and_proven_ones(db_session):
    _author(db_session, "neo")
    web = Web()
    web.gh_user_repos["neo"] = _profile()
    run = pipeline.run_collection(db_session, sources=["github"], transport=web.transport)

    promoted = {i.external_id for i in db_session.query(models.Item)}
    assert promoted == {"neo/fresh", "neo/classic"}
    fresh = _item(db_session, "neo/fresh")
    assert fresh.status == models.Status.NEW  # a suggestion, not your choice
    found = db_session.query(models.ItemDiscovery).filter_by(item_id=fresh.id).one()
    assert (found.project_slug, found.query_text) == (authors.AUTHOR_VIA, "neo")
    assert db_session.query(models.AuthorRepo).count() == 6
    assert db_session.query(models.AuthorRepo).filter(models.AuthorRepo.promoted_at.isnot(None)).count() == 2
    stage = next(s for s in run.stages if s["name"] == "author_watch")
    assert (stage["checked"], stage["promoted"]) == (1, 2)
    assert db_session.query(models.Author).one().repos_checked_at is not None


def test_an_author_is_checked_weekly_and_only_new_repositories_come_in_later(db_session):
    _author(db_session, "neo")
    web = Web()
    web.gh_user_repos["neo"] = _profile()
    pipeline.run_collection(db_session, sources=["github"], transport=web.transport)
    calls = sum("/users/neo/repos" in r for r in web.requests)

    pipeline.run_collection(db_session, sources=["github"], transport=web.transport)
    assert sum("/users/neo/repos" in r for r in web.requests) == calls  # checked this week already

    author = db_session.query(models.Author).one()
    author.repos_checked_at = NOW - datetime.timedelta(days=8)
    db_session.commit()
    web.gh_user_repos["neo"] = [gh_repo("neo/brand-new", stars=1, created_at=_ago(1), pushed_at=_ago(1))] + _profile()
    pipeline.run_collection(db_session, sources=["github"], transport=web.transport)
    assert {i.external_id for i in db_session.query(models.Item)} == {"neo/fresh", "neo/classic", "neo/brand-new"}


def test_limits_per_author_and_per_run_leave_the_rest_for_the_next_check(db_session, monkeypatch):
    monkeypatch.setattr(config, "AUTHOR_PROMOTE_PER_RUN", 4)
    monkeypatch.setattr(config, "AUTHOR_PROMOTE_PER_AUTHOR", 3)
    a, b = _author(db_session, "a", added_manually=False), _author(db_session, "b", followed=True)
    repos = {login: [{"payload": {"source": "github", "external_id": f"{login}/r{i}", "name": f"r{i}", "stars": 0,
                                  "description": "A tool"},
                      "owner": {"login": login, "id": 1, "kind": "user"}, "fork": False, "archived": False,
                      "created_at": _ago(i + 1), "pushed_at": _ago(1)} for i in range(5)] for login in ("a", "b")}
    chosen = authors.apply_repos(db_session, {a.id: repos["a"], b.id: repos["b"]})
    by_author = [c["login"] for c in chosen]
    assert len(chosen) == 4 and by_author.count("b") == 3  # the followed author first, never more than 3
    assert [c["payload"]["external_id"] for c in chosen if c["login"] == "b"] == ["b/r0", "b/r1", "b/r2"]  # newest first

    again = authors.apply_repos(db_session, {a.id: repos["a"], b.id: repos["b"]})
    assert {c["payload"]["external_id"] for c in again} & {c["payload"]["external_id"] for c in chosen} == set()


@pytest.mark.parametrize("name, stars, description, kind, expected", [
    ("tool", 0, "A tool", "user", True),           # a person's new, described repository
    ("tool", 0, "", "user", False),                # bare and unnoticed: wait for stars
    ("tool", 5, "", "user", True),
    ("tool", 3, "A tool", "organization", False),  # organizations publish a lot: wait for interest
    ("tool", 12, "A tool", "organization", True),
    ("dotfiles", 50, "Mine", "user", False),       # housekeeping, not technology
    (".github", 50, "Profile", "organization", False),
    ("homebrew-tap", 50, "Tap", "organization", False),
    ("render-examples", 50, "Samples", "user", False),
])
def test_new_repositories_must_show_some_substance(db_session, name, stars, description, kind, expected):
    author = _author(db_session, "neo")
    author.kind = kind
    db_session.commit()
    repo = {"payload": {"source": "github", "external_id": f"neo/{name}", "name": name, "stars": stars,
                        "description": description},
            "owner": {"login": "neo", "id": 1, "kind": kind}, "fork": False, "archived": False,
            "created_at": _ago(5), "pushed_at": _ago(1)}
    assert bool(authors.apply_repos(db_session, {author.id: [repo]})) is expected


def test_a_repository_already_on_the_radar_is_not_promoted_twice(db_session):
    author = _author(db_session, "neo")
    db_session.add(models.Item(source="github", external_id="neo/fresh", name="fresh", owner="neo", url="u",
                               description="d", topics=[]))
    db_session.commit()
    payloads = [{"payload": {"source": "github", "external_id": "neo/fresh", "stars": 0},
                 "owner": {"login": "neo", "id": 1, "kind": "user"}, "fork": False, "archived": False,
                 "created_at": _ago(3), "pushed_at": _ago(1)}]
    assert authors.apply_repos(db_session, {author.id: payloads}) == []
    assert db_session.query(models.AuthorRepo).one().promoted_at is not None


def test_a_choice_lost_to_a_failed_run_is_offered_again(db_session):
    author = _author(db_session, "neo")
    repo = {"payload": {"source": "github", "external_id": "neo/fresh", "name": "fresh", "stars": 0,
                        "description": "A tool"},
            "owner": {"login": "neo", "id": 1, "kind": "user"}, "fork": False, "archived": False,
            "created_at": _ago(3), "pushed_at": _ago(1)}
    assert len(authors.apply_repos(db_session, {author.id: [repo]})) == 1
    assert authors.apply_repos(db_session, {author.id: [repo]}) == []  # same run: already chosen
    db_session.query(models.AuthorRepo).one().promoted_at = NOW - datetime.timedelta(days=2)  # no item came of it
    assert len(authors.apply_repos(db_session, {author.id: [repo]})) == 1


def test_ignored_authors_are_never_checked_and_better_yield_goes_first(db_session):
    _author(db_session, "quiet", status="ignore")
    _author(db_session, "maybe", status="candidate", added_manually=False)
    _author(db_session, "friend", followed=True, starred_repos=3, added_manually=False)
    due, stats = authors.prepare_watch(db_session, limit=5)
    assert [login for _, login in due] == ["friend", "maybe"]
    assert stats["due"] == 2


def _relevant_item(db, project, owner, name, score=0.8, ai=True, status=models.Status.NEW, note=None):
    item = models.Item(source="github", external_id=f"{owner}/{name}", name=name, owner=owner, url="u",
                       description="d", topics=[], status=status)
    db.add(item)
    db.flush()
    db.add(models.ItemProjectRelevance(item_id=item.id, project_id=project.id, score=score))
    if ai:
        db.add(models.ItemAnalysisState(item_id=item.id, provenance={"mode": "ai"}))
    if note:
        db.add(models.StatusHistory(item_id=item.id, status=status, note=note))
    db.flush()
    return item


def test_an_owner_of_several_relevant_finds_becomes_a_candidate_and_your_action_makes_it_watched(db_session):
    project = models.Project(name="P", slug="p", description="d")
    db_session.add(project)
    db_session.flush()
    _relevant_item(db_session, project, "Neo", "one")
    _relevant_item(db_session, project, "Neo", "two")
    _relevant_item(db_session, project, "Lone", "only")                  # one find is not a pattern
    _relevant_item(db_session, project, "Heur", "a", ai=False)           # keyword guesses do not count
    _relevant_item(db_session, project, "Heur", "b", ai=False)
    _relevant_item(db_session, project, "Auto", "a", status=models.Status.ASSESS, note="Auto-suggested by AI")
    db_session.commit()

    authors.prepare_watch(db_session)
    found = {a.login_key: a for a in db_session.query(models.Author)}
    assert set(found) == {"neo"} and found["neo"].status == "candidate" and found["neo"].login == "Neo"
    stats = authors.author_stats(db_session)
    assert (stats["neo"]["relevant"], stats["neo"]["engaged"]) == (2, 0)
    assert stats["auto"]["engaged"] == 0  # the system's own suggestion is not your action

    item = _item(db_session, "Neo/one")
    item.status = models.Status.TRIAL
    db_session.add(models.StatusHistory(item_id=item.id, status=models.Status.TRIAL, note="Changed from UI"))
    db_session.commit()
    authors.prepare_watch(db_session)
    assert db_session.query(models.Author).one().status == "watch"


def test_a_rate_limited_check_does_not_stop_the_run(db_session):
    _author(db_session, "neo")
    web = Web()
    web.github_rate_limited = True
    run = pipeline.run_collection(db_session, sources=["github"], transport=web.transport)
    assert run.status != "failed"
    assert next(s for s in run.stages if s["name"] == "author_watch")["rate_limited"] is True
    assert db_session.query(models.Author).one().repos_checked_at is None  # tried again next run


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


def test_the_api_lists_authors_with_yield_and_changes_their_status(api):
    client, Session = api
    with Session() as db:
        project = models.Project(name="P", slug="p", description="d")
        db.add(project)
        db.flush()
        _relevant_item(db, project, "neo", "one", score=0.9)
        _author(db, "neo", followed=True)
        _author(db, "other", status="candidate")

    (neo,) = client.get("/api/authors?status=watch").json()
    assert (neo["login"], neo["items"], neo["relevant"], neo["followed"]) == ("neo", 1, 1, True)
    assert neo["top"][0]["name"] == "one" and neo["repos_checked_at"] is None
    assert len(client.get("/api/authors").json()) == 2

    assert client.post(f"/api/authors/{neo['id']}/status", json={"status": "ignore"}).json()["status"] == "ignore"
    assert client.get("/api/authors?status=watch").json() == []
    assert client.post(f"/api/authors/{neo['id']}/status", json={"status": "famous"}).status_code == 422
    assert client.post("/api/authors/999/status", json={"status": "watch"}).status_code == 404
