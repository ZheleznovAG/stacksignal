"""Authors: GitHub accounts as a source of technologies.

Your GitHub stars are the strongest interest signal StackSignal can get, and it is free: a repository
you starred goes on the radar as Watch (once -- a status you change later is never overridden), and its
owner joins the people you follow as a watched author. Adding a profile URL by hand does the same for
one account.

Watched authors are checked about once a week: their own repositories are read (metadata only, one
request per author) and remembered, and only a few per run enter the radar -- new repositories first,
then older ones that earned stars. Those are ordinary New items that the pipeline scores and the AI
reads within its usual budget. An author's yield -- how many of their repositories the AI found
relevant to your projects, how many you acted on -- decides who is checked first, and an account that
keeps showing up in your finds is suggested as a candidate author (checked monthly).

Network first, then writes: fetch_github() touches no ORM state, apply_github() touches no network, so
SQLite's writer lock is never held while GitHub is answering.
"""

import datetime
import re
from collections import Counter, defaultdict
from urllib.parse import urlparse

from sqlalchemy import func
from sqlalchemy.orm import Session

from . import config, models
from .collectors import github
from .utils import utcnow

STAR_VIA = "GitHub star"  # ItemDiscovery.query_text of a starred repository (project_slug is empty)
AUTHOR_VIA = "@author"    # ItemDiscovery.project_slug of a repository found on a watched author's profile
STATUSES = ("watch", "candidate", "ignore")
RELEVANT = 0.6            # an AI relevance this high to any project counts toward an author's yield
CANDIDATE_MIN_RELEVANT = 2
_AUTO_NOTES = ("Discovered", "Auto-suggested", "Reset:")  # statuses the system set, not you
# Repositories that are housekeeping, not technology: dotfiles, taps, profile/site repos, examples.
_HOUSEKEEPING = re.compile(r"^\.|^dotfiles$|^homebrew-|\.github\.io$|(^|[-_.])examples?$|^example[-_]", re.IGNORECASE)
_LOGIN = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
# github.com/<first segment> pages that are not accounts.
_RESERVED = {"about", "apps", "collections", "contact", "customer-stories", "enterprise", "events", "explore",
             "features", "issues", "login", "marketplace", "new", "notifications", "pricing", "pulls", "search",
             "security", "settings", "signup", "sponsors", "topics", "trending"}


def parse_author_url(url: str) -> str | None:
    """The login of a GitHub profile URL (github.com/<login> or github.com/orgs/<login>), else None."""
    text = (url or "").strip()
    if "://" not in text:
        text = "https://" + text
    parsed = urlparse(text)
    if (parsed.hostname or "").lower().removeprefix("www.") != "github.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) == 2 and parts[0] == "orgs":
        parts = parts[1:]
    if len(parts) != 1 or parts[0].lower() in _RESERVED or not _LOGIN.fullmatch(parts[0]):
        return None
    return parts[0]


def _parse_dt(value) -> datetime.datetime | None:
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _upsert(db: Session, known: dict[str, models.Author], account: dict, now: datetime.datetime) -> models.Author:
    key = account["login"].lower()
    author = known.get(key)
    if author is None:
        author = models.Author(login=account["login"], login_key=key, status="watch", followed=False,
                               starred_repos=0, added_manually=False, first_seen_at=now)
        db.add(author)
        known[key] = author
    author.login = account["login"]
    author.github_id = account.get("id") or author.github_id
    author.kind = account.get("kind") or author.kind or "user"
    author.last_seen_at = now
    return author


def fetch_github(login: str, *, transport=None) -> dict:
    """Your stars and follows. Network only; raises github.RateLimited."""
    return {"starred": github.user_starred(login, transport=transport),
            "following": github.user_following(login, transport=transport)}


def apply_github(db: Session, fetched: dict, *, login: str) -> dict:
    """Mirror follows and per-owner star counts onto authors. Repositories are the pipeline's job."""
    now = utcnow()
    me = login.lower()
    known = {a.login_key: a for a in db.query(models.Author)}
    followed, stars, last_star = set(), Counter(), {}
    for account in fetched["following"]:
        _upsert(db, known, account, now).followed = True
        followed.add(account["login"].lower())
    for star in fetched["starred"]:
        key = star["owner"]["login"].lower()
        if key == me:
            continue  # your own repositories are not a source you discover
        _upsert(db, known, star["owner"], now)
        stars[key] += 1
        starred_at = _parse_dt(star.get("starred_at"))
        if starred_at and (key not in last_star or starred_at > last_star[key]):
            last_star[key] = starred_at
    for key, author in known.items():
        # A complete fetch is the truth: an unfollow or an unstar shows up here.
        author.followed = key in followed
        author.starred_repos = stars.get(key, 0)
        author.last_starred_at = last_star.get(key, author.last_starred_at)
    db.flush()
    return {"stars": len(fetched["starred"]), "following": len(followed), "authors": len(known)}


