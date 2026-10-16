from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from .. import queries as queries_module
from ..ai_analysis import AIContext
from ..db import get_db
from ..serializers import ser_query

router = APIRouter()


def _serialize(db: Session, rows: list[models.SearchQuery]) -> list[dict]:
    projects = db.query(models.Project).all()
    names = {p.slug: p.name for p in projects}
    fingerprints = {p.slug: queries_module.query_profile_fingerprint(p) for p in projects}
    return [ser_query(q, names, fingerprints) for q in rows]


@router.get("/api/queries")
def list_queries(db: Session = Depends(get_db)):
    rows = db.query(models.SearchQuery).order_by(models.SearchQuery.project_slug, models.SearchQuery.source,
                                                 models.SearchQuery.id).all()
    return _serialize(db, rows)


@router.post("/api/queries")
def add_query(body: schemas.QueryIn, db: Session = Depends(get_db)):
    if db.query(models.Project).filter_by(slug=body.project_slug).one_or_none() is None:
        raise HTTPException(400, "Unknown project")
    if body.source not in ("github", "huggingface", "arxiv", "npm"):
        raise HTTPException(400, "source must be github, huggingface, arxiv or npm")
    if queries_module.store(db, body.project_slug, body.source, [body.text], "user") == 0:
        raise HTTPException(400, "Empty, too short, or already saved for this project")
    db.commit()
    created = (db.query(models.SearchQuery).filter_by(project_slug=body.project_slug, source=body.source)
               .order_by(models.SearchQuery.id.desc()).first())
    return _serialize(db, [created])[0]


@router.put("/api/queries/{query_id}")
def toggle_query(query_id: int, body: schemas.QueryPatch, db: Session = Depends(get_db)):
    query = db.get(models.SearchQuery, query_id)
    if query is None:
        raise HTTPException(404, "Query not found")
    query.enabled = body.enabled
    db.commit()
    return _serialize(db, [query])[0]


@router.delete("/api/queries/{query_id}")
def delete_query(query_id: int, db: Session = Depends(get_db)):
    query = db.get(models.SearchQuery, query_id)
    if query is None:
        raise HTTPException(404, "Query not found")
    db.delete(query)
    db.commit()
    return {"ok": True}


@router.post("/api/queries/regenerate")
def regenerate_queries(body: schemas.RegenerateIn, db: Session = Depends(get_db)):
    """Rebuild the generated queries (yours are kept): heuristics first, then one AI call."""
    projects = db.query(models.Project).all()
    if body.project_slug is not None:
        projects = [p for p in projects if p.slug == body.project_slug]
        if not projects:
            raise HTTPException(404, "Project not found")
    context = AIContext(max_calls=1) if body.ai else None
    result = queries_module.regenerate(db, projects, context=context)
    db.commit()
    return result


@router.get("/api/coverage")
def coverage(db: Session = Depends(get_db)):
    """Per project: how well collection is serving it (items, searches, recent finds)."""
    out = []
    for project in db.query(models.Project).order_by(models.Project.name).all():
        relevant = (db.query(models.ItemProjectRelevance).join(models.Item)
                    .filter(models.ItemProjectRelevance.project_id == project.id,
                            models.ItemProjectRelevance.score >= 0.6, models.Item.archived.is_(False)).count())
        found = (db.query(models.ItemDiscovery).join(models.Item)
                 .filter(models.ItemDiscovery.project_slug == project.slug, models.Item.archived.is_(False)).count())
        searches = db.query(models.SearchQuery).filter_by(project_slug=project.slug).all()
        out.append({"slug": project.slug, "name": project.name, "relevant": relevant, "found_by_search": found,
                    "queries": sum(1 for q in searches if q.enabled), "last_new": sum(q.last_new for q in searches)})
    return out
