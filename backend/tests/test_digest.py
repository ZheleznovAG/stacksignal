"""Telegram digest: what gets selected, how it is formatted, delivery and failure handling, the
token never leaking, the daily schedule and the HTTP API. Telegram is replaced by
httpx.MockTransport, so nothing here touches the network."""

import datetime
from types import SimpleNamespace
import json
import logging

import httpx
import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app import config, db as db_module, digest, feedback, models, scheduler, telegram
from app.main import app

TOKEN = "123456:SECRET-TOKEN-VALUE"


@pytest.fixture(autouse=True)
def telegram_settings(monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "42")
    monkeypatch.setattr(config, "TELEGRAM_PROXY", "")
    monkeypatch.setattr(config, "DIGEST_LANG", "en")
    monkeypatch.setattr(config, "DIGEST_MIN_RELEVANCE", 0.6)
    monkeypatch.setattr(config, "DIGEST_MAX_ITEMS", 8)
    monkeypatch.setattr(config, "DASHBOARD_URL", "http://localhost:8000")


class Telegram:
    """A fake Bot API: records requests, answers ok unless told otherwise."""

    def __init__(self, status=200, body=None, error=None):
        self.requests = []
        self.status, self.body, self.error = status, body, error
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request):
        if self.error:
            raise self.error
        self.requests.append({"path": request.url.path, "json": json.loads(request.content)})
        return httpx.Response(self.status, json=self.body if self.body is not None else {"ok": True, "result": True})

    @property
    def texts(self):
        return [r["json"]["text"] for r in self.requests if r["path"].endswith("/sendMessage")]


def _project(db, slug="chitalka", name="Читалка"):
    project = models.Project(name=name, slug=slug, keywords=[], description="d")
    db.add(project)
    db.flush()
    return project


def _item(db, name, relevance, *, ai=True, momentum=0.5, status=models.Status.NEW, url=None, reason="Fits well"):
    item = models.Item(source="github", external_id=f"o/{name}", name=name, owner="o",
                       url=url if url is not None else f"https://github.com/o/{name}",
                       description="d", llm_summary="An AI summary", topics=[], momentum_score=momentum,
                       raw={}, status=status)
    db.add(item)
    db.flush()
    db.add(models.ItemAnalysisState(item_id=item.id, provenance={"mode": "ai" if ai else "heuristic"}))
    for project, score in relevance.items():
        db.add(models.ItemProjectRelevance(item_id=item.id, project_id=project.id, score=score, base_score=score,
                                           rationale=reason))
    db.flush()
    return item


def _names(entries):
    return [e["name"] for e in entries]


# ---- selection ---------------------------------------------------------------------------

def test_only_ai_scored_items_above_the_threshold_are_selected(db_session):
    chitalka = _project(db_session)
    _item(db_session, "strong", {chitalka: 0.9})
    _item(db_session, "weak", {chitalka: 0.5})
    _item(db_session, "keywords-only", {chitalka: 0.95}, ai=False)

    assert _names(digest.select_items(db_session)) == ["strong"]


def test_keyword_only_items_are_used_only_while_there_is_no_ai_analysis_at_all(db_session):
    chitalka = _project(db_session)
    _item(db_session, "kw", {chitalka: 0.8}, ai=False)

    selected = digest.select_items(db_session)

    assert _names(selected) == ["kw"] and selected[0]["ai"] is False


def test_ignored_voted_down_and_already_sent_items_are_left_out(db_session):
    chitalka = _project(db_session)
    _item(db_session, "ignored", {chitalka: 0.9}, status=models.Status.IGNORE)
    voted_down = _item(db_session, "voted-down", {chitalka: 0.9})
    feedback.record_vote(db_session, voted_down, "chitalka", -1)
    keep = _item(db_session, "keep", {chitalka: 0.8})

    assert _names(digest.select_items(db_session)) == ["keep"]

    fake = Telegram()
    digest.send_digest(db_session, transport=fake.transport)
    assert digest.select_items(db_session) == []  # sent once, never again
    assert keep.id in db_session.query(models.DigestLog).one().item_ids


def test_an_item_appears_once_under_its_best_project(db_session):
    chitalka, softbody = _project(db_session), _project(db_session, "softbody", "Softbody Demo")
    _item(db_session, "both", {chitalka: 0.7, softbody: 0.95})

    (entry,) = digest.select_items(db_session)

    assert entry["project_name"] == "Softbody Demo" and entry["score"] == 0.95


