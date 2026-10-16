"""Analysis by a local agent (e.g. Claude Code) instead of the AI gateway -- for the backlog.

The agent gets exactly what a gateway batch gets: the same system prompt, the same payload, the same
output schema, and its answer passes the same validation. Work goes through files in
data/agent-analysis/ so any agent that can read and write files can do it:

  context.json              the system prompt and what every batch shares (projects, capability
                            vocabulary, your judgments) -- read once per session
  batch-<n>.json            the items of one answer, and their fingerprints at export time
  batch-<n>.result.json     the agent's answer: {"results": [...]} as the prompt describes

apply() stores an answer as an ordinary AI analysis (provenance mode "ai", provider "claude-code"), so
the pipeline treats those items as read and the gateway never redoes them. An item whose content
changed since export is skipped and stays in the backlog; relevance to a project edited since export is
redone by the pipeline's usual short relevance call.
"""

import contextlib
import json
import os
import re
import time

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import config, contexts, models, pipeline
from .ai_analysis import lang_version
from .ai_schemas import BatchEnrichmentResult
from .enrichment import (BATCH_PROMPT_VERSION, Enrichment, batch_context, batch_item, batch_prompt,
                         content_fingerprint, project_fingerprint, validate_batch)
from .utils import utcnow

WORK_DIR = config.DATA_DIR / "agent-analysis"
PROVIDER = "claude-code"
MAX_BATCH = 20  # BatchEnrichmentResult accepts at most 20 results


def _analysed(db: Session) -> set[int]:
    return {s.item_id for s in db.query(models.ItemAnalysisState) if (s.provenance or {}).get("mode") == "ai"}


def _open_batches(work_dir) -> list:
    return sorted(work_dir.glob("batch-*.json"), key=lambda p: p.name) if work_dir.exists() else []


def _batch_files(work_dir) -> list:
    return [p for p in _open_batches(work_dir) if not p.name.endswith(".result.json")]


def _reserved(work_dir) -> set[int]:
    ids = set()
    for path in _batch_files(work_dir):
        ids |= {item["id"] for item in json.loads(path.read_text(encoding="utf-8"))["items"]}
    return ids


def backlog(db: Session, *, exclude: set[int] = frozenset()) -> list[models.Item]:
    """Items no AI has read, most worth reading first -- the pipeline's own order: what you acted on,
    then (keyword relevance) x (momentum)."""
    analysed = _analysed(db) | set(exclude)
    relevance = dict(db.query(models.ItemProjectRelevance.item_id, func.max(models.ItemProjectRelevance.score))
                     .group_by(models.ItemProjectRelevance.item_id))
    items = [i for i in db.query(models.Item).filter(models.Item.archived.is_(False)) if i.id not in analysed]

    def priority(item):
        acted = getattr(item.status, "value", item.status) != "new"
        return (acted, (0.2 + relevance.get(item.id, 0.0)) * (0.3 + (item.momentum_score or 0.0)), -item.id)

    return sorted(items, key=priority, reverse=True)


@contextlib.contextmanager
def _exclusive(work_dir, *, timeout: float = 60.0):
    """One export at a time across processes, so parallel agents never get the same items or batch
    numbers. A lock older than `timeout` is left over from a crash and is taken over."""
    lock = work_dir / ".export.lock"
    deadline = time.monotonic() + timeout
    while True:
        try:
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > timeout:
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise TimeoutError("another export holds the lock")
            time.sleep(0.2)
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


def export(db: Session, *, items_per_batch: int = 10, batches: int = 1, work_dir=None) -> list:
    """Write context.json and up to `batches` new batch files of the next backlog items. Items already
    in an open batch are not handed out twice, so several agents can work side by side."""
    work_dir = work_dir or WORK_DIR
    items_per_batch = max(1, min(items_per_batch, MAX_BATCH))
    work_dir.mkdir(parents=True, exist_ok=True)
    with _exclusive(work_dir):
        return _export(db, items_per_batch, batches, work_dir)


