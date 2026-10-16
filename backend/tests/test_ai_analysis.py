"""Tests for the AI Gateway caching/circuit-breaker layer (ai_analysis.analyze). No
real network: a FakeClient stands in for AIClient. These exercise exactly the state
machine that has no other test coverage, since conftest.py forces AI_ENABLED=0 for
every other test in this suite.
"""

import datetime

import pytest
from pydantic import BaseModel

from app import models
from app.ai_analysis import AIContext, analyze, messages_for
from app.ai_client import AIError
from app.utils import utcnow


class _Result(BaseModel):
    value: str


class FakeClient:
    """Stands in for AIClient. `responses` is a queue of either a full gateway
    envelope dict or an exception instance to raise, one per generate() call."""

    def __init__(self, responses, namespace="ns-test"):
        self.namespace = namespace
        self._responses = list(responses)
        self.generate_calls = 0
        self.profiles_calls = 0

    def profiles(self):
        self.profiles_calls += 1
        return {"routing_version": "v1"}

    def generate(self, body, key):
        self.generate_calls += 1
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _messages(payload="hello"):
    return messages_for("system prompt", {"q": payload})


def _completed_envelope(data):
    return {"id": "resp_abc123", "status": "completed", "provider": "test", "model": "test-model",
            "output": [{"type": "json", "data": data}]}


def test_analyze_returns_completed_and_caches_within_a_context(db_session):
    client = FakeClient([_completed_envelope({"value": "ok"})])
    context = AIContext(client=client, max_calls=5)

    result1 = analyze(db_session, context=context, task="t", profile="p", prompt_version="v1",
                       messages=_messages(), response_model=_Result)
    result2 = analyze(db_session, context=context, task="t", profile="p", prompt_version="v1",
                       messages=_messages(), response_model=_Result)

    assert result1.data == {"value": "ok"}
    assert result1.provenance["cached"] is False
    assert result2.data == {"value": "ok"}
    assert result2.provenance["cached"] is True
    assert client.generate_calls == 1  # second call served from cache, no new HTTP call
    assert client.profiles_calls == 1  # routing memoized on the context


def test_indeterminate_result_is_never_retried(db_session):
    client = FakeClient([AIError("client_timeout", indeterminate=True)])
    context = AIContext(client=client, max_calls=5)

    with pytest.raises(AIError):
        analyze(db_session, context=context, task="t", profile="p", prompt_version="v1",
                messages=_messages(), response_model=_Result)

    # A second attempt, even in a fresh context, must not call generate() again.
    context2 = AIContext(client=client, max_calls=5)
    with pytest.raises(AIError):
        analyze(db_session, context=context2, task="t", profile="p", prompt_version="v1",
                messages=_messages(), response_model=_Result)

    assert client.generate_calls == 1


def test_schema_validation_failure_with_response_id_is_retried_after_cooldown(db_session):
    """Regression test: a completed-but-schema-rejected response (has a response_id,
    status ends up "failed") must be retryable after the cooldown — it is a known,
    non-ambiguous outcome, unlike a genuinely indeterminate one."""
    client = FakeClient([
        _completed_envelope({"unexpected_field": "wrong shape"}),  # fails _Result validation
        _completed_envelope({"value": "ok on retry"}),
    ])
    context = AIContext(client=client, max_calls=5)

    with pytest.raises(AIError):
        analyze(db_session, context=context, task="t", profile="p", prompt_version="v1",
                messages=_messages(), response_model=_Result)

    record = db_session.query(models.AIAnalysis).one()
    assert record.status == "failed"
    assert record.response_id  # the gateway did return an envelope
    db_session.commit()

    # Immediately retrying within the 60s cooldown must not call generate() again.
    with pytest.raises(AIError):
        analyze(db_session, context=context, task="t", profile="p", prompt_version="v1",
                messages=_messages(), response_model=_Result)
    assert client.generate_calls == 1

    # After the cooldown, it must retry and can succeed.
    record.updated_at = utcnow() - datetime.timedelta(seconds=61)
    db_session.commit()

    result = analyze(db_session, context=context, task="t", profile="p", prompt_version="v1",
                      messages=_messages(), response_model=_Result)
    assert result.data == {"value": "ok on retry"}
    assert client.generate_calls == 2


