"""Versioned task cache and per-run call budget/circuit breaker."""
from dataclasses import dataclass
from datetime import datetime, timezone

from pydantic import ValidationError
from sqlalchemy.orm import Session

from . import config, models
from .ai_client import AIClient, AIError, digest


@dataclass
class AnalysisResult:
    data: dict | str
    provenance: dict


class AIContext:
    def __init__(self, *, client=None, max_calls=None):
        self.client = client or (AIClient(config.AI_BASE_URL, config.AI_API_KEY, config.AI_TIMEOUT) if config.LLM_ENABLED else None)
        self.remaining = config.AI_MAX_CALLS_PER_RUN if max_calls is None else max_calls
        self.routing_version = None
        self.unavailable = False
        self.reserved = 0  # calls held back for a later task in the same run

    def routing(self):
        if not self.client:
            raise AIError("ai_disabled")
        if self.unavailable:
            raise AIError("gateway_unavailable")
        if self.routing_version is None:
            try:
                self.routing_version = self.client.profiles()["routing_version"]
            except AIError:
                self.unavailable = True
                raise
        return self.routing_version


def _result(record, *, cached):
    return AnalysisResult(record.result, {
        "mode": "ai", "cached": cached, "cache_key": record.cache_key,
        "provider": record.provider, "model": record.model, "profile": record.profile,
        "prompt_version": record.prompt_version, "routing_version": record.routing_version,
        "response_id": record.response_id,
    })


def analyze(db, *, context, task, profile, prompt_version, messages, response_model=None, item_id=None, validate=None):
    """Call the gateway between transactions and persist its outcome independently.

    The caller must checkpoint its completed work BEFORE calling this function. In
    particular, a flushed heuristic/batch must not hold SQLite's writer lock here.
    Only plain request data and validators that do not use the caller's ORM session
    may cross this boundary. Cache persistence never commits caller-owned work.
    """
    if not context.client:
        raise AIError("ai_disabled")
    if db.in_transaction():
        raise RuntimeError("Checkpoint the caller session before AI analysis")
    with Session(db.get_bind(), expire_on_commit=False) as cache_db:
        return _analyze(cache_db, context=context, task=task, profile=profile,
                        prompt_version=prompt_version, messages=messages,
                        response_model=response_model, item_id=item_id, validate=validate)


