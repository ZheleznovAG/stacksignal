from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from .. import contexts, models
from ..db import get_db
from ..enrichment import content_fingerprint, project_fingerprint
from ..pipeline import _fresh_scoped_relevance
from ..serializers import load_item_context, ser_item

router = APIRouter()


@router.get("/api/relevant")
def relevant(project_slug: str | None = None, limit: int = 40, ai_only: bool = False, db: Session = Depends(get_db)):
    limit = max(1, min(limit, 200))
    q = db.query(models.ItemProjectRelevance).join(models.Item)
    if project_slug:
        q = q.join(models.Project).filter(models.Project.slug == project_slug)
    q = (q.filter(models.ItemProjectRelevance.score > 0.1, models.Item.archived.is_(False))
         .order_by(models.ItemProjectRelevance.score.desc()))

    rels = q.limit(limit * (12 if ai_only else 3)).all()
    context = load_item_context(db, [r.item for r in rels])
    ids = list({r.item_id for r in rels})
    provenance = {}
    for start in range(0, len(ids), 500):
        provenance.update({s.item_id: s.provenance or {} for s in db.query(models.ItemAnalysisState).filter(
            models.ItemAnalysisState.item_id.in_(ids[start:start + 500]))})
    fingerprints, project_fps = {}, {}
    seen = set()
    out = []
    for rel in rels:
        if rel.item_id in seen:
            continue
        if rel.item_id not in fingerprints:
            fingerprints[rel.item_id] = content_fingerprint(rel.item)
        if rel.project.slug not in project_fps:
            project_fps[rel.project.slug] = project_fingerprint(rel.project)
        scoped = (contexts.analysis_allowed(rel.project) and rel.project.slug in _fresh_scoped_relevance(
            provenance.get(rel.item_id, {}), fingerprints[rel.item_id],
            {rel.project.slug: project_fps[rel.project.slug]}))
        if ai_only and context.modes.get(rel.item_id) != "ai" and not scoped:
            continue
        seen.add(rel.item_id)
        entry = ser_item(rel.item, context=context)
        entry["matched_relevance"] = {"project_slug": rel.project.slug, "project_name": rel.project.name,
                                      "score": rel.score, "rationale": rel.rationale}
        if scoped:
            source = provenance[rel.item_id]["relevance_provenance"][rel.project.slug]
            entry["matched_relevance"]["analysis"] = {"mode": "ai", "scope": "project_relevance",
                **{key: source.get(key, "") for key in ("provider", "model", "updated_at")}}
        out.append(entry)
        if len(out) >= limit:
            break
    return out
