"""Scoped agent assessments preserve analyses and user judgments, and survive collection."""

import copy
import json

import pytest

from app import agent_relevance as agent, enrichment, feedback, models, pipeline
from app.ai_analysis import AIContext


def _setup(db, n=3):
    projects = [models.Project(slug=slug, name=slug, description=f"{slug} project", keywords=[slug])
                for slug in ("alpha", "beta", "other")]
    db.add_all(projects)
    items = [models.Item(source="github", external_id=f"o/r{i}", name=f"r{i}", owner="o", url="u",
                         description=f"alpha tool {i}", topics=["alpha"], momentum_score=i / 10,
                         llm_summary=f"Original summary {i}", why_interesting="Original reason") for i in range(n)]
    db.add_all(items)
    db.commit()
    return projects, items


def _answer(path, relevance=None):
    batch = json.loads(path.read_text(encoding="utf-8"))
    answer = {"results": [{"item_id": item["id"], "relevance": relevance if relevance is not None else {
        slug: {"score": 0.8, "rationale": f"For {slug}"} for slug in item["requested_projects"]}}
        for item in batch["items"]]}
    _save_answer(path, answer)
    return answer


def _save_answer(path, answer):
    path.with_name(path.stem + ".result.json").write_text(json.dumps(answer), encoding="utf-8")


def _rows(db, item):
    return {r.project.slug: (r.score, r.base_score, r.rationale)
            for r in db.query(models.ItemProjectRelevance).filter_by(item_id=item.id)}


def test_plan_and_export_include_ai_and_heuristic_items_and_reserve_pairs(db_session, tmp_path):
    projects, items = _setup(db_session, 5)
    db_session.add(models.ItemAnalysisState(item_id=items[0].id, provenance={"mode": "ai"}))
    db_session.add(models.ItemAnalysisState(item_id=items[1].id, provenance={"mode": "heuristic"}))
    items[-1].archived = True
    db_session.commit()
    work = tmp_path / "work"
    plan = agent.plan(db_session, projects=["alpha,beta"], items_per_batch=2, work_dir=work)
    assert not work.exists()
    assert (plan["items"], plan["pending_pairs"], plan["batches_needed"]) == (4, 8, 2)
    paths = agent.export(db_session, projects=["alpha", "beta"], items_per_batch=2, batches=10, work_dir=work)
    assert len(paths) == 2
    assert not agent.export(db_session, projects=["beta,alpha"], work_dir=work)
    exported = [i for p in paths for i in json.loads(p.read_text(encoding="utf-8"))["items"]]
    assert {i["id"] for i in exported} == {i.id for i in items[:-1]}
    assert all(i["summary"] and i["requested_projects"] == ["alpha", "beta"] for i in exported)
    batch = json.loads(paths[0].read_text(encoding="utf-8"))
    assert [p["slug"] for p in batch["projects"]] == ["alpha", "beta"]
    assert "requested_projects" in batch["system_prompt"]
    assert batch["project_fingerprints"]["alpha"] == enrichment.project_fingerprint(projects[0])
    assert agent.status(db_session, projects=["alpha,beta"], work_dir=work)["reserved_pairs"] == 8
    # Another selected project is independently exportable for the same items.
    assert agent.export(db_session, projects=["other"], items_per_batch=5, work_dir=work)


@pytest.mark.parametrize("mode", ["ai", "heuristic", None])
def test_apply_preserves_original_analysis_outside_context_and_user_decisions(db_session, tmp_path, mode):
    (alpha, beta, other), (item,) = _setup(db_session, 1)
    original = {"mode": mode, "provider": "original", "model": "old", "content_fingerprint": "original-content",
                "project_fingerprints": {"other": "old-other"}, "custom": {"keep": True}}
    if mode is not None:
        db_session.add(models.ItemAnalysisState(item_id=item.id, provenance=copy.deepcopy(original)))
    db_session.add_all([
        models.ItemProjectRelevance(item_id=item.id, project_id=other.id, score=0.4, base_score=0.4, rationale="Keep"),
        models.ItemProjectDecision(item_id=item.id, project_id=alpha.id, status="hold", verdict="Wait for tests"),
    ])
    cap = models.Capability(name="Original capability", slug="original-capability")
    db_session.add(cap)
    db_session.flush()
    db_session.add(models.ItemCapability(item_id=item.id, capability_id=cap.id, confidence=0.6))
    db_session.commit()
    feedback.record_vote(db_session, item, "alpha", -1)
    db_session.commit()
    status, enriched = item.status, item.last_enriched_at
    (path,) = agent.export(db_session, projects=["alpha,beta"], work_dir=tmp_path)
    _answer(path)
    stats = agent.apply(db_session, path, provider="claude-code", model="actual-claude")
    assert (stats["applied"], stats["applied_pairs"]) == (1, 2)
    assert (item.llm_summary, item.why_interesting) == ("Original summary 0", "Original reason")
    assert item.status == status and item.last_enriched_at == enriched
    assert [link.capability.name for link in item.capability_links] == ["Original capability"]
    assert _rows(db_session, item)["other"] == (0.4, 0.4, "Keep")
    assert _rows(db_session, item)["alpha"][0:2] == (feedback.SUPPRESS_SCORE, 0.8)
    assert db_session.query(models.ItemProjectDecision).one().verdict == "Wait for tests"
    provenance = db_session.get(models.ItemAnalysisState, item.id).provenance
    for key in ("mode", "provider", "model", "content_fingerprint", "custom"):
        if mode is not None:
            assert provenance[key] == original[key]
        else:
            assert key not in provenance
    assert provenance["project_fingerprints"]["alpha"] == enrichment.project_fingerprint(alpha)
    if mode is not None:
        assert provenance["project_fingerprints"]["other"] == "old-other"
    scope = provenance["relevance_provenance"]["alpha"]
    assert (scope["mode"], scope["provider"], scope["model"]) == ("ai", "claude-code", "actual-claude")
    assert scope["content_fingerprint"] == enrichment.content_fingerprint(item)
    assert agent.plan(db_session, projects=["alpha,beta"], work_dir=tmp_path)["pending_pairs"] == 0
    assert path.exists() and path.with_name(path.stem + ".applied.json").exists()
    assert agent.apply(db_session, path)["already_current_pairs"] == 2


