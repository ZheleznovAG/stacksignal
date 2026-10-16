"""Correctness fixes found in an external review: prompts that know the item's kind, a run with an outcome,
a project deleted together with everything about it, and items the AI merely read not living forever."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import db as db_module, enrichment, feedback, maintenance, models, pipeline, scheduler
from app.main import app
from tests.fakes import Web, gh_repo


# ---- prompts and payloads know what kind of item they describe ----------------------------------------------

def _item(source, **fields):
    return models.Item(source=source, external_id="x", name="x", owner="o", url="u", description="d", topics=[],
                       stars=0, forks=0, likes=0, downloads=0, **fields)


@pytest.mark.parametrize("source, kind", [("github", "repository"), ("huggingface", "model"), ("arxiv", "paper"),
                                          ("npm", "package"), ("pypi", "package")])
def test_every_item_says_what_kind_it_is(source, kind):
    assert enrichment._item_payload(_item(source))["kind"] == kind


def test_no_prompt_still_claims_everything_is_a_github_repo_or_hf_model():
    for prompt in (enrichment._SYSTEM_PROMPT, enrichment._BATCH_SYSTEM_PROMPT, enrichment._RELEVANCE_SYSTEM_PROMPT):
        assert "software item" not in prompt
        assert "paper" in prompt  # the kinds are named where the items are introduced
    for word in ("paper", "package", "model", "repository"):
        assert word in enrichment._KINDS_NOTE


def test_the_relevance_only_call_also_tells_the_kind(db_session):
    from app.ai_analysis import AIContext

    class Gateway:
        namespace = "ns-kind"
        seen = None

        def profiles(self):
            return {"routing_version": "v1"}

        def generate(self, body, key):
            import json
            Gateway.seen = json.loads(body["messages"][1]["content"][0]["text"])
            ids = [i["id"] for i in Gateway.seen["items"]]
            data = {"results": [{"item_id": i, "relevance": {}} for i in ids]}
            return {"id": "r", "status": "completed", "provider": "f", "model": "f", "output": [{"type": "json", "data": data}]}

    project = models.Project(name="P", slug="p", description="d", keywords=["k"])
    item = _item("arxiv")
    db_session.add_all([project, item])
    db_session.flush()

    enrichment.score_relevance([item], [project], context=AIContext(client=Gateway(), max_calls=1))

    assert Gateway.seen["items"][0]["kind"] == "paper"


# ---- a run has an outcome ---------------------------------------------------------------------------------------

def test_a_clean_run_is_completed_and_one_with_a_failing_source_is_partial(db_session):
    web = Web()
    assert pipeline.run_collection(db_session, transport=web.transport).status == "completed"

    web.github_rate_limited = True
    partial = pipeline.run_collection(db_session, transport=web.transport)
    assert partial.status == "partial" and "github" in partial.error


def test_an_unexpected_error_after_checkpoints_is_partial_and_keeps_saved_work(db_session, monkeypatch):
    web = Web()
    web.gh_created = [gh_repo("o/saved", 300)]

    def boom(*args, **kwargs):
        raise RuntimeError("the pipeline broke")

    monkeypatch.setattr(pipeline, "synthesize_opportunities", boom)
    db_session.add(models.Project(name="P", slug="p", description="d", keywords=["k"]))
    db_session.commit()

    with pytest.raises(RuntimeError):
        pipeline.run_collection(db_session, transport=web.transport)

    (run,) = db_session.query(models.CollectionRun).all()
    assert run.status == "partial" and "the pipeline broke" in run.error and run.finished_at is not None
    assert run.items_found == 1 and run.items_new == 1
    assert db_session.query(models.Item).filter_by(external_id="o/saved").one().last_enriched_at is not None


@pytest.mark.parametrize("status, sent", [("completed", True), ("partial", True), ("failed", False)])
def test_the_daily_digest_follows_only_a_run_that_finished(monkeypatch, status, sent):
    calls = []
    monkeypatch.setattr(scheduler, "SessionLocal", sessionmaker(bind=create_engine("sqlite://")))
    monkeypatch.setattr(scheduler, "run_collection", lambda db: SimpleNamespace(status=status))
    monkeypatch.setattr(scheduler, "_send_digest", lambda: calls.append("digest"))

    scheduler._job()

    assert calls == (["digest"] if sent else [])


def test_no_digest_after_a_crash_or_a_skipped_run(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "SessionLocal", sessionmaker(bind=create_engine("sqlite://")))
    monkeypatch.setattr(scheduler, "_send_digest", lambda: calls.append("digest"))

    def crash(db):
        raise RuntimeError("boom")

    def busy(db):
        raise pipeline.CollectionBusy("busy")

    monkeypatch.setattr(scheduler, "run_collection", crash)
    scheduler._job()
    monkeypatch.setattr(scheduler, "run_collection", busy)
    scheduler._job()

    assert calls == []


# ---- deleting a project leaves nothing of it behind ----------------------------------------------------------------

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
    yield TestClient(app), Session
    app.dependency_overrides.clear()


def _seed_project_traces(session):
    keep = models.Project(name="Keep", slug="keep", description="d", keywords=["k"])
    gone = models.Project(name="Gone", slug="gone", description="d", keywords=["k"])
    session.add_all([keep, gone])
    item = _item("github", status=models.Status.NEW)
    session.add(item)
    session.flush()
    for project in (keep, gone):
        session.add(models.ItemFeedback(item_id=item.id, kind="vote", project_slug=project.slug, verdict=1))
        session.add(models.Opportunity(title=f"about {project.slug}", body="b", project_slug=project.slug, item_ids=[item.id]))
        session.add(models.ItemDiscovery(item_id=item.id, project_slug=project.slug, query_text="q"))
        session.add(models.StatusHistory(item_id=item.id, project_id=project.id, status=models.Status.WATCH, note="goal: x"))
    session.add(models.ItemFeedback(item_id=item.id, kind="note", note="a note about the item itself"))
    session.commit()
    return gone.id


def test_deleting_a_project_removes_its_votes_opportunities_discoveries_and_history(api):
    client, Session = api
    gone_id = _seed_project_traces(Session())

    assert client.delete(f"/api/projects/{gone_id}").json() == {"ok": True}

    session = Session()
    assert [f.project_slug for f in session.query(models.ItemFeedback).filter_by(kind="vote")] == ["keep"]
    assert [o.project_slug for o in session.query(models.Opportunity)] == ["keep"]
    assert [d.project_slug for d in session.query(models.ItemDiscovery)] == ["keep"]
    assert session.query(models.StatusHistory).filter(models.StatusHistory.project_id == gone_id).count() == 0
    assert session.query(models.ItemFeedback).filter_by(kind="note").count() == 1  # a note is about the item, it stays


def test_the_migration_cleans_up_what_earlier_deletions_left(db_session):
    db_session.add(models.Project(name="Keep", slug="keep", description="d", keywords=["k"]))
    item = _item("github")
    db_session.add(item)
    db_session.commit()
    # These rows are what deletions left behind before foreign keys were enforced; enforcement
    # would refuse to create them now, so it is off while they are planted (first statement of a
    # new transaction, where SQLite still accepts the pragma).
    db_session.execute(text("PRAGMA foreign_keys=OFF"))
    db_session.add_all([
        models.ItemFeedback(item_id=item.id, kind="vote", project_slug="ghost", verdict=-1),
        models.ItemFeedback(item_id=item.id, kind="vote", project_slug="keep", verdict=1),
        models.ItemFeedback(item_id=item.id, kind="note", note="stays"),
        models.Opportunity(title="ghost", body="b", project_slug="ghost", item_ids=[]),
        models.ItemDiscovery(item_id=item.id, project_slug="ghost", query_text="q"),
        models.StatusHistory(item_id=item.id, project_id=999, status=models.Status.WATCH, note="orphan"),
        models.StatusHistory(item_id=item.id, project_id=None, status=models.Status.WATCH, note="global")])
    db_session.flush()

    assert maintenance.purge_orphans(db_session) == 4
    assert maintenance.purge_orphans(db_session) == 0


def test_a_vote_about_a_deleted_project_no_longer_teaches_the_ai(db_session):
    db_session.add(models.Project(name="Keep", slug="keep", description="d", keywords=["k"]))
    item = _item("github")
    db_session.add(item)
    db_session.flush()
    db_session.add_all([models.ItemFeedback(item_id=item.id, kind="vote", project_slug="ghost", verdict=-1),
                        models.ItemFeedback(item_id=item.id, kind="vote", project_slug="keep", verdict=1)])
    db_session.flush()

    assert [e["project"] for e in feedback.examples(db_session)] == ["keep"]
