"""The analysis backlog worked off by a local agent: the same prompt, payload and validation as a gateway
batch, stored as an ordinary AI analysis that the pipeline then leaves alone."""

import json

import pytest

from app import agent_analysis, enrichment, models, pipeline
from app.ai_analysis import AIContext


def _setup(db, n=3):
    project = models.Project(name="Alpha", slug="alpha", description="d", keywords=["shader"])
    db.add(project)
    items = [models.Item(source="github", external_id=f"o/r{i}", name=f"r{i}", owner="o", url="u",
                         description=f"shader tool {i}", topics=[], momentum_score=0.1 * i) for i in range(n)]
    db.add_all(items)
    db.commit()
    return project, items


def _answer(batch_path, **overrides):
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    results = [{"item_id": item["id"], "summary": f"Что такое {item['name']}", "why_interesting": "",
                "capabilities": ["shader authoring"], "relevance": {"alpha": {"score": 0.8, "rationale": "Шейдеры"}},
                **overrides} for item in batch["items"]]
    batch_path.with_name(batch["result_file"]).write_text(json.dumps({"results": results}, ensure_ascii=False),
                                                          encoding="utf-8")
    return batch


def test_export_hands_out_the_backlog_once_with_the_gateway_prompt_and_payload(db_session, tmp_path):
    _, items = _setup(db_session, 5)
    first = agent_analysis.export(db_session, items_per_batch=2, batches=2, work_dir=tmp_path)
    second = agent_analysis.export(db_session, items_per_batch=2, batches=5, work_dir=tmp_path)

    context = json.loads((tmp_path / "context.json").read_text(encoding="utf-8"))
    assert context["system_prompt"] == enrichment.batch_prompt()
    assert [p["slug"] for p in context["projects"]] == ["alpha"]
    handed = [i["id"] for p in first + second for i in json.loads(p.read_text(encoding="utf-8"))["items"]]
    assert sorted(handed) == sorted(i.id for i in items)  # every item once, open batches are reserved
    assert [p.name for p in first + second] == ["batch-0001.json", "batch-0002.json", "batch-0003.json"]
    top = json.loads(first[0].read_text(encoding="utf-8"))["items"][0]
    assert top["name"] == "r4" and top["kind"] == "repository"  # most momentum first, gateway payload shape


def test_an_applied_answer_is_an_ai_analysis_the_pipeline_does_not_redo(db_session, tmp_path):
    project, items = _setup(db_session, 2)
    (path,) = agent_analysis.export(db_session, items_per_batch=5, work_dir=tmp_path)
    _answer(path)
    stats = agent_analysis.apply(db_session, path, model="claude-opus-5-5")

    assert stats == {"applied": 2, "changed": 0, "already_analysed": 0, "missing": 0, "unanswered": 0}
    item = db_session.get(models.Item, items[0].id)
    assert item.llm_summary == "Что такое r0"
    state = db_session.get(models.ItemAnalysisState, item.id).provenance
    assert (state["mode"], state["provider"], state["model"]) == ("ai", "claude-code", "claude-opus-5-5")
    assert [r.score for r in item.relevances] == [0.8]
    assert [link.capability.name for link in item.capability_links] == ["shader authoring"]
    assert not (tmp_path / path.name).exists() and (tmp_path / "done" / path.name).exists()

    stats = pipeline._enrich_items(db_session, items, [project], AIContext())
    assert (stats["skipped_current"], stats["heuristic"]) == (2, 0)  # read: neither redone nor overwritten
    assert agent_analysis.status(db_session, work_dir=tmp_path)["backlog"] == 0


def test_explicit_refresh_updates_existing_ai_and_records_actual_agent(db_session, tmp_path):
    project, items = _setup(db_session, 1)
    (path,) = agent_analysis.export(db_session, work_dir=tmp_path)
    _answer(path)
    # Another agent finished while this batch was open. Default apply must preserve its answer.
    db_session.add(models.ItemAnalysisState(item_id=items[0].id, provenance={"mode": "ai", "provider": "other"}))
    db_session.commit()
    stats = agent_analysis.apply(db_session, path, provider="codex", model="GPT-6", refresh=True)
    assert stats["applied"] == 1 and stats["already_analysed"] == 0
    provenance = db_session.get(models.ItemAnalysisState, items[0].id).provenance
    assert provenance["provider"] == "codex" and provenance["model"] == "GPT-6"
    assert provenance["project_fingerprints"][project.slug] == enrichment.project_fingerprint(project)

