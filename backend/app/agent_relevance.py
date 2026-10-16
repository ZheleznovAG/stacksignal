"""Resumable, scoped relevance refresh through files, without replacing item analyses.

Each batch is self-contained: prompt, selected project profiles, feedback, items and
export-time fingerprints. The agent writes batch-N.result.json. Applying it updates
only unchanged requested pairs. Batch/result files remain available for audit; an
atomic batch-N.applied.json receipt marks completed work. Reapplying after a crash
is safe because already-current pairs are skipped.
"""

import json
import math
import re
from pathlib import Path

from pydantic import Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from . import config, contexts, models, pipeline
from .agent_analysis import _exclusive
from .ai_analysis import lang_version, with_language
from .ai_schemas import RelevanceOnlyItem, StrictOutput
from .enrichment import (RELEVANCE_PROMPT_VERSION, _KINDS_NOTE, _RELEVANCE_SYSTEM_PROMPT,
                         batch_context, batch_item, content_fingerprint, project_fingerprint)
from .utils import utcnow

WORK_DIR = config.DATA_DIR / "agent-relevance"
PROVIDER = "claude-code"
MAX_BATCH = 100
FORMAT_VERSION = "agent_relevance_v1"


class RelevanceAnswer(StrictOutput):
    results: list[RelevanceOnlyItem] = Field(min_length=1, max_length=MAX_BATCH)


def _read(path) -> dict:
    def unique(pairs):
        obj = {}
        for key, value in pairs:
            if key in obj:
                raise ValueError(f"Duplicate JSON key: {key}")
            obj[key] = value
        return obj
    text = Path(path).read_text(encoding="utf-8-sig").strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    return json.loads(fenced[1] if fenced else text, object_pairs_hook=unique)


def _write(path, data) -> None:
    """Readers never observe half-written batch or receipt files."""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    temporary.replace(path)


def _projects(db, projects) -> list[models.Project]:
    slugs = {slug.strip() for value in projects or [] for slug in value.split(",") if slug.strip()}
    if not slugs:
        raise ValueError("Select at least one project with --projects")
    found = {p.slug: p for p in db.query(models.Project).filter(models.Project.slug.in_(slugs))}
    if slugs - found.keys():
        raise ValueError(f"Unknown projects: {', '.join(sorted(slugs - found.keys()))}")
    if any(not contexts.analysis_allowed(p) for p in found.values()):
        raise ValueError("Archived projects cannot be analysed")
    return [found[slug] for slug in sorted(slugs)]


def _current(provenance, fingerprint, project_fps) -> set[str]:
    current = pipeline._fresh_scoped_relevance(provenance, fingerprint, project_fps)
    if provenance.get("mode") == "ai" and provenance.get("content_fingerprint") == fingerprint:
        current |= {slug for slug, fp in project_fps.items()
                    if provenance.get("project_fingerprints", {}).get(slug) == fp}
    return current


def _batch_files(work_dir) -> list[Path]:
    return sorted(p for p in work_dir.glob("batch-*.json") if re.fullmatch(r"batch-\d+\.json", p.name))


def _receipt(path):
    return path.with_name(path.stem + ".applied.json")


def _reserved(work_dir) -> set[tuple]:
    reserved = set()
    for path in _batch_files(work_dir):
        if _receipt(path).exists():
            continue
        batch = _read(path)
        if batch.get("format") != FORMAT_VERSION:
            raise ValueError(f"Unrecognised batch format: {path}")
        for item in batch["items"]:
            for slug in item["requested_projects"]:
                reserved.add((item["id"], slug, batch["content_fingerprints"][str(item["id"])],
                              batch["project_fingerprints"][slug]))
    return reserved


