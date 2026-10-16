"""Stored, inspectable technical understanding of a project.

The model is a derived snapshot. Project profile + scanned stack are authoritative; manual needs are
separate authoritative requirements. Keeping the model explicit makes project-first ranking explainable
and lets us detect when the understanding is stale after the profile changes.
"""
from __future__ import annotations

import re

from sqlalchemy.orm import Session

from . import models
from .ai_client import digest
from .utils import utcnow

_VALID_SUBSYSTEM_STATUS = {"existing", "partial", "planned", "unknown"}


def source_payload(project: models.Project) -> dict:
    deps = sorted(
        ({"ecosystem": d.ecosystem or "", "name": d.name or "", "package_key": d.package_key or "",
          "declared_spec": d.declared_spec or "", "scope": d.scope or ""} for d in (project.dependencies or [])),
        key=lambda d: (d["ecosystem"], d["package_key"], d["scope"], d["declared_spec"]),
    )
    return {
        "name": project.name,
        "kind": project.kind,
        "description": project.description or "",
        "keywords": list(project.keywords or []),
        "stack": list(project.stack or []),
        "dependencies": deps,
    }


def source_fingerprint(project: models.Project) -> str:
    return digest({"contract": "project-technical-model-v1", "project": source_payload(project)})


def _key(value: str, fallback: str) -> str:
    value = re.sub(r"[^a-z0-9_-]+", "-", (value or "").strip().casefold()).strip("-")[:80]
    return value or fallback


def _text(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def normalise(model: dict, *, need_rows: list[dict]) -> dict:
    """Clean references and attach applied need ids to gaps without trusting arbitrary AI keys."""
    goals, goal_keys = [], set()
    for idx, row in enumerate((model or {}).get("goals") or []):
        key = _key(row.get("key"), f"goal-{idx+1}")
        if key in goal_keys:
            continue
        goal_keys.add(key)
        goals.append({"key": key, "title": _text(row.get("title"), 300),
                      "priority": max(1, min(int(row.get("priority") or 2), 3)),
                      "outcome": _text(row.get("outcome"), 800)})

    subsystems, subsystem_keys = [], set()
    for idx, row in enumerate((model or {}).get("subsystems") or []):
        key = _key(row.get("key"), f"subsystem-{idx+1}")
        if key in subsystem_keys:
            continue
        subsystem_keys.add(key)
        status = (row.get("status") or "unknown").strip().casefold()
        if status not in _VALID_SUBSYSTEM_STATUS:
            status = "unknown"
        subsystems.append({"key": key, "title": _text(row.get("title"), 300),
                           "purpose": _text(row.get("purpose"), 800), "status": status,
                           "current_capabilities": [_text(v, 220) for v in (row.get("current_capabilities") or []) if _text(v, 220)][:16],
                           "stack_evidence": [_text(v, 220) for v in (row.get("stack_evidence") or []) if _text(v, 220)][:16]})

    need_by_gap: dict[str, list[dict]] = {}
    for row in need_rows:
        for raw in row.get("gap_keys") or []:
            gap_key = _key(raw, "")
            if gap_key:
                need_by_gap.setdefault(gap_key, []).append({"id": row.get("id"), "title": row.get("title")})

    gaps, gap_keys = [], set()
    for idx, row in enumerate((model or {}).get("gaps") or []):
        key = _key(row.get("key"), f"gap-{idx+1}")
        if key in gap_keys:
            continue
        gap_keys.add(key)
        subsystem_key = _key(row.get("subsystem_key"), "")
        if subsystem_key not in subsystem_keys:
            subsystem_key = ""
        linked_goals = []
        for value in row.get("goal_keys") or []:
            value = _key(value, "")
            if value in goal_keys and value not in linked_goals:
                linked_goals.append(value)
        gaps.append({"key": key, "title": _text(row.get("title"), 400),
                     "subsystem_key": subsystem_key, "goal_keys": linked_goals[:6],
                     "priority": max(1, min(int(row.get("priority") or 2), 3)),
                     "why": _text(row.get("why"), 1000),
                     "current_state": _text(row.get("current_state"), 800),
                     "desired_state": _text(row.get("desired_state"), 800),
                     "needs": need_by_gap.get(key, [])[:8]})

    risks = [{"title": _text(r.get("title"), 400), "why": _text(r.get("why"), 1000)}
             for r in ((model or {}).get("risks") or []) if _text(r.get("title"), 400)][:8]
    return {"summary": _text((model or {}).get("summary"), 1600), "goals": goals[:8],
            "subsystems": subsystems[:12], "gaps": gaps[:12], "risks": risks}


def save(db: Session, project: models.Project, model: dict, *, need_rows: list[dict], provenance: dict,
         prompt_version: str) -> models.ProjectTechnicalModel:
    clean = normalise(model or {}, need_rows=need_rows)
    row = db.query(models.ProjectTechnicalModel).filter_by(project_id=project.id).one_or_none()
    if row is None:
        row = models.ProjectTechnicalModel(project_id=project.id, source_fingerprint=source_fingerprint(project))
        db.add(row)
    row.source_fingerprint = source_fingerprint(project)
    row.prompt_version = prompt_version
    row.summary = clean["summary"]
    row.goals = clean["goals"]
    row.subsystems = clean["subsystems"]
    row.gaps = clean["gaps"]
    row.risks = clean["risks"]
    row.provenance = provenance or {}
    row.updated_at = utcnow()
    db.flush()
    return row


def serialise(db: Session, project: models.Project) -> dict | None:
    row = db.query(models.ProjectTechnicalModel).filter_by(project_id=project.id).one_or_none()
    if row is None:
        return None
    current = source_fingerprint(project)
    return {
        "project_id": project.id, "project_slug": project.slug, "project_name": project.name,
        "summary": row.summary or "", "goals": row.goals or [], "subsystems": row.subsystems or [],
        "gaps": row.gaps or [], "risks": row.risks or [], "provenance": row.provenance or {},
        "prompt_version": row.prompt_version, "source_fingerprint": row.source_fingerprint,
        "current_fingerprint": current, "stale": row.source_fingerprint != current,
        "updated_at": row.updated_at.isoformat(),
    }


def need_gap_map(db: Session, project_id: int) -> dict[int, dict]:
    row = db.query(models.ProjectTechnicalModel).filter_by(project_id=project_id).one_or_none()
    if row is None:
        return {}
    subsystems = {s.get("key"): s for s in (row.subsystems or [])}
    goals = {g.get("key"): g for g in (row.goals or [])}
    out: dict[int, dict] = {}
    for gap in row.gaps or []:
        info = {"key": gap.get("key"), "title": gap.get("title"), "priority": gap.get("priority", 2),
                "why": gap.get("why", ""), "current_state": gap.get("current_state", ""),
                "desired_state": gap.get("desired_state", ""),
                "subsystem": subsystems.get(gap.get("subsystem_key")),
                "goals": [goals[k] for k in gap.get("goal_keys", []) if k in goals]}
        for need in gap.get("needs") or []:
            if need.get("id") is not None:
                out.setdefault(int(need["id"]), info)
    return out
