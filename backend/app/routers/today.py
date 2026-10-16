"""The home screen in one request: what is new for your projects, what waits for your decision, the
briefings to read and what changed in what you already use -- plus whether the last run and the AI
actually worked, so a dead gateway cannot hide behind a normal-looking item count again."""

import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import decisions as decisions_module
from .. import contexts, models, release_watch, revisit, stack_watch
from ..config import LLM_ENABLED
from ..db import get_db
from ..serializers import load_item_context, ser_item, ser_opportunity, ser_run
from ..utils import utcnow

router = APIRouter()

FRESH_MIN_RELEVANCE = 0.6  # the digest's default bar: only what an AI analysis clearly tied to a project
INBOX_PREVIEW = 3


def _fresh(db: Session, days: int, limit: int) -> list[dict]:
    """Items first seen in the last `days` days that an AI analysis found strongly relevant to a
    project you still attend to, and that you have not decided on for that project yet."""
    cutoff = utcnow() - datetime.timedelta(days=days)
    modes = {s.item_id: s.provenance.get("mode") for s in db.query(models.ItemAnalysisState).all()}
    ai_exists = "ai" in modes.values()
    decided = {(d.item_id, d.project_id) for d in db.query(models.ItemProjectDecision).all()}
    rows = (db.query(models.ItemProjectRelevance).join(models.Item).join(models.Project)
            .filter(models.ItemProjectRelevance.score >= FRESH_MIN_RELEVANCE,
                    models.Item.first_seen_at >= cutoff, models.Item.archived.is_(False),
                    models.Item.status != models.Status.IGNORE, contexts.attended_filter())
            .all())
    best: dict[int, models.ItemProjectRelevance] = {}
    for row in rows:
        if (ai_exists and modes.get(row.item_id) != "ai") or (row.item_id, row.project_id) in decided:
            continue
        if row.item_id not in best or row.score > best[row.item_id].score:
            best[row.item_id] = row
    top = sorted(best.values(), key=lambda r: (r.score, r.item.momentum_score), reverse=True)[:limit]
    context = load_item_context(db, [row.item for row in top])
    out = []
    for row in top:
        entry = ser_item(row.item, context=context)
        entry["matched_relevance"] = {"project_slug": row.project.slug, "project_name": row.project.name,
                                      "score": row.score, "rationale": row.rationale}
        out.append(entry)
    return out


def _inbox(db: Session) -> list[dict]:
    out = []
    for project in db.query(models.Project).filter(contexts.attended_filter()).order_by(models.Project.name):
        rows = decisions_module.undecided_for(db, project)
        if not rows:
            continue
        out.append({"project_slug": project.slug, "project_name": project.name, "tier": project.tier,
                    "count": len(rows), "capped": len(rows) >= decisions_module.NEW_SUGGESTION_LIMIT,
                    "top": [{"id": r.item.id, "name": r.item.name, "source": r.item.source, "score": r.score,
                             "rationale": r.rationale} for r in rows[:INBOX_PREVIEW]]})
    return out


def _health(db: Session) -> dict:
    last = db.query(models.CollectionRun).order_by(models.CollectionRun.started_at.desc()).first()
    enrich = next((s for s in (last.stages or []) if s.get("name") == "enrich"), None) if last else None
    ai_read = enrich.get("ai") if enrich else None
    # The AI read nothing although there was something to read (keyword fallbacks) or its calls failed.
    ai_silent = bool(LLM_ENABLED and enrich and ai_read == 0
                     and (enrich.get("heuristic", 0) > 0 or enrich.get("ai_errors", 0) > 0))
    return {"llm_enabled": LLM_ENABLED, "last_run": ser_run(last) if last else None,
            "ai_read_last_run": ai_read, "ai_silent": ai_silent}


@router.get("/api/today")
def today(days: int = 7, limit: int = 12, db: Session = Depends(get_db)):
    days = max(1, min(days, 60))
    limit = max(1, min(limit, 40))
    attended_slugs = db.query(models.Project.slug).filter(contexts.attended_filter())
    opportunities = (db.query(models.Opportunity).filter(models.Opportunity.state == "active",
                     (models.Opportunity.project_slug == "") | models.Opportunity.project_slug.in_(attended_slugs))
                     .order_by(models.Opportunity.last_seen_at.desc(), models.Opportunity.id.desc()).limit(5).all())
    wanted = {i for o in opportunities for i in (o.item_ids or [])}
    evidence = {i.id: i for i in db.query(models.Item).filter(models.Item.id.in_(wanted)).all()} if wanted else {}
    return {
        "health": _health(db),
        "fresh": _fresh(db, days, limit),
        "inbox": _inbox(db),
        "opportunities": [ser_opportunity(o, evidence) for o in opportunities],
        "changes": {
            "stack_updates": stack_watch.updates(db)[:10],
            "releases": release_watch.recent(db, days=14, include_prereleases=False, limit=8),
            "revisit": revisit.overview(db, only_notable=True)[:6],
        },
    }
