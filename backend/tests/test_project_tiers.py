"""Project tiers decide how much attention a project gets: active is searched and in the digest,
watch is searched but gets a single digest line, dormant is kept but neither searched nor mentioned."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as db_module, digest, discovery, models
from app.ai_analysis import AIContext
from app.main import app
from app import opportunities
from app.utils import utcnow

NOW = utcnow()


def _projects(db):
    made = {}
    for slug in ("act", "wat", "dor"):
        project = models.Project(name=slug, slug=slug, description="d", keywords=["k"], tier={"act": "active", "wat": "watch", "dor": "dormant"}[slug])
        db.add(project)
        made[slug] = project
    db.flush()
    return made


def _relevant_items(db, projects, per_project=3):
    for slug, project in projects.items():
        for i in range(per_project):
            item = models.Item(source="github", external_id=f"o/{slug}{i}", name=f"{slug}{i}", owner="o", url="u",
                               description="d", topics=[], momentum_score=0.5, last_seen_at=NOW)
            db.add(item)
            db.flush()
            db.add(models.ItemProjectRelevance(item_id=item.id, project_id=project.id, score=0.9 - i / 100, rationale="r"))
    db.flush()


def test_dormant_projects_are_not_searched(db_session):
    projects = _projects(db_session)
    for slug in projects:
        db_session.add(models.SearchQuery(project_slug=slug, source="github", text=f"query {slug}", origin="auto"))
    db_session.flush()

    due = discovery.due_queries(db_session, github_limit=10, hf_limit=10)

    assert sorted(q.project_slug for q in due) == ["act", "wat"]


def test_the_digest_skips_dormant_projects_and_gives_watch_ones_a_single_line(db_session):
    _relevant_items(db_session, _projects(db_session))

    picked = digest.select_items(db_session, min_relevance=0.6, limit=20)

    by_project = {}
    for entry in picked:
        by_project[entry["project_slug"]] = by_project.get(entry["project_slug"], 0) + 1
    assert by_project == {"act": digest.PER_PROJECT_CAP, "wat": digest.WATCH_PROJECT_CAP}


def test_opportunities_of_a_dormant_project_are_neither_written_nor_sent(db_session):
    projects = _projects(db_session)
    db_session.add(models.Opportunity(title="old note", body="b", project_slug="dor", item_ids=[]))
    db_session.add(models.Opportunity(title="live note", body="b", project_slug="act", item_ids=[]))
    db_session.flush()

    assert [o["title"] for o in digest.select_opportunities(db_session)] == ["live note"]
    opportunities.synthesize_opportunities(db_session, list(projects.values()), context=AIContext(client=None, max_calls=0))
    assert not any(o.project_slug == "dor" and o.origin == "heuristic" for o in db_session.query(models.Opportunity))


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
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_tier_can_be_set_kept_and_is_validated(api):
    created = api.post("/api/projects", json={"name": "Alpha", "keywords": ["x"]}).json()
    assert created["tier"] == "active"  # the default

    changed = api.put(f"/api/projects/{created['id']}", json={"name": "Alpha", "keywords": ["x"], "tier": "dormant"}).json()
    assert changed["tier"] == "dormant"

    kept = api.put(f"/api/projects/{created['id']}", json={"name": "Alpha 2", "keywords": ["x"]}).json()
    assert kept["tier"] == "dormant"  # an edit that says nothing about the tier does not reset it

    assert api.put(f"/api/projects/{created['id']}", json={"name": "A", "tier": "urgent"}).status_code == 400
    assert api.post("/api/projects", json={"name": "B", "tier": "nope"}).status_code == 400
