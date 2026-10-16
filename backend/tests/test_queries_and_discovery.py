"""Search queries per project (heuristic + AI), and discovery: running them within a budget,
refreshing the items that matter, and archiving what went stale. No network: tests.fakes.Web."""

import datetime
import json

import pytest

from app import discovery, feedback, models, pipeline, queries
from app.ai_analysis import AIContext
from app.ai_client import AIError
from tests.fakes import Web, gh_repo, hf_model
from app.utils import utcnow

NOW = utcnow()


def _project(db, slug, keywords, name=None):
    project = models.Project(name=name or slug, slug=slug, keywords=list(keywords), description="d")
    db.add(project)
    db.flush()
    return project


def _texts(db, slug, source):
    return [q.text for q in db.query(models.SearchQuery).filter_by(project_slug=slug, source=source)
            .order_by(models.SearchQuery.id)]


class Gateway:
    def __init__(self, groups=None, fail=None):
        self.namespace = "ns-q"
        self.groups = groups or []
        self.fail = fail
        self.calls = 0

    def profiles(self):
        return {"routing_version": "v1"}

    def generate(self, body, key):
        self.calls += 1
        self.body = body
        if self.fail:
            raise self.fail
        return {"id": "resp_q", "status": "completed", "provider": "fake", "model": "fake",
                "output": [{"type": "json", "data": {"projects": self.groups}}]}


# ---- heuristic queries -------------------------------------------------------------------

def test_heuristic_prefers_specific_phrases_and_skips_generic_ones(db_session):
    softbody = _project(db_session, "softbody", ["Unity", "C#", "soft-body physics", "position based dynamics", "XPBD",
                                           "performance optimization", "GPU simulation"])
    learning = _project(db_session, "learning", ["Unity", "C#", "architecture", "performance", "dependency injection"])

    found = queries.heuristic_queries(softbody, [softbody, learning])

    assert "position based dynamics" in found["github"] and "soft-body physics" in found["github"]
    assert "XPBD" in found["github"]  # a distinctive lone word (unique to this project) is fine
    assert not any(q in found["github"] for q in ("Unity", "C#", "performance optimization"))
    assert all(len(q.split()) <= 2 for q in found["huggingface"])  # short phrases suit Hugging Face search


def test_heuristic_drops_near_duplicate_phrases(db_session):
    project = _project(db_session, "p", ["position based dynamics", "based dynamics", "position based fluids"])

    found = queries.heuristic_queries(project, [project])["github"]

    assert "based dynamics" not in found and "position based dynamics" in found


def test_heuristic_respects_per_project_limits(db_session):
    project = _project(db_session, "p", [f"unique topic {i}" for i in range(20)])

    found = queries.heuristic_queries(project, [project])

    assert len(found["github"]) == queries.GITHUB_PER_PROJECT
    assert len(found["huggingface"]) <= queries.HF_PER_PROJECT


def test_project_without_specific_keywords_gets_no_queries(db_session):
    project = _project(db_session, "p", ["performance", "testing", "architecture"])
    assert all(texts == [] for texts in queries.heuristic_queries(project, [project]).values())


def test_need_has_priority_over_generic_keyword(db_session, monkeypatch):
    project = _project(db_session, "p", ["performance", "shader compiler", "game rendering"])
    project.needs = ["persistent outbox"]
    project.stack = ["FastAPI", "SQLite", "Playwright"]
    monkeypatch.setattr(queries, "GITHUB_PER_PROJECT", 1)

    assert queries.heuristic_queries(project, [project])["github"] == ["persistent outbox"]


def test_russian_needs_are_kept_in_heuristic_discovery(db_session):
    project = _project(db_session, "p", [])
    project.needs = ["распознавание рукописного текста"]
    assert queries.heuristic_queries(project, [project])["github"] == project.needs


# ---- storing -----------------------------------------------------------------------------

