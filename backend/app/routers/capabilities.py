from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import capabilities as capabilities_module
from ..ai_analysis import AIContext
from ..ai_client import AIError
from ..db import get_db

router = APIRouter()


@router.get("/api/capabilities")
def list_capabilities(q: str | None = None, min_items: int = 1, db: Session = Depends(get_db)):
    return capabilities_module.overview(db, query=q, min_items=min_items)


@router.post("/api/capabilities/consolidate")
def consolidate_capabilities(db: Session = Depends(get_db)):
    """Ask the gateway to merge synonym capabilities (one call, ~20-40s)."""
    try:
        stats = capabilities_module.consolidate(db, context=AIContext(max_calls=1))
        db.commit()
    except AIError as error:
        db.commit()  # keep the attempt record
        raise HTTPException(503, f"Consolidation unavailable: {error.code}")
    return stats
