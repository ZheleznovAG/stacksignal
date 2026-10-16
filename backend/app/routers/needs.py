from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models, needs, need_analysis
from ..db import get_db
from ..utils import utcnow

router = APIRouter()


@router.get("/api/needs/plan")
def need_plan(db: Session = Depends(get_db)):
    return {k: v for k, v in need_analysis.plan(db).items() if k != "batches"}


@router.post("/api/needs/analyze")
def analyze_needs(db: Session = Depends(get_db)):
    return need_analysis.run(db, max_batches=1)


class NeedIn(BaseModel):
    title: str = Field(min_length=1, max_length=600)
    need_id: int | None = None
    priority: int = Field(default=2, ge=1, le=3)
    constraints: str = Field(default="", max_length=2000)
    acceptance_criteria: str = Field(default="", max_length=2000)
    search_terms: list[str] = Field(default_factory=list, max_length=12)
    pilot: bool = False


class NeedUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=600)
    state: Literal["open", "evaluating", "satisfied", "deferred", "dropped"] | None = None
    priority: int | None = Field(default=None, ge=1, le=3)
    constraints: str | None = Field(default=None, max_length=2000)
    acceptance_criteria: str | None = Field(default=None, max_length=2000)
    search_terms: list[str] | None = Field(default=None, max_length=12)
    pilot: bool | None = None
    result_note: str | None = Field(default=None, max_length=3000)
    confirmed_item_id: int | None = None


def _pilot_limit(db, project_id, excluding=None):
    query = db.query(models.ContextNeed).filter_by(pilot=True)
    if excluding is not None:
        query = query.filter(models.ContextNeed.id != excluding)
    selected = query.all()
    if sum(l.project_id == project_id for l in selected) >= 3 or len(selected) >= 9:
        raise HTTPException(400, "pilot_limit: at most 3 needs per context and 9 in total")


def _result(db, link):
    return next(row for row in needs.overview(db, link.project_id) if row["id"] == link.id)


@router.get("/api/needs")
def list_needs(project_id: int | None = None, pilot_only: bool = False, db: Session = Depends(get_db)):
    return needs.overview(db, project_id, pilot_only=pilot_only)


@router.post("/api/projects/{project_id}/needs")
def create_need(project_id: int, body: NeedIn, db: Session = Depends(get_db)):
    project = db.get(models.Project, project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    if not body.title.strip() or any(not t.strip() or len(t) > 100 for t in body.search_terms):
        raise HTTPException(400, "Invalid title or search terms")
    if body.pilot:
        _pilot_limit(db, project_id)
    need = db.get(models.Need, body.need_id) if body.need_id is not None else None
    if body.need_id is not None and need is None:
        raise HTTPException(404, "Need not found")
    if need and db.query(models.ContextNeed.id).filter_by(project_id=project_id, need_id=need.id).first():
        raise HTTPException(409, "Need already linked to this context")
    if not need:
        if any(l.need.title.casefold() == body.title.strip().casefold() for l in needs.links(db, project_id)):
            raise HTTPException(409, "Need already exists in this context")
        need = models.Need(title=body.title.strip(), origin="user")
        db.add(need)
        db.flush()
    link = models.ContextNeed(project_id=project_id, need_id=need.id,
        **body.model_dump(exclude={"title", "need_id"}))
    db.add(link)
    db.flush()
    needs.event(db, link, "create", {"title": need.title})
    needs.project_projection(db, project)
    db.commit()
    return _result(db, link)


@router.put("/api/needs/{link_id}")
def update_need(link_id: int, body: NeedUpdate, db: Session = Depends(get_db)):
    link = db.get(models.ContextNeed, link_id)
    if not link:
        raise HTTPException(404, "Need not found")
    changes = body.model_dump(exclude_unset=True)
    if any(value is None for key, value in changes.items() if key != "confirmed_item_id"):
        raise HTTPException(400, "Need fields cannot be null")
    if body.search_terms is not None and any(not t.strip() or len(t) > 100 for t in body.search_terms):
        raise HTTPException(400, "Invalid search terms")
    if body.pilot and not link.pilot:
        _pilot_limit(db, link.project_id, link.id)
    if body.confirmed_item_id is not None and db.get(models.Item, body.confirmed_item_id) is None:
        raise HTTPException(404, "Item not found")
    if changes.get("state", link.state) == "satisfied" and not changes.get("result_note", link.result_note).strip():
        raise HTTPException(400, "A confirmed result needs a written outcome")
    before = {key: link.need.title if key == "title" else getattr(link, key) for key in changes}
    if body.title is not None:
        title = body.title.strip()
        if not title:
            raise HTTPException(400, "Invalid title")
        if any(other.id != link.id and other.need.title.casefold() == title.casefold() for other in needs.links(db, link.project_id)):
            raise HTTPException(409, "Need already exists in this context")
        if db.query(models.ContextNeed).filter_by(need_id=link.need_id).count() > 1 and title != link.need.title:
            new_need = models.Need(title=title, origin="user")
            db.add(new_need)
            db.flush()
            link.need_id, link.need = new_need.id, new_need
        else:
            link.need.title = title
    for key, value in changes.items():
        if key != "title":
            setattr(link, key, value)
    if link.state == "satisfied" and (before.get("state") not in (None, "satisfied") or
            any(key in changes and before[key] != changes[key] for key in ("result_note", "confirmed_item_id"))):
        link.result_fingerprint = needs.result_fingerprint(link)
    link.updated_at = utcnow()
    needs.event(db, link, "update", {"before": before, "after": changes})
    needs.project_projection(db, link.project)
    db.commit()
    return _result(db, link)


@router.get("/api/needs/{link_id}/history")
def need_history(link_id: int, db: Session = Depends(get_db)):
    return [{"actor": e.actor, "action": e.action, "detail": e.detail, "created_at": e.created_at.isoformat()}
            for e in db.query(models.NeedEvent).filter_by(context_need_id=link_id).order_by(models.NeedEvent.id.desc())]