def test_store_cleans_and_deduplicates_case_insensitively(db_session):
    _project(db_session, "softbody", [])

    added = queries.store(db_session, "softbody", "github", ['"soft   body"', "SOFT BODY", "x", "  "], "auto")

    assert added == 1 and _texts(db_session, "softbody", "github") == ["soft body"]
    assert queries.store(db_session, "softbody", "github", ["Soft Body"], "user") == 0
    assert queries.store(db_session, "softbody", "huggingface", ["soft body"], "user") == 1  # per source


def test_store_caps_the_length_of_a_query(db_session):
    _project(db_session, "softbody", [])
    queries.store(db_session, "softbody", "github", ["word " * 40], "auto")
    assert len(_texts(db_session, "softbody", "github")[0]) <= queries.MAX_QUERY_LENGTH


def test_default_queries_are_added_once_and_only_where_missing(db_session):
    softbody = _project(db_session, "softbody", ["soft-body physics", "position based dynamics"])
    pixel = _project(db_session, "pixel", ["sprite sheet", "palette reduction"])
    queries.store(db_session, "pixel", "github", ["my own query"], "user")

    added = queries.ensure_default_queries(db_session, [softbody, pixel])

    assert added > 0 and _texts(db_session, "pixel", "github") == ["my own query"]  # untouched
    assert _texts(db_session, "softbody", "github")
    assert queries.ensure_default_queries(db_session, [softbody, pixel]) == 0  # idempotent


# ---- AI generation and regeneration ------------------------------------------------------

def test_ai_generation_stores_queries_as_origin_ai(db_session):
    softbody = _project(db_session, "softbody", ["soft-body physics"])
    gateway = Gateway([{"project_slug": "softbody", "github": ["soft body simulation", "xpbd unity"],
                        "huggingface": ["physics"]}])

    added = queries.generate_with_ai(db_session, [softbody], context=AIContext(client=gateway, max_calls=1))

    assert added == 3
    stored = db_session.query(models.SearchQuery).filter_by(origin="ai").all()
    assert {(q.source, q.text) for q in stored} == {("github", "soft body simulation"), ("github", "xpbd unity"),
                                                    ("huggingface", "physics")}


def test_query_generation_receives_stack_and_needs(db_session):
    project = _project(db_session, "p", ["flashcards"])
    project.needs = ["распознавание рукописного текста"]
    project.stack = ["SQLite", "FastAPI"]
    gateway = Gateway([{"project_slug": "p", "github": ["handwriting OCR"], "huggingface": []}])

    queries.generate_with_ai(db_session, [project], context=AIContext(client=gateway, max_calls=1))

    payload = json.loads(gateway.body["messages"][1]["content"][0]["text"])
    assert payload["projects"][0]["needs"] == project.needs
    assert payload["projects"][0]["stack"] == ["FastAPI", "SQLite"]
    assert gateway.body["metadata"]["prompt_version"] == "query_generation_v3"
    assert set(_texts(db_session, "p", "github")) == {"handwriting OCR", "flashcards", project.needs[0]}


def test_ai_generation_rejects_an_unknown_project_and_stores_nothing(db_session):
    softbody = _project(db_session, "softbody", [])
    gateway = Gateway([{"project_slug": "invented", "github": ["x y"], "huggingface": []}])

    with pytest.raises(AIError):
        queries.generate_with_ai(db_session, [softbody], context=AIContext(client=gateway, max_calls=1))

    assert db_session.query(models.SearchQuery).count() == 0


def test_regenerate_keeps_the_users_queries_and_replaces_generated_ones(db_session):
    softbody = _project(db_session, "softbody", ["soft-body physics", "position based dynamics"])
    queries.store(db_session, "softbody", "github", ["stale generated"], "auto")
    queries.store(db_session, "softbody", "github", ["my own"], "user")

    result = queries.regenerate(db_session, [softbody])

    assert result["ai"] == 0 and result["ai_error"] == ""
    texts = _texts(db_session, "softbody", "github")
    assert "my own" in texts and "stale generated" not in texts and "position based dynamics" in texts


