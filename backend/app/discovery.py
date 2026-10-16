"""Project-driven discovery, tracking and housekeeping.

  * run_queries()    runs the saved search queries (stalest first, within a per-run budget) and
                     returns candidates tagged with which project's query found them;
  * refresh_tracked() re-fetches the current numbers of items that matter to the user (status set,
                     voted, or highly relevant) — otherwise an item's stars/likes would only ever
                     update when it happened to reappear in a global list;
  * housekeeping()   archives untouched, long-unseen items so lists stay current.

Budgets exist because GitHub is rate-limited hard without a token (search 10/min, other calls
60/hour). On RateLimited the source is skipped for the rest of the run; nothing is lost, the
stalest queries and items simply go first next time.
"""

import datetime
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from . import config, contexts, models, quality
from .collectors import arxiv, github, huggingface, npm, pypi
from .utils import utcnow

logger = logging.getLogger("stacksignal.discovery")

TRACKED_MIN_RELEVANCE = quality.AI_KEEP_MIN_RELEVANCE


BACKOFF_AFTER = 4          # runs in a row that found nothing new before a query is slowed down
BACKOFF_MAX_DAYS = 14


def backoff_days(empty_streak: int) -> int:
    """How long a query that keeps finding nothing new rests: 2, 4, 8, then 14 days at most."""
    if empty_streak < BACKOFF_AFTER:
        return 0
    return min(2 ** (empty_streak - BACKOFF_AFTER + 1), BACKOFF_MAX_DAYS)


def _resting(query: models.SearchQuery, now: datetime.datetime) -> bool:
    days = backoff_days(query.empty_streak or 0)
    return bool(days and query.last_run_at and now - query.last_run_at < datetime.timedelta(days=days))


def due_queries(db: Session, *, github_limit: int, hf_limit: int, arxiv_limit: int | None = None,
                npm_limit: int | None = None) -> list[models.SearchQuery]:
    """Enabled queries never run or run longest ago, up to the per-source limits. A query that has found
    nothing new for several runs in a row rests (2, 4, 8, 14 days), so the budget goes to ones that pay off."""
    now = utcnow()
    picked: list[models.SearchQuery] = []
    disabled = [p.slug for p in db.query(models.Project) if not contexts.discovery_allowed(p)]
    arxiv_limit = config.ARXIV_QUERIES_PER_RUN if arxiv_limit is None else arxiv_limit
    npm_limit = config.NPM_QUERIES_PER_RUN if npm_limit is None else npm_limit
    for source, limit in (("github", github_limit), ("huggingface", hf_limit), ("arxiv", arxiv_limit), ("npm", npm_limit)):
        rows = (
            db.query(models.SearchQuery)
            .filter_by(source=source, enabled=True)
            .filter(~models.SearchQuery.project_slug.in_(disabled))
            .order_by(models.SearchQuery.last_run_at.is_(None).desc(), models.SearchQuery.last_run_at, models.SearchQuery.id)
            .all()
        )
        picked.extend([q for q in rows if not _resting(q, now)][:max(limit, 0)])
    return picked


@dataclass(frozen=True)
class _QueryRequest:
    id: int
    source: str
    text: str
    project_slug: str


def _search(query: _QueryRequest, transport) -> list[dict]:
    if query.source == "github":
        return github.search_repos(query.text, transport=transport)
    if query.source == "arxiv":
        return arxiv.search_papers(query.text, transport=transport)
    if query.source == "npm":
        return npm.search_packages(query.text, transport=transport)
    return huggingface.search_models(query.text, transport=transport)


def _get_one(source: str, external_id: str, transport) -> dict | None:
    getters = {"github": github.get_repo, "huggingface": huggingface.get_model, "npm": npm.get_package,
               "pypi": pypi.get_package, "arxiv": arxiv.get_paper}
    return getters[source](external_id, transport=transport)