def mark_starred(db: Session, item: models.Item, starred_at=None) -> bool:
    """Record the star on the item, and put it on the radar as Watch the first time it is seen starred.
    Returns True when the star is new to StackSignal."""
    if db.query(models.ItemDiscovery).filter_by(item_id=item.id, project_slug="").first():
        return False
    db.add(models.ItemDiscovery(item_id=item.id, project_slug="", query_text=STAR_VIA,
                                found_at=_parse_dt(starred_at) or utcnow()))
    if item.status == models.Status.NEW:
        item.status = models.Status.WATCH
        item.status_updated_at = utcnow()
        db.add(models.StatusHistory(item_id=item.id, status=models.Status.WATCH, note="Starred on GitHub"))
    return True


def add_author(db: Session, login: str, *, transport=None) -> tuple[models.Author, bool]:
    """Watch one account you named yourself. Raises LookupError when GitHub has no such account."""
    db.commit()  # nothing of ours is pending while GitHub answers
    account = github.get_user(login, transport=transport)
    if account is None:
        raise LookupError(login)
    known = {a.login_key: a for a in db.query(models.Author).filter_by(login_key=account["login"].lower())}
    created = not known
    author = _upsert(db, known, account, utcnow())
    author.added_manually = True
    author.status = "watch"  # naming an account again also brings back one you had ignored
    db.flush()
    return author, created


def sync_enabled(sources) -> bool:
    return bool(config.GITHUB_LOGIN) and "github" in sources


def serialize(author: models.Author) -> dict:
    return {"id": author.id, "login": author.login, "account_type": author.kind, "status": author.status,
            "followed": author.followed, "starred_repos": author.starred_repos,
            "added_manually": author.added_manually, "url": f"https://github.com/{author.login}"}


# ---- yield ---------------------------------------------------------------------------------------

def author_stats(db: Session) -> dict[str, dict]:
    """Per owner (lowercase login) of GitHub items: how many you have, how many the AI found relevant to
    a project, how many you acted on, and the best few. A handful of aggregate queries, not one per item."""
    ai = {s.item_id for s in db.query(models.ItemAnalysisState) if (s.provenance or {}).get("mode") == "ai"}
    relevance = dict(db.query(models.ItemProjectRelevance.item_id, func.max(models.ItemProjectRelevance.score))
                     .group_by(models.ItemProjectRelevance.item_id))
    # A project's history cannot establish who set an item's GLOBAL status. Old auto statuses
    # have recognizable notes; a status without any history has unknown provenance.
    last_ids = (db.query(func.max(models.StatusHistory.id)).filter(models.StatusHistory.project_id.is_(None))
                .group_by(models.StatusHistory.item_id))
    history = {h.item_id: h for h in db.query(models.StatusHistory).filter(models.StatusHistory.id.in_(last_ids))}
    signals: dict[int, dict[str, str]] = defaultdict(dict)
    noted = set()
    for row in db.query(models.ItemFeedback).order_by(models.ItemFeedback.id):
        if row.kind == "note":
            noted.add(row.item_id)
        elif row.kind == "vote":
            # Only the latest vote counts; zero clears it, including its positive yield.
            signals[row.item_id][row.project_slug] = {1: "positive", -1: "negative"}.get(row.verdict, "")
    for decision, slug in (db.query(models.ItemProjectDecision, models.Project.slug)
                           .join(models.Project, models.Project.id == models.ItemProjectDecision.project_id)):
        # A current decision is authoritative for its context (an old like cannot undo a rejection).
        signals[decision.item_id][slug] = (
            "positive" if decision.status in (models.Status.TRIAL, models.Status.ADOPT)
            else "negative" if decision.status == models.Status.IGNORE else "neutral")
    stats: dict[str, dict] = {}
    rows = (db.query(models.Item.id, models.Item.owner, models.Item.name, models.Item.status)
            .filter(models.Item.source == models.Source.GITHUB, models.Item.archived.is_(False)))
    for item_id, owner, name, status in rows:
        if not owner:
            continue
        entry = stats.setdefault(owner.lower(), {"login": owner, "items": 0, "relevant": 0, "engaged": 0,
                                                 "positive": 0, "negative": 0, "neutral": 0, "top": []})
        score = relevance.get(item_id, 0.0) if item_id in ai else 0.0
        status = getattr(status, "value", status)
        entry["items"] += 1
        entry["relevant"] += score >= RELEVANT
        kinds = set(signals.get(item_id, {}).values()) - {""}
        if item_id in noted:
            kinds.add("neutral")
        last = history.get(item_id)
        if last and last.status == status and status != "new" and not (last.note or "").startswith(_AUTO_NOTES):
            kinds.add("positive" if status in ("trial", "adopt") else "negative" if status == "ignore" else "neutral")
        entry["engaged"] += bool(kinds)  # UI count, never a ranking signal by itself
        for kind in ("positive", "negative", "neutral"):
            entry[kind] += kind in kinds  # at most once per item, even across several projects
        entry["top"].append((score, item_id, name))
    for entry in stats.values():
        entry["top"] = [{"id": i, "name": n, "relevance": round(sc, 2)}
                        for sc, i, n in sorted(entry["top"], reverse=True)[:3]]
    return stats


