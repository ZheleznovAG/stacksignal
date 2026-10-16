"""A whole collection run against a fake GitHub + Hugging Face (global lists, project searches,
refresh of tracked items, quality gate, archive/revive, housekeeping), the discovery priority in AI
selection, the one-time archive migration and the HTTP API for queries and coverage."""

import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import config, db as db_module, maintenance, models, pipeline
from app.main import app
from tests.fakes import Web, gh_repo, hf_model
from tests.test_batch_enrichment import FakeGateway, _context, _items, _project as _batch_project
from app.utils import utcnow

NOW = utcnow()


def _softbody(db):
    project = models.Project(name="Softbody Demo", slug="softbody", description="d",
                             keywords=["soft-body physics", "position based dynamics"])
    db.add(project)
    db.flush()
    return project


def _web():
    web = Web()
    web.gh_created = [gh_repo("a/global", 300)]
    web.gh_queries["soft-body physics"] = [gh_repo("b/niche", 20), gh_repo("c/junk", 2)]
    web.gh_queries["position based dynamics"] = [gh_repo("b/niche", 20)]
    web.hf_lists["likes7d"] = [hf_model("m/good", 40, 9000), hf_model("m/test", 0, 0)]
    web.hf_search["soft-body physics"] = [hf_model("m/physics", 5, 100)]
    return web


def _get(db, external_id):
    return db.query(models.Item).filter_by(external_id=external_id).one_or_none()


def _existing(db, external_id, source="huggingface", **fields):
    item = models.Item(source=source, external_id=external_id, name=external_id.split("/")[1], owner="o", url="u",
                       description="d", topics=[], raw=fields.pop("raw", {}), likes=fields.pop("likes", 0),
                       last_seen_at=fields.pop("last_seen_at", NOW), **fields)
    db.add(item)
    db.flush()
    return item


# ---- a whole run -------------------------------------------------------------------------

def test_a_run_stores_what_passes_the_gate_and_records_why_it_was_found(db_session):
    _softbody(db_session)
    web = _web()

    run = pipeline.run_collection(db_session, transport=web.transport)

    stored = {i.external_id for i in db_session.query(models.Item).all()}
    assert stored == {"a/global", "b/niche", "m/good", "m/physics"}  # c/junk (2 stars), m/test rejected
    assert run.items_found == 4 and run.items_new == 4
    niche = _get(db_session, "b/niche")
    discovery = db_session.query(models.ItemDiscovery).filter_by(item_id=niche.id).one()
    assert discovery.project_slug == "softbody" and discovery.query_text in ("soft-body physics", "position based dynamics")
    assert db_session.query(models.ItemDiscovery).filter_by(item_id=_get(db_session, "a/global").id).count() == 0
    counts = {q.text: q.last_new for q in db_session.query(models.SearchQuery).filter_by(source="github")}
    assert counts == {"soft-body physics": 1, "position based dynamics": 1}


def test_two_queries_of_one_project_finding_the_same_item_record_one_discovery(db_session):
    _softbody(db_session)
    web = _web()  # b/niche is returned by both "soft-body physics" and "position based dynamics"

    pipeline.run_collection(db_session, transport=web.transport)
    pipeline.run_collection(db_session, transport=web.transport)

    niche = _get(db_session, "b/niche")
    assert db_session.query(models.ItemDiscovery).filter_by(item_id=niche.id).count() == 1


def test_default_queries_are_created_for_projects_without_any(db_session):
    _softbody(db_session)
    pipeline.run_collection(db_session, transport=_web().transport)

    texts = {(q.source, q.text) for q in db_session.query(models.SearchQuery).all()}
    assert ("github", "position based dynamics") in texts and ("huggingface", "soft-body physics") in texts


def test_a_second_run_is_stable_and_does_not_duplicate_discoveries(db_session):
    _softbody(db_session)
    web = _web()
    pipeline.run_collection(db_session, transport=web.transport)
    before = (db_session.query(models.Item).count(), db_session.query(models.ItemDiscovery).count())

    run = pipeline.run_collection(db_session, transport=web.transport)

    assert (db_session.query(models.Item).count(), db_session.query(models.ItemDiscovery).count()) == before
    assert run.items_new == 0