def _analyze(db, *, context, task, profile, prompt_version, messages, response_model, item_id, validate):
    response_format = {"type": "text"} if response_model is None else {
        "type": "json_schema", "schema": response_model.model_json_schema(),
    }
    content = {"task": task, "profile": profile, "prompt_version": prompt_version,
               "messages": messages, "response_format": response_format, "item_id": item_id}
    input_hash = digest(content)
    namespace = context.client.namespace
    try:
        routing = context.routing()
    except AIError:
        # Previously validated data remains useful while the gateway is offline.
        cached = db.query(models.AIAnalysis).filter_by(namespace=namespace, input_hash=input_hash, status="completed").order_by(models.AIAnalysis.updated_at.desc()).first()
        if cached:
            return _result(cached, cached=True)
        raise
    body = {"messages": messages, "profile": profile, "response_format": response_format,
            "timeout_ms": config.AI_REQUEST_TIMEOUT_MS,
            "metadata": {"application": "StackSignal", "task": task, "prompt_version": prompt_version,
                         "input_hash": input_hash, "gateway_routing_version": routing}}
    key = digest({"namespace": namespace, "body": body})
    record = db.get(models.AIAnalysis, key)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if record:
        if record.status == "completed":
            return _result(record, cached=True)
        # Ambiguous gateway jobs (we don't know if they ran) must never be retried
        # under any key. A "failed" record is different even when it carries a
        # response_id: that only means the gateway *did* return a completed envelope
        # which we then rejected (schema/validate() failure) — a known, non-ambiguous
        # outcome that's safe to retry under this same deterministic key after a
        # cooldown, same as any other definite failure.
        if record.status in ("indeterminate", "pending"):
            raise AIError(record.error_code or "execution_indeterminate")
        if (now - record.updated_at).total_seconds() < 60:
            raise AIError(record.error_code or "gateway_unavailable")
    if context.remaining - context.reserved <= 0:
        raise AIError("run_budget_exhausted")
    request_key = f"stacksignal:{key}"
    # End even the cache read transaction. A crash before persistence can safely
    # repeat this exact request: the gateway receives the same idempotency key.
    db.rollback()
    context.remaining -= 1
    envelope = None
    data = None
    error = None
    try:
        envelope = context.client.generate(body, request_key)
        outputs = envelope["output"]
        expected_type = "json" if response_model else "text"
        if len(outputs) != 1 or not isinstance(outputs[0], dict) or outputs[0].get("type") != expected_type:
            raise AIError("invalid_structured_output", response=envelope)
        if response_model:
            data = response_model.model_validate(outputs[0].get("data"), strict=True).model_dump()
        else:
            data = outputs[0].get("text")
            if not isinstance(data, str) or not data.strip():
                raise AIError("invalid_provider_response", response=envelope)
        if validate:
            validate(data)
    except (AIError, ValidationError, ValueError, TypeError, KeyError) as exc:
        error = exc if isinstance(exc, AIError) else AIError("invalid_structured_output", response=envelope)
        # Only trip the run-wide circuit breaker when we never got a response at all
        # (envelope is None: client.generate() itself raised — a transport/timeout
        # problem that will affect every other item too). A response that came back
        # fine but failed our own schema/validate() check says nothing about whether
        # the gateway is reachable for the *next* item — treating it as "unavailable"
        # would silently downgrade the rest of the run to heuristic mode over one
        # malformed reply.
        if envelope is None:
            context.unavailable = True

    # Re-read after the network call: another caller may already have persisted
    # the same idempotent request. All cache writes live in this short transaction.
    record = db.get(models.AIAnalysis, key, populate_existing=True)
    if record is None:
        record = models.AIAnalysis(cache_key=key, namespace=namespace, input_hash=input_hash, item_id=item_id,
                                   task=task, profile=profile, prompt_version=prompt_version, routing_version=routing,
                                   request_key=request_key)
        db.add(record)
    if record.status != "completed":
        record.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        record.raw_response = (error.response or envelope) if error else envelope
        if isinstance(record.raw_response, dict):
            record.response_id = record.raw_response.get("id")
            record.provider = record.raw_response.get("provider")
            record.model = record.raw_response.get("model")
        record.result = None if error else data
        record.status = ("indeterminate" if error.indeterminate else "failed") if error else "completed"
        record.error_code = error.code if error else ""
    db.commit()
    if error:
        raise error
    return _result(record, cached=False)


_RU_RULE = """

LANGUAGE: write every human-readable text field ({fields}) in Russian. Keep in English, exactly as \
written: names of products, libraries, models, repositories, companies, programming languages, \
acronyms and established technical terms (e.g. fine-tuning, inference, embeddings, quantization, \
pipeline, open-source, benchmark, latency, API, GPU, LLM), plus code and identifiers. Do not \
transliterate them and do not invent Russian equivalents for terms engineers say in English. \
Everything that is an identifier stays English and unchanged: JSON keys, project slugs, item ids, \
and capability names. Scores and meaning must not change because of the language."""


def with_language(system, fields):
    """Append the reading-language rule to a system prompt (a no-op when AI_TEXT_LANG=en)."""
    return system + _RU_RULE.format(fields=fields) if config.AI_TEXT_LANG == "ru" else system


def lang_version(version):
    """Cache key part: analyses written in another language are different cache entries."""
    return f"{version}-{config.AI_TEXT_LANG}"


def messages_for(system, payload):
    import json
    return [{"role": "system", "content": [{"type": "text", "text": system}]},
            {"role": "user", "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False, sort_keys=True)}]}]
