"""What changed since you decided.

A decision is a snapshot of a judgment: "rejected: too slow on Android", "adopted". Months later the world
has moved. This module answers, for each decision, what is different now, using only what StackSignal already
knows (no AI call):

  * new stable releases of that item since the decision;
  * a real shift in its momentum or maturity compared with the snapshot taken when you decided;
  * NEW alternatives: items found after the decision that share a capability with it and that the AI found
    strongly relevant to the same project — candidates that might remove the reason you said no.

For a rejected or paused item that is a reason to look again ("revisit"); for one you use or are trying it is
a heads-up that something changed ("update"). Assess and Watch decisions are not judgments yet, so they are
left out. Your own "revisit when" note is shown next to the evidence so you can judge it yourself.
"""

import datetime

from sqlalchemy.orm import Session

from . import contexts, models
from .utils import utcnow

SHIFT = 0.15                # a momentum/maturity change smaller than this is noise
MAX_ALTERNATIVES = 5
ALTERNATIVE_MIN_RELEVANCE = 0.6
_REVISIT = ("ignore", "hold")
_UPDATE = ("adopt", "trial")


def snapshot(item: models.Item) -> dict:
    """The state of an item at the moment of a decision, kept to compare against later."""
    stable = [r for r in item.releases if not r.prerelease and r.published_at]
    return {"momentum": item.momentum_score, "maturity": item.maturity_score, "stars": item.stars,
            "likes": item.likes, "downloads": item.downloads, "release": stable[0].tag if stable else "",
            "capabilities": sorted(link.capability.name for link in item.capability_links),
            "at": utcnow().isoformat()}


def _since(decision: models.ItemProjectDecision) -> datetime.datetime:
    return decision.decided_at or decision.updated_at or decision.created_at


def changes_since(db: Session, decision: models.ItemProjectDecision) -> dict:
    item, since = decision.item, _since(decision)
    before = decision.snapshot or {}

    releases = [r for r in item.releases if not r.prerelease and r.published_at and r.published_at > since]
    shifts = {}
    for key in ("momentum", "maturity"):
        if key in before and abs((getattr(item, f"{key}_score") or 0) - (before[key] or 0)) >= SHIFT:
            shifts[key] = {"before": round(before[key] or 0, 2), "now": round(getattr(item, f"{key}_score") or 0, 2)}

    capabilities = {link.capability_id for link in item.capability_links}
    alternatives = []
    if capabilities:
        decided = {d.item_id for d in db.query(models.ItemProjectDecision).filter_by(project_id=decision.project_id)}
        rows = (db.query(models.ItemProjectRelevance).join(models.Item)
                .filter(models.ItemProjectRelevance.project_id == decision.project_id,
                        models.ItemProjectRelevance.score >= ALTERNATIVE_MIN_RELEVANCE,
                        models.Item.id != item.id, models.Item.archived.is_(False),
                        models.Item.first_seen_at > since, models.Item.status != models.Status.IGNORE)
                .order_by(models.ItemProjectRelevance.score.desc()).all())
        for relevance in rows:
            other = relevance.item
            shared = sorted({link.capability.name for link in other.capability_links if link.capability_id in capabilities})
            if shared and other.id not in decided:
                alternatives.append({"item_id": other.id, "name": other.name, "url": other.url,
                                     "source": getattr(other.source, "value", other.source),
                                     "score": round(relevance.score, 2), "reason": relevance.rationale,
                                     "shared_capabilities": shared})
            if len(alternatives) >= MAX_ALTERNATIVES:
                break

    return {"releases": [{"tag": r.tag, "url": r.url, "published_at": r.published_at.isoformat()} for r in releases[:3]],
            "release_count": len(releases), "shifts": shifts, "alternatives": alternatives}


def _signature(changes: dict) -> str:
    """Identity of the current evidence, so the digest mentions the same change only once."""
    return "|".join([str(changes["release_count"]), ",".join(str(a["item_id"]) for a in changes["alternatives"]),
                     ",".join(sorted(changes["shifts"]))])


def overview(db: Session, *, only_notable: bool = False) -> list[dict]:
    """Decisions you made (rejected/paused/adopted/trying) with what changed since. Notable ones first."""
    out = []
    for decision in db.query(models.ItemProjectDecision).filter(models.ItemProjectDecision.status.in_(_REVISIT + _UPDATE)):
        if decision.item.archived or not contexts.attended(decision.project):
            continue
        changes = changes_since(db, decision)
        notable = bool(changes["release_count"] or changes["alternatives"] or changes["shifts"])
        if only_notable and not notable:
            continue
        kind = "revisit" if decision.status in _REVISIT else "update"
        out.append({"decision_id": decision.id, "kind": kind, "notable": notable, "item_id": decision.item_id,
                    "item_name": decision.item.name, "item_url": decision.item.url, "project_slug": decision.project.slug,
                    "project_name": decision.project.name, "status": decision.status, "goal": decision.goal,
                    "verdict": decision.verdict, "criteria": decision.criteria, "revisit_when": decision.revisit_when,
                    "decided_at": _since(decision).isoformat(), "changes": changes, "key": f"{decision.id}:{_signature(changes)}"})
    out.sort(key=lambda entry: (not entry["notable"], entry["kind"] != "revisit", entry["decided_at"]))
    return out