def _export(db: Session, items_per_batch: int, batches: int, work_dir) -> list:
    projects = [p for p in db.query(models.Project) if contexts.analysis_allowed(p)]
    context = {"system_prompt": batch_prompt(), **batch_context(db, projects),
               "answer_format": 'One JSON object {"results": [...]} exactly as the system prompt describes; '
                                "the user message is this context's projects, known_capabilities and "
                                "user_feedback plus the batch's items."}
    (work_dir / "context.json").write_text(json.dumps(context, ensure_ascii=False, indent=1), encoding="utf-8")
    queue = backlog(db, exclude=_reserved(work_dir))
    numbers = [int(m[1]) for p in work_dir.rglob("batch-*.json") if (m := re.match(r"batch-(\d+)", p.name))]
    number = max(numbers, default=0)
    written = []
    for start in range(0, min(len(queue), items_per_batch * batches), items_per_batch):
        chunk = queue[start:start + items_per_batch]
        number += 1
        path = work_dir / f"batch-{number:04d}.json"
        path.write_text(json.dumps({
            "batch": number, "created_at": utcnow().isoformat(),
            "items": [batch_item(item) for item in chunk],
            "content_fingerprints": {str(item.id): content_fingerprint(item) for item in chunk},
            "project_fingerprints": {p.slug: project_fingerprint(p) for p in projects},
            "result_file": f"batch-{number:04d}.result.json",
        }, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        written.append(path)
    return written


def describe(batch_path) -> str:
    """A compact text view of a batch for an agent to read: one item per three lines, noise tags dropped."""
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    lines = [f"batch {batch['batch']} -> write {batch['result_file']}"]
    for item in batch["items"]:
        kind = item["kind"]
        numbers = (f"stars {item['stars']}" if kind == "repository" else
                   f"likes {item['likes']} downloads {item['downloads']}" if kind == "model" else
                   f"downloads {item['downloads']}" if kind == "package" else "")
        topics = ", ".join(t for t in item["topics"] if not t.startswith(("region:", "license:", "arxiv:", "base_model:quantized")))
        lines += [f"[{item['id']}] {kind} {item['owner']}/{item['name']} {numbers} updated {str(item['source_updated_at'])[:10]}",
                  f"   {(item['description'] or '').strip()[:400]}",
                  f"   topics: {topics[:240]}"]
    return "\n".join(lines)


def _parse(text: str) -> dict:
    text = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    return json.loads(fenced[1] if fenced else text)


def apply(db: Session, batch_path, *, result_path=None, model: str = "", provider: str = PROVIDER,
          refresh: bool = False) -> dict:
    """Validate an agent's answer and store it as AI analyses. Raises ValueError (nothing stored) when
    the answer does not match the schema or names items/projects that are not in the batch."""
    batch = json.loads(batch_path.read_text(encoding="utf-8"))
    result_path = result_path or batch_path.with_name(batch["result_file"])
    data = BatchEnrichmentResult.model_validate(_parse(result_path.read_text(encoding="utf-8"))).model_dump()
    projects = [p for p in db.query(models.Project) if p.slug in batch["project_fingerprints"]]
    requested = {item["id"] for item in batch["items"]}
    validate_batch(data, requested, {p.slug for p in projects})

    stats = {"applied": 0, "changed": 0, "already_analysed": 0, "missing": 0,
             "unanswered": len(requested - {e["item_id"] for e in data["results"]})}
    analysed = _analysed(db)
    current_projects = {p.slug: project_fingerprint(p) for p in projects}
    for entry in data["results"]:
        item = db.get(models.Item, entry["item_id"])
        if item is None:
            stats["missing"] += 1
            continue
        if item.id in analysed and not refresh:
            stats["already_analysed"] += 1  # the gateway or another agent got there first
            continue
        fingerprint = content_fingerprint(item)
        if fingerprint != batch["content_fingerprints"].get(str(item.id)):
            stats["changed"] += 1  # read from outdated content: stays in the backlog
            continue
        # A project edited after export keeps its export-time fingerprint, so the pipeline redoes
        # relevance to that project only.
        previous = db.get(models.ItemAnalysisState, item.id)
        project_fps = dict((previous.provenance or {}).get("project_fingerprints", {})) if previous else {}
        project_fps.update({slug: batch["project_fingerprints"].get(slug, "") for slug in current_projects})
        result = Enrichment(
            summary=entry["summary"], why_interesting=entry["why_interesting"],
            capability_names=[name.strip() for name in entry["capabilities"]], relevance=entry["relevance"],
            provenance={"mode": "ai", "provider": provider, "model": model,
                        "prompt_version": lang_version(BATCH_PROMPT_VERSION), "batch_size": len(requested),
                        "content_fingerprint": fingerprint, "project_fingerprints": project_fps})
        pipeline._apply_result(db, item, projects, result)
        stats["applied"] += 1
    db.commit()

    done = batch_path.parent / "done"
    done.mkdir(exist_ok=True)
    batch_path.replace(done / batch_path.name)  # unanswered items return to the backlog
    result_path.replace(done / result_path.name)
    return stats


def release(work_dir=None) -> int:
    """Drop open batches (their items return to the backlog). Returns how many."""
    paths = _open_batches(work_dir or WORK_DIR)
    for path in paths:
        path.unlink()
    return len([p for p in paths if not p.name.endswith(".result.json")])


def status(db: Session, work_dir=None) -> dict:
    """Backlog size, AI analyses by provider (the gateway's model provider, or claude-code), open batches."""
    providers: dict[str, int] = {}
    for state in db.query(models.ItemAnalysisState):
        provenance = state.provenance or {}
        if provenance.get("mode") == "ai":
            key = provenance.get("provider") or "gateway"
            providers[key] = providers.get(key, 0) + 1
    return {"backlog": len(backlog(db)), "analysed": providers, "open_batches": len(_batch_files(work_dir or WORK_DIR))}
