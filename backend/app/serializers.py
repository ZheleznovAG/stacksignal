"""Turns a model into the dict shape the dashboard and API clients see. Kept apart from the
routers (app/routers/) because most of them need at least one of these."""

from sqlalchemy.orm import Session, object_session, selectinload

from . import decisions as decisions_module
from . import discovery
from . import feedback as feedback_module
from . import contexts, models


def ser_project(p: models.Project) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "slug": p.slug,
        "description": p.description,
        "keywords": p.keywords,
        "tier": p.tier or "active",
        "kind": p.kind,
        "lifecycle_status": p.lifecycle_status,
        "discovery_enabled": p.discovery_enabled,
        "discovery_allowed": contexts.discovery_allowed(p),
        "stack": p.stack or [],
        "stack_packages": p.stack_packages or [],
        "dependencies": [{"id": d.id, "ecosystem": d.ecosystem, "package_key": d.package_key, "name": d.name,
                          "declared_spec": d.declared_spec, "resolved_version": d.resolved_version,
                          "manifest_path": d.manifest_path, "scope": d.scope, "purl": d.purl}
                         for d in p.dependencies],
        "needs": p.needs or [],
        "stack_path": p.stack_path or "",
        "stack_scanned_at": p.stack_scanned_at.isoformat() if p.stack_scanned_at else None,
        "created_at": p.created_at.isoformat(),
    }


class ItemContext:
    """What ser_item() needs besides the item's own relations, looked up once for a whole list.
    Without it ser_item() looks each piece up per item, which is fine for one item and an N+1 for a list."""

    def __init__(self, modes: dict[int, str], found_via: dict[int, list[dict]]):
        self.modes = modes
        self.found_via = found_via


_LIST_OPTIONS = (
    selectinload(models.Item.capability_links).joinedload(models.ItemCapability.capability),
    selectinload(models.Item.relevances).joinedload(models.ItemProjectRelevance.project),
    selectinload(models.Item.decisions).joinedload(models.ItemProjectDecision.project),
    selectinload(models.Item.releases),
    selectinload(models.Item.status_history),
    selectinload(models.Item.feedback),
    selectinload(models.Item.canonical_item),
    selectinload(models.Item.same_as_items),
)


def load_item_context(db: Session, items: list[models.Item]) -> ItemContext:
    """Loads every relation ser_item() touches for these items in a fixed number of queries."""
    ids = list({i.id for i in items})
    modes: dict[int, str] = {}
    found: dict[int, list[dict]] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start:start + 500]
        # Re-selecting the items with eager options fills the not-yet-loaded relations of the
        # instances already in the session instead of creating new ones.
        db.query(models.Item).filter(models.Item.id.in_(chunk)).options(*_LIST_OPTIONS).all()
        for state in db.query(models.ItemAnalysisState).filter(models.ItemAnalysisState.item_id.in_(chunk)):
            modes[state.item_id] = state.provenance.get("mode", "heuristic")
        for row in (db.query(models.ItemDiscovery).filter(models.ItemDiscovery.item_id.in_(chunk))
                    .order_by(models.ItemDiscovery.id)):
            found.setdefault(row.item_id, []).append({"project_slug": row.project_slug, "query": row.query_text})
    return ItemContext(modes, found)


def ser_items(db: Session, items: list[models.Item]) -> list[dict]:
    context = load_item_context(db, items)
    return [ser_item(i, context=context) for i in items]


def _analysis_mode(i: models.Item, context: ItemContext | None = None) -> str:
    if context is not None:
        return context.modes.get(i.id, "heuristic")
    state = object_session(i).get(models.ItemAnalysisState, i.id)
    return state.provenance.get("mode", "heuristic") if state else "heuristic"


def _status_source(i: models.Item) -> str:
    """"you" when the current status was set by a person, "auto" when the system suggested it."""
    last = i.status_history[-1] if i.status_history else None
    if last is None or last.note.startswith(("Discovered", "Auto-suggested", "Reset:")):
        return "auto"
    return "you"


def _found_via(i: models.Item, context: ItemContext | None = None) -> list[dict]:
    if context is not None:
        return context.found_via.get(i.id, [])
    rows = object_session(i).query(models.ItemDiscovery).filter_by(item_id=i.id).order_by(models.ItemDiscovery.id).all()
    return [{"project_slug": r.project_slug, "query": r.query_text} for r in rows]


def _latest_release(i: models.Item) -> dict | None:
    stable = [r for r in i.releases if not r.prerelease and r.published_at] or [r for r in i.releases if r.published_at]
    if not stable:
        return None
    latest = stable[0]
    return {"tag": latest.tag, "published_at": latest.published_at.isoformat(), "url": latest.url or i.url,
            "prerelease": latest.prerelease}


