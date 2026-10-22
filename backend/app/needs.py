"""Needs keep requirements and user-confirmed results separate from candidate suggestions."""
import re

from sqlalchemy.orm import selectinload

from . import contexts, models, project_model
from .ai_client import digest
from .enrichment import content_fingerprint
from .utils import utcnow

OPEN = ("open", "evaluating")
STATES = (*OPEN, "satisfied", "deferred", "dropped")
PILOT_ITEMS = 20
PILOT_NEEDS_PER_CONTEXT = 3


def links(db, project_id=None, *, pilot_only=False):
    query = db.query(models.ContextNeed).options(selectinload(models.ContextNeed.need), selectinload(models.ContextNeed.project))
    if project_id is not None:
        query = query.filter_by(project_id=project_id)
    if pilot_only:
        query = query.filter(models.ContextNeed.pilot.is_(True))
    return query.order_by(models.ContextNeed.priority.desc(), models.ContextNeed.id).all()


def event(db, link, action, detail, actor="user"):
    db.add(models.NeedEvent(context_need_id=link.id, actor=actor, action=action, detail=detail))


def project_projection(db, project):
    db.flush()
    project.needs = list(dict.fromkeys(link.need.title for link in links(db, project.id) if link.state in OPEN))


def sync_legacy(db, project, titles, *, actor="import"):
    """Reconcile profile edits without deleting constraints, results, IDs or history."""
    wanted = list(dict.fromkeys(t.strip() for t in titles if t and t.strip()))[:80]
    existing = links(db, project.id)
    known = {link.need.title: link for link in existing}
    for title in wanted:
        if title not in known:
            need = models.Need(title=title, origin="profile_import")
            db.add(need)
            db.flush()
            link = models.ContextNeed(project_id=project.id, need_id=need.id)
            db.add(link)
            db.flush()
            event(db, link, "import", {"title": title}, actor)
    for link in existing:
        if link.need.title not in wanted and link.state == "open":
            link.state = "deferred"
            event(db, link, "defer", {"reason": "removed from profile"}, actor)
    project_projection(db, project)


def import_missing(db):
    for project in db.query(models.Project).all():
        if project.needs and not db.query(models.ContextNeed.id).filter_by(project_id=project.id).first():
            sync_legacy(db, project, project.needs)
    db.commit()


def requirement(link):
    return {"context_need_id": link.id, "project_slug": link.project.slug, "title": link.need.title,
            "constraints": link.constraints, "acceptance_criteria": link.acceptance_criteria,
            "search_terms": link.search_terms or []}


def fingerprint(link, item):
    # A change in one requirement invalidates only that requirement's assessments.
    return digest({"contract": "need-pilot-v1", "need": requirement(link),
                   "item": content_fingerprint(item), "summary": item.llm_summary,
                   "project": {"kind": link.project.kind, "description": link.project.description,
                               "stack": link.project.stack}})


def result_fingerprint(link):
    return digest({"title": link.need.title, "constraints": link.constraints,
                   "acceptance_criteria": link.acceptance_criteria})


def result_current(link):
    return (link.state == "satisfied" and bool((link.result_note or "").strip())
            and link.result_fingerprint == result_fingerprint(link))


def shortlist(db, selected=None, *, limit=PILOT_ITEMS):
    selected = selected if selected is not None else links(db, pilot_only=True)
    states = {}
    for project_id in {link.project_id for link in selected}:
        states.update(graph_state(db, project_id))
    selected = [link for link in selected if link.state in OPEN and contexts.analysis_allowed(link.project)
                and states.get(link.id, {}).get("readiness") != "blocked"]
    items = (db.query(models.Item).filter(models.Item.archived.is_(False))
             .options(selectinload(models.Item.capability_links).selectinload(models.ItemCapability.capability)).all())
    scores = {(r.item_id, r.project_id): r.score for r in db.query(models.ItemProjectRelevance)}
    ai_ids = {s.item_id for s in db.query(models.ItemAnalysisState) if (s.provenance or {}).get("mode") == "ai"}
    ranked = []
    for link in selected:
        terms = [t.casefold().strip() for t in link.search_terms or [] if t.strip()]
        if not terms:
            terms = [t for t in re.findall(r"[\w-]+", link.need.title.casefold()) if len(t) >= 4][:12]
        queue = []
        for item in items:
            text = " ".join([item.name, item.description or "", item.llm_summary or "", " ".join(item.topics or []),
                             " ".join(c.capability.name for c in item.capability_links)]).casefold()
            hits = [term for term in terms if term in text]
            if not hits:
                continue
            relevance = scores.get((item.id, link.project_id), 0)
            if item.id not in ai_ids:
                relevance = min(relevance, 0.15)  # keyword confidence is not an AI judgment
            score = len(hits) / max(len(terms), 1) + relevance + 0.1 * item.maturity_score
            queue.append((score, item, hits))
        queue.sort(key=lambda row: (-row[0], row[1].id))
        ranked.append((link, queue))
    # Round robin prevents a broad need from taking all twenty candidate slots.
    chosen, pairs = set(), []
    for offset in range(limit):
        for link, queue in ranked:
            if offset >= len(queue):
                continue
            _, item, hits = queue[offset]
            if item.id not in chosen and len(chosen) >= limit:
                continue
            chosen.add(item.id)
            pairs.append((link, item, hits))
    return pairs