def test_regenerate_survives_an_ai_failure_and_reports_it(db_session):
    softbody = _project(db_session, "softbody", ["soft-body physics"])
    context = AIContext(client=Gateway(fail=AIError("transport_error", indeterminate=True)), max_calls=1)

    result = queries.regenerate(db_session, [softbody], context=context)

    assert result["ai_error"] == "transport_error" and result["heuristic"] > 0
    assert _texts(db_session, "softbody", "github")  # the heuristic queries are still there


@pytest.mark.parametrize("field,value", [("description", "changed purpose"), ("keywords", ["new phrase"]),
                                        ("needs", ["persistent outbox"]), ("stack", ["SQLite"])])
def test_profile_change_makes_generated_queries_stale(db_session, field, value):
    project = _project(db_session, "p", ["soft-body physics"])
    queries.ensure_default_queries(db_session, [project])
    generated = db_session.query(models.SearchQuery).filter_by(origin="auto").first()
    before = queries.query_profile_fingerprint(project)
    assert generated.profile_fingerprint == before
    setattr(project, field, value)
    assert queries.is_stale(generated, queries.query_profile_fingerprint(project))


def test_need_changes_refresh_heuristics_and_record_their_basis(db_session):
    project = _project(db_session, "p", ["old keyword"])
    queries.ensure_default_queries(db_session, [project])
    project.keywords = []
    project.needs = ["persistent outbox"]
    queries.ensure_default_queries(db_session, [project])
    rows = db_session.query(models.SearchQuery).all()
    assert rows and {q.text for q in rows} == {"persistent outbox"}
    assert all(q.basis == ["need:persistent outbox"] for q in rows)
    assert all(not queries.is_stale(q, queries.query_profile_fingerprint(project)) for q in rows)
    assert queries.ensure_default_queries(db_session, [project]) == 0


def test_collection_regenerates_stale_ai_queries_but_leaves_current_ones(db_session):
    project = _project(db_session, "p", [])
    gateway = Gateway([{"project_slug": "p", "github": ["old query"], "huggingface": []}])
    pipeline._generate_ai_queries(db_session, [project], AIContext(client=gateway, max_calls=1))
    assert gateway.calls == 1
    pipeline._generate_ai_queries(db_session, [project], AIContext(client=gateway, max_calls=1))
    assert gateway.calls == 1
    project.needs = ["persistent outbox"]
    gateway.groups = [{"project_slug": "p", "github": ["transactional outbox"], "huggingface": []}]
    pipeline._generate_ai_queries(db_session, [project], AIContext(client=gateway, max_calls=1))
    assert gateway.calls == 2
    assert set(_texts(db_session, "p", "github")) == {"transactional outbox", "persistent outbox"}


def test_failed_ai_regeneration_keeps_stale_queries_usable(db_session):
    project = _project(db_session, "p", ["old keyword"])
    gateway = Gateway([{"project_slug": "p", "github": ["old ai search"], "huggingface": []}])
    queries.generate_with_ai(db_session, [project], context=AIContext(client=gateway, max_calls=1))
    old = db_session.query(models.SearchQuery).filter_by(origin="ai").one()
    old_id, fingerprint = old.id, old.profile_fingerprint
    project.needs = ["persistent outbox"]
    gateway.fail = AIError("transport_error")
    result = queries.regenerate(db_session, [project], context=AIContext(client=gateway, max_calls=1))
    assert result["ai_error"] == "transport_error"
    assert db_session.get(models.SearchQuery, old_id).profile_fingerprint == fingerprint
    assert old in discovery.due_queries(db_session, github_limit=20, hf_limit=0)
    assert queries.is_stale(old, queries.query_profile_fingerprint(project))
    assert "persistent outbox" in _texts(db_session, "p", "github")


@pytest.mark.parametrize("groups", [[], [{"project_slug": "p", "github": [], "huggingface": []}],
    [{"project_slug": "p", "github": ["replacement"], "huggingface": []}] * 2])