def test_per_project_and_total_caps_apply(db_session, monkeypatch):
    chitalka, softbody = _project(db_session), _project(db_session, "softbody", "Softbody Demo")
    for index in range(6):
        _item(db_session, f"s{index}", {chitalka: 0.9 - index * 0.01})
    for index in range(2):
        _item(db_session, f"j{index}", {softbody: 0.7 - index * 0.01})

    selected = digest.select_items(db_session)
    assert [e["project_slug"] for e in selected].count("chitalka") == digest.PER_PROJECT_CAP
    assert len(selected) == digest.PER_PROJECT_CAP + 2

    monkeypatch.setattr(config, "DIGEST_MAX_ITEMS", 2)
    assert len(digest.select_items(db_session)) == 2


def test_opportunities_are_capped_deduplicated_and_not_repeated(db_session):
    _project(db_session)
    for index, title in enumerate(["A", "B", "C", "D", "A"]):
        db_session.add(models.Opportunity(title=title, body=f"body {index}", project_slug="chitalka", item_ids=[]))
    db_session.flush()

    first = digest.select_opportunities(db_session)
    assert [o["title"] for o in first] == ["A", "B", "C"] and first[0]["project_name"] == "Читалка"

    digest.send_digest(db_session, transport=Telegram().transport)
    assert [o["title"] for o in digest.select_opportunities(db_session)] == ["D"]


# ---- formatting --------------------------------------------------------------------------

def test_html_in_names_and_reasons_is_escaped(db_session):
    chitalka = _project(db_session, "chitalka", "A & B <project>")
    _item(db_session, "<script>alert(1)</script>", {chitalka: 0.9}, reason="uses <b>bold</b> & more")

    text = "\n".join(digest.build_digest(db_session).messages)

    assert "<script>" not in text and "&lt;script&gt;" in text
    assert "&lt;b&gt;bold&lt;/b&gt; &amp; more" in text and "A &amp; B &lt;project&gt;" in text


def test_unsafe_urls_do_not_become_links(db_session):
    chitalka = _project(db_session)
    _item(db_session, "evil", {chitalka: 0.9}, url="javascript:alert(1)")
    _item(db_session, "fine", {chitalka: 0.8}, url="https://github.com/o/fine")

    text = "\n".join(digest.build_digest(db_session).messages)

    assert "javascript:" not in text
    assert '<a href="https://github.com/o/fine">fine</a>' in text and "• evil" in text


def test_long_content_is_split_into_messages_below_telegram_limit():
    blocks = [f"block {i} " + "x" * 900 for i in range(12)] + ["y" * 6000]
    messages = digest._chunk(blocks)

    assert len(messages) > 2 and all(len(m) <= 4096 for m in messages)
    assert sum(m.count("block") for m in messages) == 12  # nothing lost while splitting
    assert sum(m.count("y") for m in messages) == 6000


def test_shortening_prefers_sentence_then_word_boundaries():
    text = "This first sentence is long enough to count. Second one goes on and on and on for a while longer."

    assert digest._short(text, 60) == "This first sentence is long enough to count."  # ends on a sentence
    assert digest._short("Tiny. " + "word " * 30, 40).endswith("…")  # a very short first sentence is not a boundary
    assert digest._short("word " * 40, 22) == "word word word word…"  # no sentence: ends on a word
    assert digest._short("short", 60) == "short"
    assert digest._short("  spaced \n out  ", 60) == "spaced out"
    assert len(digest._short("x" * 500, 100)) <= 101  # unbreakable text is still cut


def test_language_labels_and_date(db_session):
    chitalka = _project(db_session)
    _item(db_session, "thing", {chitalka: 0.9})

    ru = digest.build_digest(db_session, lang="ru", today=datetime.date(2026, 9, 19)).messages[0]
    en = digest.build_digest(db_session, lang="en", today=datetime.date(2026, 9, 19)).messages[0]

    assert "дайджест, 19 сен" in ru and "Новое и полезное" in ru
    assert "digest, 19 Sep" in en and "New and useful" in en


def test_footer_reports_the_last_run_and_links_the_dashboard(db_session):
    chitalka = _project(db_session)
    _item(db_session, "thing", {chitalka: 0.9})
    db_session.add(models.CollectionRun(items_found=160, items_new=6))
    db_session.flush()

    text = "\n".join(digest.build_digest(db_session).messages)

    assert "Collected 160, 6 new · AI has read 1 of 1" in text
    assert '<a href="http://localhost:8000">Open the dashboard</a>' in text


def test_nothing_new_gives_an_empty_digest_with_a_quiet_message(db_session):
    built = digest.build_digest(db_session)
    assert built.empty and "quiet day" in built.messages[0]


# ---- delivery ----------------------------------------------------------------------------

