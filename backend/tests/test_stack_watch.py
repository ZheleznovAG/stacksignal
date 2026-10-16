"""Stack watch: new versions of the packages your projects depend on are noticed, reported once, and only
for projects that are not dormant."""

import datetime

import pytest

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as db_module, digest, models, pipeline, stack_watch
from app.main import app
from tests.fakes import Web, npm_hit, pypi_doc
from app.utils import utcnow

NOW = utcnow()


def _project(db, name="Alpha", tier="active", packages=(("pypi", "FastAPI"), ("npm", "react"), ("maven", "ktor"))):
    project = models.Project(name=name, slug=name.lower(), description="d", keywords=["k"], tier=tier,
                             stack_packages=[{"name": n, "ecosystem": e} for e, n in packages])
    db.add(project)
    db.flush()
    return project


def _web(fastapi="0.140.0", react="18.2.0"):
    web = Web()
    web.pypi_docs["fastapi"] = pypi_doc("fastapi", fastapi)
    web.npm_packages["react"] = {"name": "react", "version": react, "description": "UI"}
    return web


def test_only_pypi_and_npm_packages_of_live_projects_are_watched(db_session):
    _project(db_session)
    _project(db_session, "Sleeping", tier="dormant", packages=(("pypi", "requests"),))

    assert set(stack_watch.watched(db_session)) == {("pypi", "fastapi"), ("npm", "react")}  # not maven, not dormant


def test_structured_dependencies_override_legacy_and_compare_resolved_versions(db_session):
    project = _project(db_session)
    project.dependencies = [models.ProjectDependency(ecosystem="pypi", package_key="fastapi", name="FastAPI",
                                                    declared_spec=">=0.115", resolved_version="0.140.0",
                                                    manifest_path="requirements.txt")]
    db_session.flush()
    assert stack_watch.watched(db_session) == {("pypi", "fastapi"): ["Alpha"]}
    stack_watch.check(db_session, transport=_web(fastapi="0.141.1").transport)

    [status] = stack_watch.dependency_status(db_session)
    assert (status["declared"], status["resolved"], status["latest"]) == (">=0.115", "0.140.0", "0.141.1")
    assert status["update_kind"] == "minor" and status["projects"] == ["Alpha"]
    assert status["manifest_path"] == "requirements.txt"


def test_declared_constraints_are_never_used_as_resolved_versions(db_session):
    project = _project(db_session)
    project.dependencies = [models.ProjectDependency(ecosystem="pypi", package_key="fastapi", name="FastAPI",
                                                    declared_spec=">=0.115")]
    db_session.flush()
    stack_watch.check(db_session, transport=_web().transport)
    stack_watch.check(db_session, transport=_web(fastapi="0.141.1").transport)
    [status] = stack_watch.dependency_status(db_session)
    assert status["declared"] == ">=0.115" and status["resolved"] == ""
    assert status["latest"] == "0.141.1" and status["update_kind"] is None
    [update] = stack_watch.updates(db_session)
    assert update["declared"] == ">=0.115" and update["resolved"] == "" and update["update_kind"] is None


def test_each_projects_versions_stay_separate(db_session):
    for name, version in (("Alpha", "0.140.0"), ("Beta", "0.141.1")):
        project = _project(db_session, name)
        project.dependencies = [models.ProjectDependency(ecosystem="pypi", package_key="fastapi", name="FastAPI",
                                                        resolved_version=version)]
    db_session.flush()
    stack_watch.check(db_session, transport=_web(fastapi="0.141.1").transport)
    states = stack_watch.dependency_status(db_session)
    assert {s["projects"][0]: s["update_kind"] for s in states} == {"Alpha": "minor", "Beta": "current"}


def test_canonical_package_keys_reuse_existing_registry_history(db_session):
    _project(db_session, packages=(("pypi", "Foo_Bar"),))
    db_session.add(models.PackageVersion(ecosystem="pypi", name="foo_bar", latest_version="1.0.0"))
    db_session.flush()
    web = Web()
    web.pypi_docs["foo-bar"] = pypi_doc("foo-bar", "1.1.0")

    result = stack_watch.check(db_session, transport=web.transport)

    assert result["updated"] == 1
    assert db_session.query(models.PackageVersion).count() == 1
    [update] = stack_watch.updates(db_session)
    assert update["previous"] == "1.0.0" and update["latest"] == "1.1.0"
    assert update["key"] == "pypi:foo_bar@1.1.0"
    [status] = stack_watch.dependency_status(db_session)
    assert status["package_key"] == "foo-bar" and status["latest"] == "1.1.0"


@pytest.mark.parametrize("resolved,latest,kind", [("1.2.3", "2.0.0", "major"), ("1.2.3", "1.3.0", "minor"),
    ("1.2.3", "1.2.4", "patch"), ("1.2.3", "1.2.3", "current"), ("2.0.0", "1.9.0", "ahead"),
    ("", "1.2.3", None), (">=1.0", "2.0.0", None), ("1.0rc1", "1.0", None)])
