import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import config, models
from ..ai_client import AIClient, AIError
from ..config import LLM_ENABLED
from ..db import get_db

router = APIRouter()


def gateway_state() -> dict | None:
    """None when no AI is configured; otherwise whether the gateway's worker can take a job now."""
    if not LLM_ENABLED:
        return None
    try:
        state = AIClient(config.AI_BASE_URL, config.AI_API_KEY, config.AI_TIMEOUT).readiness()
        dispatch = state.get("dispatch") or {}
        if state["ready"] and dispatch.get("next_allowed_at", 0) > time.time() * 1000:
            state.update(ready=False, code="local_rate_limited")
        return state
    except AIError as error:
        return {"ready": False, "code": error.code}


@router.get("/api/health")
def health():
    return {"status": "ok", "llm_enabled": LLM_ENABLED, "ai_backend": "gateway" if LLM_ENABLED else "heuristic",
            "ai_gateway": gateway_state()}


@router.get("/api/stats")
def stats(db: Session = Depends(get_db)):
    """Small facts the dashboard header and Guide show: how much of the database the AI has seen."""
    ai = sum(1 for (provenance,) in db.query(models.ItemAnalysisState.provenance)
             .join(models.Item).filter(models.Item.archived.is_(False)).all()
             if provenance.get("mode") == "ai")
    last = db.query(models.CollectionRun).order_by(models.CollectionRun.started_at.desc()).first()
    last_ai = db.query(models.AIAnalysis).order_by(models.AIAnalysis.updated_at.desc()).first()
    votes = db.query(models.ItemFeedback).filter_by(kind="vote").count()
    return {
        "items": db.query(models.Item).filter(models.Item.archived.is_(False)).count(), "ai_analyzed": ai,
        "archived": db.query(models.Item).filter(models.Item.archived.is_(True)).count(),
        "projects": db.query(models.Project).count(), "capabilities": db.query(models.Capability).count(),
        "votes": votes, "notes": db.query(models.ItemFeedback).filter_by(kind="note").count(),
        "ai_enabled": LLM_ENABLED,
        "last_ai_attempt": None if last_ai is None else {"task": last_ai.task, "status": last_ai.status,
            "error_code": last_ai.error_code, "at": last_ai.updated_at.isoformat()},
        "last_run": None if last is None else {
            "started_at": last.started_at.isoformat(),
            "finished_at": last.finished_at.isoformat() if last.finished_at else None,
            "items_found": last.items_found, "items_new": last.items_new},
    }
