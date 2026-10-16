"""Release watch: notice new releases of the GitHub repositories you track.

"Tracked" here is stricter than for refreshing numbers: only what YOU did something about counts (a status you
set, a decision, a vote, a note, Add URL). A status the system suggested on its own does not: with dozens of
those, every digest would be a list of nightly builds. A new release of something you are trying or adopting is
exactly the change that should reach you. Each check is one API call, so a per-run budget applies and the ones
checked longest ago go first. Nightly builds and alpha/beta/rc tags are not stored as news.
"""

import re

import datetime
import logging

from sqlalchemy.orm import Session

from . import config, discovery, models
from .collectors import github
from .utils import utcnow

logger = logging.getLogger("stacksignal.release_watch")

DIGEST_DAYS = 7          # a release older than this is no longer news for the digest
NOTES_CHARS = 400
_AUTO_NOTES = ("Discovered", "Auto-suggested", "Reset:")
_NOISY_TAG = re.compile(r"nightly|canary|snapshot|(^|[-_.])build[-_.]?\d|^latest$|^dev$", re.IGNORECASE)
_PRE_TAG = re.compile(r"(\d|[-_.])(a|b|rc|alpha|beta|dev|pre|preview)[-_.]?\d*$", re.IGNORECASE)


def user_engaged(item: models.Item) -> bool:
    """True when a person did something about this item (not merely the system's own suggestion)."""
    if item.decisions or item.feedback:
        return True
    if item.status == models.Status.NEW:
        return False
    last = item.status_history[-1] if item.status_history else None
    return last is not None and not last.note.startswith(_AUTO_NOTES)


def _is_prerelease(release: dict) -> bool:
    return bool(release["prerelease"]) or bool(_PRE_TAG.search(release["tag"]))


def check(db: Session, *, limit: int | None = None, transport=None) -> dict:
    """Look up the releases of tracked GitHub items (checked longest ago first, up to `limit`)."""
    limit = config.REFRESH_RELEASES_PER_RUN if limit is None else limit
    stats = {"tracked": 0, "checked": 0, "new_releases": 0, "errors": 0, "rate_limited": False}
    items = [i for i in discovery.tracked_items(db)
             if getattr(i.source, "value", i.source) == "github" and user_engaged(i)]
    stats["tracked"] = len(items)
    items.sort(key=lambda i: i.releases_checked_at or datetime.datetime.min)
    requests = [(i.id, i.external_id) for i in items[:max(limit, 0)]]
    db.commit()  # release previous writes and detach request data from ORM lazy loads
    now = utcnow()
    outcomes = []
    for item_id, external_id in requests:
        try:
            found = github.get_releases(external_id, transport=transport)
        except github.RateLimited:
            stats["rate_limited"] = True
            logger.warning("GitHub rate limit reached; release watch stops for this run")
            break
        except Exception as error:
            stats["errors"] += 1
            logger.warning("Releases of %s failed: %s", external_id, error.__class__.__name__)
            continue
        outcomes.append((item_id, found))
        stats["checked"] += 1
    for item_id, found in outcomes:
        item = db.get(models.Item, item_id)
        if item is None:
            continue
        known = {r.tag for r in db.query(models.ItemRelease).filter_by(item_id=item.id)}
        for release in found:
            if release["tag"] in known or _NOISY_TAG.search(release["tag"]):
                continue
            db.add(models.ItemRelease(item_id=item.id, tag=release["tag"], name=release["name"], url=release["url"],
                                      published_at=_date(release["published_at"]), prerelease=_is_prerelease(release),
                                      notes=release["notes"][:NOTES_CHARS], first_seen_at=now))
            known.add(release["tag"])
            stats["new_releases"] += 1
        item.releases_checked_at = now
    db.flush()
    return stats


def _date(value) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None) if value else None
    except ValueError:
        return None


def recent(db: Session, *, days: int = 30, include_prereleases: bool = True, limit: int = 50) -> list[dict]:
    """Releases of items you track published in the last `days` days, newest first."""
    cutoff = utcnow() - datetime.timedelta(days=days)
    query = (db.query(models.ItemRelease).join(models.Item)
             .filter(models.ItemRelease.published_at >= cutoff, models.Item.archived.is_(False),
                     models.Item.status != models.Status.IGNORE))
    if not include_prereleases:
        query = query.filter(models.ItemRelease.prerelease.is_(False))
    out = []
    for release in query.order_by(models.ItemRelease.published_at.desc()).limit(limit * 4):
        item = release.item
        if not user_engaged(item):
            continue
        if len(out) >= limit:
            break
        out.append({"item_id": item.id, "name": item.name, "full_name": item.external_id, "status": item.status,
                    "tag": release.tag, "release_name": release.name, "url": release.url or item.url,
                    "published_at": release.published_at.isoformat(), "prerelease": release.prerelease,
                    "notes": release.notes, "key": f"{item.external_id}@{release.tag}"})
    return out