def test_incomplete_or_empty_ai_responses_never_delete_previous_queries(db_session, groups):
    project = _project(db_session, "p", [])
    queries.store(db_session, "p", "github", ["old ai search"], "ai")
    with pytest.raises(AIError):
        queries.generate_with_ai(db_session, [project], context=AIContext(client=Gateway(groups), max_calls=1))
    assert _texts(db_session, "p", "github") == ["old ai search"]


def test_regeneration_preserves_user_queries_toggles_and_surviving_history(db_session):
    project = _project(db_session, "p", ["soft-body physics"])
    queries.store(db_session, "p", "github", ["xpbd unity"], "user")
    queries.store(db_session, "p", "github", ["keep this", "old ai search"], "ai")
    user = db_session.query(models.SearchQuery).filter_by(origin="user").one()
    kept = db_session.query(models.SearchQuery).filter_by(text="keep this").one()
    user.enabled = kept.enabled = False
    user.runs = kept.runs = 7
    user_id, kept_id = user.id, kept.id
    project.needs = ["new need"]
    gateway = Gateway([{"project_slug": "p", "github": ["XPBD UNITY", "keep this", "new ai search"], "huggingface": []}])

    queries.regenerate(db_session, [project], context=AIContext(client=gateway, max_calls=1))

    assert db_session.get(models.SearchQuery, user_id) is user
    assert user.origin == "user" and user.text == "xpbd unity" and not user.enabled and user.runs == 7
    assert not queries.is_stale(user, queries.query_profile_fingerprint(project))
    assert db_session.get(models.SearchQuery, kept_id) is kept and not kept.enabled and kept.runs == 7
    assert not queries.is_stale(kept, queries.query_profile_fingerprint(project))
    assert "old ai search" not in _texts(db_session, "p", "github")


def test_ai_replacement_is_owned_by_the_callers_transaction(db_session):
    project = _project(db_session, "p", [])
    queries.store(db_session, "p", "github", ["old ai search"], "ai")
    db_session.commit()
    gateway = Gateway([{"project_slug": "p", "github": ["new ai search"], "huggingface": []}])
    queries.generate_with_ai(db_session, [project], context=AIContext(client=gateway, max_calls=1))
    db_session.rollback()
    assert _texts(db_session, "p", "github") == ["old ai search"]


def test_an_older_cached_empty_response_cannot_delete_saved_searches(db_session):
    project = _project(db_session, "p", [])
    gateway = Gateway([{"project_slug": "p", "github": ["saved search"], "huggingface": []}])
    queries.generate_with_ai(db_session, [project], context=AIContext(client=gateway, max_calls=1))
    record = db_session.query(models.AIAnalysis).one()
    record.result = {"projects": [{"project_slug": "p", "github": [], "huggingface": [], "arxiv": []}]}
    db_session.flush()
    with pytest.raises(AIError, match="invalid_structured_output"):
        queries.generate_with_ai(db_session, [project], context=AIContext(client=gateway, max_calls=1))
    assert _texts(db_session, "p", "github") == ["saved search"]
    assert gateway.calls == 1


# ---- running queries ---------------------------------------------------------------------

def _query(db, slug, source, text, *, last_run=None, enabled=True):
    q = models.SearchQuery(project_slug=slug, source=source, text=text, last_run_at=last_run, enabled=enabled)
    db.add(q)
    db.flush()
    return q


def test_due_queries_go_never_run_first_then_oldest_within_limits(db_session):
    _project(db_session, "softbody", [])
    fresh = _query(db_session, "softbody", "github", "fresh", last_run=NOW - datetime.timedelta(hours=1))
    old = _query(db_session, "softbody", "github", "old", last_run=NOW - datetime.timedelta(days=3))
    never = _query(db_session, "softbody", "github", "never")
    _query(db_session, "softbody", "github", "disabled", enabled=False)
    hf = _query(db_session, "softbody", "huggingface", "hf")

    due = discovery.due_queries(db_session, github_limit=2, hf_limit=5)

    assert [q.text for q in due if q.source == "github"] == ["never", "old"]
    assert [q.text for q in due if q.source == "huggingface"] == ["hf"]
    assert fresh not in due and never in due and old in due and hf in due


