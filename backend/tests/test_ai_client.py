"""Tests for the AIClient HTTP adapter: retry/backoff, polling, deadlines, and the
defensive checks (response size cap, response-id format, base URL shape). All against
httpx.MockTransport — no real network, no real gateway needed.
"""

import json

import httpx
import pytest

from app.ai_client import AIClient, AIError

NO_SLEEP = lambda seconds: None  # noqa: E731 - skip real backoff delays in tests


def _client(handler, *, timeout=5):
    return AIClient("http://127.0.0.1:8787", "test-key", timeout=timeout,
                     transport=httpx.MockTransport(handler), sleep=NO_SLEEP)


def _json_response(status_code, payload):
    return httpx.Response(status_code, json=payload)


def test_generate_completes_on_first_response():
    def handler(request):
        assert request.method == "POST"
        assert request.url.path == "/v1/generate"
        assert request.headers["Authorization"] == "Bearer test-key"
        assert request.headers["Idempotency-Key"] == "key-1"
        return _json_response(200, {"id": "resp_1", "status": "completed", "provider": "p",
                                     "model": "m", "output": [{"type": "text", "text": "hi"}]})

    client = _client(handler)
    result = client.generate({"messages": []}, "key-1")
    assert result["status"] == "completed"
    assert result["output"][0]["text"] == "hi"


def test_generate_polls_until_completed():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            assert request.method == "POST"
            return _json_response(202, {"id": "resp_2", "status": "queued"})
        if len(calls) == 2:
            assert request.method == "GET"
            assert request.url.path == "/v1/responses/resp_2"
            return _json_response(200, {"id": "resp_2", "status": "running"})
        return _json_response(200, {"id": "resp_2", "status": "completed", "provider": "p",
                                     "model": "m", "output": [{"type": "text", "text": "done"}]})

    client = _client(handler)
    result = client.generate({"messages": []}, "key-2")
    assert result["status"] == "completed"
    assert len(calls) == 3


def test_generate_retries_on_retryable_503_then_succeeds():
    calls = []

    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return _json_response(503, {"error": {"code": "overloaded", "retryable": True}})
        return _json_response(200, {"id": "resp_3", "status": "completed", "provider": "p",
                                     "model": "m", "output": [{"type": "text", "text": "ok"}]})

    client = _client(handler)
    result = client.generate({"messages": []}, "key-3")
    assert result["status"] == "completed"
    assert len(calls) == 2


def test_generate_raises_immediately_on_non_retryable_error():
    def handler(request):
        return _json_response(400, {"error": {"code": "bad_request", "retryable": False}})

    client = _client(handler)
    with pytest.raises(AIError) as exc_info:
        client.generate({"messages": []}, "key-4")
    assert exc_info.value.code == "bad_request"
    assert exc_info.value.indeterminate is False


def test_generate_treats_unexpected_state_as_indeterminate():
    def handler(request):
        return _json_response(200, {"id": "resp_5", "status": "something_new"})

    client = _client(handler)
    with pytest.raises(AIError) as exc_info:
        client.generate({"messages": []}, "key-5")
    assert exc_info.value.indeterminate is True


def test_generate_rejects_malformed_response_id_before_polling():
    def handler(request):
        return _json_response(202, {"id": "not-a-valid-id; DROP TABLE items", "status": "queued"})

    client = _client(handler)
    with pytest.raises(AIError) as exc_info:
        client.generate({"messages": []}, "key-6")
    assert exc_info.value.code == "invalid_http_response"


def test_generate_response_too_large_is_rejected():
    huge_payload = {"id": "resp_7", "status": "completed", "provider": "p", "model": "m",
                     "output": [{"type": "text", "text": "a" * (5 * 1024 * 1024)}]}

    def handler(request):
        return httpx.Response(200, content=json.dumps(huge_payload).encode())

    client = _client(handler)
    with pytest.raises(AIError) as exc_info:
        client.generate({"messages": []}, "key-7")
    assert exc_info.value.code == "response_too_large"


def test_generate_transport_error_is_indeterminate_after_retries():
    attempts = []

    def handler(request):
        attempts.append(request)
        raise httpx.ConnectError("boom", request=request)

    client = _client(handler)
    with pytest.raises(AIError) as exc_info:
        client.generate({"messages": []}, "key-8")
    assert exc_info.value.code == "transport_error"
    assert exc_info.value.indeterminate is True
    assert len(attempts) == 3  # initial attempt + 2 retries


def test_profiles_returns_routing_version():
    def handler(request):
        assert request.method == "GET"
        assert request.url.path == "/v1/profiles"
        return _json_response(200, {"routing_version": "v42"})

    client = _client(handler)
    assert client.profiles()["routing_version"] == "v42"


def test_profiles_raises_when_routing_version_missing():
    def handler(request):
        return _json_response(200, {"unexpected": "shape"})

    client = _client(handler)
    with pytest.raises(AIError):
        client.profiles()


@pytest.mark.parametrize("bad_url", [
    "http://user:pass@127.0.0.1:8787",  # credentials
    "http://127.0.0.1:8787/?token=x",   # query string
    "ftp://127.0.0.1:8787",             # wrong scheme
    "http:///no-host",                  # no hostname
])
def test_base_url_validation_rejects_unsafe_urls(bad_url):
    with pytest.raises(ValueError):
        AIClient(bad_url, "key")


def test_readiness_reports_a_parked_worker_instead_of_just_a_live_process():
    def handler(request):
        assert request.url.path == "/readyz"
        return _json_response(503, {"ready": False, "code": "deadline_exceeded", "queue_depth": 0, "busy": False})

    assert _client(handler).readiness() == {"ready": False, "code": "deadline_exceeded"}


def test_readiness_ready():
    assert _client(lambda request: _json_response(200, {"ready": True, "code": "ready"})).readiness() == \
        {"ready": True, "code": "ready"}


def test_readiness_of_an_unreachable_gateway_is_a_gateway_error():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(AIError) as info:
        _client(handler).readiness()
    assert info.value.code == "gateway_unavailable"
