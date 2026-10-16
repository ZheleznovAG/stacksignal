"""Entity resolution: a package is linked to an already-tracked GitHub repository only through a
repository/homepage URL the registry itself gives, never by matching names."""

from app import entities, models


def _repo(db, full_name, **fields) -> models.Item:
    item = models.Item(source="github", external_id=full_name, name=full_name.split("/")[1], owner="o",
                       url=f"https://github.com/{full_name}", description="d", topics=[], raw={}, **fields)
    db.add(item)
    db.flush()
    return item


def _package(db, source, name, **raw) -> models.Item:
    item = models.Item(source=source, external_id=name, name=name, owner="o", url=f"https://x/{name}",
                       description="d", topics=[], raw=raw)
    db.add(item)
    db.flush()
    return item


def test_links_a_pypi_package_to_its_repository_url(db_session):
    repo = _repo(db_session, "openai/whisper")
    package = _package(db_session, "pypi", "openai-whisper", repository="https://github.com/openai/whisper")

    linked = entities.link_packages_to_repos(db_session)

    assert linked == 1 and package.canonical_item_id == repo.id
    assert package in repo.same_as_items


def test_links_an_npm_package_through_a_git_plus_url(db_session):
    repo = _repo(db_session, "vercel/next.js")
    package = _package(db_session, "npm", "next", repository="git+https://github.com/vercel/next.js.git")

    entities.link_packages_to_repos(db_session)

    assert package.canonical_item_id == repo.id


def test_falls_back_to_homepage_when_there_is_no_repository_field(db_session):
    repo = _repo(db_session, "someone/tool")
    package = _package(db_session, "pypi", "tool", repository=None, homepage="https://github.com/someone/tool")

    entities.link_packages_to_repos(db_session)

    assert package.canonical_item_id == repo.id


def test_matching_is_case_insensitive_on_owner_and_repo(db_session):
    repo = _repo(db_session, "OpenAI/Whisper")
    package = _package(db_session, "pypi", "openai-whisper", repository="https://github.com/openai/whisper")

    entities.link_packages_to_repos(db_session)

    assert package.canonical_item_id == repo.id


def test_no_link_when_the_repository_was_never_collected(db_session):
    package = _package(db_session, "pypi", "lonely-package", repository="https://github.com/nobody/uncollected")

    linked = entities.link_packages_to_repos(db_session)

    assert linked == 0 and package.canonical_item_id is None


def test_a_generic_github_url_with_no_repo_path_does_not_match_anything(db_session):
    _repo(db_session, "sponsors/whoever")  # would false-match a naive "github.com/<owner>/<x>" regex
    package = _package(db_session, "pypi", "p", repository="https://github.com/sponsors/whoever")

    entities.link_packages_to_repos(db_session)

    assert package.canonical_item_id is None


def test_archived_repositories_and_packages_are_not_linked(db_session):
    _repo(db_session, "o/r", archived=True)
    archived_package = _package(db_session, "pypi", "p", repository="https://github.com/o/r")
    archived_package.archived = True
    db_session.flush()

    linked = entities.link_packages_to_repos(db_session)

    assert linked == 0


def test_already_linked_packages_are_not_re_checked(db_session, monkeypatch):
    repo = _repo(db_session, "o/r")
    package = _package(db_session, "pypi", "p", repository="https://github.com/o/r")
    package.canonical_item_id = repo.id
    db_session.flush()
    monkeypatch.setattr(entities, "_repo_full_name", lambda url: (_ for _ in ()).throw(AssertionError("should not run")))

    assert entities.link_packages_to_repos(db_session) == 0