def run_queries(db: Session, *, github_limit: int | None = None, hf_limit: int | None = None,
                arxiv_limit: int | None = None, npm_limit: int | None = None,
                sources: set[str] | None = None, transport=None) -> tuple[list[dict], dict]:
    """Execute due queries. Returns (candidates, stats); each candidate is
    {"payload": <normalized>, "vias": [{"project_slug", "query_id", "query_text"}]}."""
    github_limit = config.GITHUB_QUERIES_PER_RUN if github_limit is None else github_limit
    hf_limit = config.HF_QUERIES_PER_RUN if hf_limit is None else hf_limit
    stats = {"queries": 0, "candidates": 0, "rejected": 0, "errors": 0, "github_rate_limited": False, "ran_ids": []}
    unreachable: set[str] = set()  # sources that failed once this run: their remaining queries are skipped
    candidates: dict[tuple[str, str], dict] = {}
    now = utcnow()

    requests = [_QueryRequest(q.id, q.source, q.text, q.project_slug) for q in
                due_queries(db, github_limit=github_limit, hf_limit=hf_limit, arxiv_limit=arxiv_limit,
                            npm_limit=npm_limit)]
    db.commit()  # query generation is durable; the network phase uses no ORM objects
    outcomes = []
    for query in requests:
        if sources is not None and query.source not in sources:
            continue
        if (query.source == "github" and stats["github_rate_limited"]) or query.source in unreachable:
            continue
        try:
            payloads = _search(query, transport)
        except github.RateLimited:
            stats["github_rate_limited"] = True
            logger.warning("GitHub rate limit reached; skipping remaining GitHub queries this run")
            continue
        except Exception as error:
            stats["errors"] += 1
            logger.warning("Query %r failed: %s", query.text, error.__class__.__name__)
            if query.source in ("arxiv", "npm"):  # a slow retry per query would waste minutes when the site is down
                unreachable.add(query.source)
            continue
        stats["queries"] += 1
        stats["ran_ids"].append(query.id)
        kept = 0
        for payload in payloads:
            passes, _ = quality.gate(payload, targeted=True)
            if not passes:
                stats["rejected"] += 1
                continue
            kept += 1
            entry = candidates.setdefault((payload["source"], payload["external_id"]), {"payload": payload, "vias": []})
            entry["vias"].append({"project_slug": query.project_slug, "query_id": query.id, "query_text": query.text})
        outcomes.append((query, kept))
    for request, kept in outcomes:
        query = db.get(models.SearchQuery, request.id)
        # A user may delete or edit a query while its old request is in flight.
        if query is not None and (query.source, query.text, query.project_slug) == (
                request.source, request.text, request.project_slug):
            query.last_run_at = now
            query.last_found = kept
            query.last_new = 0  # filled in once the pipeline knows which items are new
    db.flush()
    stats["candidates"] = len(candidates)
    return list(candidates.values()), stats


def _max_relevance(item: models.Item) -> float:
    return max((r.score for r in item.relevances), default=0.0)


def tracked_items(db: Session) -> list[models.Item]:
    """Items worth keeping current: the user acted on them (status, vote, note), or an AI analysis
    found them strongly relevant. Keyword-only matches are not worth a rate-limited request."""
    ai = {s.item_id for s in db.query(models.ItemAnalysisState).all() if s.provenance.get("mode") == "ai"}
    return [
        item for item in db.query(models.Item).filter(models.Item.archived.is_(False)).all()
        if quality.has_engagement(item, False)
        or (item.id in ai and _max_relevance(item) >= TRACKED_MIN_RELEVANCE)
    ]


def refresh_tracked(db: Session, *, exclude: set[tuple[str, str]], github_limit: int | None = None,
                    hf_limit: int | None = None, sources: set[str] | None = None,
                    transport=None) -> tuple[list[dict], dict]:
    """Fetch fresh numbers for tracked items not already collected in this run, least recently
    seen first. Returns (payloads, stats)."""
    github_limit = config.REFRESH_GITHUB_PER_RUN if github_limit is None else github_limit
    hf_limit = config.REFRESH_HF_PER_RUN if hf_limit is None else hf_limit
    budget = {"github": github_limit, "huggingface": hf_limit, "npm": config.REFRESH_NPM_PER_RUN,
              "pypi": config.REFRESH_PYPI_PER_RUN, "arxiv": 0}  # a paper's numbers never change
    stats = {"refreshed": 0, "missing": 0, "errors": 0, "github_rate_limited": False}
    payloads: list[dict] = []

    requests = [(getattr(i.source, "value", i.source), i.external_id)
                for i in sorted(tracked_items(db), key=lambda i: i.last_seen_at)]
    db.commit()  # includes query outcomes; refresh itself is read-only
    for source, external_id in requests:
        if (sources is not None and source not in sources) or (source, external_id) in exclude:
            continue
        if budget.get(source, 0) <= 0 or (source == "github" and stats["github_rate_limited"]):
            continue
        budget[source] -= 1
        try:
            payload = _get_one(source, external_id, transport)
        except github.RateLimited:
            stats["github_rate_limited"] = True
            continue
        except Exception as error:
            stats["errors"] += 1
            logger.warning("Refreshing %s failed: %s", external_id, error.__class__.__name__)
            continue
        if payload is None:
            stats["missing"] += 1  # deleted upstream: keep it, the user may still care
            continue
        payloads.append(payload)
        stats["refreshed"] += 1
    return payloads, stats


def housekeeping(db: Session, *, stale_days: int | None = None) -> dict:
    """Archive items nobody engaged with that no collection has seen for `stale_days`."""
    stale_days = config.STALE_DAYS if stale_days is None else stale_days
    cutoff = utcnow() - datetime.timedelta(days=stale_days)
    ai = {s.item_id for s in db.query(models.ItemAnalysisState).all() if s.provenance.get("mode") == "ai"}
    archived = 0
    for item in db.query(models.Item).filter(models.Item.archived.is_(False), models.Item.last_seen_at < cutoff).all():
        if not quality.has_engagement(item, item.id in ai):  # read by the AI counts only if strongly relevant
            item.archived = True
            archived += 1
    db.flush()
    return {"archived_stale": archived}