def _queue(db, projects, work_dir):
    project_fps = {p.slug: project_fingerprint(p) for p in projects}
    states = {s.item_id: s.provenance or {} for s in db.query(models.ItemAnalysisState)}
    reserved = _reserved(work_dir)
    relevance = dict(db.query(models.ItemProjectRelevance.item_id, func.max(models.ItemProjectRelevance.score))
                     .group_by(models.ItemProjectRelevance.item_id))
    counts = {"items": 0, "pairs": 0, "current_pairs": 0, "pending_items": 0,
              "pending_pairs": 0, "reserved_pairs": 0, "available_items": 0, "available_pairs": 0}
    queue = []
    for item in db.query(models.Item).filter(models.Item.archived.is_(False)):
        fingerprint = content_fingerprint(item)
        current = _current(states.get(item.id, {}), fingerprint, project_fps)
        pending = project_fps.keys() - current
        available = {slug for slug in pending if (item.id, slug, fingerprint, project_fps[slug]) not in reserved}
        counts["items"] += 1
        counts["pairs"] += len(project_fps)
        counts["current_pairs"] += len(current)
        counts["pending_items"] += bool(pending)
        counts["pending_pairs"] += len(pending)
        counts["reserved_pairs"] += len(pending - available)
        counts["available_items"] += bool(available)
        counts["available_pairs"] += len(available)
        if available:
            acted = getattr(item.status, "value", item.status) != "new"
            priority = (acted, (0.2 + relevance.get(item.id, 0.0)) * (0.3 + (item.momentum_score or 0.0)), -item.id)
            queue.append((priority, item, sorted(available), fingerprint))
    return sorted(queue, key=lambda entry: entry[0], reverse=True), counts, project_fps


def plan(db: Session, *, projects, items_per_batch: int = 100, work_dir=None) -> dict:
    if not 1 <= items_per_batch <= MAX_BATCH:
        raise ValueError(f"items_per_batch must be between 1 and {MAX_BATCH}")
    selected = _projects(db, projects)
    _, counts, _ = _queue(db, selected, Path(work_dir or WORK_DIR))
    return {"projects": [p.slug for p in selected], **counts, "items_per_batch": items_per_batch,
            "batches_needed": math.ceil(counts["available_items"] / items_per_batch)}


def status(db: Session, *, projects, work_dir=None) -> dict:
    work_dir = Path(work_dir or WORK_DIR)
    paths = _batch_files(work_dir)
    opened = [p for p in paths if not _receipt(p).exists()]
    return {**plan(db, projects=projects, work_dir=work_dir), "open_batches": len(opened),
            "applied_batches": len(paths) - len(opened),
            "ready_results": sum(p.with_name(p.stem + ".result.json").exists() for p in opened)}


def export(db: Session, *, projects, items_per_batch: int = 100, batches: int = 1, work_dir=None) -> list[Path]:
    if not 1 <= items_per_batch <= MAX_BATCH or batches < 1:
        raise ValueError(f"items_per_batch must be 1..{MAX_BATCH}; batches must be positive")
    selected = _projects(db, projects)
    work_dir = Path(work_dir or WORK_DIR)
    work_dir.mkdir(parents=True, exist_ok=True)
    with _exclusive(work_dir):
        queue, _, project_fps = _queue(db, selected, work_dir)
        context = batch_context(db, selected)
        context.pop("known_capabilities", None)
        prompt = with_language(_RELEVANCE_SYSTEM_PROMPT + _KINDS_NOTE, '"rationale"')
        prompt += ("\nOnly score each item's requested_projects; these can differ between items. "
                   "Return every item exactly once, including items with empty relevance {}. "
                   "An omitted requested project means no applicable match. Do not output summaries or capabilities.")
        number = max((int(p.stem.split("-")[1]) for p in _batch_files(work_dir)), default=0)
        written = []
        for start in range(0, min(len(queue), items_per_batch * batches), items_per_batch):
            number += 1
            chunk = queue[start:start + items_per_batch]
            path = work_dir / f"batch-{number:04d}.json"
            _write(path, {"format": FORMAT_VERSION, "batch": number, "created_at": utcnow().isoformat(),
                          "system_prompt": prompt, **context,
                          "items": [{**batch_item(item), "summary": item.llm_summary,
                                     "capabilities": sorted(link.capability.name for link in item.capability_links),
                                     "requested_projects": slugs} for _, item, slugs, _ in chunk],
                          "content_fingerprints": {str(item.id): fp for _, item, _, fp in chunk},
                          "project_fingerprints": project_fps, "result_file": f"batch-{number:04d}.result.json"})
            written.append(path)
        return written


