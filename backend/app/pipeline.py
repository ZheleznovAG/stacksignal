"""Orchestrates one end-to-end collection pass: fetch -> upsert -> score -> enrich ->
recompute relevance -> auto-advance radar status -> synthesize opportunities.

Called both by the scheduler (daily) and by the manual "run now" API endpoint.
"""

import datetime
import logging
import threading
import time
from contextlib import contextmanager

from sqlalchemy.orm import Session

from . import authors, config, contexts, discovery, embeddings, entities, feedback, history, models, quality, queries, release_watch, scoring, stack_watch
from .collectors import github, huggingface
from .ai_analysis import AIContext
from .ai_client import AIError
from .capabilities import consolidate as consolidate_capabilities
from .enrichment import (content_fingerprint, enrich_batch, enrich_item, heuristic_enrich,
                         project_fingerprint, score_relevance)
from .opportunities import synthesize_opportunities
from .utils import slugify, utcnow

# Share of a run's AI calls that relevance-only work may take while full analyses are waiting too.
RELEVANCE_BUDGET_SHARE = 0.4

logger = logging.getLogger("stacksignal.pipeline")

COLLECTORS = {
    models.Source.GITHUB.value: github.collect,
    models.Source.HUGGINGFACE.value: huggingface.collect,
}

# Sources without a global "hot" list (papers, packages) take part through project searches and refresh only.
ALL_SOURCES = [*COLLECTORS, models.Source.ARXIV.value, models.Source.NPM.value, models.Source.PYPI.value]

def _parse_dt(value) -> datetime.datetime | None:
    if not value:
        return None
    if isinstance(value, datetime.datetime):
        return value
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _upsert_item(db: Session, payload: dict) -> tuple[models.Item, bool]:
    existing = (
        db.query(models.Item)
        .filter_by(source=payload["source"], external_id=payload["external_id"])
        .one_or_none()
    )
    is_new = existing is None
    item = existing or models.Item(source=payload["source"], external_id=payload["external_id"])

    item.name = payload["name"]
    item.owner = payload["owner"]
    item.url = payload["url"]
    item.description = payload["description"]
    item.topics = payload["topics"]
    item.stars = payload["stars"]
    item.forks = payload["forks"]
    item.downloads = payload["downloads"]
    item.likes = payload["likes"]
    item.source_created_at = _parse_dt(payload["source_created_at"])
    item.source_updated_at = _parse_dt(payload["source_updated_at"])
    item.raw = payload["raw"]
    item.last_seen_at = utcnow()

    if is_new:
        item.first_seen_at = utcnow()
        item.status = models.Status.NEW
        db.add(item)
        db.flush()
        db.add(models.StatusHistory(item_id=item.id, status=models.Status.NEW, note="Discovered"))
    return item, is_new


def _get_or_create_capability(db: Session, name: str) -> models.Capability:
    slug = slugify(name)
    cap = db.query(models.Capability).filter_by(slug=slug).one_or_none()
    if cap is None:
        cap = models.Capability(name=name, slug=slug)
        db.add(cap)
        db.flush()
    return cap


def _apply_enrichment(db: Session, item: models.Item, projects: list[models.Project], *, context=None) -> None:
    result = enrich_item(item, projects, context=context) if context is not None else enrich_item(item, projects)
    _apply_result(db, item, projects, result)