def test_junk_already_stored_is_archived_when_seen_again_but_engaged_items_are_kept(db_session):
    _softbody(db_session)
    junk = _existing(db_session, "m/test")
    engaged = _existing(db_session, "m/keepme", status=models.Status.ASSESS)
    web = _web()
    web.hf_lists["likes7d"] += [hf_model("m/keepme", 0, 0)]  # no traction, but the user assessed it

    pipeline.run_collection(db_session, transport=web.transport)

    assert junk.archived is True
    assert engaged.archived is False and engaged.last_seen_at > NOW - datetime.timedelta(minutes=5)


def test_an_archived_item_that_now_passes_the_gate_comes_back(db_session):
    _softbody(db_session)
    back = _existing(db_session, "m/good", archived=True)

    pipeline.run_collection(db_session, transport=_web().transport)

    assert back.archived is False and back.likes == 40  # revived and refreshed


def test_tracked_items_get_fresh_numbers_even_when_no_list_mentions_them(db_session):
    _softbody(db_session)
    tracked = _existing(db_session, "z/tracked", source="github", status=models.Status.ASSESS,
                        last_seen_at=NOW - datetime.timedelta(days=4))
    tracked.stars = 10
    web = _web()
    web.gh_repos["z/tracked"] = gh_repo("z/tracked", 999)

    pipeline.run_collection(db_session, transport=web.transport)

    assert tracked.stars == 999 and tracked.last_seen_at > NOW - datetime.timedelta(minutes=5)


def test_stale_untouched_items_are_archived_by_the_run(db_session):
    _softbody(db_session)
    stale = _existing(db_session, "old/thing", last_seen_at=NOW - datetime.timedelta(days=60))

    pipeline.run_collection(db_session, transport=_web().transport)

    assert stale.archived is True


def test_a_github_outage_still_collects_hugging_face(db_session):
    _softbody(db_session)
    web = _web()
    web.github_rate_limited = True

    run = pipeline.run_collection(db_session, transport=web.transport)

    assert "github" in run.error
    stored = {i.external_id for i in db_session.query(models.Item).all()}
    assert {"m/good", "m/physics"} <= stored and not any(i.startswith(("a/", "b/")) for i in stored)


def test_discovery_can_be_switched_off(db_session, monkeypatch):
    _softbody(db_session)
    monkeypatch.setattr(config, "DISCOVERY_ENABLED", False)
    web = _web()

    pipeline.run_collection(db_session, transport=web.transport)

    assert db_session.query(models.SearchQuery).count() == 0
    assert not any("in%3Aname" in r or "search=" in r for r in web.requests)
    assert {i.external_id for i in db_session.query(models.Item).all()} == {"a/global", "m/good"}


# ---- pipeline observability: which stage failed, how long it took ------------------------

def test_a_run_records_the_duration_and_outcome_of_every_stage(db_session):
    _softbody(db_session)

    run = pipeline.run_collection(db_session, transport=_web().transport)

    names = [s["name"] for s in run.stages]
    assert names == ["collect", "discovery", "gate_and_score", "entity_linking", "enrich", "opportunities",
                     "capabilities", "embeddings", "housekeeping"]
    assert all(s["status"] == "ok" for s in run.stages)
    assert all(isinstance(s["duration_s"], float) and s["duration_s"] >= 0 for s in run.stages)
    enrich = next(s for s in run.stages if s["name"] == "enrich")
    assert enrich["ai"] == 0 and enrich["heuristic"] > 0  # AI is off in tests: everything was keyword-scored


def test_a_fatal_stage_failure_keeps_the_timings_of_the_stages_that_ran(db_session, monkeypatch):
    _softbody(db_session)
    monkeypatch.setattr(pipeline.discovery, "housekeeping", lambda db: (_ for _ in ()).throw(RuntimeError("boom")))

    with pytest.raises(RuntimeError):
        pipeline.run_collection(db_session, transport=_web().transport)

    failed = db_session.query(models.CollectionRun).one()
    assert failed.status == "partial"
    assert failed.items_found == 4 and failed.items_new == 4 and failed.items_enriched == 4
    assert "boom" in failed.error
    names = [s["name"] for s in failed.stages]
    assert names[-1] == "housekeeping" and failed.stages[-1]["status"] == "error"
    assert "boom" in failed.stages[-1]["error"]
    assert names[:-1] == ["collect", "discovery", "gate_and_score", "entity_linking", "enrich", "opportunities",
                          "capabilities", "embeddings"]  # everything before the failure still has its own timing


