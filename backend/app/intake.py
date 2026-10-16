"""Add URL: put a GitHub repository or Hugging Face model you found yourself into StackSignal.

It goes through the same steps as a collected item (numbers, momentum, keyword relevance, one AI
read when the gateway is available) but skips the quality gate — you chose it — and it is placed on
the radar as Watch, so it is never archived as untouched. Adding a known item only refreshes it.
"""

import re
from urllib.parse import unquote, urlparse

from sqlalchemy.orm import Session

from . import history, models, pipeline, scoring
from .ai_analysis import AIContext
from .collectors import arxiv, github, huggingface, npm, pypi
from .enrichment import content_fingerprint, enrich_item, heuristic_enrich, project_fingerprint
from .utils import utcnow

_HF_RESERVED = {"datasets", "spaces", "docs", "blog", "papers", "collections", "models", "organizations",
                "pricing", "join", "login", "settings", "tasks", "learn", "posts"}


class IntakeError(ValueError):
    """`code` is a stable machine-readable reason: invalid_url, unsupported_url, not_found, fetch_failed."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def parse_url(url: str) -> tuple[str, str]:
    """(source, external_id) for a GitHub repository or Hugging Face model URL (or owner/name)."""
    text = (url or "").strip()
    if not text or len(text) > 500:
        raise IntakeError("invalid_url")
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    parts = [part for part in parsed.path.split("/") if part]
    if parsed.scheme not in ("http", "https") or not host:
        raise IntakeError("invalid_url")
    if host == "github.com":
        if len(parts) < 2:
            raise IntakeError("invalid_url")
        return "github", f"{parts[0]}/{parts[1].removesuffix('.git')}"
    if host == "arxiv.org":
        match = re.fullmatch(r"/(?:abs|pdf|html)/(.+?)(?:\.pdf)?", parsed.path)
        paper = re.match(r"(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$", match.group(1)) if match else None
        if not paper:
            raise IntakeError("invalid_url")
        return "arxiv", paper.group(1)
    if host in ("npmjs.com", "npmjs.org"):
        parts = [unquote(part) for part in parts]
        if len(parts) >= 3 and parts[0] == "package" and parts[1].startswith("@"):
            return "npm", f"{parts[1]}/{parts[2]}"
        if len(parts) >= 2 and parts[0] == "package":
            return "npm", parts[1]
        raise IntakeError("invalid_url")
    if host == "pypi.org":
        if len(parts) >= 2 and parts[0] in ("project", "pypi"):
            return "pypi", parts[1]
        raise IntakeError("invalid_url")
    if host in ("huggingface.co", "hf.co"):
        if not parts or parts[0] in _HF_RESERVED:
            raise IntakeError("unsupported_url")  # datasets, spaces, docs... are not models
        return "huggingface", "/".join(parts[:2]) if len(parts) >= 2 else parts[0]
    raise IntakeError("unsupported_url")


def _fetch(source: str, external_id: str, transport=None) -> dict:
    try:
        getters = {"github": github.get_repo, "huggingface": huggingface.get_model, "arxiv": arxiv.get_paper,
                   "npm": npm.get_package, "pypi": pypi.get_package}
        payload = getters[source](external_id, transport=transport)
    except Exception as error:  # network failure, rate limit, unexpected answer
        raise IntakeError("fetch_failed") from error
    if payload is None:
        raise IntakeError("not_found")
    return payload


def add_url(db: Session, url: str, *, analyze: bool = True, transport=None, context=None) -> tuple[models.Item, bool]:
    """Returns (item, created). Raises IntakeError."""
    source, external_id = parse_url(url)
    payload = _fetch(source, external_id, transport)
    item, created = pipeline._upsert_item(db, payload)
    item.archived = False
    db.flush()

    velocity = history.recent_velocity(db, item)
    item.momentum_score = scoring.compute_momentum(item, velocity)
    item.maturity_score = scoring.compute_maturity(item)
    history.record_snapshot(db, item)

    if item.status == models.Status.NEW:
        item.status = models.Status.WATCH
        item.status_updated_at = utcnow()
        db.add(models.StatusHistory(item_id=item.id, status=models.Status.WATCH, note="Added by you from a URL"))

    projects = db.query(models.Project).all()
    result = (enrich_item(item, projects, context=context or AIContext(max_calls=1)) if analyze
              else heuristic_enrich(item, projects))
    if result.provenance.get("mode") == "ai":
        result.provenance = {**result.provenance, "content_fingerprint": content_fingerprint(item),
                             "project_fingerprints": {p.slug: project_fingerprint(p) for p in projects}}
    pipeline._apply_result(db, item, projects, result)
    db.flush()
    return item, created
