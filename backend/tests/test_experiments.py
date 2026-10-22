import copy

import pytest

from app import decisions, experiments, models, needs, project_intelligence
from tests.test_project_tiers import api  # noqa: F401


@pytest.fixture()
def scenario(db_session):
    db = db_session
    project = models.Project(name="Reader", slug="reader", description="Offline speech import", stack=["Python"])
    need = models.Need(title="Speech recognition", origin="user")
    item = models.Item(name="Speech demo", source="github", external_id="demo/speech", url="https://example.org/speech",
                       description="Local speech recognition", llm_summary="Offline speech")
    db.add_all([project, need, item])
    db.flush()
    link = models.ContextNeed(project_id=project.id, need_id=need.id, constraints="Offline",
                              acceptance_criteria="Run ten samples", search_terms=["speech"])
    db.add(link)
    db.commit()
    return db, project, link, item


def test_brief_is_frozen_idempotent_and_does_not_claim_an_outcome(scenario):
    db, project, link, item = scenario
    row = experiments.create(db, link, item)
    db.commit()
    assert row.status == "draft" and link.state == "open"
    assert row.snapshot["assessment"]["status"] == "unassessed"
    assert "Run ten samples" in row.brief and item.url in row.brief
    assert experiments.create(db, link, item).id == row.id
    assert db.query(models.NeedExperiment).count() == 1
    assert db.query(models.ItemProjectDecision).count() == 0
    original = row.brief
    link.constraints = "Android only"
    db.commit()
    assert experiments.serialise(db, row)["stale"]
    assert row.brief == original and row.snapshot["need"]["constraints"] == "Offline"
    other = experiments.create(db, link, item)
    db.commit()
    assert other.id != row.id


@pytest.mark.parametrize("change", ["requirements", "description", "dependencies", "item", "summary", "archived"])
def test_changed_context_keeps_history_but_cannot_confirm_current_need(scenario, change):
    db, project, link, item = scenario
    row = experiments.create(db, link, item)
    db.commit()
    if change == "requirements":
        link.acceptance_criteria = "Now run twenty samples"
    elif change == "description":
        project.description = "Server-side transcription"
    elif change == "dependencies":
        db.add(models.ProjectDependency(project_id=project.id, name="changed", package_key="changed", ecosystem="pypi"))
        db.flush()
        db.expire(project, ["dependencies"])
    elif change == "item":
        item.description = "Changed upstream"
    elif change == "summary":
        item.llm_summary = "Updated analysis"
    else:
        item.archived = True
    db.commit()
    assert experiments.is_stale(db, row)
    with pytest.raises(experiments.ExperimentError):
        experiments.update(db, row, status="running")
    with pytest.raises(experiments.ExperimentError):
        experiments.update(db, row, status="completed", outcome="useful", result_note="Ten samples checked",
                           evidence=["report.json"], confirm_need=True)
    assert row.status == "draft" and link.state == "open"
    experiments.update(db, row, status="completed", outcome="useful", result_note="Result for old conditions", evidence=["report.json"])
    db.commit()
    assert row.status == "completed" and link.state == "open"


def test_explicit_result_closes_need_and_keeps_decision_history(scenario):
    db, project, link, item = scenario
    decisions.set_decision(db, item, project, "assess", goal="Keep my original goal", criteria="Keep original criterion")
    row = experiments.create(db, link, item)
    db.commit()
    experiments.update(db, row, status="running")
    db.commit()
    assert link.state == "evaluating"
    experiments.update(db, row, status="completed", outcome="useful", result_note="Ten samples: 90 ms offline",
                       evidence=["python benchmark.py", "results/speech.json"], confirm_need=True, decision="adopt")
    db.commit()
    assert link.state == "satisfied" and link.confirmed_item_id == item.id
    assert needs.overview(db, project.id)[0]["coverage"] == "confirmed"
    decision = decisions.get(db, item.id, project.id)
    assert (decision.status, decision.goal, decision.criteria) == (models.Status.ADOPT, "Keep my original goal", "Keep original criterion")
    assert db.query(models.StatusHistory).count() == 2
    assert db.query(models.NeedEvent).order_by(models.NeedEvent.id.desc()).first().detail["need_confirmed"]
    saved = copy.deepcopy(row.snapshot)
    needs.sync_legacy(db, project, [link.need.title])
    project_intelligence.apply(db, project, {"needs": []})
    db.commit()
    assert row.snapshot == saved and row.result_note == "Ten samples: 90 ms offline"
    with pytest.raises(experiments.ExperimentError):
        experiments.update(db, row, status="completed", outcome="inconclusive", result_note="Overwrite")


@pytest.mark.parametrize("params", [
    {"outcome": "useful", "result_note": "Only a claim"},
    {"outcome": "not_useful", "result_note": "Only a claim"},
    {"outcome": "useful", "evidence": ["report.json"]},
    {"outcome": "inconclusive", "result_note": "No measurements", "confirm_need": True},
])
def test_completion_validates_before_mutating(scenario, params):
    db, _, link, item = scenario
    row = experiments.create(db, link, item)
    db.commit()
    with pytest.raises(experiments.ExperimentError):
        experiments.update(db, row, status="completed", **params)
    assert row.status == "draft" and link.state == "open"