def _replace_relevance(db: Session, item: models.Item, projects: list[models.Project], relevance: dict,
                       only_slugs: set[str] | None = None) -> None:
    """Replace the item's relevance rows (all of them, or only for `only_slugs`): if a pass no longer
    considers the item relevant to a project, that stale judgment must not survive. Done as a diff
    through the ORM -- update the rows that stay, delete the rest, add the new ones -- because a bulk
    delete behind the session's back left stale objects in it that a re-used primary key then collided with."""
    by_slug = {p.slug: p for p in projects}
    in_scope = {p.id for slug, p in by_slug.items()
                if contexts.analysis_allowed(p) and (only_slugs is None or slug in only_slugs)}
    rows = db.query(models.ItemProjectRelevance).filter_by(item_id=item.id).all()
    existing = {r.project_id: r for r in rows if r.project_id in in_scope}
    wanted: dict[int, dict] = {}
    for slug, info in relevance.items():
        project = by_slug.get(slug)
        if project is not None and project.id in in_scope:
            wanted[project.id] = info
    for project_id, row in existing.items():
        if project_id not in wanted:
            db.delete(row)
            rows.remove(row)
    for project_id, info in wanted.items():
        score = float(info.get("score", 0))
        row = existing.get(project_id)
        if row is None:
            row = models.ItemProjectRelevance(item_id=item.id, project_id=project_id, score=score, base_score=score,
                                              rationale=info.get("rationale", ""))
            db.add(row)
            rows.append(row)
        else:
            row.score, row.base_score, row.rationale = score, score, info.get("rationale", "")
    db.flush()
    db.expire(item, ["relevances"])
    # The user's votes outrank the analysis and must survive re-analysis.
    feedback.apply_votes(db, item, projects=projects, relevances=rows)


def _suggest_status(db: Session, item: models.Item, provenance: dict) -> None:
    """Only an AI judgment may advance the radar on its own. Keyword matching is good enough to
    rank a list but not to put "Trial" next to an item as if someone had decided it."""
    max_relevance = max((r.score for r in item.relevances if contexts.analysis_allowed(r.project)), default=0.0)
    if item.status == models.Status.NEW and provenance.get("mode") == "ai":
        new_status = scoring.suggest_status(item.momentum_score, item.maturity_score, max_relevance)
        if new_status != item.status:
            item.status = new_status
            item.status_updated_at = utcnow()
            db.add(models.StatusHistory(
                item_id=item.id, status=new_status,
                note=f"Auto-suggested (momentum={item.momentum_score}, "
                     f"maturity={item.maturity_score}, relevance={max_relevance:.2f})"))


def _apply_result(db: Session, item: models.Item, projects: list[models.Project], result) -> None:
    item.llm_summary = result.summary
    item.why_interesting = result.why_interesting
    item.last_enriched_at = utcnow()
    state = db.get(models.ItemAnalysisState, item.id)
    if state is None:
        state = models.ItemAnalysisState(item_id=item.id)
        db.add(state)
    state.provenance = result.provenance

    db.query(models.ItemCapability).filter_by(item_id=item.id).delete()
    seen_capabilities = set()
    for cap_name in result.capability_names:
        if slugify(cap_name) in seen_capabilities:
            continue
        seen_capabilities.add(slugify(cap_name))
        cap = _get_or_create_capability(db, cap_name)
        db.add(models.ItemCapability(item_id=item.id, capability_id=cap.id, confidence=0.7))

    _replace_relevance(db, item, projects, result.relevance)
    _suggest_status(db, item, result.provenance)
    db.flush()  # keep ItemAnalysisState visible to db.get() when an item is applied twice in a run


def _apply_relevance_only(db: Session, item: models.Item, projects: list[models.Project], slugs: set[str],
                          relevance: dict, fingerprints: dict[str, str]) -> None:
    """A relevance-only pass: summary and capabilities stay, only the rows for `slugs` are redone."""
    _replace_relevance(db, item, projects, relevance, only_slugs=slugs)
    state = db.get(models.ItemAnalysisState, item.id)
    provenance = dict(state.provenance)
    provenance["project_fingerprints"] = {**provenance.get("project_fingerprints", {}),
                                          **{s: fingerprints[s] for s in slugs}}
    state.provenance = provenance  # reassigned: JSON columns do not track in-place changes
    _suggest_status(db, item, provenance)
    db.flush()


def _fresh_scoped_relevance(provenance: dict, fingerprint: str, project_fps: dict[str, str]) -> set[str]:
    """Relevance read by an agent can be current even when the item's summary is heuristic."""
    scopes = provenance.get("relevance_provenance", {})
    return {slug for slug, fp in project_fps.items()
            if scopes.get(slug, {}).get("mode") == "ai"
            and scopes[slug].get("content_fingerprint") == fingerprint
            and scopes[slug].get("project_fingerprint") == fp}