def test_run_queries_merges_finds_applies_the_targeted_gate_and_records_the_run(db_session):
    _project(db_session, "softbody", [])
    first = _query(db_session, "softbody", "github", "soft body physics")
    second = _query(db_session, "softbody", "github", "xpbd")
    hf = _query(db_session, "softbody", "huggingface", "whisper")
    web = Web()
    good = gh_repo("a/softbody", 20)  # too few stars for a global list, fine for a targeted search
    web.gh_queries["soft body physics"] = [good, gh_repo("a/tiny", 3)]
    web.gh_queries["xpbd"] = [good]
    web.hf_search["whisper"] = [hf_model("o/whisper-fast", 3, 0), hf_model("o/test-model", 500, 90000)]

    candidates, stats = discovery.run_queries(db_session, github_limit=5, hf_limit=5, transport=web.transport)

    by_id = {c["payload"]["external_id"]: c for c in candidates}
    assert set(by_id) == {"a/softbody", "o/whisper-fast"}  # a/tiny (3 stars) and o/test-model (junk) rejected
    assert [v["query_text"] for v in by_id["a/softbody"]["vias"]] == ["soft body physics", "xpbd"]
    assert stats["queries"] == 3 and stats["rejected"] == 2 and stats["candidates"] == 2
    assert first.last_found == 1 and second.last_found == 1 and hf.last_found == 1
    assert all(q.last_run_at is not None for q in (first, second, hf))


def test_a_github_rate_limit_skips_github_but_not_hugging_face(db_session):
    _project(db_session, "softbody", [])
    gh = _query(db_session, "softbody", "github", "soft body")
    hf = _query(db_session, "softbody", "huggingface", "whisper")
    web = Web()
    web.github_rate_limited = True
    web.hf_search["whisper"] = [hf_model("o/whisper-fast", 30, 900)]

    candidates, stats = discovery.run_queries(db_session, github_limit=5, hf_limit=5, transport=web.transport)

    assert stats["github_rate_limited"] is True and len(candidates) == 1
    assert gh.last_run_at is None  # not marked as run, so it goes first next time
    assert hf.last_run_at is not None


def test_one_failing_query_does_not_stop_the_others(db_session):
    _project(db_session, "softbody", [])
    broken = _query(db_session, "softbody", "huggingface", "broken")
    working = _query(db_session, "softbody", "huggingface", "works")
    web = Web()
    web.hf_fail = {"broken"}
    web.hf_search["works"] = [hf_model("o/ok", 30, 900)]

    candidates, stats = discovery.run_queries(db_session, github_limit=0, hf_limit=5, transport=web.transport)

    assert stats["errors"] == 1 and len(candidates) == 1
    assert broken.last_run_at is None and working.last_run_at is not None


def test_run_queries_honours_a_source_filter(db_session):
    _project(db_session, "softbody", [])
    _query(db_session, "softbody", "github", "a b")
    hf = _query(db_session, "softbody", "huggingface", "whisper")
    web = Web()

    discovery.run_queries(db_session, github_limit=5, hf_limit=5, sources={"huggingface"}, transport=web.transport)

    assert all("huggingface.co" in r for r in web.requests) and hf.last_run_at is not None


# ---- refreshing tracked items ------------------------------------------------------------

def _item(db, name, source="github", status=models.Status.NEW, last_seen_days=5, ai=False, relevance=0.0):
    item = models.Item(source=source, external_id=f"o/{name}", name=name, owner="o", url="u", description="d",
                       topics=[], raw={}, status=status, stars=10,
                       last_seen_at=NOW - datetime.timedelta(days=last_seen_days))
    db.add(item)
    db.flush()
    if ai:
        db.add(models.ItemAnalysisState(item_id=item.id, provenance={"mode": "ai"}))
    if relevance:
        project = db.query(models.Project).first() or _project(db, "p", [])
        db.add(models.ItemProjectRelevance(item_id=item.id, project_id=project.id, score=relevance, rationale="r"))
    db.flush()
    return item


