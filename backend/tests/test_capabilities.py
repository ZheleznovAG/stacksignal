"""Capabilities: AI consolidation of synonym names (links re-pointed, nothing lost), the
vocabulary offered back to the model, and the read model behind the Capabilities screen."""

import pytest

from app import capabilities, models
from app.ai_analysis import AIContext
from app.ai_client import AIError
from app.utils import slugify

FILLER = [f"filler {i}" for i in range(8)]  # keeps the vocabulary above the "too few" threshold


class FakeGateway:
    def __init__(self, groups, namespace="ns-cap"):
        self.namespace = namespace
        self.groups = groups
        self.calls = 0
        self.payload = None

    def profiles(self):
        return {"routing_version": "v1"}

    def generate(self, body, key):
        import json
        self.calls += 1
        self.payload = json.loads(body["messages"][1]["content"][0]["text"])
        return {"id": "resp_cap", "status": "completed", "provider": "fake", "model": "fake",
                "output": [{"type": "json", "data": {"groups": self.groups}}]}


def _caps(db, names):
    out = {}
    for name in names:
        cap = models.Capability(name=name, slug=slugify(name))
        db.add(cap)
        out[name] = cap
    db.flush()
    fillers = [cap for name, cap in out.items() if name.startswith("filler ")]
    if fillers:  # unused capabilities are pruned, so fillers share one carrier item
        carrier = _item(db, "carrier")
        for cap in fillers:
            _link(db, carrier, cap)
    return out


def _item(db, name, momentum=0.3):
    item = models.Item(source="github", external_id=f"o/{name}", name=name, owner="o", url=f"https://x/{name}",
                       description="d", topics=[], momentum_score=momentum, raw={})
    db.add(item)
    db.flush()
    return item


def _link(db, item, cap):
    db.add(models.ItemCapability(item_id=item.id, capability_id=cap.id, confidence=0.7))
    db.flush()


def _names(db):
    return {c.name for c in db.query(models.Capability).all()}


def _context(gateway):
    return AIContext(client=gateway, max_calls=2)


def test_synonyms_are_merged_and_links_repointed(db_session):
    caps = _caps(db_session, ["local inference", "on-device inference", "local LLM inference", *FILLER])
    a, b, c = _item(db_session, "a"), _item(db_session, "b"), _item(db_session, "c")
    _link(db_session, a, caps["local inference"])
    _link(db_session, b, caps["on-device inference"])
    _link(db_session, c, caps["local LLM inference"])
    gateway = FakeGateway([{"canonical": "local inference",
                            "members": ["local inference", "on-device inference", "local LLM inference"]}])

    stats = capabilities.consolidate(db_session, context=_context(gateway))

    assert stats["removed"] == 2
    assert "on-device inference" not in _names(db_session) and "local LLM inference" not in _names(db_session)
    canonical = db_session.query(models.Capability).filter_by(name="local inference").one()
    assert {link.item_id for link in canonical.item_links} == {a.id, b.id, c.id}
    assert db_session.query(models.ItemCapability).filter(
        models.ItemCapability.item_id.in_([a.id, b.id, c.id])).count() == 3  # nothing lost


def test_an_item_with_two_synonyms_ends_up_with_one_link(db_session):
    caps = _caps(db_session, ["local inference", "on-device inference", *FILLER])
    item = _item(db_session, "both")
    _link(db_session, item, caps["local inference"])
    _link(db_session, item, caps["on-device inference"])
    gateway = FakeGateway([{"canonical": "local inference", "members": ["local inference", "on-device inference"]}])

    capabilities.consolidate(db_session, context=_context(gateway))

    assert db_session.query(models.ItemCapability).filter_by(item_id=item.id).count() == 1


def test_a_new_canonical_name_is_created(db_session):
    caps = _caps(db_session, ["agent workflows", "multi-agent workflows", *FILLER])
    one, two = _item(db_session, "one"), _item(db_session, "two")
    _link(db_session, one, caps["agent workflows"])
    _link(db_session, two, caps["multi-agent workflows"])
    gateway = FakeGateway([{"canonical": "agent orchestration",
                            "members": ["agent workflows", "multi-agent workflows"]}])

    capabilities.consolidate(db_session, context=_context(gateway))

    assert {"agent orchestration"} <= _names(db_session)
    assert not {"agent workflows", "multi-agent workflows"} & _names(db_session)
    created = db_session.query(models.Capability).filter_by(name="agent orchestration").one()
    assert {link.item_id for link in created.item_links} == {one.id, two.id}


def test_unknown_member_is_rejected_and_nothing_changes(db_session):
    caps = _caps(db_session, ["real name", *FILLER])
    _link(db_session, _item(db_session, "holder"), caps["real name"])
    before = _names(db_session)
    gateway = FakeGateway([{"canonical": "x", "members": ["real name", "invented name"]}])

    with pytest.raises(AIError):
        capabilities.consolidate(db_session, context=_context(gateway))

    assert _names(db_session) == before


