import copy
import time

import pytest

from app import models, need_analysis, needs
from app.ai_analysis import AIContext
from app.ai_client import AIError
from tests.test_ai_analysis import FakeClient, _completed_envelope
from tests.test_project_tiers import api  # noqa: F401


@pytest.fixture()
def pilot(db_session):
    db = db_session
    project = models.Project(name="Voice", slug="voice", needs=["offline speech"])
    other = models.Project(name="Desktop", slug="desktop")
    need = models.Need(title="offline speech")
    db.add_all([project, other, need])
    db.flush()
    links = [models.ContextNeed(project_id=p.id, need_id=need.id, constraints=constraint,
                                search_terms=["speech"], pilot=True)
             for p, constraint in [(project, "Android offline"), (other, "Desktop streaming")]]
    items = [models.Item(name=f"Voice {i}", source="github", external_id=f"o/voice{i}", url=f"https://example.org/{i}",
                         description="Offline speech recognition on Android", llm_summary="Speech recognition") for i in range(4)]
    db.add_all([*links, *items])
    db.commit()
    return db, project, links, items


def answer(batch, *, partial=False):
    pairs = batch["pairs"][:1] if partial else batch["pairs"]
    return {"schema_version": need_analysis.VERSION, "results": [{"item_id": p["item_id"],
        "context_need_id": p["context_need_id"], "score": 0.8, "constraint_status": "unknown",
        "rationale": "Check real device latency", "evidence": []} for p in pairs]}


def test_shared_need_keeps_context_constraints_and_only_invalidates_changed_context(pilot):
    db, project, links, items = pilot
    plan = need_analysis.plan(db)
    for batch in plan["batches"]:
        need_analysis.apply_result(db, batch, answer(batch), {"provider": "local-agent"})
    assert need_analysis.plan(db)["pending_pairs"] == 0
    links[0].constraints = "Android offline with 200 MB RAM"
    db.commit()
    assert need_analysis.plan(db)["pending_pairs"] == 4
    assert {r["project_id"]: r["pending_count"] for r in needs.overview(db)} == {project.id: 4, links[1].project_id: 0}
    assert all(link.state == "open" for link in links)
    assert db.query(models.ItemProjectDecision).count() == 0


def test_partial_results_leave_missing_pairs_pending_and_skip_changed_snapshots(pilot):
    db, _, links, _ = pilot
    batch = need_analysis.plan(db)["batches"][0]
    outcome = need_analysis.apply_result(db, batch, answer(batch, partial=True), {"provider": "gateway"})
    assert outcome["saved"] == 1 and outcome["missing"] == len(batch["pairs"]) - 1
    assert need_analysis.plan(db)["pending_pairs"] == 7
    links[0].constraints = "changed while request ran"
    db.commit()
    outcome = need_analysis.apply_result(db, batch, answer(batch), {"provider": "local-agent"})
    assert outcome["stale"] == 2 and outcome["saved"] == 2


def test_project_analysis_does_not_send_other_projects_or_requirements(pilot):
    db, project, links, items = pilot
    for link in links:
        db.add(models.ItemFeedback(item_id=items[0].id, kind="vote", project_slug=link.project.slug, verdict=1))
    db.commit()
    plan = need_analysis.plan(db, project_id=project.id)
    assert plan["candidate_pairs"] == 4
    for batch in plan["batches"]:
        assert {pair["context_need_id"] for pair in batch["pairs"]} == {links[0].id}
        assert {row["slug"] for row in batch["projects"]} == {project.slug}
        assert {row["project"] for row in batch["user_feedback"]} == {project.slug}
    assert plan["pending_pairs"] == 4
    first = plan["batches"][0]
    client = FakeClient([_completed_envelope(answer(first))])
    result = need_analysis.run(db, project_id=project.id, context=AIContext(client=client, max_calls=1))
    assert result["saved"] == 2
    assert {a.context_need_id for a in db.query(models.NeedAssessment)} == {links[0].id}


@pytest.mark.parametrize("mutation", ["duplicate", "foreign", "evidence", "unsupported"])
def test_invalid_agent_result_is_atomic(pilot, mutation):
    db, _, _, _ = pilot
    batch = need_analysis.plan(db)["batches"][0]
    data = answer(batch)
    if mutation == "duplicate":
        data["results"].append(copy.deepcopy(data["results"][0]))
    elif mutation == "foreign":
        data["results"][0]["context_need_id"] = 999
    elif mutation == "evidence":
        data["results"][0]["evidence"] = ["Invented successful benchmark"]
    else:
        data["results"][0]["constraint_status"] = "supported"
    with pytest.raises(ValueError):
        need_analysis.apply_result(db, batch, data, {})
    assert db.query(models.NeedAssessment).count() == 0


