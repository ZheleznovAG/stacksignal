from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, release_watch, revisit, schemas, stack_watch
from ..db import get_db
from ..serializers import ser_opportunity

router = APIRouter()


@router.get("/api/revisit")
def revisit_overview(notable: bool = False, db: Session = Depends(get_db)):
    """Your rejected, paused, adopted and trial decisions with what changed since you made them."""
    return revisit.overview(db, only_notable=notable)


@router.get("/api/releases")
def releases(days: int = 30, db: Session = Depends(get_db)):
    """New releases of the GitHub repositories you track, newest first."""
    return release_watch.recent(db, days=max(1, min(days, 365)))


@router.get("/api/stack-updates")
def stack_updates(days: int = 30, db: Session = Depends(get_db)):
    """New versions of the packages your (non-dormant) projects depend on, newest first."""
    return stack_watch.updates(db, days=max(1, min(days, 365)))


@router.get("/api/stack-watch")
def stack_dependencies(project_id: int | None = None, db: Session = Depends(get_db)):
    """Declared and resolved project versions alongside the latest registry version."""
    if project_id is not None and db.get(models.Project, project_id) is None:
        raise HTTPException(404, "Project not found")
    return stack_watch.dependency_status(db, project_id=project_id)


@router.get("/api/opportunities")
def opportunities(state: str = "active", db: Session = Depends(get_db)):
    """Persistent opportunities. state: active (default), faded, dismissed, done or all."""
    query = db.query(models.Opportunity)
    if state != "all":
        query = query.filter(models.Opportunity.state == state)
    rows = query.order_by(models.Opportunity.last_seen_at.desc(), models.Opportunity.id.desc()).all()
    wanted = {i for o in rows for i in (o.item_ids or [])}
    items = {i.id: i for i in db.query(models.Item).filter(models.Item.id.in_(wanted)).all()} if wanted else {}
    return [ser_opportunity(o, items) for o in rows]


@router.post("/api/opportunities/{opportunity_id}/state")
def set_opportunity_state(opportunity_id: int, body: schemas.OpportunityStateIn, db: Session = Depends(get_db)):
    """Dismiss it, mark it done, or reopen it. A dismissed or done opportunity stays so even when it comes back."""
    opportunity = db.get(models.Opportunity, opportunity_id)
    if opportunity is None:
        raise HTTPException(404, "Opportunity not found")
    if body.state not in ("active", "dismissed", "done"):
        raise HTTPException(400, "state must be active, dismissed or done")
    opportunity.state = body.state
    db.commit()
    return ser_opportunity(opportunity)