def ser_item(i: models.Item, include_history: bool = False, context: ItemContext | None = None) -> dict:
    """One item. For a list use ser_items(), which loads the relations for all of them at once."""
    data = {
        "found_via": _found_via(i, context),
        "ai_analyzed": _analysis_mode(i, context) == "ai",
        "status_source": _status_source(i),
        "id": i.id,
        "source": i.source,
        "external_id": i.external_id,
        "name": i.name,
        "owner": i.owner,
        "url": i.url,
        "description": i.description,
        "topics": i.topics,
        "stars": i.stars,
        "forks": i.forks,
        "downloads": i.downloads,
        "likes": i.likes,
        "stars_delta_7d": i.stars_delta_7d,
        "source_created_at": i.source_created_at.isoformat() if i.source_created_at else None,
        "source_updated_at": i.source_updated_at.isoformat() if i.source_updated_at else None,
        "first_seen_at": i.first_seen_at.isoformat(),
        "last_seen_at": i.last_seen_at.isoformat(),
        "llm_summary": i.llm_summary,
        "why_interesting": i.why_interesting,
        "momentum_score": i.momentum_score,
        "maturity_score": i.maturity_score,
        "status": i.status,
        "status_updated_at": i.status_updated_at.isoformat(),
        "capabilities": [
            {"name": link.capability.name, "confidence": link.confidence}
            for link in i.capability_links
        ],
        "relevance": [
            {
                "project_slug": r.project.slug,
                "project_name": r.project.name,
                "score": r.score,
                "rationale": r.rationale,
            }
            for r in i.relevances
        ],
        "decisions": [decisions_module.serialise(d) for d in i.decisions],
        "latest_release": _latest_release(i),
        "feedback": feedback_module.current_votes(i),
        "notes_count": len(feedback_module.notes(i)),
        # Entity resolution (app/entities.py): this item's own repository, or the packages
        # that are really this same technology -- at most one of the two is ever non-empty.
        "same_as": ({"id": i.canonical_item.id, "source": i.canonical_item.source, "name": i.canonical_item.name}
                    if i.canonical_item else None),
        "canonical_of": [{"id": p.id, "source": p.source, "name": p.name} for p in i.same_as_items
                         if not p.archived],
    }
    if include_history:
        data["notes"] = [{"id": n.id, "note": n.note, "created_at": n.created_at.isoformat()}
                         for n in feedback_module.notes(i)]
        state = object_session(i).get(models.ItemAnalysisState, i.id)
        data["analysis"] = state.provenance if state else {"mode": "unknown"}
        slugs = {p.id: p.slug for p in object_session(i).query(models.Project).all()}
        data["status_history"] = [
            {"status": h.status, "note": h.note, "created_at": h.created_at.isoformat(),
             "project_slug": slugs.get(h.project_id)}
            for h in i.status_history
        ]
    return data


def ser_opportunity(o: models.Opportunity, items: dict[int, models.Item] | None = None) -> dict:
    trend = o.trend or []
    evidence = [items[i] for i in (o.item_ids or []) if items and i in items]
    return {
        "id": o.id,
        "title": o.title,
        "body": o.body,
        "project_slug": o.project_slug,
        "item_ids": o.item_ids,
        "created_at": o.created_at.isoformat(),
        "first_seen_at": (o.first_seen_at or o.created_at).isoformat(),
        "last_seen_at": (o.last_seen_at or o.created_at).isoformat(),
        "times_seen": o.times_seen or 1,
        "state": o.state or "active",
        "origin": o.origin or "ai",
        "evidence_added": max(len(o.item_ids or []) - (trend[0]["items"] if trend else len(o.item_ids or [])), 0),
        "evidence": [{"id": i.id, "name": i.name, "url": i.url, "source": i.source, "status": i.status}
                     for i in evidence],
    }


def ser_query(q: models.SearchQuery, names: dict[str, str], fingerprints: dict[str, str] | None = None) -> dict:
    return {
        "id": q.id, "project_slug": q.project_slug, "project_name": names.get(q.project_slug, q.project_slug),
        "source": q.source, "text": q.text, "origin": q.origin, "enabled": q.enabled,
        "profile_fingerprint": q.profile_fingerprint, "basis": q.basis or [],
        "stale": q.origin != "user" and fingerprints is not None
                 and q.profile_fingerprint != fingerprints.get(q.project_slug, ""),
        "last_run_at": q.last_run_at.isoformat() if q.last_run_at else None,
        "last_found": q.last_found, "last_new": q.last_new,
        "runs": q.runs or 0, "total_new": q.total_new or 0, "empty_streak": q.empty_streak or 0,
        "resting_days": discovery.backoff_days(q.empty_streak or 0),
    }


def ser_run(r: models.CollectionRun) -> dict:
    return {
        "id": r.id,
        "started_at": r.started_at.isoformat(),
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "sources": r.sources,
        "items_found": r.items_found,
        "items_new": r.items_new,
        "items_enriched": r.items_enriched,
        "status": r.status,
        "error": r.error,
        # Per top-level phase (collect, discovery, gate_and_score, enrich, opportunities,
        # capabilities, embeddings, housekeeping): how long it took and whether it raised.
        "stages": r.stages or [],
    }
