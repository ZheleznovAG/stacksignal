"""Alembic replaces the old hand-rolled column list (db._ADDED_COLUMNS). This checks that
upgrading a blank database to head produces exactly the schema the models declare, so a model
change made without a matching Alembic revision is caught here instead of drifting silently in
production."""

from sqlalchemy import create_engine

from app import config, models  # noqa: F401  (registers every table on Base.metadata)
from app.db import Base, init_db


def test_alembic_upgrade_head_matches_the_models_on_a_fresh_database(tmp_path, monkeypatch):
    scratch = tmp_path / "fresh.db"
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{scratch}")

    init_db()

    engine = create_engine(f"sqlite:///{scratch}")
    with engine.connect() as connection:
        actual = {row[0] for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='table' AND name != 'alembic_version'")}
    assert actual == set(Base.metadata.tables)


def test_init_db_is_idempotent_on_an_already_migrated_database(tmp_path, monkeypatch):
    scratch = tmp_path / "fresh2.db"
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{scratch}")

    init_db()
    init_db()  # must fast-forward from head to head, not try to create every table again


def test_migrations_match_the_models_column_by_column(tmp_path, monkeypatch):
    """The table-name check above misses a new column, index, constraint or type change made in
    models.py without a revision. Alembic's autogenerate comparison sees all of those: it must
    find nothing to generate on a database upgraded to head (the same thing `alembic check` does)."""
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    scratch = tmp_path / "drift.db"
    monkeypatch.setattr(config, "DATABASE_URL", f"sqlite:///{scratch}")
    init_db()

    engine = create_engine(f"sqlite:///{scratch}")
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True, "render_as_batch": True})
        diff = compare_metadata(context, Base.metadata)
    assert diff == [], f"models.py and the Alembic revisions differ -- write a revision: {diff}"


def test_dependency_migration_backfills_without_inventing_versions(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy.orm import Session

    url = f"sqlite:///{tmp_path / 'legacy.db'}"
    monkeypatch.setattr(config, "DATABASE_URL", url)
    cfg = Config(str(config.ROOT_DIR / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(config.ROOT_DIR / "backend" / "alembic"))
    command.upgrade(cfg, "29a12cdfa2f9")
    engine = create_engine(url)
    packages = [{"name": "FastAPI", "ecosystem": "pypi"}, {"name": "fastapi", "ecosystem": "pypi"},
                {"name": "Foo_Bar", "ecosystem": "pypi"}, {"name": "react", "ecosystem": "npm"}]
    with engine.begin() as conn:
        from sqlalchemy import MetaData, Table
        # Build legacy fixtures from that revision, not from today's evolving model columns.
        projects = Table("projects", MetaData(), autoload_with=conn)
        conn.execute(projects.insert().values(name="Legacy", slug="legacy", description="", keywords=[],
            tier="active", stack=[], needs=[], stack_packages=packages, stack_path="", created_at=models.now()))
        searches = Table("search_queries", MetaData(), autoload_with=conn)
        conn.execute(searches.insert(), [{"project_slug": "legacy", "source": "github", "text": f"{origin} search",
            "origin": origin, "enabled": False, "created_at": models.now(), "last_run_at": None,
            "last_found": 2, "last_new": 1, "runs": 5, "total_new": 3, "empty_streak": 1} for origin in ("ai", "user")])
    command.upgrade(cfg, "head")
    with Session(engine) as db:
        project = db.query(models.Project).one()
        assert project.stack_packages == packages
        assert {(d.ecosystem, d.package_key) for d in project.dependencies} == {
            ("pypi", "fastapi"), ("pypi", "foo-bar"), ("npm", "react")}
        assert next(d for d in project.dependencies if d.package_key == "fastapi").name == "FastAPI"
        assert all(d.declared_spec == d.resolved_version == d.manifest_path == "" for d in project.dependencies)
        queries = db.query(models.SearchQuery).all()
        assert len(queries) == 2
        assert all(q.profile_fingerprint == "" and q.basis == [] and q.runs == 5 and not q.enabled for q in queries)
    command.downgrade(cfg, "29a12cdfa2f9")
    command.upgrade(cfg, "head")
    with Session(engine) as db:
        assert db.query(models.ProjectDependency).count() == 3
    engine.dispose()


def test_context_migration_preserves_legacy_relationships_and_tier_behavior(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import MetaData, Table, inspect
    from sqlalchemy.orm import Session

    url = f"sqlite:///{tmp_path / 'contexts.db'}"
    monkeypatch.setattr(config, "DATABASE_URL", url)
    cfg = Config(str(config.ROOT_DIR / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(config.ROOT_DIR / "backend" / "alembic"))
    command.upgrade(cfg, "d7e2a9c4b815")
    engine = create_engine(url)
    with engine.begin() as conn:
        projects = Table("projects", MetaData(), autoload_with=conn)
        for id_, tier in enumerate(("active", "watch", "dormant"), 1):
            conn.execute(projects.insert().values(id=id_, name=tier, slug=tier, description="Personal brief",
                keywords=["custom"], tier=tier, stack=[], needs=["offline"], stack_packages=[], stack_path="", created_at=models.now()))
        conn.execute(models.Item.__table__.insert().values(id=10, name="Tool", source="github", external_id="o/tool", url="u"))
        conn.execute(models.ItemProjectRelevance.__table__.insert().values(item_id=10, project_id=1, score=0.9))
        conn.execute(models.ItemProjectDecision.__table__.insert().values(item_id=10, project_id=1, status="adopt", verdict="Proven"))
        conn.execute(models.StatusHistory.__table__.insert().values(item_id=10, project_id=1, status="adopt", note="Keep this history"))
        conn.execute(models.ItemFeedback.__table__.insert().values(item_id=10, project_slug="active", kind="vote", verdict=1))
        conn.execute(models.SearchQuery.__table__.insert().values(project_slug="active", source="github", text="manual",
                                                                  origin="user", enabled=False, runs=7, total_new=3))
    for _ in range(2):
        command.upgrade(cfg, "head")
        with Session(engine) as db:
            rows = db.query(models.Project).order_by(models.Project.id).all()
            assert [(p.id, p.slug, p.tier, p.lifecycle_status, p.discovery_enabled) for p in rows] == [
                (1, "active", "active", "active", True), (2, "watch", "watch", "watch", True),
                (3, "dormant", "dormant", "dormant", False)]
            assert all(p.kind == "project" and p.needs == ["offline"] and p.description == "Personal brief" for p in rows)
            assert db.query(models.ItemProjectDecision).one().verdict == "Proven"
            assert db.query(models.StatusHistory).one().note == "Keep this history"
            assert db.query(models.ItemFeedback).one().verdict == 1
            assert db.query(models.ItemProjectRelevance).one().score == 0.9
            query = db.query(models.SearchQuery).one()
            assert (query.origin, query.enabled, query.runs, query.total_new) == ("user", False, 7, 3)
        with engine.connect() as conn:
            assert conn.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        assert "tier" not in {c["name"] for c in inspect(engine).get_columns("projects")}
    engine.dispose()