def test_failure_before_first_checkpoint_keeps_one_failed_run(db_session, monkeypatch):
    _softbody(db_session)

    def fail(db, projects):
        db.add(models.SearchQuery(project_slug="softbody", source="github", text="unfinished"))
        db.flush()
        raise RuntimeError("discovery broke")

    monkeypatch.setattr(pipeline.queries, "ensure_default_queries", fail)
    with pytest.raises(RuntimeError, match="discovery broke"):
        pipeline.run_collection(db_session, transport=_web().transport)

    run = db_session.query(models.CollectionRun).one()
    assert run.status == "failed" and run.finished_at is not None
    assert run.stages[-1]["name"] == "discovery" and run.stages[-1]["status"] == "error"
    assert db_session.query(models.SearchQuery).count() == 0


def test_failure_rolls_back_only_the_unfinished_stage(db_session, monkeypatch):
    _softbody(db_session)

    def fail(db):
        item = _get(db, "b/niche")
        item.description = "unfinished change"
        db.flush()
        raise RuntimeError("housekeeping broke")

    monkeypatch.setattr(pipeline.discovery, "housekeeping", fail)
    with pytest.raises(RuntimeError, match="housekeeping broke"):
        pipeline.run_collection(db_session, transport=_web().transport)

    db_session.expire_all()
    assert _get(db_session, "b/niche").description != "unfinished change"
    assert db_session.query(models.Item).count() == 4
    assert db_session.query(models.CollectionRun).one().status == "partial"


