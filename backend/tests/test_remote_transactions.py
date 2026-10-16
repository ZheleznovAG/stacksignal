"""A second SQLite connection must be able to commit inside every remote call.

File-backed databases are essential: an in-memory StaticPool can hide writer locks.
Both journaling modes and both autoflush settings exercise the actual boundaries.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import config, discovery, feedback, models, pipeline, queries, release_watch, stack_watch
from app.ai_analysis import AIContext, analyze, messages_for
from app.ai_client import AIError
from app.db import Base
from tests.test_batch_enrichment import FakeGateway, _items, _project


@pytest.fixture(params=[("WAL", False), ("DELETE", True)])
def sessions(tmp_path, request):
    journal, autoflush = request.param
    engine = create_engine(f"sqlite:///{tmp_path / 'remote.db'}", connect_args={"timeout": 0.05})
    with engine.connect() as conn:
        conn.exec_driver_sql(f"PRAGMA journal_mode={journal}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=autoflush)
    try:
        yield factory
    finally:
        engine.dispose()


def _write(factory, label):
    with factory() as writer:
        writer.add(models.Project(name=label, slug=label))
        writer.commit()


@pytest.mark.parametrize("outcome", ["completed", "failed", "indeterminate"])
def test_gateway_requests_allow_writes_and_outcomes_survive_caller_rollback(sessions, outcome):
    class Gateway:
        namespace = "transactions"

        def profiles(self):
            assert not db.in_transaction()
            _write(sessions, "during-profiles")
            return {"routing_version": "v1"}

        def generate(self, body, key):
            assert not db.in_transaction()
            _write(sessions, "during-generate")
            with sessions() as reader:
                assert reader.query(models.AIAnalysis).count() == 0  # no pre-network pending write
            if outcome == "indeterminate":
                raise AIError("client_timeout", indeterminate=True)
            return {"id": "response", "output": [{"type": "text", "text": "ok" if outcome == "completed" else ""}]}

    with sessions() as db:
        kwargs = dict(context=AIContext(client=Gateway()), task="test", profile="default",
                      prompt_version="v1", messages=messages_for("test", {}))
        if outcome == "completed":
            assert analyze(db, **kwargs).data == "ok"
        else:
            with pytest.raises(AIError):
                analyze(db, **kwargs)
        db.rollback()
    with sessions() as reader:
        assert reader.query(models.AIAnalysis).one().status == outcome
        assert reader.query(models.Project).count() == 2


def test_ai_refuses_to_commit_or_discard_caller_writes(sessions):
    with sessions() as db:
        db.add(models.Project(name="unfinished", slug="unfinished"))
        db.flush()
        with pytest.raises(RuntimeError, match="Checkpoint"):
            analyze(db, context=AIContext(client=FakeGateway()), task="test", profile="default",
                    prompt_version="v1", messages=[])
        db.rollback()
    with sessions() as reader:
        assert reader.query(models.Project).count() == 0


def test_crash_before_cache_persistence_reuses_the_same_request_key(sessions):
    keys = []

    class Crash(BaseException):
        pass

    class Gateway:
        namespace = "crash"

        def profiles(self):
            return {"routing_version": "v1"}

        def generate(self, body, key):
            keys.append(key)
            if len(keys) == 1:
                raise Crash()
            return {"output": [{"type": "text", "text": "replayed"}]}

    with sessions() as db:
        kwargs = dict(task="test", profile="default", prompt_version="v1", messages=[])
        with pytest.raises(Crash):
            analyze(db, context=AIContext(client=Gateway()), **kwargs)
        assert analyze(db, context=AIContext(client=Gateway()), **kwargs).data == "replayed"
    assert len(keys) == 2 and keys[0] == keys[1]


def test_each_enrichment_batch_releases_writes_and_keeps_user_actions(sessions, monkeypatch):
    monkeypatch.setattr(config, "AI_BATCH_SIZE", 1)
    monkeypatch.setattr(config, "AI_RELEVANCE_BATCH_SIZE", 1)
    with sessions() as db:
        project = _project(db)
        items = _items(db, [("shader first", 0.9), ("shader second", 0.5)])
        ids = [i.id for i in items]
        project_id = project.id

        class Gateway(FakeGateway):
            def profiles(self):
                _write(sessions, "enrichment-profiles")
                with sessions() as reader:
                    assert reader.query(models.ItemAnalysisState).count() == 2
                return super().profiles()

            def generate(self, body, key):
                payload = json.loads(body["messages"][1]["content"][0]["text"])
                item_id = payload["items"][0]["id"]
                with sessions() as writer:
                    item = writer.get(models.Item, item_id)
                    item.status = models.Status.IGNORE
                    feedback.record_vote(writer, item, "alpha", -1)
                    feedback.record_note(writer, item, "written during AI")
                    decision = writer.query(models.ItemProjectDecision).filter_by(
                        item_id=item_id, project_id=project_id).first()
                    if decision is None:
                        writer.add(models.ItemProjectDecision(item_id=item_id, project_id=project_id,
                                                             status=models.Status.IGNORE, goal="user decision"))
                    writer.commit()
                response = super().generate(body, key)
                if body["metadata"]["task"] == "relevance_batch":
                    response["output"][0]["data"]["results"] = [
                        {"item_id": entry["item_id"], "relevance": entry["relevance"]}
                        for entry in response["output"][0]["data"]["results"]]
                return response

        gateway = Gateway()
        context = AIContext(client=gateway, max_calls=4)
        assert pipeline._enrich_items(db, items, [project], context)["ai"] == 2
        project.description = "edited profile"
        # The same invariant must hold between relevance-only batches, too.
        assert pipeline._enrich_items(db, items, [project], context)["relevance_refreshed"] == 2
        db.commit()
    with sessions() as reader:
        for item_id in ids:
            item = reader.get(models.Item, item_id)
            assert item.status == models.Status.IGNORE
            assert item.relevances[0].score == feedback.SUPPRESS_SCORE
            assert len(item.decisions) == 1 and item.decisions[0].goal == "user decision"
            assert len(feedback.notes(item)) == 2


def test_remote_discovery_releases_writes_between_all_requests(sessions, monkeypatch):
    calls = []

    def remote(label, result):
        calls.append(label)
        _write(sessions, f"remote-{len(calls)}")
        return result

    with sessions() as db:
        project = _project(db)
        project.stack_packages = [{"ecosystem": "pypi", "name": "one"}, {"ecosystem": "npm", "name": "two"}]
        items = _items(db, [("shader one", 0.9), ("shader two", 0.5)])
        for item in items:
            item.status = models.Status.TRIAL
            db.add(models.StatusHistory(item_id=item.id, status=models.Status.TRIAL, note="Chosen by user"))
        for text in ("one", "two"):
            db.add(models.SearchQuery(project_slug=project.slug, source="github", text=text))
        db.flush()
        monkeypatch.setattr(discovery, "_search", lambda q, t: remote("search", []))
        _, stats = discovery.run_queries(db, github_limit=2, hf_limit=0, arxiv_limit=0, npm_limit=0)
        assert stats["queries"] == 2 and stats["errors"] == 0
        monkeypatch.setattr(discovery, "_get_one", lambda s, e, t: remote("refresh", {}))
        _, stats = discovery.refresh_tracked(db, exclude=set(), github_limit=2)
        assert stats["refreshed"] == 2 and stats["errors"] == 0
        monkeypatch.setattr(stack_watch, "_fetch", lambda e, n, t: remote("registry", {"raw": {"version": "1.2.3"}}))
        assert stack_watch.check(db)["checked"] == 2
        monkeypatch.setattr(release_watch.github, "get_releases", lambda e, transport: remote("release", [{
            "tag": "v1.0", "name": "release", "url": "https://example.com", "published_at": None,
            "prerelease": False, "notes": "release notes"}]))
        assert release_watch.check(db)["new_releases"] == 2
        db.commit()
    assert calls == ["search"] * 2 + ["refresh"] * 2 + ["registry"] * 2 + ["release"] * 2


def test_ai_replacement_restores_heuristic_query_and_keeps_request_fingerprint(sessions):
    with sessions() as db:
        project = _project(db, keywords=())
        project.needs = ["persistent outbox"]
        queries.ensure_default_queries(db, [project])
        row = db.query(models.SearchQuery).filter_by(source="github").one()
        row.origin = "ai"
        fingerprint = queries.query_profile_fingerprint(project)
        project_id = project.id

        class Gateway:
            namespace = "queries"

            def profiles(self):
                _write(sessions, "query-profiles")
                return {"routing_version": "v1"}

            def generate(self, body, key):
                with sessions() as writer:
                    writer.get(models.Project, project_id).description = "edited during generation"
                    writer.commit()
                return {"output": [{"type": "json", "data": {"projects": [{"project_slug": "alpha",
                    "github": ["transactional outbox"], "huggingface": [], "arxiv": []}]}}]}

        queries.regenerate(db, [project], context=AIContext(client=Gateway()))
        rows = {q.text: q for q in db.query(models.SearchQuery).filter_by(source="github")}
        assert rows["persistent outbox"].origin == "auto"
        assert rows["transactional outbox"].origin == "ai"
        assert rows["transactional outbox"].profile_fingerprint == fingerprint
        assert queries.is_stale(rows["transactional outbox"], queries.query_profile_fingerprint(project))
