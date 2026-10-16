from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import decisions as decisions_module
from .. import models, needs, preference_learning
from ..db import get_db
from ..serializers import load_item_context, ser_item, ser_items

router = APIRouter()


@router.get("/api/radar/preference")
def preference_pilot(project_slug: str, limit: int = 8, db: Session = Depends(get_db)):
    """A small, read-only training queue for one project.

    The queue contains only already-relevant, still-undecided and *unjudged* items. Voting on these
    candidates through the existing feedback endpoint supplies balanced manual evidence; once enough
    positive and negative embedded examples exist, the learned profile starts weakly reranking future
    candidates. No labels are created by this endpoint.
    """
    project = db.query(models.Project).filter_by(slug=project_slug).one_or_none()
    if project is None:
        raise HTTPException(404, "Project not found")
    limit = max(1, min(limit, 24))
    stats = preference_learning.profile_stats(db, [project.id])
    judged = set(preference_learning.project_judgments(db, [project.id]))
    rows = [row for row in decisions_module.undecided_for(db, project) if row.item_id not in judged][:limit]
    learned = preference_learning.preference_scores(db, [project.id], [row.item_id for row in rows])
    need_context = needs.item_need_context(db, project, [row.item for row in rows])
    context = load_item_context(db, [row.item for row in rows])
    candidates = []
    for row in rows:
        entry = ser_item(row.item, context=context)
        entry["matched_relevance"] = {"project_slug": project.slug, "project_name": project.name,
                                      "score": row.score, "rationale": row.rationale}
        if row.item_id in need_context:
            entry["matched_need"] = need_context[row.item_id]
        if row.item_id in learned:
            entry["learning_affinity"] = round(learned[row.item_id], 4)
        candidates.append(entry)
    return {
        "project_slug": project.slug,
        "project_name": project.name,
        "profile": stats,
        "candidates": candidates,
    }


@router.get("/api/radar")
def radar(project_slug: str | None = None, per_status: int | None = None, db: Session = Depends(get_db)):
    """Without a project: the overall radar (global statuses). With one: that project's radar —
    your decisions for it, and in "new" the strongly relevant items you have not decided on yet.

    per_status (overall radar only) returns {"groups", "counts"} with at most that many items per
    status: serializing every live item took ~15 s on a real catalogue to show 25 per column."""
    if per_status is not None and not project_slug:
        limit = max(1, min(per_status, 200))
        live = db.query(models.Item).filter(models.Item.archived.is_(False))
        counts = dict(live.with_entities(models.Item.status, func.count(models.Item.id)).group_by(models.Item.status).all())
        picked = {s.value: live.filter(models.Item.status == s).order_by(models.Item.status_updated_at.desc())
                  .limit(limit).all() for s in models.Status}
        context = load_item_context(db, [i for items in picked.values() for i in items])
        return {
            "groups": {status: [ser_item(i, context=context) for i in items] for status, items in picked.items()},
            "counts": {s.value: counts.get(s, 0) for s in models.Status},
        }
    groups: dict[str, list] = {s.value: [] for s in models.Status}
    if project_slug:
        project = db.query(models.Project).filter_by(slug=project_slug).one_or_none()
        if project is None:
            raise HTTPException(404, "Project not found")
        rows = (db.query(models.ItemProjectDecision).join(models.Item)
                .filter(models.ItemProjectDecision.project_id == project.id, models.Item.archived.is_(False))
                .order_by(models.ItemProjectDecision.updated_at.desc()).all())
        undecided = decisions_module.undecided_for(db, project)
        need_context = needs.item_need_context(db, project, [r.item for r in undecided])
        context = load_item_context(db, [d.item for d in rows] + [r.item for r in undecided])
        for decision in rows:
            entry = ser_item(decision.item, context=context)
            entry["decision"] = decisions_module.serialise(decision)
            groups[decision.status].append(entry)
        for rel in undecided:
            entry = ser_item(rel.item, context=context)
            entry["matched_relevance"] = {"project_slug": project.slug, "project_name": project.name,
                                          "score": rel.score, "rationale": rel.rationale}
            if rel.item_id in need_context:
                entry["matched_need"] = need_context[rel.item_id]
            groups["new"].append(entry)
        return groups
    for entry in ser_items(db, db.query(models.Item).filter(models.Item.archived.is_(False))
                           .order_by(models.Item.status_updated_at.desc()).all()):
        groups[entry["status"]].append(entry)
    return groups
