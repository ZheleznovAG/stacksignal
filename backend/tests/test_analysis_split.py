"""AI analysis is tracked in two parts: the item's content (summary, capabilities) and, separately,
each project's profile (relevance). Editing one project must redo only relevance to that project,
in a short call, and leave summaries and the other projects' relevance alone."""

import json

from app import config, enrichment, maintenance, models, pipeline
from tests.test_batch_enrichment import FakeGateway, _context, _items, _project


class SplitGateway(FakeGateway):
    """Answers full analyses like FakeGateway and relevance-only requests with a marker rationale."""

    def __init__(self):
        super().__init__(namespace="ns-split")
        self.relevance_calls = []

    def generate(self, body, key):
        system = body["messages"][0]["content"][0]["text"]
        if "Score how useful each item is for EACH" not in system:
            return super().generate(body, key)
        self.calls += 1
        payload = json.loads(body["messages"][1]["content"][0]["text"])
        slugs = [p["slug"] for p in payload["projects"]]
        self.relevance_calls.append({"items": [i["id"] for i in payload["items"]], "projects": slugs,
                                    "has_summary": all(i["summary"] for i in payload["items"])})
        results = [{"item_id": i["id"], "relevance": {s: {"score": 0.55, "rationale": "redone"} for s in slugs}}
                   for i in payload["items"]]
        return {"id": f"resp_{self.calls}", "status": "completed", "provider": "fake", "model": "fake",
                "output": [{"type": "json", "data": {"results": results}}]}


def _analysed(db, monkeypatch):
    monkeypatch.setattr(config, "AI_BATCH_SIZE", 5)
    alpha = _project(db, "alpha", ("shader",))
    beta = _project(db, "beta", ("audio",))
    (item,) = _items(db, [("a shader tool", 0.5)])
    gateway = SplitGateway()
    pipeline._enrich_items(db, [item], [alpha, beta], _context(gateway, 6, reserved=0))
    return alpha, beta, item, gateway


def _relevance(db, item):
    return {r.project.slug: (r.score, r.rationale) for r in db.query(models.ItemProjectRelevance).filter_by(item_id=item.id)}


def test_a_full_analysis_records_a_fingerprint_for_content_and_for_each_project(db_session, monkeypatch):
    alpha, beta, item, _ = _analysed(db_session, monkeypatch)

    provenance = db_session.get(models.ItemAnalysisState, item.id).provenance

    assert provenance["content_fingerprint"] == enrichment.content_fingerprint(item)
    assert provenance["project_fingerprints"] == {"alpha": enrichment.project_fingerprint(alpha),
                                                  "beta": enrichment.project_fingerprint(beta)}


def test_nothing_is_redone_while_nothing_changed(db_session, monkeypatch):
    alpha, beta, item, gateway = _analysed(db_session, monkeypatch)
    calls = gateway.calls

    stats = pipeline._enrich_items(db_session, [item], [alpha, beta], _context(gateway, 6, reserved=0))

    assert gateway.calls == calls and stats["skipped_current"] == 1


def test_editing_one_project_redoes_only_relevance_to_that_project(db_session, monkeypatch):
    alpha, beta, item, gateway = _analysed(db_session, monkeypatch)
    before = _relevance(db_session, item)
    summary = item.llm_summary
    beta.keywords = ["audio", "speech"]

    stats = pipeline._enrich_items(db_session, [item], [alpha, beta], _context(gateway, 6, reserved=0))

    assert gateway.relevance_calls == [{"items": [item.id], "projects": ["beta"], "has_summary": True}]
    assert stats["relevance_refreshed"] == 1 and stats["ai"] == 0
    after = _relevance(db_session, item)
    assert after["beta"] == (0.55, "redone")
    assert after["alpha"] == before["alpha"]  # the other project's judgment is untouched
    assert item.llm_summary == summary
    provenance = db_session.get(models.ItemAnalysisState, item.id).provenance
    assert provenance["project_fingerprints"]["beta"] == enrichment.project_fingerprint(beta)