def test_send_posts_html_to_the_configured_chat_and_records_the_digest(db_session):
    chitalka = _project(db_session)
    item = _item(db_session, "thing", {chitalka: 0.9})
    fake = Telegram()

    result = digest.send_digest(db_session, trigger="scheduled", transport=fake.transport)

    assert result == {"sent": 1, "items": 1, "opportunities": 0}
    request = fake.requests[0]
    assert request["path"] == f"/bot{TOKEN}/sendMessage"
    assert request["json"]["chat_id"] == "42" and request["json"]["parse_mode"] == "HTML"
    assert request["json"]["disable_web_page_preview"] is True
    log = db_session.query(models.DigestLog).one()
    assert log.ok and log.trigger == "scheduled" and log.item_ids == [item.id]


def test_a_failed_send_records_the_failure_and_keeps_items_unsent(db_session):
    chitalka = _project(db_session)
    _item(db_session, "thing", {chitalka: 0.9})
    fake = Telegram(status=401, body={"ok": False, "description": "Unauthorized"})

    with pytest.raises(telegram.TelegramError) as caught:
        digest.send_digest(db_session, transport=fake.transport)

    assert caught.value.code == "telegram_bad_token"
    log = db_session.query(models.DigestLog).one()
    assert not log.ok and log.error == "telegram_bad_token" and log.item_ids == []
    assert len(digest.select_items(db_session)) == 1  # will be retried next time


def test_nothing_new_is_skipped_unless_forced(db_session):
    fake = Telegram()

    assert digest.send_digest(db_session, transport=fake.transport)["skipped"] == "nothing_new"
    assert fake.requests == []
    assert digest.send_digest(db_session, force=True, transport=fake.transport)["sent"] == 1
    assert "quiet day" in fake.texts[0]


def test_unconfigured_telegram_sends_nothing(db_session, monkeypatch):
    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    fake = Telegram()

    with pytest.raises(telegram.TelegramError) as caught:
        digest.send_digest(db_session, force=True, transport=fake.transport)

    assert caught.value.code == "telegram_not_configured" and fake.requests == []


@pytest.mark.parametrize("status, body, code", [
    (403, {"ok": False, "description": "Forbidden: bot was blocked by the user"}, "telegram_bot_blocked"),
    (400, {"ok": False, "description": "Bad Request: chat not found"}, "telegram_chat_not_found"),
    (429, {"ok": False, "description": "Too Many Requests"}, "telegram_rate_limited"),
    (404, {"ok": False, "description": "Not Found"}, "telegram_bad_token"),  # what Telegram says to a malformed token
    (500, {"ok": False, "description": "boom"}, "telegram_error_500"),
])
def test_telegram_errors_map_to_short_codes(status, body, code):
    with pytest.raises(telegram.TelegramError) as caught:
        telegram.send_message("hi", transport=Telegram(status=status, body=body).transport)
    assert caught.value.code == code


def test_network_failure_never_leaks_the_token():
    fake = Telegram(error=httpx.ConnectError(f"cannot reach https://api.telegram.org/bot{TOKEN}/sendMessage"))

    with pytest.raises(telegram.TelegramError) as caught:
        telegram.send_message("hi", transport=fake.transport)

    assert caught.value.code == "telegram_unreachable"
    assert TOKEN not in str(caught.value) and caught.value.__cause__ is None
    assert caught.value.__suppress_context__ is True


def test_httpx_request_logging_is_silenced_because_urls_carry_the_token():
    import app.main  # noqa: F401  (configures logging)
    assert logging.getLogger("httpx").level >= logging.WARNING


def test_find_chats_lists_each_chat_once():
    updates = {"ok": True, "result": [
        {"message": {"chat": {"id": 42, "type": "private", "first_name": "Alex", "last_name": "Z"}}},
        {"message": {"chat": {"id": 42, "type": "private", "first_name": "Alex"}}},
        {"channel_post": {"chat": {"id": -100, "type": "channel", "title": "Radar"}}},
        {"poll": {}},
    ]}

    chats = telegram.find_chats(transport=Telegram(body=updates).transport)

    assert chats == [{"id": 42, "type": "private", "name": "Alex"},
                     {"id": -100, "type": "channel", "name": "Radar"}]


# ---- schedule ----------------------------------------------------------------------------

def test_the_daily_job_runs_at_the_configured_local_time(monkeypatch):
    monkeypatch.setattr(config, "DAILY_RUN_HOUR", 7)
    monkeypatch.setattr(config, "DAILY_RUN_MINUTE", 30)
    sched = BackgroundScheduler(timezone="Asia/Bangkok")

    scheduler.add_jobs(sched)

    job = sched.get_job(scheduler.JOB_ID)
    fields = {f.name: str(f) for f in job.trigger.fields}
    assert fields["hour"] == "7" and fields["minute"] == "30"
    assert job.coalesce is True and job.misfire_grace_time == 6 * 3600


