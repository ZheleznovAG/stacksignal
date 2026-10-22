from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from .. import experiments, models
from ..db import get_db

router = APIRouter()


class ExperimentIn(BaseModel):
    item_id: int = Field(gt=0)


class ExperimentUpdate(BaseModel):
    status: Literal["running", "completed", "cancelled"]
    outcome: Literal["", "useful", "not_useful", "inconclusive"] = ""
    result_note: str = Field(default="", max_length=6000)
    evidence: list[str] = Field(default_factory=list, max_length=12)
    confirm_need: bool = False
    decision: Literal["adopt", "ignore", "watch", "hold"] | None = None


def _get(db, model, identity):
    row = db.get(model, identity)
    if row is None:
        raise HTTPException(404, "Запись не найдена.")
    return row


@router.get("/api/experiments")
def project_experiments(project_id: int, db: Session = Depends(get_db)):
    _get(db, models.Project, project_id)
    rows = (db.query(models.NeedExperiment).join(models.ContextNeed)
            .filter(models.ContextNeed.project_id == project_id).order_by(models.NeedExperiment.id.desc()))
    return [{key: value for key, value in experiments.serialise(db, row).items()
             if key not in ("brief", "snapshot")} | {"candidate_name": row.snapshot["candidate"]["name"]}
            for row in rows]


@router.get("/api/needs/{link_id}/experiments")
def list_experiments(link_id: int, db: Session = Depends(get_db)):
    _get(db, models.ContextNeed, link_id)
    return [experiments.serialise(db, row) for row in db.query(models.NeedExperiment)
            .filter_by(context_need_id=link_id).order_by(models.NeedExperiment.id.desc())]


@router.post("/api/needs/{link_id}/experiments")
def prepare_experiment(link_id: int, body: ExperimentIn, db: Session = Depends(get_db)):
    link = _get(db, models.ContextNeed, link_id)
    item = _get(db, models.Item, body.item_id)
    try:
        row = experiments.create(db, link, item)
        db.commit()
    except experiments.ExperimentError as error:
        db.rollback()
        raise HTTPException(400, str(error))
    return experiments.serialise(db, row)


@router.get("/api/experiments/{experiment_id}")
def get_experiment(experiment_id: int, db: Session = Depends(get_db)):
    return experiments.serialise(db, _get(db, models.NeedExperiment, experiment_id))


@router.get("/api/experiments/{experiment_id}/brief")
def download_brief(experiment_id: int, db: Session = Depends(get_db)):
    row = _get(db, models.NeedExperiment, experiment_id)
    return Response(row.brief, media_type="text/markdown", headers={
        "Content-Disposition": f'attachment; filename="experiment-{row.id}.md"', "Cache-Control": "no-store"})


@router.put("/api/experiments/{experiment_id}")
def update_experiment(experiment_id: int, body: ExperimentUpdate, db: Session = Depends(get_db)):
    row = _get(db, models.NeedExperiment, experiment_id)
    if any(not value.strip() or len(value) > 1500 for value in body.evidence):
        raise HTTPException(400, "Свидетельство проверки должно содержать от 1 до 1500 символов.")
    try:
        experiments.update(db, row, **body.model_dump())
        db.commit()
    except experiments.ExperimentError as error:
        db.rollback()
        raise HTTPException(400, str(error))
    except StaleDataError:
        db.rollback()
        raise HTTPException(409, "Проверка уже изменена в другом окне. Обновите страницу.")
    return experiments.serialise(db, row)
