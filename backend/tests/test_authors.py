"""Your GitHub stars and follows: starred repositories go on the radar as Watch (once, bypassing the
quality gate), their owners and the people you follow become watched authors, and a GitHub profile
URL in "add URL" watches that author."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import authors, config, db as db_module, models, pipeline
from app.collectors import github
from app.main import app
from tests.fakes import Web, gh_account, gh_repo


@pytest.fixture(autouse=True)
def _login(monkeypatch):
    monkeypatch.setattr(config, "GITHUB_LOGIN", "Me")


def _web():
    web = Web()
    web.gh_starred["Me"] = [
        ("2026-09-30T10:00:00Z", gh_repo("hadashiA/VContainer", stars=3)),  # far below the quality gate
        ("2026-05-01T10:00:00Z", gh_repo("Cysharp/UniTask", stars=9000, owner_type="Organization")),
        ("2024-01-01T10:00:00Z", gh_repo("hadashiA/VYaml", stars=800)),
        ("2023-01-01T10:00:00Z", gh_repo("Me/dotfiles", stars=1)),
    ]
    web.gh_following["Me"] = [gh_account("neuecc"), gh_account("HadashiA")]
    return web


def _item(db, external_id):
    return db.query(models.Item).filter_by(external_id=external_id).one_or_none()


def _authors(db):
    return {a.login_key: a for a in db.query(models.Author)}


def test_starred_repositories_go_on_the_radar_as_watch_regardless_of_the_gate(db_session):
    run = pipeline.run_collection(db_session, sources=["github"], transport=_web().transport)

    small = _item(db_session, "hadashiA/VContainer")
    assert small is not None and small.status == models.Status.WATCH
    assert [h.note for h in small.status_history][-1] == "Starred on GitHub"
    found = db_session.query(models.ItemDiscovery).filter_by(item_id=small.id).one()
    assert (found.project_slug, found.query_text, found.found_at.year) == ("", authors.STAR_VIA, 2026)
    assert db_session.query(models.ItemDiscovery).filter_by(query_text=authors.STAR_VIA).count() == 4
    stage = next(s for s in run.stages if s["name"] == "github_stars")
    assert (stage["stars"], stage["following"]) == (4, 2)


def test_stars_and_follows_become_authors_merged_case_insensitively(db_session):
    pipeline.run_collection(db_session, sources=["github"], transport=_web().transport)

    found = _authors(db_session)
    assert set(found) == {"hadashia", "cysharp", "neuecc"}  # your own repositories are not a source
    hadashi = found["hadashia"]
    assert (hadashi.followed, hadashi.starred_repos, hadashi.last_starred_at.month) == (True, 2, 9)
    assert hadashi.login == "hadashiA" and hadashi.status == "watch"  # one row; the latest spelling wins
    assert (found["cysharp"].kind, found["cysharp"].followed) == ("organization", False)
    assert (found["neuecc"].starred_repos, found["neuecc"].last_starred_at) == (0, None)


def test_the_star_sets_watch_only_once_and_never_overrides_your_status(db_session):
    web = _web()
    pipeline.run_collection(db_session, sources=["github"], transport=web.transport)
    item = _item(db_session, "hadashiA/VContainer")
    item.status = models.Status.IGNORE
    db_session.commit()

    pipeline.run_collection(db_session, sources=["github"], transport=web.transport)
    assert _item(db_session, "hadashiA/VContainer").status == models.Status.IGNORE
    assert db_session.query(models.ItemDiscovery).filter_by(project_slug="").count() == 4


def test_a_star_on_a_known_item_moves_it_from_new_to_watch_but_keeps_other_statuses(db_session):
    for external_id, status in (("hadashiA/VYaml", models.Status.NEW), ("Cysharp/UniTask", models.Status.ASSESS)):
        db_session.add(models.Item(source="github", external_id=external_id, name=external_id.split("/")[1],
                                   owner=external_id.split("/")[0], url="u", description="d", topics=[], status=status))
    db_session.commit()
    pipeline.run_collection(db_session, sources=["github"], transport=_web().transport)
    assert _item(db_session, "hadashiA/VYaml").status == models.Status.WATCH
    assert _item(db_session, "Cysharp/UniTask").status == models.Status.ASSESS


def test_unfollow_and_unstar_are_mirrored_but_authors_are_kept(db_session):
    web = _web()
    pipeline.run_collection(db_session, sources=["github"], transport=web.transport)
    web.gh_following["Me"] = []
    web.gh_starred["Me"] = web.gh_starred["Me"][1:2]  # only UniTask is still starred
    pipeline.run_collection(db_session, sources=["github"], transport=web.transport)

    found = _authors(db_session)
    assert set(found) == {"hadashia", "cysharp", "neuecc"}
    assert (found["hadashia"].followed, found["hadashia"].starred_repos) == (False, 0)
    assert found["hadashia"].last_starred_at is not None  # history: when you last starred them
    assert _item(db_session, "hadashiA/VContainer").status == models.Status.WATCH  # the radar is yours to change


def test_stars_are_read_across_pages(db_session):
    web = Web()
    web.gh_starred["Me"] = [(f"2026-01-01T00:00:{i % 60:02d}Z", gh_repo(f"o{i}/r{i}", stars=1)) for i in range(150)]
    starred = github.user_starred("Me", transport=web.transport)
    assert len(starred) == 150
    assert sum("/users/Me/starred" in r for r in web.requests) == 2


def test_no_login_or_no_github_source_means_no_sync(db_session, monkeypatch):
    web = _web()
    pipeline.run_collection(db_session, sources=["huggingface"], transport=web.transport)
    monkeypatch.setattr(config, "GITHUB_LOGIN", "")
    pipeline.run_collection(db_session, sources=["github"], transport=web.transport)
    assert not any("/users/" in r for r in web.requests)
    assert db_session.query(models.Author).count() == 0


def test_a_rate_limited_sync_does_not_stop_the_run(db_session):
    web = _web()
    web.github_rate_limited = True
    run = pipeline.run_collection(db_session, sources=["github"], transport=web.transport)
    assert run.status != "failed"
    assert next(s for s in run.stages if s["name"] == "github_stars")["rate_limited"] is True
    assert db_session.query(models.Author).count() == 0


@pytest.mark.parametrize("url, login", [
    ("https://github.com/hadashiA", "hadashiA"),
    ("github.com/hadashiA/", "hadashiA"),
    ("https://www.github.com/orgs/Cysharp", "Cysharp"),
    ("https://github.com/hadashiA/VContainer", None),   # a repository, not an account
    ("https://github.com/topics", None),
    ("https://github.com/settings", None),
    ("https://gitlab.com/hadashiA", None),
    ("https://github.com/bad_login!", None),
])
def test_profile_urls_are_recognized(url, login):
    assert authors.parse_author_url(url) == login


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

    web = Web()
    web.gh_users["hadashia"] = gh_account("hadashiA")
    real = github.get_user
    monkeypatch.setattr(github, "get_user", lambda login, transport=None: real(login, transport=web.transport))
    app.dependency_overrides[db_module.get_db] = override
    yield TestClient(app), Session, web
    app.dependency_overrides.clear()


def test_adding_a_profile_url_watches_the_author(api):
    client, Session, web = api
    first = client.post("/api/items/add-url", json={"url": "https://github.com/HADASHIA"}).json()
    assert (first["kind"], first["login"], first["created"], first["status"]) == ("author", "hadashiA", True, "watch")

    with Session() as db:
        db.query(models.Author).one().status = "ignore"
        db.commit()
    again = client.post("/api/items/add-url", json={"url": "github.com/hadashiA"}).json()
    assert (again["created"], again["status"], again["added_manually"]) == (False, "watch", True)

    assert client.post("/api/items/add-url", json={"url": "https://github.com/nobody"}).status_code == 404
    web.github_rate_limited = True
    assert client.post("/api/items/add-url", json={"url": "https://github.com/hadashiA"}).status_code == 502