def priority(author: models.Author, stats: dict | None) -> float:
    """Who is checked first: your own signals (follow, stars, a manual add), then what their work yielded."""
    stats = stats or {}
    score = 1.0 if (author.followed or author.added_manually) else 0.3 if author.status == "candidate" else 0.6
    score += 0.4 * min(author.starred_repos or 0, 5)
    if author.last_starred_at and author.last_starred_at > utcnow() - datetime.timedelta(days=365):
        score += 0.5
    return round(score + 1.0 * stats.get("positive", 0) + 0.6 * stats.get("relevant", 0), 2)


# ---- watching authors -----------------------------------------------------------------------------

def watch_enabled(sources) -> bool:
    return "github" in sources and config.AUTHOR_CHECKS_PER_RUN > 0


def prepare_watch(db: Session, *, limit: int | None = None) -> tuple[list[tuple[int, str]], dict]:
    """Local work before any request: suggest candidate authors from your finds, promote a candidate you
    acted on, and pick who is due for a check (best yield first). Returns ([(author id, login)], stats)."""
    limit = config.AUTHOR_CHECKS_PER_RUN if limit is None else limit
    now = utcnow()
    stats = author_stats(db)
    me = config.GITHUB_LOGIN.lower()
    known = {a.login_key: a for a in db.query(models.Author)}
    suggested = 0
    for key, entry in stats.items():
        if key not in known and key != me and entry["relevant"] >= CANDIDATE_MIN_RELEVANT:
            _upsert(db, known, {"login": entry["login"]}, now).status = "candidate"
            suggested += 1
    for key, author in known.items():
        if author.status == "candidate" and stats.get(key, {}).get("positive"):
            author.status = "watch"  # you liked, tried or adopted one of their repositories
    db.flush()

    def due(author):
        days = config.AUTHOR_CHECK_DAYS if author.status == "watch" else config.AUTHOR_CANDIDATE_CHECK_DAYS
        return author.repos_checked_at is None or author.repos_checked_at <= now - datetime.timedelta(days=days)

    waiting = [a for a in known.values() if a.status in ("watch", "candidate") and due(a)]
    waiting.sort(key=lambda a: (-priority(a, stats.get(a.login_key)), a.repos_checked_at or datetime.datetime.min))
    return [(a.id, a.login) for a in waiting[:max(limit, 0)]], {"suggested": suggested, "due": len(waiting)}


