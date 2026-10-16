"""Decisions per project: what *you* decided about an item for one project.

An item has one global status (a general stance, which the AI may suggest up to Assess) but the
question that matters is "for this project": a new local model can be Trial for one project and
Rejected for another. A decision is (item, project) -> status + goal (what to try) + verdict (what
came out of it, or why not). Every change is written to the status history with the project, so
"why did we drop it?" can be answered months later.
"""

from sqlalchemy.orm import Session

from . import models, needs, preference_learning
from .utils import utcnow

# What the user sees: watch -> assess -> trial (trying) -> adopt (using) / ignore (rejected) / hold (paused).
STATUSES = [s for s in models.Status if s is not models.Status.NEW]
NEW_SUGGESTION_MIN_RELEVANCE = 0.6
NEW_SUGGESTION_LIMIT = 25


class DecisionError(ValueError):
    pass


def _status(value: str) -> models.Status:
    try:
        status = models.Status(value)
    except ValueError:
        raise DecisionError(f"Invalid status: {value}")
    if status is models.Status.NEW:
        raise DecisionError("A decision needs a real status; clear it to go back to undecided")
    return status


def get(db: Session, item_id: int, project_id: int) -> models.ItemProjectDecision | None:
    return db.query(models.ItemProjectDecision).filter_by(item_id=item_id, project_id=project_id).one_or_none()


def set_decision(db: Session, item: models.Item, project: models.Project, status: str, *,
                 goal: str | None = None, verdict: str | None = None, criteria: str | None = None,
                 revisit_when: str | None = None) -> models.ItemProjectDecision:
    """Create or change the decision. Fields left as None keep what was there. When the status changes, the
    item is snapshotted so "what changed since you decided" has something to compare against."""
    from . import revisit
    new_status = _status(status)
    now = utcnow()
    decision = get(db, item.id, project.id)
    if decision is None:
        decision = models.ItemProjectDecision(item_id=item.id, project_id=project.id, status=new_status,
                                              goal=(goal or "").strip(), verdict=(verdict or "").strip(),
                                              criteria=(criteria or "").strip(), revisit_when=(revisit_when or "").strip(),
                                              decided_at=now, snapshot=revisit.snapshot(item))
        db.add(decision)
    else:
        if decision.status != new_status:
            decision.decided_at, decision.snapshot = now, revisit.snapshot(item)
        decision.status = new_status
        for field, value in (("goal", goal), ("verdict", verdict), ("criteria", criteria), ("revisit_when", revisit_when)):
            if value is not None:
                setattr(decision, field, value.strip())
        decision.updated_at = now
    note = " — ".join(part for part in (decision.goal and f"goal: {decision.goal}",
                                        decision.criteria and f"success: {decision.criteria}",
                                        decision.verdict and f"verdict: {decision.verdict}",
                                        decision.revisit_when and f"revisit when: {decision.revisit_when}") if part)
    db.add(models.StatusHistory(item_id=item.id, project_id=project.id, status=new_status, note=note))
    db.flush()
    return decision


def clear_decision(db: Session, item: models.Item, project: models.Project) -> bool:
    decision = get(db, item.id, project.id)
    if decision is None:
        return False
    db.delete(decision)
    db.add(models.StatusHistory(item_id=item.id, project_id=project.id, status=models.Status.NEW,
                                note="Decision cleared"))
    db.flush()
    return True


EXAMPLES_PER_SIDE = 6
_POSITIVE = {"adopt": "adopted", "trial": "trying"}
_NEGATIVE = {"ignore": "rejected", "hold": "paused"}


def examples(db: Session) -> list[dict]:
    """Your decisions as calibration for the AI: what you adopted or are trying, and what you rejected or
    paused — with the reason you wrote down. Ones with a written reason come first, then the newest.
    This is how "rejected: too slow on Android" stops similar items from being pitched again."""
    rows = db.query(models.ItemProjectDecision).order_by(models.ItemProjectDecision.updated_at.desc()).all()
    out: list[dict] = []
    for labels in (_POSITIVE, _NEGATIVE):
        chosen = sorted((d for d in rows if d.status in labels), key=lambda d: not (d.verdict or d.goal))
        for decision in chosen[:EXAMPLES_PER_SIDE]:
            item = decision.item
            entry = {"project": decision.project.slug, "verdict": labels[decision.status], "item": item.name,
                     "summary": (item.llm_summary or item.description or "")[:140]}
            reason = decision.verdict or decision.goal
            if reason:
                entry["note"] = reason[:200]
            if decision.revisit_when:
                entry["revisit_when"] = decision.revisit_when[:160]
            out.append(entry)
    return out


def serialise(decision: models.ItemProjectDecision) -> dict:
    return {"project_slug": decision.project.slug, "project_name": decision.project.name,
            "status": decision.status, "goal": decision.goal, "verdict": decision.verdict,
            "criteria": decision.criteria or "", "revisit_when": decision.revisit_when or "",
            "decided_at": (decision.decided_at or decision.updated_at).isoformat(),
            "updated_at": decision.updated_at.isoformat()}


def undecided_for(db: Session, project: models.Project) -> list[models.ItemProjectRelevance]:
    """Strongly relevant items nobody has decided on for this project yet — the project's inbox.
    Once the AI has read anything, only AI-read items count: a keyword overlap is not a recommendation."""
    decided = db.query(models.ItemProjectDecision.item_id).filter_by(project_id=project.id)
    rows = (db.query(models.ItemProjectRelevance)
            .join(models.Item)
            .filter(models.ItemProjectRelevance.project_id == project.id,
                    models.ItemProjectRelevance.score >= NEW_SUGGESTION_MIN_RELEVANCE,
                    models.Item.archived.is_(False),
                    ~models.ItemProjectRelevance.item_id.in_(decided))
            .order_by(models.ItemProjectRelevance.score.desc(), models.Item.momentum_score.desc())
            .all())
    modes = {s.item_id: s.provenance.get("mode") for s in db.query(models.ItemAnalysisState).all()}
    if "ai" in modes.values():
        rows = [r for r in rows if modes.get(r.item_id) == "ai"]

    # Project understanding is primary personalization: explicit/inferred high-priority needs can gently move an
    # already-relevant candidate up. Manual preference is only a smaller calibration signal on top of that.
    relevance_order = [row.item_id for row in rows]
    need_context = needs.item_need_context(db, project, [row.item for row in rows])
    learned = preference_learning.preference_scores(db, [project.id], relevance_order)
    rows.sort(key=lambda row: (
        row.score + 0.08 * need_context.get(row.item_id, {}).get("score", 0.0)
        + 0.025 * learned.get(row.item_id, 0.0),
        need_context.get(row.item_id, {}).get("effective_priority", 0),
        row.item.momentum_score,
    ), reverse=True)
    return rows[:NEW_SUGGESTION_LIMIT]