def dependency_edges(db, project_id):
    return (db.query(models.NeedDependency).filter_by(project_id=project_id)
            .order_by(models.NeedDependency.id).all())


def graph_state(db, project_id):
    """Return scheduling metadata for every need in one project.

    A need is blocked until every direct prerequisite is satisfied. Ready prerequisites that unlock
    downstream work get a small ``effective_priority`` bonus; this is deliberately bounded so graph
    structure refines the user's/AI's 1..3 priority instead of replacing it.
    """
    project_links = links(db, project_id)
    by_id = {link.id: link for link in project_links}
    prerequisites = {link.id: [] for link in project_links}
    dependents = {link.id: [] for link in project_links}
    for edge in dependency_edges(db, project_id):
        if edge.prerequisite_id not in by_id or edge.dependent_id not in by_id:
            continue
        prerequisites[edge.dependent_id].append(edge.prerequisite_id)
        dependents[edge.prerequisite_id].append(edge.dependent_id)

    depth_cache = {}
    def depth(node_id, trail=frozenset()):
        if node_id in depth_cache:
            return depth_cache[node_id]
        if node_id in trail:  # defensive: application validation should prevent cycles
            return 0
        parents = prerequisites.get(node_id) or []
        value = 0 if not parents else 1 + max(depth(parent, trail | {node_id}) for parent in parents)
        depth_cache[node_id] = value
        return value

    out = {}
    for link in project_links:
        blockers = [by_id[i] for i in prerequisites.get(link.id, []) if not result_current(by_id[i])]
        if link.state == "satisfied":
            readiness = "satisfied" if result_current(link) else "stale_result"
        elif link.state not in OPEN:
            readiness = "inactive"
        else:
            readiness = "blocked" if blockers else "ready"
        unlocked = [by_id[i] for i in dependents.get(link.id, []) if by_id[i].state in OPEN]
        unlock_bonus = min(0.5, 0.2 * len(unlocked)) if readiness == "ready" else 0.0
        out[link.id] = {
            "readiness": readiness,
            "depth": depth(link.id),
            "effective_priority": round(min(3.5, float(link.priority or 2) + unlock_bonus), 2),
            "blocked_by": [{"id": row.id, "title": row.need.title, "state": row.state}
                           for row in blockers],
            "depends_on": [{"id": by_id[i].id, "title": by_id[i].need.title, "state": by_id[i].state}
                           for i in prerequisites.get(link.id, [])],
            "unlocks": [{"id": row.id, "title": row.need.title, "state": row.state}
                        for row in unlocked],
        }
    return out


def project_plan(db, project_id):
    state = graph_state(db, project_id)
    rows = links(db, project_id)
    ready = [row for row in rows if state[row.id]["readiness"] == "ready"]
    blocked = [row for row in rows if state[row.id]["readiness"] == "blocked"]
    ready.sort(key=lambda row: (-state[row.id]["effective_priority"], state[row.id]["depth"], row.id))
    blocked.sort(key=lambda row: (state[row.id]["depth"], -row.priority, row.id))
    pack = lambda row: {"id": row.id, "title": row.need.title, "priority": row.priority, **state[row.id]}
    return {"ready": [pack(row) for row in ready], "blocked": [pack(row) for row in blocked],
            "satisfied": sum(meta["readiness"] == "satisfied" for meta in state.values())}