def test_empty_relevance_removes_stale_matches_but_preserves_positive_vote(db_session, tmp_path):
    (alpha, beta, other), (item,) = _setup(db_session, 1)
    db_session.add_all([models.ItemProjectRelevance(item_id=item.id, project_id=p.id,
                                                  score=0.5, base_score=0.5, rationale="Old")
                       for p in (alpha, beta, other)])
    feedback.record_vote(db_session, item, "alpha", 1)
    db_session.commit()
    (path,) = agent.export(db_session, projects=["alpha,beta"], work_dir=tmp_path)
    _answer(path, {})
    agent.apply(db_session, path)
    rows = _rows(db_session, item)
    assert "beta" not in rows and rows["alpha"][0] == feedback.BOOST_SCORE
    assert rows["other"] == (0.5, 0.5, "Old")
    assert agent.plan(db_session, projects=["alpha,beta"], work_dir=tmp_path)["current_pairs"] == 2


@pytest.mark.parametrize("invalid", ["missing", "duplicate", "extra_item", "extra_project", "extra_field", "bad_score"])
def test_invalid_response_mutates_nothing_and_stays_resumable(db_session, tmp_path, invalid):
    _, items = _setup(db_session, 2)
    (path,) = agent.export(db_session, projects=["alpha"], work_dir=tmp_path)
    answer = _answer(path)
    if invalid == "missing":
        answer["results"].pop()
    elif invalid == "duplicate":
        answer["results"].append(copy.deepcopy(answer["results"][0]))
    elif invalid == "extra_item":
        answer["results"][0]["item_id"] = 99999
    elif invalid == "extra_project":
        answer["results"][0]["relevance"]["beta"] = {"score": 0.5, "rationale": "Unknown"}
    elif invalid == "extra_field":
        answer["results"][0]["summary"] = "Must not replace"
    else:
        answer["results"][0]["relevance"]["alpha"]["score"] = 1.5
    _save_answer(path, answer)
    with pytest.raises(ValueError):
        agent.apply(db_session, path)
    assert db_session.query(models.ItemAnalysisState).count() == 0
    assert db_session.query(models.ItemProjectRelevance).count() == 0
    assert items[0].llm_summary == "Original summary 0"
    assert agent.status(db_session, projects=["alpha"], work_dir=tmp_path)["open_batches"] == 1


def test_apply_skips_changed_content_and_only_changed_project_pairs(db_session, tmp_path):
    (alpha, beta, _), items = _setup(db_session, 3)
    (path,) = agent.export(db_session, projects=["alpha,beta"], work_dir=tmp_path)
    _answer(path)
    alpha.description = "Edited profile"
    items[0].description = "Edited content"
    items[1].archived = True
    db_session.commit()
    stats = agent.apply(db_session, path)
    assert stats["changed_items"] == stats["archived_items"] == stats["changed_pairs"] == 1
    assert (stats["applied"], stats["applied_pairs"]) == (1, 1)
    provenance = db_session.get(models.ItemAnalysisState, items[2].id).provenance
    assert set(provenance["project_fingerprints"]) == {"beta"}
    assert set(_rows(db_session, items[2])) == {"beta"}
    assert agent.plan(db_session, projects=["alpha,beta"], work_dir=tmp_path)["available_pairs"] == 3


def test_changed_open_batch_does_not_reserve_updated_pairs(db_session, tmp_path):
    (alpha, _, _), (item,) = _setup(db_session, 1)
    agent.export(db_session, projects=["alpha"], work_dir=tmp_path)
    alpha.description = "New version"
    db_session.commit()
    (new_path,) = agent.export(db_session, projects=["alpha"], work_dir=tmp_path)
    assert new_path.name == "batch-0002.json"