def test_the_job_sends_the_digest_after_collecting_and_survives_a_digest_failure(monkeypatch):
    calls = []
    engine = create_engine("sqlite://")
    monkeypatch.setattr(scheduler, "SessionLocal", sessionmaker(bind=engine))
    monkeypatch.setattr(scheduler, "run_collection", lambda db: calls.append("collect") or SimpleNamespace(status="completed"))

    def fake_send(db, trigger):
        calls.append(f"digest:{trigger}")
        raise telegram.TelegramError("telegram_unreachable")

    monkeypatch.setattr(scheduler.digest, "send_digest", fake_send)

    scheduler._job()  # must not raise

    assert calls == ["collect", "digest:scheduled"]


def test_the_job_skips_the_digest_when_telegram_is_not_configured(monkeypatch):
    calls = []
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "")
    monkeypatch.setattr(scheduler, "SessionLocal", sessionmaker(bind=create_engine("sqlite://")))
    monkeypatch.setattr(scheduler, "run_collection", lambda db: calls.append("collect") or SimpleNamespace(status="completed"))
    monkeypatch.setattr(scheduler.digest, "send_digest", lambda *a, **k: calls.append("digest"))

    scheduler._job()

    assert calls == ["collect"]


@pytest.mark.parametrize("value", ["25:00", "8", "abc", "08:60", ""])
def test_daily_run_time_is_validated(value):
    with pytest.raises(ValueError):
        config._clock(value)


def test_daily_run_time_parses():
    assert config._clock("08:00") == (8, 0) and config._clock("7:05") == (7, 5)


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
    chitalka = _project(seed)
    _item(seed, "thing", {chitalka: 0.9})
    seed.commit()
    seed.close()
    fake = Telegram()
    monkeypatch.setattr(telegram, "_client", lambda transport=None: httpx.Client(transport=fake.transport))
    yield TestClient(app), fake, Session
    app.dependency_overrides.clear()


@pytest.mark.parametrize("token, ok", [
    ("123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw", True),
    ("AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw", False),  # only the part after the colon
    ("bot123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw", False),
    ("123456789:short", False),
    ("", False),
])
def test_token_shape_is_recognised(monkeypatch, token, ok):
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", token)
    assert telegram.token_shape_ok() is ok


def test_api_status_flags_a_token_of_the_wrong_shape(api, monkeypatch):
    client, _, _ = api
    assert client.get("/api/digest/status").json()["token_shape_ok"] is False  # the suite's fake token
    monkeypatch.setattr(config, "TELEGRAM_BOT_TOKEN", "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw")
    assert client.get("/api/digest/status").json()["token_shape_ok"] is True


def test_api_status_never_exposes_the_token(api):
    client, _, _ = api
    response = client.get("/api/digest/status")

    assert response.status_code == 200
    body = response.json()
    assert body["configured"] is True and body["token_set"] is True and body["daily_run_time"] == config.DAILY_RUN_TIME
    assert TOKEN not in response.text


def test_api_preview_shows_the_message_without_sending_or_recording(api):
    client, fake, Session = api
    body = client.get("/api/digest/preview").json()

    assert body["items"] == 1 and "thing" in body["messages"][0] and "<a " not in body["messages"][0]
    assert fake.requests == [] and Session().query(models.DigestLog).count() == 0


def test_api_send_delivers_and_lists_the_digest_in_recent(api):
    client, fake, _ = api
    assert client.post("/api/digest/send").json() == {"sent": 1, "items": 1, "opportunities": 0}
    assert len(fake.texts) == 1
    recent = client.get("/api/digest/status").json()["recent"]
    assert recent[0]["ok"] is True and recent[0]["items"] == 1 and recent[0]["trigger"] == "manual"


def test_api_send_reports_configuration_and_delivery_problems(api, monkeypatch):
    client, fake, _ = api
    fake.status, fake.body = 401, {"ok": False, "description": "Unauthorized"}
    assert client.post("/api/digest/send").status_code == 502

    monkeypatch.setattr(config, "TELEGRAM_CHAT_ID", "")
    unconfigured = client.post("/api/digest/send")
    assert unconfigured.status_code == 400 and unconfigured.json()["detail"] == "telegram_not_configured"
    assert TOKEN not in unconfigured.text


def test_api_chats_returns_recent_chats(api):
    client, fake, _ = api
    fake.body = {"ok": True, "result": [{"message": {"chat": {"id": 7, "type": "private", "first_name": "Me"}}}]}
    assert client.post("/api/digest/chats").json() == [{"id": 7, "type": "private", "name": "Me"}]