def _validate(data, batch):
    data = RelevanceAnswer.model_validate(data).model_dump()
    requested = {item["id"]: set(item["requested_projects"]) for item in batch["items"]}
    ids = [entry["item_id"] for entry in data["results"]]
    if len(ids) != len(set(ids)) or set(ids) != requested.keys():
        raise ValueError("Every requested item_id must appear exactly once, with no extra items")
    if any(set(entry["relevance"]) - requested[entry["item_id"]] for entry in data["results"]):
        raise ValueError("Unrequested project in relevance")
    return data, requested


def apply(db: Session, batch_path, *, result_path=None, model: str = "", provider: str = PROVIDER) -> dict:
    batch_path = Path(batch_path)
    with _exclusive(batch_path.parent):
        batch = _read(batch_path)
        if batch.get("format") != FORMAT_VERSION:
            raise ValueError("Unrecognised batch format")
        result_path = Path(result_path) if result_path else batch_path.with_name(batch["result_file"])
        data, requested = _validate(_read(result_path), batch)
        # A long-lived caller may still have objects loaded before the agent began.
        if db.new or db.dirty or db.deleted:
            raise ValueError("Commit pending session changes before applying an agent answer")
        db.expire_all()
        projects = {p.slug: p for p in db.query(models.Project).filter(
            models.Project.slug.in_(batch["project_fingerprints"]))}
        project_fps = {slug: project_fingerprint(p) for slug, p in projects.items() if contexts.analysis_allowed(p)}
        unchanged = {slug for slug, fp in project_fps.items() if fp == batch["project_fingerprints"].get(slug)}
        stats = {"applied": 0, "applied_pairs": 0, "changed_items": 0, "changed_pairs": 0,
                 "already_current_pairs": 0, "missing_items": 0, "archived_items": 0}
        try:
            for entry in data["results"]:
                item_id = entry["item_id"]
                slugs = requested[item_id]
                item = db.get(models.Item, item_id)
                if item is None:
                    stats["missing_items"] += 1
                    continue
                if item.archived:
                    stats["archived_items"] += 1
                    continue
                fingerprint = content_fingerprint(item)
                if fingerprint != batch["content_fingerprints"].get(str(item_id)):
                    stats["changed_items"] += 1
                    continue
                stats["changed_pairs"] += len(slugs - unchanged)
                state = db.get(models.ItemAnalysisState, item_id)
                provenance = dict(state.provenance or {}) if state else {}
                current = _current(provenance, fingerprint, project_fps)
                valid = slugs & unchanged
                stats["already_current_pairs"] += len(valid & current)
                valid -= current
                if not valid:
                    continue
                scoped_projects = [projects[slug] for slug in sorted(valid)]
                pipeline._replace_relevance(db, item, scoped_projects, entry["relevance"], only_slugs=valid)
                if state is None:
                    state = models.ItemAnalysisState(item_id=item_id)
                    db.add(state)
                provenance["project_fingerprints"] = {**provenance.get("project_fingerprints", {}),
                    **{slug: project_fps[slug] for slug in valid}}
                scopes = dict(provenance.get("relevance_provenance", {}))
                for slug in valid:
                    scopes[slug] = {"mode": "ai", "provider": provider, "model": model,
                                   "prompt_version": lang_version(RELEVANCE_PROMPT_VERSION),
                                   "content_fingerprint": fingerprint, "project_fingerprint": project_fps[slug],
                                   "updated_at": utcnow().isoformat(), "batch": batch_path.name}
                provenance["relevance_provenance"] = scopes
                state.provenance = provenance
                db.flush()
                stats["applied"] += 1
                stats["applied_pairs"] += len(valid)
            db.commit()
        except Exception:
            db.rollback()
            raise
        _write(_receipt(batch_path), {"applied_at": utcnow().isoformat(), "provider": provider, "model": model,
                                     "result_file": result_path.name, **stats})
        return stats