def test_only_items_that_matter_are_tracked(db_session):
    _project(db_session, "p", [])
    assessed = _item(db_session, "assessed", status=models.Status.ASSESS)
    voted = _item(db_session, "voted")
    db_session.add(models.ItemFeedback(item_id=voted.id, kind="note", note="x"))
    db_session.flush()
    db_session.expire(voted, ["feedback"])
    ai_hot = _item(db_session, "ai-hot", ai=True, relevance=0.9)
    _item(db_session, "keyword-hot", relevance=0.95)  # keyword-only: not worth a rate-limited request
    _item(db_session, "plain")
    archived = _item(db_session, "archived", status=models.Status.ASSESS)
    archived.archived = True

    tracked = {i.name for i in discovery.tracked_items(db_session)}

    assert tracked == {"assessed", "voted", "ai-hot"} and archived.name not in tracked


def test_refresh_fetches_current_numbers_within_budget_stalest_first(db_session):
    _project(db_session, "p", [])
    oldest = _item(db_session, "oldest", status=models.Status.ASSESS, last_seen_days=9)
    newer = _item(db_session, "newer", status=models.Status.ASSESS, last_seen_days=2)
    seen_now = _item(db_session, "seen-now", status=models.Status.ASSESS, last_seen_days=1)
    model = _item(db_session, "model", source="huggingface", status=models.Status.WATCH, last_seen_days=4)
    gone = _item(db_session, "gone", source="huggingface", status=models.Status.WATCH, last_seen_days=6)
    web = Web()
    for name, stars in (("oldest", 900), ("newer", 700), ("seen-now", 500)):
        web.gh_repos[f"o/{name}"] = gh_repo(f"o/{name}", stars)
    web.hf_models["o/model"] = hf_model("o/model", likes=77)

    payloads, stats = discovery.refresh_tracked(db_session, exclude={("github", "o/seen-now")}, github_limit=1,
                                                hf_limit=5, transport=web.transport)

    assert [p["external_id"] for p in payloads if p["source"] == "github"] == ["o/oldest"]  # budget 1, stalest
    assert next(p for p in payloads if p["source"] == "huggingface")["likes"] == 77
    assert stats["refreshed"] == 2 and stats["missing"] == 1
    assert not any("seen-now" in r or "newer" in r for r in web.requests)
    assert gone.id and oldest.id and newer.id and seen_now.id and model.id


def test_refresh_stops_using_github_when_rate_limited(db_session):
    _project(db_session, "p", [])
    _item(db_session, "a", status=models.Status.ASSESS, last_seen_days=9)
    _item(db_session, "b", status=models.Status.ASSESS, last_seen_days=8)
    web = Web()
    web.github_rate_limited = True

    payloads, stats = discovery.refresh_tracked(db_session, exclude=set(), transport=web.transport)

    assert payloads == [] and stats["github_rate_limited"] is True
    assert len(web.requests) == 1  # it did not keep asking


# ---- housekeeping ------------------------------------------------------------------------

def test_stale_untouched_items_are_archived_but_anything_engaged_is_kept(db_session):
    _project(db_session, "p", [])
    stale = _item(db_session, "stale", last_seen_days=45)
    recent = _item(db_session, "recent", last_seen_days=3)
    assessed = _item(db_session, "assessed", status=models.Status.ASSESS, last_seen_days=45)
    ai = _item(db_session, "ai", ai=True, relevance=0.9, last_seen_days=45)   # read by the AI and strongly relevant
    read_only = _item(db_session, "read-only", ai=True, relevance=0.2, last_seen_days=45)  # read, but nothing in it
    voted = _item(db_session, "voted", last_seen_days=45)
    feedback.record_note(db_session, voted, "keep me")

    result = discovery.housekeeping(db_session, stale_days=30)

    assert result == {"archived_stale": 2}
    assert stale.archived is True and read_only.archived is True  # having been read is not a reason to stay forever
    assert not any(i.archived for i in (recent, assessed, ai, voted))
