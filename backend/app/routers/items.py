from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import decisions as decisions_module
from .. import feedback as feedback_module
from .. import authors, intake, models, schemas
from ..db import get_db
from ..serializers import ser_item
from ..utils import utcnow

router = APIRouter()


@router.get("/api/items/{item_id}")
def get_item(item_id: int, db: Session = Depends(get_db)):
    item = db.get(models.Item, item_id)
    if item is None:
        raise HTTPException(404, "Item not found")
    return ser_item(item, include_history=True)


@router.post("/api/items/{item_id}/status")
def set_status(item_id: int, body: schemas.StatusIn, db: Session = Depends(get_db)):
    item = db.get(models.Item, item_id)
    if item is None:
        raise HTTPException(404, "Item not found")
    try:
        status = models.Status(body.status)
    except ValueError:
        raise HTTPException(400, f"Invalid status: {body.status}")
    item.status = status
    item.status_updated_at = utcnow()
    db.add(models.StatusHistory(item_id=item.id, status=status, note=body.note))
    db.commit()
    return ser_item(item, include_history=True)


@router.put("/api/items/{item_id}/decisions/{project_slug}")
def put_decision(item_id: int, project_slug: str, body: schemas.DecisionIn, db: Session = Depends(get_db)):
    """Your decision about this item for one project: status + what to try + what came out of it."""
    item = db.get(models.Item, item_id)
    project = db.query(models.Project).filter_by(slug=project_slug).one_or_none()
    if item is None or project is None:
        raise HTTPException(404, "Item or project not found")
    try:
        decisions_module.set_decision(db, item, project, body.status, goal=body.goal, verdict=body.verdict,
                                      criteria=body.criteria, revisit_when=body.revisit_when)
    except decisions_module.DecisionError as error:
        raise HTTPException(400, str(error))
    db.commit()
    return ser_item(item, include_history=True)


@router.delete("/api/items/{item_id}/decisions/{project_slug}")
def delete_decision(item_id: int, project_slug: str, db: Session = Depends(get_db)):
    item = db.get(models.Item, item_id)
    project = db.query(models.Project).filter_by(slug=project_slug).one_or_none()
    if item is None or project is None:
        raise HTTPException(404, "Item or project not found")
    decisions_module.clear_decision(db, item, project)
    db.commit()
    return ser_item(item, include_history=True)


@router.post("/api/items/{item_id}/feedback")
def add_feedback(item_id: int, body: schemas.FeedbackIn, db: Session = Depends(get_db)):
    """A vote (project_slug + verdict of 1, -1 or 0 to clear) or a note — exactly one of them."""
    item = db.get(models.Item, item_id)
    if item is None:
        raise HTTPException(404, "Item not found")
    is_note = body.note is not None
    is_vote = body.project_slug is not None or body.verdict is not None
    if is_note == is_vote:
        raise HTTPException(400, "Send either a note, or a project_slug with a verdict")
    try:
        if is_note:
            feedback_module.record_note(db, item, body.note)
        else:
            if body.project_slug is None or body.verdict is None:
                raise HTTPException(400, "A vote needs both project_slug and verdict")
            feedback_module.record_vote(db, item, body.project_slug, body.verdict)
    except ValueError as error:
        db.rollback()
        raise HTTPException(400, str(error))
    db.commit()
    return ser_item(item, include_history=True)


@router.post("/api/items/add-url")
def add_item_by_url(body: schemas.AddUrlIn, db: Session = Depends(get_db)):
    """Put a GitHub repository or Hugging Face model you found yourself on the radar (as Watch).
    A GitHub profile URL (github.com/<login>) watches that author instead."""
    login = authors.parse_author_url(body.url)
    if login:
        try:
            author, created = authors.add_author(db, login)
        except LookupError:
            raise HTTPException(404, "not_found")
        except Exception:  # rate limit, network failure, unexpected answer
            db.rollback()
            raise HTTPException(502, "fetch_failed")
        db.commit()
        return {"kind": "author", **authors.serialize(author), "created": created}
    try:
        item, created = intake.add_url(db, body.url, analyze=body.analyze)
    except intake.IntakeError as error:
        raise HTTPException(404 if error.code == "not_found" else 502 if error.code == "fetch_failed" else 400,
                            error.code)
    db.commit()
    return {"kind": "item", **ser_item(item, include_history=True), "created": created}
