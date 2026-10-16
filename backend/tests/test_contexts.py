"""Context policies separate searching from attention and retain legacy client behavior."""

from app import context_profiles, contexts, digest, discovery, models, pipeline, queries, stack_watch
from app.enrichment import content_fingerprint, project_fingerprint
from tests.test_project_tiers import api  # noqa: F401  shared isolated HTTP fixture


def test_context_api_keeps_new_fields_without_tier_and_rejects_conflicts(api):
    response = api.post("/api/projects", json={"name": "Lab", "kind": "lab", "lifecycle_status": "active",
                       "discovery_enabled": False, "needs": ["local voice", "local voice"]})
    assert response.status_code == 200
    project = response.json()
    assert (project["kind"], project["tier"], project["discovery_allowed"], project["needs"]) == ("lab", "active", False, ["local voice"])
    path = f"/api/projects/{project['id']}"
    kept = api.put(path, json={"name": "Lab renamed"}).json()
    assert (kept["slug"], kept["kind"], kept["discovery_enabled"]) == (project["slug"], "lab", False)
    assert api.put(path, json={"name": "Lab", "tier": "active", "discovery_enabled": False}).status_code == 400
    assert api.put(path, json={"name": "Lab", "tier": "watch", "lifecycle_status": "active"}).status_code == 400
    for field, value in [("kind", "unknown"), ("lifecycle_status", "unknown")]:
        assert api.put(path, json={"name": "Lab", field: value}).status_code == 400
    changed = api.put(path, json={"name": "Lab", "lifecycle_status": "incubate", "discovery_enabled": True}).json()
    assert (changed["tier"], changed["discovery_allowed"]) == ("watch", True)
    legacy = api.put(path, json={"name": "Lab", "tier": "dormant"}).json()
    assert (legacy["lifecycle_status"], legacy["discovery_enabled"], legacy["kind"]) == ("dormant", False, "lab")


def test_search_digest_and_dependency_watch_are_independent(db_session):
    db = db_session
    projects = []
    for name, state, enabled in [("lab", "active", False), ("idea", "incubate", True),
                                 ("sleep", "dormant", True), ("old", "archived", True)]:
        project = models.Project(name=name, slug=name, kind="lab", lifecycle_status=state,
                                 discovery_enabled=enabled, keywords=["offline speech recognition"],
                                 stack_packages=[{"name": name, "ecosystem": "pypi"}])
        db.add(project)
        db.flush()
        projects.append(project)
        db.add(models.SearchQuery(project_slug=name, source="github", text="manual", origin="user"))
        for index in range(4):
            item = models.Item(source="github", external_id=f"o/{name}{index}", name=name, url="u")
            db.add(item)
            db.flush()
            db.add(models.ItemProjectRelevance(item_id=item.id, project_id=project.id, score=0.9))
    db.flush()
    queries.ensure_default_queries(db, projects)
    queries.regenerate(db, projects)
    db.flush()
    assert {q.project_slug for q in discovery.due_queries(db, github_limit=100, hf_limit=100)} == {"idea", "sleep"}
    for slug in ("lab", "old"):
        assert [(q.text, q.origin) for q in db.query(models.SearchQuery).filter_by(project_slug=slug)] == [("manual", "user")]
    picked = digest.select_items(db, limit=50)
    assert [p["project_slug"] for p in picked].count("lab") == 3
    assert [p["project_slug"] for p in picked].count("idea") == 1
    assert not any(p["project_slug"] in ("sleep", "old") for p in picked)
    assert {p["project_slug"] for p in stack_watch._usage(db)} == {"lab", "idea"}


def test_policy_changes_preserve_analysis_but_type_changes_invalidate_only_its_profile(db_session):
    db = db_session
    project = models.Project(name="P", slug="p")
    other = models.Project(name="Other", slug="other")
    item = models.Item(source="github", external_id="o/i", name="i", url="u")
    db.add_all([project, other, item])
    db.flush()
    before = project_fingerprint(project), project_fingerprint(other), content_fingerprint(item)
    query_fp = queries.query_profile_fingerprint(project)
    contexts.apply_policy(project, discovery_enabled=False, lifecycle_status="incubate")
    assert (project_fingerprint(project), project_fingerprint(other), content_fingerprint(item)) == before
    assert queries.query_profile_fingerprint(project) == query_fp
    project.kind = "lab"
    assert project_fingerprint(project) != before[0]
    assert queries.query_profile_fingerprint(project) != query_fp
    assert (project_fingerprint(other), content_fingerprint(item)) == before[1:]


def test_archiving_keeps_relevance_history_during_subsequent_analysis(db_session):
    db = db_session
    project = models.Project(name="Old", slug="old", lifecycle_status="archived")
    item = models.Item(source="github", external_id="o/i", name="i", url="u")
    db.add_all([project, item])
    db.flush()
    row = models.ItemProjectRelevance(item_id=item.id, project_id=project.id, score=0.9, rationale="Historic")
    db.add(row)
    db.flush()
    pipeline._replace_relevance(db, item, [project], {})
    assert item.relevances == [row] and row.rationale == "Historic"
    pipeline._suggest_status(db, item, {"mode": "ai"})
    assert item.status == models.Status.NEW


def test_reclassification_is_once_only_and_preserves_custom_content_and_slugs(db_session, monkeypatch):
    monkeypatch.setattr(context_profiles, "PROFILES", {
        "engine-lab": {"kind": "lab", "discovery_enabled": False,
                       "rename": ("Engine Lab", "Unity Engineering Lab")},
        "sprite-lab": {"kind": "lab"},
    })
    db = db_session
    lab = models.Project(name="Engine Lab", slug="engine-lab", description="My private brief", needs=["outbox"])
    custom = models.Project(name="My graphics lab", slug="sprite-lab")
    db.add_all([lab, custom])
    db.flush()
    old_id = lab.id
    proposal = context_profiles.preview(db)
    assert lab.kind == "project" and lab.name == "Engine Lab"
    assert next(p for p in proposal if p["slug"] == "engine-lab")["fields"]["kind"]["after"] == "lab"
    context_profiles.apply(db)
    assert (lab.id, lab.slug, lab.name, lab.kind, lab.discovery_enabled) == (old_id, "engine-lab", "Unity Engineering Lab", "lab", False)
    assert (lab.description, lab.needs) == ("My private brief", ["outbox"])
    assert custom.name == "My graphics lab" and custom.kind == "lab"
    lab.name, lab.discovery_enabled = "Renamed again", True
    db.delete(custom)
    db.commit()
    assert context_profiles.apply(db) == []
    assert lab.name == "Renamed again" and lab.discovery_enabled
    assert db.query(models.Project).count() == 1  # deleted profiles are never resurrected


def test_reclassification_does_not_steal_an_existing_display_name(db_session, monkeypatch):
    monkeypatch.setattr(context_profiles, "PROFILES", {
        "engine-lab": {"kind": "lab", "rename": ("Engine Lab", "Unity Engineering Lab")},
    })
    db = db_session
    db.add_all([models.Project(name="Engine Lab", slug="engine-lab"),
                models.Project(name="Unity Engineering Lab", slug="custom")])
    db.flush()
    context_profiles.apply(db)
    assert db.query(models.Project).filter_by(slug="engine-lab").one().name == "Engine Lab"