def item_need_context(db, project, items):
    """A lightweight project-derived signal for already relevant items.

    This is intentionally not a second relevance model. It answers a narrower question: among items
    that already belong to the project, which ones visibly address the project's current high-priority
    needs? Search terms inferred from the project profile are the strongest evidence; title tokens are
    a fallback for hand-written needs.
    """
    selected = [link for link in links(db, project.id) if link.state in OPEN]
    graph = graph_state(db, project.id)
    gap_map = project_model.need_gap_map(db, project.id)
    prepared = []
    for link in selected:
        terms = [t.casefold().strip() for t in link.search_terms or [] if t and t.strip()]
        if not terms:
            terms = [t for t in re.findall(r"[\w+#.\-]+", link.need.title.casefold()) if len(t) >= 4][:10]
        if terms:
            prepared.append((link, terms))
    out = {}
    for item in items:
        text = " ".join([item.name or "", item.description or "", item.llm_summary or "",
                         " ".join(item.topics or [])]).casefold()
        best = None
        for link, terms in prepared:
            hits = [term for term in terms if term in text]
            if not hits:
                continue
            # One explicit technical phrase is useful evidence; a second one makes the match stronger.
            coverage = min(1.0, len(hits) / 2.0)
            priority = max(1, min(link.priority or 2, 3))
            meta = graph.get(link.id, {"readiness": "ready", "effective_priority": float(priority),
                                       "blocked_by": [], "unlocks": []})
            readiness_factor = 0.20 if meta["readiness"] == "blocked" else 1.0
            effective = float(meta["effective_priority"])
            score = min(1.0, (effective / 3.5) * (0.65 + 0.35 * coverage) * readiness_factor)
            candidate = {"score": score, "need_id": link.id, "title": link.need.title,
                         "priority": priority, "effective_priority": effective,
                         "readiness": meta["readiness"], "blocked_by": meta["blocked_by"],
                         "unlocks": meta["unlocks"], "matched_terms": hits[:4]}
            if link.id in gap_map:
                candidate["gap"] = gap_map[link.id]
            if best is None or (candidate["score"], candidate["effective_priority"]) > (best["score"], best["effective_priority"]):
                best = candidate
        if best is not None:
            out[item.id] = best
    return out

def overview(db, project_id=None, *, pilot_only=False):
    selected = links(db, project_id, pilot_only=pilot_only)
    # Closed needs retain their results but do not create new work.
    pairs = shortlist(db, selected)
    assessed = {(a.context_need_id, a.item_id): a for a in db.query(models.NeedAssessment)}
    decisions = {(d.project_id, d.item_id): d.status for d in db.query(models.ItemProjectDecision)}
    inference = {}
    graph_by_project = {}
    gap_by_project = {}
    for project_id in {link.project_id for link in selected}:
        graph_by_project[project_id] = graph_state(db, project_id)
        gap_by_project[project_id] = project_model.need_gap_map(db, project_id)
    for event_row in (db.query(models.NeedEvent).filter(models.NeedEvent.action == "infer")
                      .order_by(models.NeedEvent.created_at.desc(), models.NeedEvent.id.desc())):
        inference.setdefault(event_row.context_need_id, (event_row.detail or {}).get("rationale", ""))
    out = []
    for link in selected:
        candidates = []
        for candidate_link, item, hits in pairs:
            if candidate_link.id != link.id:
                continue
            assessment = assessed.get((link.id, item.id))
            current = bool(assessment and assessment.fingerprint == fingerprint(link, item))
            status = "assessed" if current and assessment.score is not None else "pending" if not assessment or current else "stale"
            candidates.append({"id": item.id, "name": item.name, "url": item.url, "summary": item.llm_summary or item.description,
                "matched_terms": hits, "analysis_status": status,
                "score": assessment.score if current else None,
                "constraint_status": assessment.constraint_status if current else "unknown",
                "rationale": assessment.rationale if current else "", "evidence": assessment.evidence if current else [],
                "provenance": assessment.provenance if current else {},
                "decision": decisions.get((link.project_id, item.id))})
        candidates.sort(key=lambda c: (c["constraint_status"] == "conflict", c["score"] is None,
                                       -(c["score"] or 0)))
        current_count = sum(c["analysis_status"] == "assessed" for c in candidates)
        suitable = [c for c in candidates if c["score"] is not None and c["score"] >= 0.6 and c["constraint_status"] != "conflict"]
        result_current = link.result_fingerprint == result_fingerprint(link)
        graph_meta = graph_by_project.get(link.project_id, {}).get(link.id, {"readiness": "ready", "depth": 0,
            "effective_priority": float(link.priority or 2), "blocked_by": [], "depends_on": [], "unlocks": []})
        coverage = ("confirmed" if link.state == "satisfied" and result_current else "result_stale" if link.state == "satisfied"
                    else "blocked" if graph_meta["readiness"] == "blocked"
                    else "evaluating" if link.state == "evaluating"
                    else "candidates" if suitable else "pending" if current_count < len(candidates) or not candidates else "no_match")
        out.append({**requirement(link), "id": link.id, "need_id": link.need_id, "project_id": link.project_id,
            "project_name": link.project.name, "priority": link.priority, "state": link.state, "pilot": link.pilot,
            "origin": link.need.origin, "inference_rationale": inference.get(link.id, ""),
            "gap": gap_by_project.get(link.project_id, {}).get(link.id),
            "readiness": graph_meta["readiness"], "depth": graph_meta["depth"],
            "effective_priority": graph_meta["effective_priority"], "blocked_by": graph_meta["blocked_by"],
            "depends_on": graph_meta["depends_on"], "unlocks": graph_meta["unlocks"],
            "result_note": link.result_note, "confirmed_item_id": link.confirmed_item_id,
            "coverage": coverage, "candidate_count": len(candidates), "assessed_count": current_count,
            "pending_count": len(candidates) - current_count, "candidates": candidates,
            "updated_at": link.updated_at.isoformat()})
    return out
