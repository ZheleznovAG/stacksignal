import datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import models
from ..db import get_db
from ..serializers import load_item_context, ser_item, ser_items
from ..utils import utcnow

router = APIRouter()


@router.get("/api/discover")
def discover(
    bucket: str = "fast_growing",
    source: str | None = None,
    limit: int = 40,
    ai_only: bool = False,
    db: Session = Depends(get_db),
):
    limit = max(1, min(limit, 200))
    q = db.query(models.Item).filter(models.Item.archived.is_(False))
    if source:
        q = q.filter(models.Item.source == source)

    if bucket == "new":
        cutoff = utcnow() - datetime.timedelta(days=14)
        q = q.filter(models.Item.first_seen_at >= cutoff).order_by(models.Item.first_seen_at.desc())
    elif bucket == "popular":
        q = q.order_by((models.Item.stars + models.Item.likes + models.Item.downloads / 100).desc())
    elif bucket == "experimental":
        q = q.filter(models.Item.maturity_score < 0.3).order_by(models.Item.momentum_score.desc())
    else:  # fast_growing
        q = q.order_by(models.Item.momentum_score.desc())

    if not ai_only:
        return ser_items(db, q.limit(limit).all())
    candidates = q.limit(limit * 12).all()
    modes = {s.item_id: s.provenance.get("mode") for s in db.query(models.ItemAnalysisState)
             .filter(models.ItemAnalysisState.item_id.in_([i.id for i in candidates]))}
    picked = [i for i in candidates if modes.get(i.id) == "ai"][:limit]
    context = load_item_context(db, picked)
    return [ser_item(i, context=context) for i in picked]