def test_inconclusive_result_does_not_change_existing_decision_or_close_need(scenario):
    db, project, link, item = scenario
    decisions.set_decision(db, item, project, "watch", verdict="My existing note")
    row = experiments.create(db, link, item)
    db.commit()
    experiments.update(db, row, status="running")
    experiments.update(db, row, status="completed", outcome="inconclusive", result_note="Target device unavailable")
    db.commit()
    assert link.state == "open" and row.active_key is None
    assert decisions.get(db, item.id, project.id).verdict == "My existing note"
    assert experiments.create(db, link, item).id != row.id


@pytest.mark.parametrize("reason", ["blocked", "archived_project", "closed", "unrelated", "conflict"])
def test_ineligible_candidate_cannot_start_a_new_experiment(scenario, reason):
    db, project, link, item = scenario
    if reason == "blocked":
        need = models.Need(title="Prerequisite")
        db.add(need)
        db.flush()
        parent = models.ContextNeed(project_id=project.id, need_id=need.id)
        db.add(parent)
        db.flush()
        db.add(models.NeedDependency(project_id=project.id, prerequisite_id=parent.id, dependent_id=link.id))
    elif reason == "archived_project":
        project.lifecycle_status = "archived"
    elif reason == "closed":
        link.state = "deferred"
    elif reason == "unrelated":
        item.name, item.description, item.llm_summary = "OCR", "Printed text", "Text scanning"
    else:
        db.add(models.NeedAssessment(context_need_id=link.id, item_id=item.id,
            fingerprint=needs.fingerprint(link, item), score=0.8, constraint_status="conflict"))
    db.commit()
    with pytest.raises(experiments.ExperimentError):
        experiments.create(db, link, item)
    assert db.query(models.NeedExperiment).count() == 0


def test_deleted_candidate_retains_frozen_brief_and_result(scenario):
    db, _, link, item = scenario
    row = experiments.create(db, link, item)
    db.commit()
    original = row.brief
    db.delete(item)
    db.commit()
    db.refresh(row)
    assert row.item_id is None and experiments.is_stale(db, row)
    experiments.update(db, row, status="completed", outcome="inconclusive", result_note="Candidate removed")
    db.commit()
    assert row.brief == original
    db.delete(link)
    db.commit()
    assert db.query(models.NeedExperiment).count() == 0


def test_concurrent_result_cannot_overwrite_a_completed_experiment(scenario):
    from sqlalchemy.orm import Session
    from sqlalchemy.orm.exc import StaleDataError

    db, _, link, item = scenario
    row = experiments.create(db, link, item)
    db.commit()
    with Session(db.get_bind()) as other:
        outdated = other.get(models.NeedExperiment, row.id)
        experiments.update(db, row, status="completed", outcome="inconclusive", result_note="First result")
        db.commit()
        with pytest.raises(StaleDataError):
            experiments.update(other, outdated, status="completed", outcome="inconclusive", result_note="Overwrite")
            other.commit()
        other.rollback()
    db.expire_all()
    assert db.get(models.NeedExperiment, row.id).result_note == "First result"


def test_a_new_constraint_conflict_prevents_starting_an_earlier_draft(scenario):
    db, _, link, item = scenario
    row = experiments.create(db, link, item)
    db.commit()
    db.add(models.NeedAssessment(context_need_id=link.id, item_id=item.id,
        fingerprint=needs.fingerprint(link, item), score=0.2, constraint_status="conflict"))
    db.commit()
    with pytest.raises(experiments.ExperimentError, match="противоречие"):
        experiments.update(db, row, status="running")
    assert row.status == "draft" and link.state == "open"


def test_api_exposes_download_and_validates_result_atomically(api):
    from app.db import get_db
    from app.main import app

    override = app.dependency_overrides[get_db]()
    db = next(override)
    project = models.Project(name="Brief API", slug="brief-api")
    need = models.Need(title="Speech import")
    item = models.Item(name="Speech demo", source="github", external_id="demo/api", url="https://example.org", description="speech")
    db.add_all([project, need, item])
    db.flush()
    link = models.ContextNeed(project_id=project.id, need_id=need.id, search_terms=["speech"])
    db.add(link)
    db.commit()
    path = f"/api/needs/{link.id}/experiments"
    row = api.post(path, json={"item_id": item.id}).json()
    assert row["status"] == "draft"
    assert api.post(path, json={"item_id": item.id}).json()["id"] == row["id"]
    download = api.get(f"/api/experiments/{row['id']}/brief")
    assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]
    assert download.text == row["brief"]
    assert api.get(f"/api/experiments?project_id={project.id}").json()[0]["candidate_name"] == item.name
    target = f"/api/experiments/{row['id']}"
    assert api.put(target, json={"status": "completed", "outcome": "useful", "result_note": "Unsupported claim"}).status_code == 400
    assert api.get(target).json()["status"] == "draft"
    assert api.put(target, json={"status": "completed", "outcome": "inconclusive", "result_note": "No target device"}).status_code == 200
    assert api.put(target, json={"status": "running"}).status_code == 400
    assert api.get("/api/experiments/99999").status_code == 404
    assert api.delete(f"/api/projects/{project.id}").status_code == 200
    assert api.get(target).status_code == 404
    override.close()