def test_update_kind_requires_comparable_resolved_versions(resolved, latest, kind):
    assert stack_watch.update_kind(resolved, latest) == kind


def test_the_first_look_only_records_the_version_and_a_later_change_is_an_update(db_session):
    _project(db_session)
    first = stack_watch.check(db_session, transport=_web().transport)
    assert first["checked"] == 2 and first["updated"] == 0
    assert stack_watch.updates(db_session) == []

    second = stack_watch.check(db_session, transport=_web(fastapi="0.141.1", react="19.0.0").transport)

    assert second["updated"] == 2
    by_name = {u["name"]: u for u in stack_watch.updates(db_session)}
    assert by_name["fastapi"]["previous"] == "0.140.0" and by_name["fastapi"]["latest"] == "0.141.1"
    assert by_name["fastapi"]["major"] is False and by_name["react"]["major"] is True  # 18 -> 19
    assert by_name["fastapi"]["projects"] == ["Alpha"] and by_name["fastapi"]["url"].startswith("https://pypi.org/")


def test_nothing_changed_means_no_update(db_session):
    _project(db_session)
    stack_watch.check(db_session, transport=_web().transport)

    again = stack_watch.check(db_session, transport=_web().transport)

    assert again["updated"] == 0 and stack_watch.updates(db_session) == []


def test_a_broken_or_missing_package_does_not_stop_the_rest(db_session):
    _project(db_session)
    web = _web()
    web.pypi_docs.clear()  # PyPI answers 404 for fastapi

    stats = stack_watch.check(db_session, transport=web.transport)

    assert stats["missing"] == 1 and stats["checked"] == 1


def test_the_per_run_limit_takes_the_longest_unchecked_first(db_session):
    _project(db_session)
    stack_watch.check(db_session, transport=_web().transport)
    for row in db_session.query(models.PackageVersion):
        row.checked_at = NOW - datetime.timedelta(days=1 if row.name == "react" else 5)  # fastapi waited longer

    stats = stack_watch.check(db_session, limit=1, transport=_web(fastapi="0.141.1", react="19.0.0").transport)

    assert stats["checked"] == 1 and {u["name"] for u in stack_watch.updates(db_session)} == {"fastapi"}


def test_updates_of_a_project_that_went_dormant_disappear(db_session):
    project = _project(db_session)
    stack_watch.check(db_session, transport=_web().transport)
    stack_watch.check(db_session, transport=_web(fastapi="0.141.1").transport)
    assert stack_watch.updates(db_session)

    project.tier = "dormant"

    assert stack_watch.updates(db_session) == []


def test_a_collection_run_looks_at_the_stack(db_session):
    _project(db_session)
    web = _web()

    pipeline.run_collection(db_session, transport=web.transport)

    assert db_session.query(models.PackageVersion).count() == 2


def test_the_digest_reports_an_update_once_and_marks_major_ones(db_session):
    _project(db_session)
    stack_watch.check(db_session, transport=_web().transport)
    stack_watch.check(db_session, transport=_web(fastapi="0.141.1", react="19.0.0").transport)

    built = digest.build_digest(db_session, lang="en")
    text = "\n".join(built.messages)

    assert [u["name"] for u in built.stack_updates] == ["react", "fastapi"]  # major first
    assert "Updates to your stack" in text and "18.2.0 → 19.0.0" in text and "(major)" in text and "Alpha" in text
    assert not built.empty

    db_session.add(models.DigestLog(ok=True, stack_update_keys=[u["key"] for u in built.stack_updates]))
    db_session.flush()
    assert digest.select_stack_updates(db_session) == []  # sent once, never repeated
    stack_watch.check(db_session, transport=_web(fastapi="0.142.0", react="19.0.0").transport)
    assert [u["name"] for u in digest.select_stack_updates(db_session)] == ["fastapi"]  # a newer version is news again


def test_the_api_lists_updates():
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
    try:
        seed = Session()
        _project(seed)
        stack_watch.check(seed, transport=_web().transport)
        stack_watch.check(seed, transport=_web(fastapi="0.141.1").transport)
        seed.commit()
        seed.close()

        client = TestClient(app)
        updates = client.get("/api/stack-updates").json()

        assert [(u["name"], u["latest"]) for u in updates] == [("fastapi", "0.141.1")]
        status = client.get("/api/stack-watch").json()
        fastapi = next(s for s in status if s["package_key"] == "fastapi")
        assert fastapi["resolved"] == fastapi["declared"] == ""
        assert fastapi["latest"] == "0.141.1"
        assert client.get("/api/stack-watch?project_id=99999").status_code == 404
    finally:
        app.dependency_overrides.clear()
