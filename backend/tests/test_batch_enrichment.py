"""Batched AI enrichment: several items per gateway call, candidates ranked by
relevance x momentum, current analyses skipped, and failures contained. A FakeGateway
stands in for the HTTP client, so nothing here touches the network."""

import json

from sqlalchemy import event

from app import config, models, pipeline
from app.ai_analysis import AIContext
from app.ai_client import AIError


class FakeGateway:
    """Answers a batch request for every item id it receives, unless told otherwise."""

    def __init__(self, script=None, namespace="ns-batch"):
        self.namespace = namespace
        self.script = list(script or [])  # per-call: None = normal answer, Exception, or callable(ids)->results
        self.calls = 0
        self.batches = []

    def profiles(self):
        return {"routing_version": "v1"}

    def generate(self, body, key):
        self.calls += 1
        payload = json.loads(body["messages"][1]["content"][0]["text"])
        ids = [item["id"] for item in payload["items"]]
        slug = payload["projects"][0]["slug"]
        self.batches.append(ids)
        step = self.script.pop(0) if self.script else None
        if isinstance(step, Exception):
            raise step
        if callable(step):
            results = step(ids)
        else:
            results = [self.answer(i, slug) for i in ids]
        return {"id": f"resp_{self.calls}", "status": "completed", "provider": "fake", "model": "fake",
                "output": [{"type": "json", "data": {"results": results}}]}

    @staticmethod
    def answer(item_id, slug):
        return {"item_id": item_id, "summary": f"AI summary {item_id}", "why_interesting": "",
                "capabilities": ["shader tooling"],
                "relevance": {slug: {"score": 0.8, "rationale": "fits"}}}


def _project(db, slug="alpha", keywords=("shader",)):
    project = models.Project(name=slug, slug=slug, keywords=list(keywords), description="d")
    db.add(project)
    db.flush()
    return project


def _items(db, specs):
    items = []
    for index, (description, momentum) in enumerate(specs):
        item = models.Item(source="github", external_id=f"o/r{index}", name=f"r{index}", owner="o",
                           url="https://example.com", description=description, topics=[],
                           stars=10, momentum_score=momentum, maturity_score=0.5, raw={})
        db.add(item)
        items.append(item)
    db.flush()
    return items


def _mode(db, item):
    state = db.get(models.ItemAnalysisState, item.id)
    return state.provenance["mode"]


def _context(gateway, max_calls, reserved=1):
    context = AIContext(client=gateway, max_calls=max_calls)
    context.reserved = reserved
    return context


def test_batches_respect_batch_size_and_reserved_budget(db_session, monkeypatch):
    monkeypatch.setattr(config, "AI_BATCH_SIZE", 2)
    projects = [_project(db_session)]
    items = _items(db_session, [("plain thing", 0.5)] * 5)
    gateway = FakeGateway()

    # 3 calls allowed, 1 reserved for opportunities -> 2 batches of 2 -> 4 items analysed.
    stats = pipeline._enrich_items(db_session, items, projects, _context(gateway, 3))

    assert gateway.calls == 2
    assert stats["batches"] == 2 and stats["ai"] == 4 and stats["heuristic"] == 5
    assert sorted(_mode(db_session, i) for i in items) == ["ai"] * 4 + ["heuristic"]


def test_candidates_are_ranked_by_relevance_times_momentum_not_momentum_alone(db_session, monkeypatch):
    monkeypatch.setattr(config, "AI_BATCH_SIZE", 1)
    projects = [_project(db_session, keywords=("shader",))]
    hot_but_irrelevant, relevant = _items(db_session, [("unrelated web framework", 0.9),
                                                       ("a shader compiler", 0.5)])
    gateway = FakeGateway()

    pipeline._enrich_items(db_session, [hot_but_irrelevant, relevant], projects, _context(gateway, 2))

    assert gateway.batches == [[relevant.id]]  # the relevant item wins the single slot
    assert _mode(db_session, relevant) == "ai"
    assert _mode(db_session, hot_but_irrelevant) == "heuristic"