def test_refresh_preserves_relevance_outside_exported_project_scope(db_session, tmp_path):
    project, items = _setup(db_session, 1)
    (path,) = agent_analysis.export(db_session, work_dir=tmp_path)
    other = models.Project(name="Other", slug="other")
    db_session.add(other)
    db_session.flush()
    db_session.add(models.ItemProjectRelevance(item_id=items[0].id, project_id=other.id,
                                              score=0.7, rationale="Existing judgment"))
    db_session.add(models.ItemAnalysisState(item_id=items[0].id, provenance={
        "mode": "ai", "project_fingerprints": {"other": "old-other"}}))
    db_session.commit()
    _answer(path)
    agent_analysis.apply(db_session, path, provider="codex", refresh=True)
    row = db_session.query(models.ItemProjectRelevance).filter_by(project_id=other.id).one()
    assert row.score == 0.7 and row.rationale == "Existing judgment"
    state = db_session.get(models.ItemAnalysisState, items[0].id).provenance
    assert state["project_fingerprints"]["other"] == "old-other"
    assert state["project_fingerprints"][project.slug] == enrichment.project_fingerprint(project)


def test_an_invalid_answer_stores_nothing_and_keeps_the_batch(db_session, tmp_path):
    _setup(db_session, 2)
    (path,) = agent_analysis.export(db_session, items_per_batch=5, work_dir=tmp_path)
    _answer(path, relevance={"unknown-project": {"score": 0.5, "rationale": "x"}})
    with pytest.raises(ValueError):
        agent_analysis.apply(db_session, path)
    _answer(path, summary="")  # schema: a summary is required
    with pytest.raises(ValueError):
        agent_analysis.apply(db_session, path)
    assert db_session.query(models.ItemAnalysisState).count() == 0
    assert path.exists()


def test_changed_items_and_unanswered_ones_return_to_the_backlog(db_session, tmp_path):
    _, items = _setup(db_session, 3)
    (path,) = agent_analysis.export(db_session, items_per_batch=5, work_dir=tmp_path)
    batch = json.loads(path.read_text(encoding="utf-8"))
    answered = [i for i in batch["items"] if i["id"] != items[0].id]
    path.with_name(batch["result_file"]).write_text(json.dumps({"results": [
        {"item_id": i["id"], "summary": "s", "why_interesting": "", "capabilities": [], "relevance": {}}
        for i in answered]}), encoding="utf-8")
    db_session.get(models.Item, items[1].id).description = "rewritten upstream"
    db_session.commit()

    stats = agent_analysis.apply(db_session, path)
    assert (stats["applied"], stats["changed"], stats["unanswered"]) == (1, 1, 1)
    again = agent_analysis.export(db_session, items_per_batch=5, work_dir=tmp_path)
    assert sorted(i["id"] for i in json.loads(again[0].read_text(encoding="utf-8"))["items"]) == sorted(
        [items[0].id, items[1].id])


def test_an_edited_project_keeps_its_old_fingerprint_so_only_its_relevance_is_redone(db_session, tmp_path):
    project, items = _setup(db_session, 1)
    (path,) = agent_analysis.export(db_session, work_dir=tmp_path)
    _answer(path)
    project.keywords = ["shader", "compute"]
    db_session.commit()
    agent_analysis.apply(db_session, path)
    provenance = db_session.get(models.ItemAnalysisState, items[0].id).provenance
    assert provenance["project_fingerprints"]["alpha"] != enrichment.project_fingerprint(project)


def test_release_returns_open_batches_to_the_backlog(db_session, tmp_path):
    _setup(db_session, 2)
    agent_analysis.export(db_session, items_per_batch=1, batches=2, work_dir=tmp_path)
    assert agent_analysis.release(tmp_path) == 2
    assert len(agent_analysis.export(db_session, items_per_batch=5, work_dir=tmp_path)) == 1


def test_parallel_exports_never_hand_out_an_item_twice(tmp_path):
    import threading
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.db import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'db.sqlite'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as db:
        _setup(db, 40)
    work = tmp_path / "work"
    paths, errors = [], []

    def worker():
        try:
            with Session() as db:
                paths.extend(agent_analysis.export(db, items_per_batch=5, batches=2, work_dir=work))
        except Exception as error:  # surfaced below
            errors.append(error)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    ids = [i["id"] for p in paths for i in json.loads(p.read_text(encoding="utf-8"))["items"]]
    assert len(paths) == len({p.name for p in paths}) == 8
    assert len(ids) == len(set(ids)) == 40
    assert not (work / ".export.lock").exists()


def test_describe_is_a_compact_view_of_the_batch(db_session, tmp_path):
    _setup(db_session, 2)
    (path,) = agent_analysis.export(db_session, work_dir=tmp_path)
    text = agent_analysis.describe(path)
    assert text.startswith("batch 1 -> write batch-0001.result.json")
    assert "repository o/r1" in text and "shader tool 1" in text