def test_one_items_schema_failure_does_not_block_the_next_items_analysis(db_session):
    """Regression test: a validate()/schema rejection for one item in a collection run
    must not trip the run-wide circuit breaker and silently downgrade every other item
    in that same run to heuristic mode — the gateway was never actually unreachable."""
    client = FakeClient([
        _completed_envelope({"unexpected_field": "wrong shape"}),  # item A: fails schema
        _completed_envelope({"value": "item B is fine"}),  # item B: different content, valid
    ])
    context = AIContext(client=client, max_calls=5)

    with pytest.raises(AIError):
        analyze(db_session, context=context, task="t", profile="p", prompt_version="v1",
                messages=_messages("item A"), response_model=_Result)

    assert context.unavailable is False  # must not have tripped the breaker

    result = analyze(db_session, context=context, task="t", profile="p", prompt_version="v1",
                      messages=_messages("item B"), response_model=_Result)

    assert result.data == {"value": "item B is fine"}
    assert result.provenance["cached"] is False
    assert client.generate_calls == 2


def test_transport_failure_does_trip_the_circuit_breaker_for_the_rest_of_the_run(db_session):
    client = FakeClient([AIError("transport_error", indeterminate=True)])
    context = AIContext(client=client, max_calls=5)

    with pytest.raises(AIError):
        analyze(db_session, context=context, task="t", profile="p", prompt_version="v1",
                messages=_messages("item A"), response_model=_Result)

    assert context.unavailable is True

    # A different item in the same run must not even attempt generate() again.
    with pytest.raises(AIError):
        analyze(db_session, context=context, task="t", profile="p", prompt_version="v1",
                messages=_messages("item B, never tried"), response_model=_Result)
    assert client.generate_calls == 1


def test_run_budget_exhausted_before_any_call(db_session):
    client = FakeClient([_completed_envelope({"value": "unused"})])
    context = AIContext(client=client, max_calls=0)

    with pytest.raises(AIError, match="run_budget_exhausted"):
        analyze(db_session, context=context, task="t", profile="p", prompt_version="v1",
                messages=_messages(), response_model=_Result)

    assert client.generate_calls == 0


def test_gateway_unreachable_falls_back_to_stale_completed_cache(db_session):
    working_client = FakeClient([_completed_envelope({"value": "stale but valid"})], namespace="shared-ns")
    context1 = AIContext(client=working_client, max_calls=5)
    first = analyze(db_session, context=context1, task="t", profile="p", prompt_version="v1",
                     messages=_messages("same question"), response_model=_Result)
    assert first.data == {"value": "stale but valid"}

    class DownClient(FakeClient):
        def profiles(self):
            self.profiles_calls += 1
            raise AIError("gateway_unavailable")

    down_client = DownClient([], namespace="shared-ns")
    context2 = AIContext(client=down_client, max_calls=5)

    result = analyze(db_session, context=context2, task="t", profile="p", prompt_version="v1",
                      messages=_messages("same question"), response_model=_Result)

    assert result.data == {"value": "stale but valid"}
    assert result.provenance["cached"] is True
    assert down_client.generate_calls == 0


def test_gateway_unreachable_with_no_cache_raises(db_session):
    class DownClient(FakeClient):
        def profiles(self):
            self.profiles_calls += 1
            raise AIError("gateway_unavailable")

    context = AIContext(client=DownClient([], namespace="ns-empty"), max_calls=5)

    with pytest.raises(AIError):
        analyze(db_session, context=context, task="t", profile="p", prompt_version="v1",
                messages=_messages("never asked before"), response_model=_Result)


def test_reserved_calls_are_held_back_from_other_tasks(db_session):
    client = FakeClient([_completed_envelope({"value": "a"}), _completed_envelope({"value": "b"})])
    context = AIContext(client=client, max_calls=2)
    context.reserved = 1

    analyze(db_session, context=context, task="t", profile="p", prompt_version="v1",
            messages=_messages("first"), response_model=_Result)
    with pytest.raises(AIError, match="run_budget_exhausted"):
        analyze(db_session, context=context, task="t", profile="p", prompt_version="v1",
                messages=_messages("second"), response_model=_Result)

    context.reserved = 0  # the reserved call is released for the final task
    result = analyze(db_session, context=context, task="t", profile="p", prompt_version="v1",
                     messages=_messages("second"), response_model=_Result)
    assert result.data == {"value": "b"}