def test_a_new_project_only_needs_a_relevance_call(db_session, monkeypatch):
    alpha, beta, item, gateway = _analysed(db_session, monkeypatch)
    gamma = _project(db_session, "gamma", ("physics",))

    pipeline._enrich_items(db_session, [item], [alpha, beta, gamma], _context(gateway, 6, reserved=0))

    assert gateway.relevance_calls[-1]["projects"] == ["gamma"]
    assert "gamma" in _relevance(db_session, item)


def test_changed_item_content_still_gets_a_full_analysis(db_session, monkeypatch):
    alpha, beta, item, gateway = _analysed(db_session, monkeypatch)
    item.description = "now something completely different"

    stats = pipeline._enrich_items(db_session, [item], [alpha, beta], _context(gateway, 6, reserved=0))

    assert stats["ai"] == 1 and stats["relevance_refreshed"] == 0 and gateway.relevance_calls == []


def test_relevance_calls_hold_more_items_and_respect_the_budget(db_session, monkeypatch):
    monkeypatch.setattr(config, "AI_BATCH_SIZE", 5)
    monkeypatch.setattr(config, "AI_RELEVANCE_BATCH_SIZE", 4)
    alpha = _project(db_session, "alpha", ("shader",))
    items = _items(db_session, [("a shader tool", 0.5)] * 9)
    gateway = SplitGateway()
    pipeline._enrich_items(db_session, items, [alpha], _context(gateway, 6, reserved=0))  # 2 full batches, 5 + 4
    alpha.keywords = ["shader", "glsl"]
    gateway.relevance_calls.clear()

    pipeline._enrich_items(db_session, items, [alpha], _context(gateway, 2, reserved=0))

    assert [len(c["items"]) for c in gateway.relevance_calls] == [4, 4]  # budget of 2 calls, 9 items wait


def test_the_migration_converts_analyses_that_are_still_current_and_leaves_stale_ones(db_session):
    alpha = _project(db_session, "alpha")
    current, stale = _items(db_session, [("one", 0.5), ("two", 0.5)])
    db_session.add(models.ItemAnalysisState(item_id=current.id, provenance={
        "mode": "ai", "item_fingerprint": enrichment.item_fingerprint(current, [alpha])}))
    db_session.add(models.ItemAnalysisState(item_id=stale.id, provenance={
        "mode": "ai", "item_fingerprint": "written-in-english-before"}))
    db_session.flush()

    assert maintenance.split_analysis_fingerprints(db_session) == 1

    converted = db_session.get(models.ItemAnalysisState, current.id).provenance
    assert converted["content_fingerprint"] == enrichment.content_fingerprint(current)
    assert converted["project_fingerprints"] == {"alpha": enrichment.project_fingerprint(alpha)}
    assert "item_fingerprint" not in converted
    assert "content_fingerprint" not in db_session.get(models.ItemAnalysisState, stale.id).provenance


def test_relevance_work_gets_a_share_of_the_budget_even_when_new_items_are_waiting(db_session, monkeypatch):
    monkeypatch.setattr(config, "AI_BATCH_SIZE", 5)
    monkeypatch.setattr(config, "AI_RELEVANCE_BATCH_SIZE", 5)
    alpha = _project(db_session, "alpha", ("shader",))
    analysed = _items(db_session, [("a shader tool", 0.5)] * 10)
    gateway = SplitGateway()
    pipeline._enrich_items(db_session, analysed, [alpha], _context(gateway, 6, reserved=0))  # both batches analysed
    alpha.keywords = ["shader", "glsl"]  # the analysed items now need their relevance redone...
    newcomers = [models.Item(source="github", external_id=f"n/{i}", name=f"n{i}", owner="o", url="u", topics=[],
                             description="another shader thing", momentum_score=0.9, maturity_score=0.5, stars=1, raw={})
                 for i in range(30)]  # ...while plenty of brand-new items also wait for a full analysis
    db_session.add_all(newcomers)
    db_session.flush()
    gateway.relevance_calls.clear()

    stats = pipeline._enrich_items(db_session, analysed + newcomers, [alpha], _context(gateway, 5, reserved=0))

    assert stats["relevance_batches"] == 2 and stats["batches"] == 3  # 40% of 5 calls, the rest for new items
    assert len(gateway.relevance_calls) == 2