def _enrich_items(db: Session, items: list[models.Item], projects: list[models.Project], context: AIContext,
                  discovered_ids: set[int] | None = None) -> dict:
    """Two phases. (1) Every item without an AI analysis gets the free heuristic result, so
    radar/relevance are always populated. (2) The items most worth a real look — ranked by
    relevance x momentum, not raw momentum — go to the gateway in batches until the per-run call
    budget (minus the reserve) is spent.

    What is "current" is tracked in two parts. The item's content decides whether its summary and
    capabilities are current; each project's profile decides whether relevance to that project is.
    Editing one project therefore costs a short relevance-only call per item for that project, not a
    full re-analysis of the catalogue."""
    stats = {"heuristic": 0, "ai": 0, "relevance_refreshed": 0, "skipped_current": 0, "batches": 0,
             "relevance_batches": 0, "batch_errors": 0, "error_codes": []}
    projects = [p for p in projects if contexts.analysis_allowed(p)]
    project_fps = {p.slug: project_fingerprint(p) for p in projects}
    wanted = []      # full analysis: (priority, item, content fingerprint)
    relevance_wanted = []  # relevance only: (priority, item, stale slugs)
    for item in items:
        fingerprint = content_fingerprint(item)
        state = db.get(models.ItemAnalysisState, item.id)
        provenance = state.provenance if state else {}
        was_ai = provenance.get("mode") == "ai"
        if was_ai:
            max_relevance = max((r.score for r in item.relevances), default=0.0)
        else:
            result = heuristic_enrich(item, projects)
            scoped = _fresh_scoped_relevance(provenance, fingerprint, project_fps)
            if provenance.get("relevance_provenance"):
                result.provenance["relevance_provenance"] = provenance["relevance_provenance"]
                result.provenance["project_fingerprints"] = provenance.get("project_fingerprints", {})
            # A new collection must not replace fresh agent judgments with keyword overlap.
            _apply_result(db, item, [p for p in projects if p.slug not in scoped], result)
            stats["heuristic"] += 1
            max_relevance = max((r.score for r in item.relevances), default=0.0)
        # The floors keep keyword-poor but possibly relevant items in the running.
        priority = (0.2 + max_relevance) * (0.3 + item.momentum_score)
        if discovered_ids and item.id in discovered_ids:
            priority += 0.15  # found by a search written for one of the user's projects
        if was_ai and provenance.get("content_fingerprint") == fingerprint:
            known = provenance.get("project_fingerprints", {})
            stale = {slug for slug, fp in project_fps.items() if known.get(slug) != fp}
            if stale:
                relevance_wanted.append((priority, item, stale))
            else:
                stats["skipped_current"] += 1
            continue
        wanted.append((priority, item, fingerprint))

    db.commit()  # heuristic coverage is complete before the first AI request

    def has_budget() -> bool:
        return bool(context.client) and not context.unavailable and context.remaining - context.reserved > 0

    if not has_budget():
        return stats

    # Items whose summary is current but whose project profiles changed: short relevance-only calls,
    # grouped by which projects are stale so every call asks the same question. They go FIRST, with a
    # capped share of the budget when full analyses are also waiting: new items arrive by the hundred every
    # day and would otherwise use every call, leaving an edited project's relevance stale for good.
    groups: dict[frozenset, list] = {}
    for priority, item, stale in sorted(relevance_wanted, key=lambda entry: entry[0], reverse=True):
        groups.setdefault(frozenset(stale), []).append(item)
    by_slug = {p.slug: p for p in projects}
    available = context.remaining - context.reserved
    relevance_calls = max(1, int(available * RELEVANCE_BUDGET_SHARE)) if wanted else available
    floor = context.remaining - relevance_calls  # relevance work stops when the budget falls to here
    for stale, group in groups.items():
        for offset in range(0, len(group), config.AI_RELEVANCE_BATCH_SIZE):
            if not has_budget() or context.remaining <= floor:
                break
            batch = group[offset:offset + config.AI_RELEVANCE_BATCH_SIZE]
            try:
                results = score_relevance(batch, [by_slug[s] for s in sorted(stale)], context=context)
            except AIError as error:
                stats["batch_errors"] += 1
                stats["error_codes"].append(error.code)
                logger.warning("AI relevance batch of %d items failed: %s", len(batch), error.code)
                continue
            stats["relevance_batches"] += 1
            for item in batch:
                if item.id in results:
                    _apply_relevance_only(db, item, projects, set(stale), results[item.id], project_fps)
                    stats["relevance_refreshed"] += 1
            db.commit()  # a later batch failure keeps this completed batch

    wanted.sort(key=lambda entry: entry[0], reverse=True)
    position = 0
    while position < len(wanted) and has_budget():
        chunk = wanted[position:position + config.AI_BATCH_SIZE]
        position += config.AI_BATCH_SIZE
        batch = [entry[1] for entry in chunk]
        fingerprints = {entry[1].id: entry[2] for entry in chunk}
        try:
            results = enrich_batch(batch, projects, context=context)
        except AIError as error:
            stats["batch_errors"] += 1
            stats["error_codes"].append(error.code)
            logger.warning("AI batch of %d items failed: %s", len(batch), error.code)
            continue  # the loop condition stops us if the gateway is down or the budget is gone
        stats["batches"] += 1
        for item in batch:
            result = results.get(item.id)
            if result is None:
                continue
            result.provenance = {**result.provenance, "content_fingerprint": fingerprints[item.id],
                                 "project_fingerprints": dict(project_fps)}
            _apply_result(db, item, projects, result)
            stats["ai"] += 1
        db.commit()

    return stats