def test_heuristic_pass_reuses_projects_and_relevance_rows(db_session):
    projects = [_project(db_session)]
    items = _items(db_session, [("a shader tool", 0.5)] * 201)
    counts = {"projects": 0, "item_project_relevance": 0}

    def count_queries(conn, cursor, statement, parameters, context, executemany):
        for table in counts:
            if statement.startswith("SELECT") and f"FROM {table}" in statement.replace("\n", " "):
                counts[table] += 1

    engine = db_session.get_bind()
    event.listen(engine, "before_cursor_execute", count_queries)
    try:
        pipeline._enrich_items(db_session, items, projects, AIContext(max_calls=0))
    finally:
        event.remove(engine, "before_cursor_execute", count_queries)
    assert counts["projects"] == 0, counts
    # One load to replace relevance, one for status suggestions; votes reuse the loaded rows.
    assert counts["item_project_relevance"] <= 2 * len(items), counts


def test_ai_result_replaces_heuristic_and_records_fingerprint(db_session):
    projects = [_project(db_session)]
    (item,) = _items(db_session, [("a shader tool", 0.5)])

    pipeline._enrich_items(db_session, [item], projects, _context(FakeGateway(), 3))

    assert item.llm_summary == f"AI summary {item.id}"
    assert [c.capability.name for c in item.capability_links] == ["shader tooling"]
    assert db_session.get(models.ItemAnalysisState, item.id).provenance["content_fingerprint"]
    assert item.relevances[0].rationale == "fits"


def test_items_with_a_current_ai_analysis_are_not_sent_again(db_session):
    projects = [_project(db_session)]
    items = _items(db_session, [("a shader tool", 0.5), ("another shader tool", 0.4)])
    gateway = FakeGateway()

    pipeline._enrich_items(db_session, items, projects, _context(gateway, 3))
    assert gateway.calls == 1
    stats = pipeline._enrich_items(db_session, items, projects, _context(gateway, 3))

    assert gateway.calls == 1  # nothing new to analyse
    assert stats["skipped_current"] == 2 and stats["ai"] == 0 and stats["heuristic"] == 0
    assert items[0].llm_summary.startswith("AI summary")  # AI text not downgraded


def test_a_changed_description_triggers_reanalysis(db_session):
    projects = [_project(db_session)]
    (item,) = _items(db_session, [("a shader tool", 0.5)])
    gateway = FakeGateway()

    pipeline._enrich_items(db_session, [item], projects, _context(gateway, 3))
    item.description = "a shader tool, now with a new backend"
    stats = pipeline._enrich_items(db_session, [item], projects, _context(gateway, 3))

    assert gateway.calls == 2 and stats["ai"] == 1


def test_items_the_model_skipped_keep_their_heuristic_result(db_session):
    projects = [_project(db_session)]
    first, second = _items(db_session, [("a shader tool", 0.5), ("a shader library", 0.4)])
    only_first = lambda ids: [FakeGateway.answer(ids[0], "alpha")]  # noqa: E731

    stats = pipeline._enrich_items(db_session, [first, second], projects,
                                   _context(FakeGateway([only_first]), 3))

    assert stats["ai"] == 1
    assert {_mode(db_session, first), _mode(db_session, second)} == {"ai", "heuristic"}


def test_one_bad_batch_does_not_stop_the_next(db_session, monkeypatch):
    monkeypatch.setattr(config, "AI_BATCH_SIZE", 1)
    projects = [_project(db_session)]
    items = _items(db_session, [("shader a", 0.9), ("shader b", 0.5)])
    bad = lambda ids: [FakeGateway.answer(999, "alpha")]  # noqa: E731  unknown item_id -> rejected

    stats = pipeline._enrich_items(db_session, items, projects, _context(FakeGateway([bad]), 3))

    assert stats["batch_errors"] == 1 and stats["batches"] == 1 and stats["ai"] == 1


def test_transport_failure_stops_further_batches(db_session, monkeypatch):
    monkeypatch.setattr(config, "AI_BATCH_SIZE", 1)
    projects = [_project(db_session)]
    items = _items(db_session, [("shader a", 0.9), ("shader b", 0.5), ("shader c", 0.4)])
    gateway = FakeGateway([AIError("transport_error", indeterminate=True)])

    stats = pipeline._enrich_items(db_session, items, projects, _context(gateway, 5))

    assert gateway.calls == 1
    assert stats["ai"] == 0 and stats["heuristic"] == 3
    assert all(_mode(db_session, item) == "heuristic" for item in items)


def test_no_client_means_pure_heuristic_run(db_session):
    projects = [_project(db_session)]
    items = _items(db_session, [("shader a", 0.5), ("shader b", 0.4)])
    context = AIContext(client=None, max_calls=5)

    stats = pipeline._enrich_items(db_session, items, projects, context)

    assert stats["heuristic"] == 2 and stats["batches"] == 0