def test_export_skips_current_ai_pairs_and_requests_only_stale_scopes(db_session, tmp_path):
    (alpha, beta, _), items = _setup(db_session, 2)
    db_session.add(models.ItemAnalysisState(item_id=items[0].id, provenance={
        "mode": "ai", "content_fingerprint": enrichment.content_fingerprint(items[0]),
        "project_fingerprints": {"alpha": enrichment.project_fingerprint(alpha)}}))
    # A heuristic profile fingerprint without scoped AI provenance is never enough.
    db_session.add(models.ItemAnalysisState(item_id=items[1].id, provenance={
        "mode": "heuristic", "content_fingerprint": enrichment.content_fingerprint(items[1]),
        "project_fingerprints": {"alpha": enrichment.project_fingerprint(alpha)}}))
    db_session.commit()
    (path,) = agent.export(db_session, projects=["alpha,beta"], work_dir=tmp_path)
    exported = {i["id"]: i for i in json.loads(path.read_text(encoding="utf-8"))["items"]}
    assert exported[items[0].id]["requested_projects"] == ["beta"]
    assert exported[items[1].id]["requested_projects"] == ["alpha", "beta"]
    answer = _answer(path)
    next(i for i in answer["results"] if i["item_id"] == items[0].id)["relevance"]["alpha"] = {
        "score": 0.3, "rationale": "Already current, outside item's scope"}
    _save_answer(path, answer)
    with pytest.raises(ValueError, match="Unrequested"):
        agent.apply(db_session, path)


@pytest.mark.parametrize("change", [None, "content", "profile"])
def test_collection_preserves_scoped_ai_without_promoting_summary_mode(db_session, tmp_path, change):
    (alpha, beta, _), (item,) = _setup(db_session, 1)
    (path,) = agent.export(db_session, projects=["alpha,beta"], work_dir=tmp_path)
    _answer(path, {"alpha": {"score": 0.8, "rationale": "Claude judgment"}})
    agent.apply(db_session, path)
    before = copy.deepcopy(db_session.get(models.ItemAnalysisState, item.id).provenance["relevance_provenance"])
    if change == "content":
        item.description = "No matching content"
        item.topics = []
    elif change == "profile":
        alpha.keywords = ["changed"]
    db_session.commit()
    pipeline._enrich_items(db_session, [item], [alpha, beta], AIContext())
    provenance = db_session.get(models.ItemAnalysisState, item.id).provenance
    assert provenance["mode"] == "heuristic"
    assert provenance["relevance_provenance"] == before
    assert "content_fingerprint" not in provenance  # no claim that AI read the summary
    if change is None:
        assert _rows(db_session, item) == {"alpha": (0.8, 0.8, "Claude judgment")}
        assert agent.plan(db_session, projects=["alpha,beta"], work_dir=tmp_path)["current_pairs"] == 2
    else:
        assert "Claude judgment" not in str(_rows(db_session, item))
        expected = 0 if change == "content" else 1
        assert agent.plan(db_session, projects=["alpha,beta"], work_dir=tmp_path)["current_pairs"] == expected


def test_scope_and_batch_limits_are_explicit(db_session, tmp_path):
    _setup(db_session, 1)
    for projects in ([], ["missing"]):
        with pytest.raises(ValueError):
            agent.plan(db_session, projects=projects, work_dir=tmp_path)
    with pytest.raises(ValueError):
        agent.export(db_session, projects=["alpha"], items_per_batch=101, work_dir=tmp_path)


@pytest.mark.parametrize("change", [None, "content", "profile"])
def test_relevant_api_admits_current_scoped_ai_and_keeps_summary_badge_honest(tmp_path, change):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base, get_db
    from app.routers.relevant import router

    engine = create_engine(f"sqlite:///{tmp_path / 'api.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        (alpha, beta, _), (item,) = _setup(db, 1)
        (path,) = agent.export(db, projects=["alpha"], work_dir=tmp_path)
        _answer(path)
        agent.apply(db, path, provider="claude-code", model="actual-claude")
        # Higher keyword-only match must not hide the lower AI-assessed match when deduplicating.
        db.add(models.ItemProjectRelevance(item_id=item.id, project_id=beta.id,
                                           score=1.0, base_score=1.0, rationale="Keywords only"))
        if change == "content":
            item.description = "Changed upstream"
        elif change == "profile":
            alpha.description = "Changed requirements"
        db.commit()

    api = FastAPI()
    api.include_router(router)

    def override():
        with Session() as db:
            yield db

    api.dependency_overrides[get_db] = override
    client = TestClient(api)
    response = client.get("/api/relevant?project_slug=alpha&ai_only=true")
    assert response.status_code == 200
    data = response.json()
    if change is None:
        assert len(data) == 1 and data[0]["ai_analyzed"] is False
        assert data[0]["llm_summary"] == "Original summary 0"
        source = data[0]["matched_relevance"]["analysis"]
        assert (source["scope"], source["provider"], source["model"]) == (
            "project_relevance", "claude-code", "actual-claude")
        assert client.get("/api/relevant?ai_only=true").json()[0]["matched_relevance"]["project_slug"] == "alpha"
    else:
        assert data == []
    assert client.get("/api/relevant?project_slug=beta&ai_only=true").json() == []