def test_a_name_in_two_groups_is_rejected(db_session):
    caps = _caps(db_session, ["a cap", "b cap", "c cap", *FILLER])
    for name in ("a cap", "b cap", "c cap"):
        _link(db_session, _item(db_session, name), caps[name])
    gateway = FakeGateway([{"canonical": "ab", "members": ["a cap", "b cap"]},
                           {"canonical": "bc", "members": ["b cap", "c cap"]}])

    with pytest.raises(AIError):
        capabilities.consolidate(db_session, context=_context(gateway))


def test_unused_capabilities_are_pruned_before_consolidating(db_session):
    caps = _caps(db_session, ["used", "orphan one", "orphan two", *FILLER])
    _link(db_session, _item(db_session, "u"), caps["used"])
    gateway = FakeGateway([])

    stats = capabilities.consolidate(db_session, context=_context(gateway))

    assert stats["pruned"] == 2
    assert "orphan one" not in _names(db_session) and "used" in _names(db_session)
    assert {e["name"] for e in gateway.payload["capabilities"]} == {"used", *FILLER}


def test_too_few_capabilities_is_a_no_call_noop(db_session):
    caps = _caps(db_session, ["only", "three", "names"])
    for name, cap in caps.items():
        _link(db_session, _item(db_session, name), cap)
    gateway = FakeGateway([])

    stats = capabilities.consolidate(db_session, context=_context(gateway))

    assert stats["skipped"] == "too_few" and gateway.calls == 0


def test_payload_carries_usage_counts(db_session):
    caps = _caps(db_session, ["popular", *FILLER])
    for name in ("p1", "p2", "p3"):
        _link(db_session, _item(db_session, name), caps["popular"])
    gateway = FakeGateway([])

    capabilities.consolidate(db_session, context=_context(gateway))

    counts = {entry["name"]: entry["items"] for entry in gateway.payload["capabilities"]}
    assert counts["popular"] == 3 and counts["filler 0"] == 1


def test_known_vocabulary_is_ordered_by_usage_and_capped(db_session):
    caps = _caps(db_session, ["rare", "common", "middling"])
    for index in range(3):
        _link(db_session, _item(db_session, f"c{index}"), caps["common"])
    _link(db_session, _item(db_session, "m"), caps["middling"])

    assert capabilities.known_vocabulary(db_session) == ["common", "middling", "rare"]
    assert capabilities.known_vocabulary(db_session, limit=2) == ["common", "middling"]


def test_overview_counts_projects_and_items_and_sorts_by_usage(db_session):
    caps = _caps(db_session, ["speech", "shaders"])
    chitalka = models.Project(name="Читалка", slug="chitalka", keywords=[], description="d")
    softbody = models.Project(name="Softbody Demo", slug="softbody", keywords=[], description="d")
    db_session.add_all([chitalka, softbody])
    db_session.flush()
    one, two, three = _item(db_session, "one", 0.2), _item(db_session, "two", 0.9), _item(db_session, "three")
    for item, cap in ((one, "speech"), (two, "speech"), (three, "shaders")):
        _link(db_session, item, caps[cap])
    db_session.add_all([
        models.ItemProjectRelevance(item_id=one.id, project_id=chitalka.id, score=0.9, rationale="r"),
        models.ItemProjectRelevance(item_id=two.id, project_id=chitalka.id, score=0.4, rationale="r"),
        models.ItemProjectRelevance(item_id=two.id, project_id=softbody.id, score=0.1, rationale="r"),  # below floor
        models.ItemProjectRelevance(item_id=three.id, project_id=softbody.id, score=0.8, rationale="r"),
    ])
    db_session.flush()

    rows = capabilities.overview(db_session)

    assert [r["name"] for r in rows] == ["speech", "shaders"]  # 2 items before 1
    speech = rows[0]
    assert speech["item_count"] == 2
    assert speech["projects"] == [{"slug": "chitalka", "name": "Читалка", "score": 0.9}]  # softbody 0.1 is noise
    assert [i["name"] for i in speech["items"]] == ["one", "two"]  # strongest relevance first


def test_overview_filters_by_query_and_minimum_usage(db_session):
    caps = _caps(db_session, ["speech synthesis", "speech recognition", "shaders"])
    for name in ("a", "b"):
        _link(db_session, _item(db_session, name), caps["speech recognition"])
    _link(db_session, _item(db_session, "c"), caps["speech synthesis"])

    assert [r["name"] for r in capabilities.overview(db_session, query="SPEECH")] == [
        "speech recognition", "speech synthesis"]
    assert [r["name"] for r in capabilities.overview(db_session, min_items=2)] == ["speech recognition"]
    assert capabilities.overview(db_session, query="nothing") == []