def _is_engaged(db: Session, item: models.Item) -> bool:
    state = db.get(models.ItemAnalysisState, item.id)
    return quality.has_engagement(item, state is not None and state.provenance.get("mode") == "ai")


def _existing(db: Session, payload: dict) -> models.Item | None:
    return (db.query(models.Item)
            .filter_by(source=payload["source"], external_id=payload["external_id"]).one_or_none())


def _keep(db: Session, payload: dict, *, targeted: bool, refresh: bool) -> bool:
    """The quality gate, except that anything the user or the AI already cares about is always
    kept up to date."""
    if refresh or quality.gate(payload, targeted=targeted)[0]:
        return True
    existing = _existing(db, payload)
    return existing is not None and _is_engaged(db, existing)


def _archive_known(db: Session, payload: dict) -> bool:
    """A stored item that now fails the gate and nobody cares about is archived, not deleted."""
    existing = _existing(db, payload)
    if existing is None or existing.archived or _is_engaged(db, existing):
        return False
    existing.archived = True
    return True


def _record_discoveries(db: Session, item: models.Item, vias: list[dict], is_new: bool, new_per_query: dict) -> None:
    recorded = set()  # sessions do not autoflush: two queries of one project can find the same item
    for via in vias:
        if is_new:
            new_per_query[via["query_id"]] = new_per_query.get(via["query_id"], 0) + 1
        known = via["project_slug"] in recorded or (
            db.query(models.ItemDiscovery).filter_by(item_id=item.id, project_slug=via["project_slug"]).first())
        recorded.add(via["project_slug"])
        if not known:
            db.add(models.ItemDiscovery(item_id=item.id, project_slug=via["project_slug"], query_text=via["query_text"]))


def _generate_ai_queries(db: Session, projects: list[models.Project], context: AIContext) -> None:
    """Generate missing or stale AI queries; failures keep the previous searches available."""
    if context.client is None:
        return
    existing: dict[str, list[models.SearchQuery]] = {}
    for query in db.query(models.SearchQuery).filter_by(origin="ai"):
        existing.setdefault(query.project_slug, []).append(query)
    missing = [p for p in projects if contexts.discovery_allowed(p) and
               (not existing.get(p.slug) or any(queries.is_stale(q, queries.query_profile_fingerprint(p))
                                                for q in existing[p.slug]))]
    if not missing:
        return
    try:
        logger.info("AI query generation: %d new queries", queries.generate_with_ai(db, missing, context=context))
    except AIError as error:
        logger.info("AI query generation skipped: %s", error.code)


class CollectionBusy(RuntimeError):
    """A collection run is already in progress (scheduled and manual runs must not overlap)."""


