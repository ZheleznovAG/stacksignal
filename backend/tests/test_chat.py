"""Chat: multilingual/project-aware retrieval and grounded, cited answers. The gateway is
replaced by a FakeGateway, so nothing touches the network."""

import datetime
import json

from app import chat, models
from app.ai_analysis import AIContext
from app.utils import utcnow


class FakeGateway:
    def __init__(self, cite=None, answer="Use whisper-fast.", namespace="ns-chat"):
        self.namespace = namespace
        self.cite = cite  # None -> cite the first two catalogue ids; list -> cite exactly these
        self.answer = answer
        self.calls = 0
        self.last_messages = None
        self.last_catalogue_ids = []

    def profiles(self):
        return {"routing_version": "v1"}

    def generate(self, body, key):
        self.calls += 1
        self.last_messages = body["messages"]
        payload = json.loads(body["messages"][-1]["content"][0]["text"])
        ids = [item["id"] for item in payload["catalogue"]["items"]]
        self.last_catalogue_ids = ids
        cited = ids[:2] if self.cite is None else self.cite
        return {"id": "resp_chat", "status": "completed", "provider": "fake", "model": "fake",
                "output": [{"type": "json", "data": {"answer": self.answer, "item_ids": cited}}]}


def _world(db):
    chitalka = models.Project(name="Читалка", slug="chitalka", keywords=["ocr"], description="d")
    softbody = models.Project(name="Softbody Demo", slug="softbody", keywords=["shader"], description="d")
    db.add_all([chitalka, softbody])
    db.flush()

    def item(name, summary, momentum=0.3, days_old=30, relevance=None):
        entry = models.Item(source="github", external_id=f"o/{name}", name=name, owner="o", url=f"https://x/{name}",
                            description=summary, llm_summary=summary, topics=[], stars=5, momentum_score=momentum,
                            raw={}, first_seen_at=utcnow() - datetime.timedelta(days=days_old))
        db.add(entry)
        db.flush()
        for project, score in (relevance or {}).items():
            db.add(models.ItemProjectRelevance(item_id=entry.id, project_id=project.id, score=score, rationale="r"))
        return entry

    whisper = item("whisper-fast", "Fast local speech recognition for transcripts", relevance={chitalka: 0.9})
    shader = item("shader-lib", "GPU compute shader toolkit", relevance={softbody: 0.9})
    scraper = item("web-scraper", "Resilient web scraping framework", momentum=0.9)
    fresh = item("fresh-thing", "A brand new unrelated release", days_old=1)
    db.flush()
    return {"whisper": whisper, "shader": shader, "scraper": scraper, "fresh": fresh}


def _names(items):
    return [i.name for i in items]


def test_russian_project_mention_finds_items_relevant_to_that_project(db_session):
    _world(db_session)
    assert _names(chat._retrieve(db_session, "что нового для Читалки?"))[0] == "whisper-fast"


def test_inflected_project_name_still_matches(db_session):
    _world(db_session)
    for form in ("Читалку", "читалка", "Читалке"):
        assert _names(chat._retrieve(db_session, f"что полезного для {form}"))[0] == "whisper-fast"


def test_russian_tech_words_find_english_items(db_session):
    _world(db_session)
    assert _names(chat._retrieve(db_session, "локальное распознавание речи"))[0] == "whisper-fast"


def test_english_keyword_query(db_session):
    _world(db_session)
    assert _names(chat._retrieve(db_session, "gpu shader"))[0] == "shader-lib"


def test_capability_names_are_searchable(db_session):
    world = _world(db_session)
    cap = models.Capability(name="speaker diarization", slug="speaker-diarization")
    db_session.add(cap)
    db_session.flush()
    db_session.add(models.ItemCapability(item_id=world["scraper"].id, capability_id=cap.id, confidence=0.7))
    db_session.flush()

    assert _names(chat._retrieve(db_session, "diarization"))[0] == "web-scraper"


def test_whats_new_questions_prefer_recent_items(db_session):
    _world(db_session)
    ranked = _names(chat._retrieve(db_session, "что нового?"))
    assert ranked.index("fresh-thing") < ranked.index("shader-lib")


def test_answer_returns_reply_and_only_cited_sources(db_session):
    world = _world(db_session)
    gateway = FakeGateway(cite=None)
    result = chat.answer(db_session, "локальное распознавание речи", context=AIContext(client=gateway, max_calls=1))

    assert result["llm"] is True and result["reply"] == "Use whisper-fast."
    assert [s["id"] for s in result["sources"]] == gateway.last_catalogue_ids[:2]
    assert result["sources"][0]["name"] == "whisper-fast" and result["sources"][0]["url"].startswith("https://")
    assert world["whisper"].id in gateway.last_catalogue_ids


def test_answer_with_an_uncatalogued_citation_falls_back_instead_of_showing_it(db_session):
    _world(db_session)
    gateway = FakeGateway(cite=[99999])
    result = chat.answer(db_session, "speech", context=AIContext(client=gateway, max_calls=1))

    assert result["llm"] is False
    assert result["ai"]["error_code"] == "invalid_structured_output"
    assert result["sources"] and "AI analysis is unavailable" in result["reply"]


def test_answer_without_a_gateway_lists_closest_matches_with_sources(db_session):
    _world(db_session)
    result = chat.answer(db_session, "gpu shader", context=AIContext(client=None, max_calls=1))

    assert result["llm"] is False and result["ai"]["error_code"] == "ai_disabled"
    assert result["sources"][0]["name"] == "shader-lib"
    assert "shader-lib" in result["reply"]


def test_history_is_sent_between_system_and_the_current_question(db_session):
    _world(db_session)
    gateway = FakeGateway()
    history = [{"role": "user", "content": "first question"}, {"role": "assistant", "content": "first answer"},
               {"role": "system", "content": "ignored"}, {"role": "user", "content": 123}]
    chat.answer(db_session, "second question", history, context=AIContext(client=gateway, max_calls=1))

    roles = [m["role"] for m in gateway.last_messages]
    assert roles == ["system", "user", "assistant", "user"]
    assert gateway.last_messages[1]["content"][0]["text"] == "first question"
    assert "second question" in gateway.last_messages[-1]["content"][0]["text"]


def test_catalogue_is_capped_and_compact(db_session):
    _world(db_session)
    for index in range(50):
        db_session.add(models.Item(source="github", external_id=f"o/bulk{index}", name=f"bulk{index}", owner="o",
                                   url="https://x", description="filler " * 100, topics=[], raw={}))
    db_session.flush()
    gateway = FakeGateway()
    chat.answer(db_session, "anything", context=AIContext(client=gateway, max_calls=1))

    payload = json.loads(gateway.last_messages[-1]["content"][0]["text"])
    assert len(payload["catalogue"]["items"]) == chat.TOP_K
    assert all(len(item["summary"]) <= 220 for item in payload["catalogue"]["items"])
