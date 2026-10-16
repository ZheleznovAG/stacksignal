"""Entity resolution: treats a package that is really the same technology as an already-tracked
GitHub repository as one thing, not two unrelated items with separate momentum, maturity and
relevance scores.

Deliberately narrow and high-precision: a package is linked to a repository only through a
repository/homepage URL the registry itself publishes, never through fuzzy name matching. Fuzzy
matching on names would catch more pairs but would also confidently merge unrelated projects that
happen to share a common name -- a false merge is worse than a missed one, since it would blend
one technology's momentum and relevance into another's. Papers and models are not linked yet: an
arXiv abstract or a Hugging Face model card can mention a repository, but neither collector
extracts it today, so there is nothing precise to key on.
"""

import re

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import models

_GITHUB_REPO = re.compile(r"github\.com[:/]+([^/\s]+)/([^/\s#?]+)", re.IGNORECASE)
_NOT_A_REPO_OWNER = {"sponsors", "apps", "marketplace", "orgs", "topics"}


def _repo_full_name(url: str | None) -> str | None:
    """"owner/repo" from a GitHub URL in any of the shapes registries publish it in
    ("https://github.com/o/r", "git+https://github.com/o/r.git", "github.com/o/r/issues"),
    or None if the URL is missing or does not point at a specific repository."""
    if not url:
        return None
    match = _GITHUB_REPO.search(url)
    if not match:
        return None
    owner, repo = match.group(1), re.sub(r"\.git$", "", match.group(2))
    if not owner or not repo or owner.lower() in _NOT_A_REPO_OWNER:
        return None
    return f"{owner}/{repo}"


def link_packages_to_repos(db: Session) -> int:
    """Links each not-yet-linked, live PyPI/npm package to its GitHub repository, when one of the
    already-tracked repos matches. Returns how many new links were made. Cheap (indexed lookups
    only), so it runs on every collection, retrying packages that had no match yet -- the matching
    repository may simply not have been collected the first time."""
    candidates = (db.query(models.Item)
                  .filter(models.Item.source.in_(["pypi", "npm"]), models.Item.archived.is_(False),
                          models.Item.canonical_item_id.is_(None)).all())
    linked = 0
    for item in candidates:
        raw = item.raw or {}
        full_name = _repo_full_name(raw.get("repository")) or _repo_full_name(raw.get("homepage"))
        if not full_name:
            continue
        repo = (db.query(models.Item)
                .filter(models.Item.source == "github", models.Item.archived.is_(False),
                        func.lower(models.Item.external_id) == full_name.lower())
                .one_or_none())
        if repo is not None and repo.id != item.id:
            item.canonical_item_id = repo.id
            linked += 1
    return linked