_RUN_LOCK = threading.Lock()


@contextmanager
def _stage(stages: list[dict], name: str):
    """Times one top-level phase of a run and records whether it let an exception through. A
    phase that already swallows its own errors (most do, to keep one bad source from failing the
    whole run) will always show "ok" here -- this answers "where did the time go" for every run
    and "which phase broke" for the rarer fatal ones."""
    start = time.monotonic()
    started_at = utcnow()
    entry = {"name": name, "started_at": started_at.isoformat()}
    try:
        yield entry  # a phase may add its own counts, e.g. how many items the AI actually read
    except Exception as error:
        entry["status"] = "error"
        entry["error"] = f"{error.__class__.__name__}: {error}"[:300]
        raise
    else:
        entry["status"] = "ok"
    finally:
        entry["duration_s"] = round(time.monotonic() - start, 3)
        stages.append(entry)


def run_collection(db: Session, sources: list[str] | None = None, *, transport=None) -> models.CollectionRun:
    if not _RUN_LOCK.acquire(blocking=False):
        raise CollectionBusy("a collection run is already in progress")
    try:
        return _run_collection(db, sources, transport=transport)
    finally:
        _RUN_LOCK.release()


def _checkpoint(db: Session, run: models.CollectionRun, stages: list[dict]) -> None:
    """Persist a completed stage; later failures can only roll back work since here."""
    run.stages = list(stages)
    db.commit()