def apply_repos(db: Session, fetched: dict[int, list[dict]], *, limit: int | None = None,
                per_author: int | None = None) -> list[dict]:
    """Remember every repository seen and choose which enter the radar: new ones (not forks, not archived)
    first, then older ones with AUTHOR_MIN_STARS stars pushed within two years. Returns
    [{"payload", "login"}] for the pipeline; already tracked repositories never count toward the limit."""
    limit = config.AUTHOR_PROMOTE_PER_RUN if limit is None else limit
    per_author = config.AUTHOR_PROMOTE_PER_AUTHOR if per_author is None else per_author
    now = utcnow()
    new_since = now - datetime.timedelta(days=config.AUTHOR_NEW_DAYS)
    active_since = now - datetime.timedelta(days=730)
    stats = author_stats(db)
    names = [r["payload"]["external_id"] for repos in fetched.values() for r in repos]
    rows, tracked = {}, set()
    if names:
        rows = {r.full_name.lower(): r for r in db.query(models.AuthorRepo).filter(models.AuthorRepo.full_name.in_(names))}
        tracked = {row[0].lower() for row in db.query(models.Item.external_id).filter(
            models.Item.source == models.Source.GITHUB, models.Item.external_id.in_(names))}
    picks = []
    for author_id, repos in fetched.items():
        author = db.get(models.Author, author_id)
        if author is None:
            continue
        author.repos_checked_at = now
        if repos and repos[0]["owner"]["login"].lower() == author.login_key:
            author.github_id = repos[0]["owner"]["id"] or author.github_id
            author.kind = repos[0]["owner"]["kind"]
        eligible = []
        for repo in repos:
            payload = repo["payload"]
            key = payload["external_id"].lower()
            row = rows.get(key)
            if row is None:
                row = models.AuthorRepo(full_name=payload["external_id"], first_seen_at=now)
                db.add(row)
                rows[key] = row
            row.author_id, row.stars, row.fork, row.archived = author.id, payload["stars"], repo["fork"], repo["archived"]
            row.created_at, row.pushed_at = _parse_dt(repo["created_at"]), _parse_dt(repo["pushed_at"])
            row.last_seen_at = now
            if row.promoted_at and key not in tracked and row.promoted_at < now - datetime.timedelta(days=1):
                row.promoted_at = None  # chosen, but its run failed before the item was stored
            if row.promoted_at or row.fork or row.archived:
                continue
            if key in tracked:
                row.promoted_at = now  # already on the radar by another route
                continue
            if _HOUSEKEEPING.search(payload.get("name") or key.rsplit("/", 1)[-1]):
                continue
            if row.created_at and row.created_at >= new_since:
                # Young repositories are judged again on the next check, once they had time to earn stars:
                # a bare one needs a description or a few stars, an organization's needs real interest.
                floor = config.AUTHOR_NEW_ORG_MIN_STARS if author.kind == "organization" else 0
                if row.stars >= max(floor, 5 if not payload.get("description") else 0):
                    eligible.append(((1, row.created_at.timestamp()), row, payload))
            elif row.stars >= config.AUTHOR_MIN_STARS and row.pushed_at and row.pushed_at >= active_since:
                eligible.append(((0, float(row.stars)), row, payload))
        eligible.sort(key=lambda e: e[0], reverse=True)
        weight = priority(author, stats.get(author.login_key))
        picks += [(rank[0], weight, rank[1], row, payload, author.login) for rank, row, payload in eligible[:per_author]]
    picks.sort(key=lambda p: p[:3], reverse=True)
    chosen = []
    for _, _, _, row, payload, login in picks[:max(limit, 0)]:
        row.promoted_at = now
        chosen.append({"payload": payload, "login": login})
    db.flush()
    return chosen


def mark_found(db: Session, item: models.Item, login: str) -> None:
    """Record that this repository came from a watched author's profile (shown on its card)."""
    if not db.query(models.ItemDiscovery).filter_by(item_id=item.id, project_slug=AUTHOR_VIA).first():
        db.add(models.ItemDiscovery(item_id=item.id, project_slug=AUTHOR_VIA, query_text=login))


def set_status(db: Session, author: models.Author, status: str) -> None:
    if status not in STATUSES:
        raise ValueError("Unknown author status")
    author.status = status


def list_authors(db: Session, status: str | None = None) -> list[dict]:
    """Authors with their yield, best first."""
    stats = author_stats(db)
    seen = dict(db.query(models.AuthorRepo.author_id, func.count(models.AuthorRepo.id))
                .group_by(models.AuthorRepo.author_id))
    query = db.query(models.Author)
    if status:
        query = query.filter_by(status=status)
    rows = []
    for author in query:
        entry = stats.get(author.login_key, {})
        rows.append({**serialize(author), "items": entry.get("items", 0), "relevant": entry.get("relevant", 0),
                     "engaged": entry.get("engaged", 0), "top": entry.get("top", []),
                     **{key: entry.get(key, 0) for key in ("positive", "negative", "neutral")},
                     "priority": priority(author, entry), "repos_seen": seen.get(author.id, 0),
                     "last_starred_at": author.last_starred_at.isoformat() if author.last_starred_at else None,
                     "repos_checked_at": author.repos_checked_at.isoformat() if author.repos_checked_at else None})
    rows.sort(key=lambda r: (-r["priority"], r["login"].lower()))
    return rows