def test_failure_after_embeddings_keeps_previous_checkpoints(tmp_path, monkeypatch):
    # Real file and a fresh reader: identity-map state cannot masquerade as committed data.
    engine = create_engine(f"sqlite:///{tmp_path / 'checkpoints.db'}")
    db_module.Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False)
    monkeypatch.setattr(config, "EMBEDDINGS_ENABLED", True)
    monkeypatch.setattr(pipeline.embeddings, "embed", lambda texts: [[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(pipeline.embeddings, "BATCH_SIZE", 1)
    with Session() as db:
        _softbody(db)
        old = _existing(db, "m/old")
        old_id = old.id
        db.add(models.ItemEmbedding(item_id=old_id, signature="stale", vector=[0.0, 1.0]))
        db.commit()

        def fail(db):
            _get(db, "b/niche").description = "unfinished housekeeping"
            db.flush()
            raise RuntimeError("housekeeping broke")

        monkeypatch.setattr(pipeline.discovery, "housekeeping", fail)
        web = _web()
        for results in web.gh_queries.values():
            results[0]["description"] = "soft-body physics position based dynamics"
        with pytest.raises(RuntimeError, match="housekeeping broke"):
            pipeline.run_collection(db, transport=web.transport)

    with Session() as db:
        niche = _get(db, "b/niche")
        assert niche is not None and niche.description != "unfinished housekeeping"
        assert niche.relevances and niche.relevances[0].score > 0
        assert db.get(models.ItemEmbedding, niche.id).vector == [1.0, 0.0]
        assert db.get(models.ItemEmbedding, old_id).signature != "stale"
        run = db.query(models.CollectionRun).one()
        assert run.status == "partial" and run.finished_at is not None
        assert run.stages[-1]["name"] == "housekeeping" and run.stages[-1]["status"] == "error"
    engine.dispose()


def test_the_api_exposes_stage_timings(api, monkeypatch):
    client, _ = api
    # The endpoint runs a real collection; give it the fake web, or it would search GitHub for real.
    from app.routers import collect as collect_router
    web = _web()
    monkeypatch.setattr(collect_router, "run_collection",
                        lambda db, sources=None: pipeline.run_collection(db, sources, transport=web.transport))

    result = client.post("/api/collect/run", json={}).json()

    assert [s["name"] for s in result["stages"]] == ["collect", "discovery", "gate_and_score", "entity_linking",
                                                      "enrich", "opportunities", "capabilities", "embeddings",
                                                      "housekeeping"]
    assert client.get("/api/collect/runs").json()[0]["stages"] == result["stages"]


# ---- discovery priority in AI selection --------------------------------------------------

def test_items_found_by_a_project_search_get_priority_for_the_ai_slot(db_session, monkeypatch):
    monkeypatch.setattr(config, "AI_BATCH_SIZE", 1)
    projects = [_batch_project(db_session)]
    plain, discovered = _items(db_session, [("a shader tool", 0.5), ("a shader tool", 0.5)])
    gateway = FakeGateway()

    pipeline._enrich_items(db_session, [plain, discovered], projects, _context(gateway, 2),
                           discovered_ids={discovered.id})

    assert gateway.batches == [[discovered.id]]  # identical scores; the found-for-your-project one wins


def test_every_collected_item_is_scored_before_ai_prioritization(db_session, monkeypatch):
    _softbody(db_session)
    monkeypatch.setattr(config, "DISCOVERY_ENABLED", False)
    monkeypatch.setattr(config, "AI_BATCH_SIZE", 1)
    gateway = FakeGateway()
    monkeypatch.setattr(pipeline, "AIContext", lambda: _context(gateway, 1, reserved=0))
    monkeypatch.setattr(pipeline.scoring, "compute_momentum",
                        lambda item, velocity: 0.1 if item.name == "niche" else 0.9)
    web = Web()
    web.gh_created = [gh_repo(f"noise/hot{i}", 10000, description="unrelated web framework") for i in range(200)]
    web.gh_created.append(gh_repo("b/niche", 100,
                                  description="soft-body physics position based dynamics"))

    run = pipeline.run_collection(db_session, sources=["github"], transport=web.transport)

    niche = _get(db_session, "b/niche")
    assert run.items_found == 201
    assert niche.relevances and niche.relevances[0].score >= 0.8
    assert gateway.batches == [[niche.id]]
    assert db_session.query(models.ItemAnalysisState).count() == 201
    stage = next(s for s in run.stages if s["name"] == "enrich")
    assert stage["heuristic"] == 201 and stage["ai"] == 1


# ---- the one-time migration --------------------------------------------------------------

def test_the_archive_migration_hides_old_junk_but_never_touches_engaged_items(db_session):
    junk = _existing(db_session, "o/test")
    engaged = _existing(db_session, "o/assessed", status=models.Status.ASSESS)
    ai = _existing(db_session, "o/ai-read")
    db_session.add(models.ItemAnalysisState(item_id=ai.id, provenance={"mode": "ai"}))
    project = models.Project(name="P", slug="p", description="d", keywords=["k"])
    db_session.add(project)
    db_session.flush()
    db_session.add(models.ItemProjectRelevance(item_id=ai.id, project_id=project.id, score=0.9, rationale="r"))
    good = _existing(db_session, "o/popular", likes=500, raw={"pipeline_tag": "x", "library_name": "y"},
                     downloads=90000)
    db_session.flush()

    assert maintenance.archive_low_quality_items(db_session) == 1

    assert junk.archived is True
    assert not (engaged.archived or ai.archived or good.archived)


# ---- HTTP API ----------------------------------------------------------------------------

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

    app.dependency_overrides[db_module.get_db] = override
    seed = Session()
    softbody = _softbody(seed)
    shown = _existing(seed, "o/shown", likes=100)
    hidden = _existing(seed, "o/hidden", likes=100, archived=True)
    for item in (shown, hidden):
        seed.add(models.ItemProjectRelevance(item_id=item.id, project_id=softbody.id, score=0.9, rationale="r"))
    seed.add(models.ItemDiscovery(item_id=shown.id, project_slug="softbody", query_text="soft-body physics"))
    seed.commit()
    seed.close()
    yield TestClient(app), Session
    app.dependency_overrides.clear()


def test_archived_items_disappear_from_every_list(api):
    client, _ = api
    assert [i["name"] for i in client.get("/api/discover?limit=20").json()] == ["shown"]
    assert [i["name"] for i in client.get("/api/relevant").json()] == ["shown"]
    radar = client.get("/api/radar").json()
    assert [i["name"] for group in radar.values() for i in group] == ["shown"]
    stats = client.get("/api/stats").json()
    assert stats["items"] == 1 and stats["archived"] == 1


def test_items_say_which_search_found_them(api):
    client, _ = api
    (item,) = client.get("/api/discover").json()
    assert item["found_via"] == [{"project_slug": "softbody", "query": "soft-body physics"}]


def test_coverage_counts_relevant_and_search_found_items(api):
    client, _ = api
    (row,) = client.get("/api/coverage").json()
    assert row["slug"] == "softbody" and row["relevant"] == 1 and row["found_by_search"] == 1 and row["queries"] == 0


def test_queries_can_be_added_toggled_and_deleted(api):
    client, Session = api

    created = client.post("/api/queries", json={"project_slug": "softbody", "source": "github", "text": "  xpbd unity "})
    assert created.status_code == 200 and created.json()["origin"] == "user" and created.json()["text"] == "xpbd unity"
    query_id = created.json()["id"]
    assert client.post("/api/queries", json={"project_slug": "softbody", "source": "github",
                                             "text": "XPBD UNITY"}).status_code == 400  # duplicate
    assert client.post("/api/queries", json={"project_slug": "nope", "source": "github", "text": "abc"}).status_code == 400
    assert client.post("/api/queries", json={"project_slug": "softbody", "source": "gitlab", "text": "abc"}).status_code == 400

    assert client.put(f"/api/queries/{query_id}", json={"enabled": False}).json()["enabled"] is False
    assert [q["text"] for q in client.get("/api/queries").json()] == ["xpbd unity"]
    assert client.get("/api/coverage").json()[0]["queries"] == 0  # switched off

    assert client.delete(f"/api/queries/{query_id}").json() == {"ok": True}
    assert client.get("/api/queries").json() == []
    assert client.delete(f"/api/queries/{query_id}").status_code == 404


def test_regenerate_rebuilds_generated_queries_and_keeps_the_users(api):
    client, _ = api
    client.post("/api/queries", json={"project_slug": "softbody", "source": "github", "text": "my own"})

    result = client.post("/api/queries/regenerate", json={"project_slug": "softbody", "ai": False}).json()

    assert result["heuristic"] > 0 and result["ai"] == 0
    texts = {q["text"] for q in client.get("/api/queries").json()}
    assert "my own" in texts and "position based dynamics" in texts
    assert client.post("/api/queries/regenerate", json={"project_slug": "nope", "ai": False}).status_code == 404


def test_query_api_exposes_stale_profile_without_marking_user_queries_stale(api):
    client, _ = api
    client.post("/api/queries", json={"project_slug": "softbody", "source": "github", "text": "my own"})
    client.post("/api/queries/regenerate", json={"project_slug": "softbody", "ai": False})
    before = client.get("/api/queries").json()
    assert all(not q["stale"] for q in before)
    project = client.get("/api/projects").json()[0]
    assert client.put(f"/api/projects/{project['id']}", json={
        "name": project["name"], "keywords": project["keywords"], "needs": ["persistent outbox"]}).status_code == 200
    after = client.get("/api/queries").json()
    assert all(q["stale"] == (q["origin"] != "user") for q in after)
    assert all(q["profile_fingerprint"] and q["basis"] for q in after if q["origin"] != "user")


def test_deleting_a_project_removes_its_searches(api):
    client, Session = api
    client.post("/api/queries", json={"project_slug": "softbody", "source": "github", "text": "xpbd unity"})
    project_id = client.get("/api/projects").json()[0]["id"]

    assert client.delete(f"/api/projects/{project_id}").json() == {"ok": True}

    assert client.get("/api/queries").json() == []


# ---- runs must not overlap, HF momentum -----------------------------------------------------

def test_a_second_collection_cannot_start_while_one_is_running(db_session):
    assert pipeline._RUN_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(pipeline.CollectionBusy):
            pipeline.run_collection(db_session, transport=Web().transport)
    finally:
        pipeline._RUN_LOCK.release()
    pipeline.run_collection(db_session, transport=Web().transport)  # and the lock was not left held


def test_the_api_answers_409_while_a_run_is_in_progress(api):
    client, _ = api
    assert pipeline._RUN_LOCK.acquire(blocking=False)
    try:
        assert client.post("/api/collect/run", json={}).status_code == 409
    finally:
        pipeline._RUN_LOCK.release()


def test_a_shrinking_downloads_window_is_not_read_as_negative_momentum_of_thousands(db_session):
    from app import history
    item = _existing(db_session, "m/rolling", likes=10, downloads=40000)
    item.source = "huggingface"
    db_session.add(models.ItemSnapshot(item_id=item.id, stars=0, forks=0, likes=10, downloads=90000,
                                       captured_at=NOW - datetime.timedelta(days=7)))
    db_session.flush()

    velocity = history.recent_velocity(db_session, item)

    assert velocity is not None and velocity["attention_per_day"] > -5  # bounded; the old formula gave about -70