def test_closed_needs_keep_results_and_stop_creating_work(pilot):
    db, _, links, _ = pilot
    batch = need_analysis.plan(db)["batches"][0]
    need_analysis.apply_result(db, batch, answer(batch), {})
    links[0].state, links[0].result_note = "satisfied", "Measured on my device"
    links[0].result_fingerprint = needs.result_fingerprint(links[0])
    links[1].state = "deferred"
    db.commit()
    assert need_analysis.plan(db)["pending_pairs"] == 0
    assert db.query(models.NeedAssessment).count() == 4
    assert needs.overview(db, pilot_only=True)[0]["coverage"] == "confirmed"


def test_legacy_import_preserves_ids_constraints_closed_states_and_results(pilot):
    db, project, links, _ = pilot
    link = links[0]
    identity = link.id
    link.state, link.result_note = "satisfied", "Measured outcome"
    db.commit()
    needs.sync_legacy(db, project, ["offline speech", "OCR"])
    db.commit()
    assert (link.id, link.state, link.constraints, link.result_note) == (identity, "satisfied", "Android offline", "Measured outcome")
    assert project.needs == ["OCR"]
    needs.sync_legacy(db, project, ["OCR"])
    db.commit()
    assert len(needs.links(db, project.id)) == 2


def test_gateway_and_agent_use_same_contract_and_resume_with_one_batch(pilot):
    db, _, _, _ = pilot
    batch = need_analysis.plan(db)["batches"][0]
    db.commit()
    client = FakeClient([_completed_envelope(answer(batch))])
    result = need_analysis.run(db, context=AIContext(client=client, max_calls=1), max_batches=5)
    assert result["saved"] == 4 and client.generate_calls == 1
    assert result["error_code"] == "run_budget_exhausted"
    assert need_analysis.plan(db)["pending_pairs"] == 4


def test_gateway_pause_or_interval_does_not_submit_or_consume_budget(pilot):
    db, _, _, _ = pilot
    for readiness in [{"ready": False, "code": "dispatch_paused"},
                      {"ready": True, "code": "ready", "dispatch": {"next_allowed_at": (time.time() + 900) * 1000}}]:
        client = FakeClient([])
        client.readiness = lambda: readiness
        context = AIContext(client=client, max_calls=1)
        result = need_analysis.run(db, context=context)
        assert result["error_code"] in ("dispatch_paused", "local_rate_limited")
        assert context.remaining == 1 and client.generate_calls == 0
        assert db.query(models.NeedAssessment).count() == 0


def test_shortlist_is_bounded_and_includes_each_need(pilot):
    db, _, links, _ = pilot
    for i in range(40):
        db.add(models.Item(name=f"More {i}", source="github", external_id=f"o/more{i}", url="u", description="speech"))
    db.commit()
    plan = need_analysis.plan(db)
    assert plan["candidate_items"] == 20
    assert plan["candidate_pairs"] == 40
    assert plan["batches_required"] == 10
    assert plan["input_bytes"] > 0


def test_heuristic_confidence_cannot_outrank_a_confirmed_ai_match(pilot):
    db, project, links, items = pilot
    db.add(models.ItemProjectRelevance(item_id=items[0].id, project_id=project.id, score=1.0))
    db.add(models.ItemProjectRelevance(item_id=items[1].id, project_id=project.id, score=0.8))
    db.add(models.ItemAnalysisState(item_id=items[1].id, provenance={"mode": "ai"}))
    db.commit()
    pairs = needs.shortlist(db, [links[0]], limit=1)
    assert pairs[0][1].id == items[1].id


def test_need_api_requires_explicit_outcome_and_keeps_history(api):
    project = api.post("/api/projects", json={"name": "Need API", "needs": ["Voice"]}).json()
    row = api.get(f"/api/needs?project_id={project['id']}").json()[0]
    path = f"/api/needs/{row['id']}"
    assert api.put(path, json={"state": "satisfied"}).status_code == 400
    saved = api.put(path, json={"state": "satisfied", "result_note": "Measured 40 ms offline"}).json()
    assert saved["coverage"] == "confirmed"
    history = api.get(path + "/history").json()
    assert history[0]["actor"] == "user" and history[0]["detail"]["after"]["state"] == "satisfied"
    assert api.put(path, json={"constraints": None}).status_code == 400
    changed = api.put(path, json={"constraints": "Now on Android"}).json()
    assert changed["coverage"] == "result_stale" and changed["result_note"] == saved["result_note"]
    api.put(f"/api/projects/{project['id']}", json={"name": "Need API", "needs": ["Voice"]})
    assert api.get(f"/api/needs?project_id={project['id']}").json()[0]["state"] == "satisfied"
    assert api.delete(f"/api/projects/{project['id']}").status_code == 200
    assert api.get(f"/api/needs?project_id={project['id']}").json() == []
