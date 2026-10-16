from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db
from ..pipeline import CollectionBusy, run_collection
from ..serializers import ser_run

router = APIRouter()


@router.post("/api/collect/run")
def collect_run(body: schemas.CollectIn, db: Session = Depends(get_db)):
    try:
        run = run_collection(db, sources=body.sources)
    except CollectionBusy:
        raise HTTPException(status_code=409, detail="collection_already_running")
    return ser_run(run)


@router.get("/api/collect/runs")
def collect_runs(limit: int = 10, db: Session = Depends(get_db)):
    runs = db.query(models.CollectionRun).order_by(models.CollectionRun.started_at.desc()).limit(limit).all()
    return [ser_run(r) for r in runs]