def _run_collection(db: Session, sources: list[str] | None = None, *, transport=None) -> models.CollectionRun:
    """One full pass: global lists + searches written for each project + refresh of the items that
    matter -> quality gate -> score -> analyse -> briefings -> housekeeping."""
    sources = sources or list(ALL_SOURCES)
    run = models.CollectionRun(sources=sources, status="running")
    db.add(run)
    db.flush()
    run_id = run.id
    db.commit()
    checkpoint_saved = False

    all_items: list[models.Item] = []
    new_count = 0
    summary: dict = {"rejected": 0, "archived_now": 0, "revived": 0}
    ai_context = AIContext()
    stages: list[dict] = []
    try:
        candidates: dict[tuple[str, str], dict] = {}

        # 1. The global "what is hot" lists.
        with _stage(stages, "collect"):
            collection_errors = []
            for source in sources:
                collector = COLLECTORS.get(source)
                if collector is None:
                    continue
                try:
                    payloads = collector(transport=transport) if transport is not None else collector()
                except Exception as exc:
                    logger.warning("Collector %s failed: %s", source, exc)
                    collection_errors.append(f"{source}: {exc}\n")
                    continue
                for payload in payloads:
                    candidates.setdefault((payload["source"], payload["external_id"]), {"payload": payload, "vias": []})
            if collection_errors:
                run.error += "".join(collection_errors)

        # 1b. Your GitHub stars (always kept, Watch the first time) and the authors behind them.
        if authors.sync_enabled(sources):
            with _stage(stages, "github_stars") as stage:
                _checkpoint(db, run, stages)  # nothing pending while GitHub answers
                fetched = None
                try:
                    fetched = authors.fetch_github(config.GITHUB_LOGIN, transport=transport)
                except github.RateLimited:
                    stage["rate_limited"] = True
                except Exception as exc:
                    logger.warning("GitHub stars sync failed: %s", exc)
                    run.error += f"github stars: {exc}\n"
                if fetched is not None:
                    stage.update(authors.apply_github(db, fetched, login=config.GITHUB_LOGIN))
                    for star in fetched["starred"]:
                        payload = star["payload"]
                        entry = candidates.setdefault((payload["source"], payload["external_id"]),
                                                      {"payload": payload, "vias": []})
                        entry["refresh"] = True  # you chose it: the quality gate does not apply
                        entry["starred_at"] = star["starred_at"]
            _checkpoint(db, run, stages)

        # 1c. Repositories of the authors you watch: new ones first, a few per run.
        due, watch_stats = authors.prepare_watch(db) if authors.watch_enabled(sources) else ([], {})
        if due:
            with _stage(stages, "author_watch") as stage:
                stage.update(watch_stats)
                _checkpoint(db, run, stages)  # suggestions are durable; no writes while GitHub answers
                fetched, failed = {}, 0
                for author_id, login in due:
                    try:
                        fetched[author_id] = github.user_repos(login, transport=transport)
                    except github.RateLimited:
                        stage["rate_limited"] = True
                        break
                    except Exception as exc:
                        failed += 1
                        logger.warning("Repositories of %s failed: %s", login, exc.__class__.__name__)
                picks = authors.apply_repos(db, fetched)
                stage.update(checked=len(fetched), failed=failed, promoted=len(picks))
                for pick in picks:
                    payload = pick["payload"]
                    entry = candidates.setdefault((payload["source"], payload["external_id"]),
                                                  {"payload": payload, "vias": []})
                    entry["refresh"] = True  # chosen by the author filter, not the popularity gate
                    entry["author"] = pick["login"]
            _checkpoint(db, run, stages)

        # 2. Searches written for the user's projects, and fresh numbers for items that matter.
        projects = db.query(models.Project).all()
        ran_ids: list[int] = []
        with _stage(stages, "discovery"):
            if config.DISCOVERY_ENABLED and projects:
                queries.ensure_default_queries(db, projects)
                _checkpoint(db, run, stages)  # local discovery work precedes any remote request
                checkpoint_saved = True
                _generate_ai_queries(db, projects, ai_context)
                found, query_stats = discovery.run_queries(db, sources=set(sources), transport=transport)
                ran_ids = query_stats.pop("ran_ids", [])
                _checkpoint(db, run, stages)
                for entry in found:
                    key = (entry["payload"]["source"], entry["payload"]["external_id"])
                    candidates.setdefault(key, {"payload": entry["payload"], "vias": []})["vias"].extend(entry["vias"])
                refreshed, refresh_stats = discovery.refresh_tracked(
                    db, exclude=set(candidates), sources=set(sources), transport=transport)
                for payload in refreshed:
                    candidates.setdefault((payload["source"], payload["external_id"]),
                                          {"payload": payload, "vias": [], "refresh": True})
                summary.update(queries=query_stats, refresh=refresh_stats)
                try:  # new versions of what your projects depend on
                    summary["stack_watch"] = stack_watch.check(db, transport=transport)
                except Exception:
                    db.rollback()
                    logger.exception("Stack watch failed")
                _checkpoint(db, run, stages)
            if config.DISCOVERY_ENABLED and "github" in sources:
                try:  # new releases of the repositories you track (they do not depend on any project)
                    summary["releases"] = release_watch.check(db, transport=transport)
                except Exception:
                    db.rollback()
                    logger.exception("Release watch failed")
        _checkpoint(db, run, stages)
        checkpoint_saved = True

        # 3. Gate, store, score.
        with _stage(stages, "gate_and_score"):
            new_per_query: dict[int, int] = {}
            discovered_ids: set[int] = set()
            for entry in candidates.values():
                payload, vias = entry["payload"], entry["vias"]
                source = payload["source"]
                if not _keep(db, payload, targeted=bool(vias), refresh=entry.get("refresh", False)):
                    summary["rejected"] += 1
                    summary["archived_now"] += int(_archive_known(db, payload))
                    continue
                item, is_new = _upsert_item(db, payload)
                db.flush()  # item.id must exist before we can look up/record snapshots
                if item.archived:
                    item.archived = False
                    summary["revived"] += 1

                velocity = history.recent_velocity(db, item)
                item.momentum_score = scoring.compute_momentum(item, velocity)
                item.maturity_score = scoring.compute_maturity(item)
                if velocity is not None:
                    rate = velocity["stars_per_day"] if source == models.Source.GITHUB.value else velocity["attention_per_day"]
                    item.stars_delta_7d = round(rate * 7, 2)
                else:
                    item.stars_delta_7d = 0.0

                history.record_snapshot(db, item)
                history.prune_snapshots(db, item.id)

                if vias:
                    discovered_ids.add(item.id)
                    _record_discoveries(db, item, vias, is_new, new_per_query)
                if "starred_at" in entry:
                    discovered_ids.add(item.id)  # first in line for an AI read, like a targeted find
                    summary["starred_new"] = summary.get("starred_new", 0) + authors.mark_starred(
                        db, item, entry["starred_at"])
                if "author" in entry:
                    discovered_ids.add(item.id)
                    authors.mark_found(db, item, entry["author"])
                all_items.append(item)
                if is_new:
                    new_count += 1
            for query_id, count in new_per_query.items():
                query = db.get(models.SearchQuery, query_id)
                if query is not None:
                    query.last_new = count
            for query_id in ran_ids:  # what each query paid off this run decides how often it runs next
                query = db.get(models.SearchQuery, query_id)
                if query is not None:
                    gained = new_per_query.get(query_id, 0)
                    query.runs = (query.runs or 0) + 1
                    query.total_new = (query.total_new or 0) + gained
                    query.empty_streak = 0 if gained else (query.empty_streak or 0) + 1
            db.flush()
        run.items_found = len(all_items)
        run.items_new = new_count
        _checkpoint(db, run, stages)

        with _stage(stages, "entity_linking"):
            linked = entities.link_packages_to_repos(db)
            if linked:
                logger.info("Entity resolution: linked %d package(s) to an already-tracked repository", linked)
            db.flush()
        _checkpoint(db, run, stages)

        # 4. Score every item before selecting the most relevant ones for the AI budget.
        with _stage(stages, "enrich") as info:
            # Hold back one call each for opportunities and capability consolidation.
            ai_context.reserved = min(2, max(ai_context.remaining - 1, 0))
            stats = _enrich_items(db, all_items, projects, ai_context, discovered_ids)
            logger.info("Enrichment: %s", stats)
            enriched = stats["heuristic"] + stats["ai"]
            # items_enriched counts keyword fallbacks too, so a dead gateway looked like a healthy run.
            # Keep the AI's own share visible: zero while AI is configured means it read nothing.
            info.update(ai=stats["ai"], heuristic=stats["heuristic"], ai_errors=stats["batch_errors"],
                        relevance_refreshed=stats["relevance_refreshed"], error_codes=stats["error_codes"])
            db.flush()
        run.items_enriched = enriched
        _checkpoint(db, run, stages)

        with _stage(stages, "opportunities"):
            ai_context.reserved = min(1, max(ai_context.remaining - 1, 0))
            synthesize_opportunities(db, projects, context=ai_context)
        _checkpoint(db, run, stages)

        with _stage(stages, "capabilities"):
            ai_context.reserved = 0
            try:
                logger.info("Capability consolidation: %s", consolidate_capabilities(db, context=ai_context))
            except AIError as error:
                logger.info("Capability consolidation skipped: %s", error.code)
        _checkpoint(db, run, stages)

        # Local embeddings for semantic chat retrieval: cheap and not AI-budget-limited, so every
        # live item gets one, not just the batch enriched above.
        try:
            with _stage(stages, "embeddings"):
                logger.info("Embeddings computed/refreshed: %d", embeddings.ensure_embeddings(db))
        except Exception as error:
            db.rollback()
            logger.exception("Embedding computation failed")
            run.error += f"embeddings: {error}\n"
        _checkpoint(db, run, stages)

        # 5. Keep the lists current.
        with _stage(stages, "housekeeping"):
            summary.update(discovery.housekeeping(db))
        logger.info("Collection: %s", summary)

        run.items_found = len(all_items)
        run.items_new = new_count
        run.items_enriched = enriched
        run.stages = stages
    except Exception as error:
        # Discard only work since the last stage/batch checkpoint. AI outcomes are
        # independently durable, including indeterminate attempts that must not retry.
        db.rollback()
        logger.exception("Collection failed")
        run = db.get(models.CollectionRun, run_id)
        run.status = "partial" if checkpoint_saved else "failed"
        run.finished_at = utcnow()
        run.error = f"fatal: {error.__class__.__name__}: {error}"[:500]
        run.stages = list(stages)
        db.commit()
        raise
    run.status = "partial" if run.error else "completed"
    run.finished_at = utcnow()
    db.commit()
    return run
