"""The daily Telegram digest: what is new and worth your attention, in one short message.

Selection is deliberately strict — a digest that cries wolf gets muted:
  * only items an AI analysis scored at or above DIGEST_MIN_RELEVANCE (keyword-only matches are
    used only while the database has no AI analysis at all, e.g. in keyword mode);
  * never an item you marked Ignore or voted down, and never one already sent in an earlier
    digest, so every digest carries only what is new;
  * the best item per project first, at most PER_PROJECT_CAP per project and DIGEST_MAX_ITEMS in total;
  * plus up to three opportunity briefings that have not been sent before.
Nothing new and relevant means no message at all (a manual "send now" still says so).
"""

import dataclasses
import datetime
import html
import re

from sqlalchemy.orm import Session

from . import config, contexts, models, telegram
from .utils import utcnow

PER_PROJECT_CAP = 3
WATCH_PROJECT_CAP = 1  # a project on "watch" gets a single line at most; a dormant one none
MAX_OPPORTUNITIES = 3
MAX_STACK_UPDATES = 8
MAX_RELEASES = 8
MAX_REVISIT = 4
STACK_UPDATE_DAYS = 14  # an update older than this is no longer news
MESSAGE_LIMIT = 3800  # Telegram allows 4096 characters per message

_MONTHS_RU = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
_MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_LABELS = {
    "ru": {
        "digest": "дайджест", "new": "🔎 <b>Новое и полезное для ваших проектов</b>",
        "opps": "💡 <b>Что это значит</b>", "keywords": "по словам",
        "quiet": "Тихий день: нет ничего нового и достаточно релевантного.",
        "stats": "Собрано {found}, новых {new} · AI прочитал {ai} из {total}", "open": "Открыть дашборд",
        "stack": "📦 <b>Обновления вашего стека</b>", "major": "крупное",
        "releases": "🏷 <b>Новые релизы того, что вы отслеживаете</b>",
        "revisit": "🔁 <b>Стоит пересмотреть</b>", "rejected": "отклонено", "paused": "на паузе",
        "new_release": "новый релиз", "alternative": "новая альтернатива", "revisit_when": "пересмотреть, если",
    },
    "en": {
        "digest": "digest", "new": "🔎 <b>New and useful for your projects</b>",
        "opps": "💡 <b>What it means</b>", "keywords": "keywords only",
        "quiet": "A quiet day: nothing new and relevant enough.",
        "stats": "Collected {found}, {new} new · AI has read {ai} of {total}", "open": "Open the dashboard",
        "stack": "📦 <b>Updates to your stack</b>", "major": "major",
        "releases": "🏷 <b>New releases of what you track</b>",
        "revisit": "🔁 <b>Worth another look</b>", "rejected": "rejected", "paused": "paused",
        "new_release": "new release", "alternative": "new alternative", "revisit_when": "revisit when",
    },
}


@dataclasses.dataclass
class Digest:
    items: list[dict]
    opportunities: list[dict]
    messages: list[str]
    empty: bool
    stack_updates: list[dict] = dataclasses.field(default_factory=list)
    releases: list[dict] = dataclasses.field(default_factory=list)
    revisit: list[dict] = dataclasses.field(default_factory=list)


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _short(text: str, limit: int) -> str:
    """Shorten to `limit` characters, preferring to end on a sentence, else on a word."""
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    head = text[:limit]
    sentence_end = max(head.rfind(". "), head.rfind("! "), head.rfind("? "))
    if sentence_end >= limit * 0.5:
        return head[: sentence_end + 1]
    return head[: max(head.rfind(" "), limit // 2)].rstrip(" ,;:-") + "…"


def _safe_url(url: str) -> str:
    url = (url or "").strip()
    return url if re.match(r"^https?://[^\s\"'<>]+$", url) else ""


def plain(message: str) -> str:
    """The message as it will read in Telegram, without the HTML tags (used for previews)."""
    return html.unescape(re.sub(r"<[^>]+>", "", message))


def _already_sent(db: Session) -> tuple[set[int], set[str]]:
    item_ids: set[int] = set()
    titles: set[str] = set()
    for log in db.query(models.DigestLog).filter_by(ok=True).all():
        item_ids.update(log.item_ids or [])
        titles.update(log.opportunity_titles or [])
    return item_ids, titles


def select_items(db: Session, *, min_relevance: float | None = None, limit: int | None = None) -> list[dict]:
    min_relevance = config.DIGEST_MIN_RELEVANCE if min_relevance is None else min_relevance
    limit = config.DIGEST_MAX_ITEMS if limit is None else limit
    sent_ids, _ = _already_sent(db)
    modes = {state.item_id: state.provenance.get("mode") for state in db.query(models.ItemAnalysisState).all()}
    ai_exists = "ai" in modes.values()

    best: dict[int, models.ItemProjectRelevance] = {}
    rows = (
        db.query(models.ItemProjectRelevance)
        .join(models.Item).join(models.Project)
        .filter(models.ItemProjectRelevance.score >= min_relevance)
        .filter(models.Item.status != models.Status.IGNORE, models.Item.archived.is_(False))
        .filter(contexts.attended_filter())
        .all()
    )
    # Already decided for this very project (trying it, using it, rejected, paused): no nudge.
    decided = {(d.item_id, d.project_id) for d in db.query(models.ItemProjectDecision).all()}
    for row in rows:
        if row.item_id in sent_ids or (ai_exists and modes.get(row.item_id) != "ai"):
            continue
        if (row.item_id, row.project_id) in decided:
            continue
        if row.item_id not in best or row.score > best[row.item_id].score:
            best[row.item_id] = row

    per_project: dict[int, int] = {}
    picked: list[dict] = []
    for row in sorted(best.values(), key=lambda r: (r.score, r.item.momentum_score), reverse=True):
        cap = contexts.digest_limit(row.project, normal=PER_PROJECT_CAP, limited=WATCH_PROJECT_CAP)
        if per_project.get(row.project_id, 0) >= cap:
            continue
        per_project[row.project_id] = per_project.get(row.project_id, 0) + 1
        picked.append({
            "item_id": row.item_id, "name": row.item.name, "url": row.item.url,
            "project_slug": row.project.slug, "project_name": row.project.name,
            "score": row.score, "reason": row.rationale, "summary": row.item.llm_summary,
            "ai": modes.get(row.item_id) == "ai",
        })
        if len(picked) >= limit:
            break
    return picked


def select_stack_updates(db: Session) -> list[dict]:
    """New versions of dependencies of your projects that no digest has mentioned yet, major ones first."""
    from . import stack_watch
    sent: set[str] = set()
    for log in db.query(models.DigestLog).filter_by(ok=True).all():
        sent.update(log.stack_update_keys or [])
    fresh = [u for u in stack_watch.updates(db, days=STACK_UPDATE_DAYS) if u["key"] not in sent]
    fresh.sort(key=lambda u: u["changed_at"], reverse=True)
    fresh.sort(key=lambda u: not u["major"])  # stable: major updates first, newest first within each group
    return fresh[:MAX_STACK_UPDATES]


def select_releases(db: Session) -> list[dict]:
    """Stable releases from the last week of repositories you track that no digest has mentioned yet."""
    from . import release_watch
    sent: set[str] = set()
    for log in db.query(models.DigestLog).filter_by(ok=True).all():
        sent.update(log.release_keys or [])
    fresh = [r for r in release_watch.recent(db, days=release_watch.DIGEST_DAYS, include_prereleases=False)
             if r["key"] not in sent]
    return fresh[:MAX_RELEASES]


def select_revisit(db: Session) -> list[dict]:
    """Rejected or paused decisions where something changed (a release, an alternative, a real shift), that no
    digest has raised with the same evidence yet."""
    from . import revisit
    sent: set[str] = set()
    for log in db.query(models.DigestLog).filter_by(ok=True).all():
        sent.update(log.revisit_keys or [])
    return [e for e in revisit.overview(db, only_notable=True)
            if e["kind"] == "revisit" and e["key"] not in sent][:MAX_REVISIT]


def select_opportunities(db: Session) -> list[dict]:
    _, sent_titles = _already_sent(db)
    names = {p.slug: p.name for p in db.query(models.Project).filter(contexts.attended_filter())}
    out: list[dict] = []
    for opportunity in (db.query(models.Opportunity)
                        .filter(models.Opportunity.state == "active", models.Opportunity.digested_at.is_(None))
                        .order_by(models.Opportunity.id).all()):
        if opportunity.title in sent_titles or any(o["title"] == opportunity.title for o in out):
            continue
        if opportunity.project_slug not in names:
            continue  # a dormant (or deleted) project
        out.append({"id": opportunity.id, "title": opportunity.title, "body": opportunity.body,
                    "project_name": names.get(opportunity.project_slug, opportunity.project_slug)})
        if len(out) >= MAX_OPPORTUNITIES:
            break
    return out


def _pieces(block: str) -> list[str]:
    """A block whole if it fits; otherwise its lines; a single line longer than a message is
    hard-sliced as a last resort, so no message can ever exceed MESSAGE_LIMIT."""
    if len(block) <= MESSAGE_LIMIT:
        return [block]
    out: list[str] = []
    for line in block.split("\n"):
        out.extend(line[i:i + MESSAGE_LIMIT] for i in range(0, max(len(line), 1), MESSAGE_LIMIT))
    return out


def _chunk(blocks: list[str]) -> list[str]:
    messages: list[str] = []
    current = ""
    for block in blocks:
        for piece in _pieces(block):
            if current and len(current) + 2 + len(piece) > MESSAGE_LIMIT:
                messages.append(current)
                current = ""
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        messages.append(current)
    return messages


def build_digest(db: Session, *, lang: str | None = None, today: datetime.date | None = None) -> Digest:
    labels = _LABELS[lang or config.DIGEST_LANG]
    months = _MONTHS_RU if (lang or config.DIGEST_LANG) == "ru" else _MONTHS_EN
    today = today or datetime.date.today()
    items = select_items(db)
    opportunities = select_opportunities(db)
    stack_updates = select_stack_updates(db)
    releases = select_releases(db)
    revisit_now = select_revisit(db)
    empty = not items and not opportunities and not stack_updates and not releases and not revisit_now

    blocks = [f"📡 <b>StackSignal</b> — {labels['digest']}, {today.day} {months[today.month - 1]}"]
    if empty:
        blocks.append(_esc(labels["quiet"]))
    if items:
        lines = [labels["new"]]
        by_project: dict[str, list[dict]] = {}
        for entry in items:
            by_project.setdefault(entry["project_name"], []).append(entry)
        for project_name, entries in by_project.items():
            lines.append(f"\n<b>{_esc(project_name)}</b>")
            for entry in entries:
                url = _safe_url(entry["url"])
                name = f'<a href="{html.escape(url)}">{_esc(entry["name"])}</a>' if url else _esc(entry["name"])
                tag = "" if entry["ai"] else f" ({_esc(labels['keywords'])})"
                lines.append(f"• {name} — {round(entry['score'] * 100)}%{tag}")
                reason = _short(entry["reason"] or entry["summary"], 180)
                if reason:
                    lines.append(f"<i>{_esc(reason)}</i>")
        blocks.append("\n".join(lines))
    if opportunities:
        lines = [labels["opps"]]
        for opportunity in opportunities:
            lines.append(f"• <b>{_esc(opportunity['title'])}</b> ({_esc(opportunity['project_name'])})\n"
                         f"{_esc(_short(opportunity['body'], 220))}")
        blocks.append("\n".join(lines))

    if revisit_now:
        lines = [labels["revisit"]]
        for entry in revisit_now:
            link = _safe_url(entry["item_url"])
            name = f'<a href="{html.escape(link)}">{_esc(entry["item_name"])}</a>' if link else _esc(entry["item_name"])
            state = labels["rejected"] if entry["status"] == "ignore" else labels["paused"]
            why = f" — {_esc(_short(entry['verdict'], 100))}" if entry["verdict"] else ""
            lines.append(f"• {name} ({_esc(entry['project_name'])}, {_esc(state)}){why}")
            seen = []
            changes = entry["changes"]
            if changes["release_count"]:
                seen.append(f"{labels['new_release']}: {changes['releases'][0]['tag']}")
            for alt in changes["alternatives"][:2]:
                seen.append(f"{labels['alternative']}: {alt['name']} ({round(alt['score'] * 100)}%)")
            if seen:
                lines.append(f"<i>{_esc('; '.join(seen))}</i>")
            if entry["revisit_when"]:
                lines.append(f"<i>{_esc(labels['revisit_when'])}: {_esc(_short(entry['revisit_when'], 120))}</i>")
        blocks.append("\n".join(lines))
    if releases:
        lines = [labels["releases"]]
        for release in releases:
            link = _safe_url(release["url"])
            name = f'<a href="{html.escape(link)}">{_esc(release["name"])} {_esc(release["tag"])}</a>' if link \
                else f'{_esc(release["name"])} {_esc(release["tag"])}'
            lines.append(f"• {name}")
            if release["notes"]:
                lines.append(f"<i>{_esc(_short(release['notes'], 160))}</i>")
        blocks.append("\n".join(lines))
    if stack_updates:
        lines = [labels["stack"]]
        for update in stack_updates:
            change = f"{_esc(update['previous'])} → {_esc(update['latest'])}" if update["previous"] else _esc(update["latest"])
            change = ("в реестре: " if (lang or config.DIGEST_LANG) == "ru" else "registry: ") + change
            context = []
            if update.get("declared"):
                context.append(("заявлено: " if (lang or config.DIGEST_LANG) == "ru" else "declared: ") + _esc(update["declared"]))
            if update.get("resolved"):
                context.append(("зафиксировано: " if (lang or config.DIGEST_LANG) == "ru" else "resolved: ") + _esc(update["resolved"]))
            if context:
                change += "; " + "; ".join(context)
            major = f" ({_esc(labels['major'])})" if update["major"] else ""
            link = _safe_url(update["url"])
            name = f'<a href="{html.escape(link)}">{_esc(update["name"])}</a>' if link else _esc(update["name"])
            lines.append(f"• {name} {change}{major} — {_esc(', '.join(update['projects']))}")
        blocks.append("\n".join(lines))

    footer = []
    last_run = db.query(models.CollectionRun).order_by(models.CollectionRun.started_at.desc()).first()
    if last_run is not None:
        ai = sum(1 for s in db.query(models.ItemAnalysisState).all() if s.provenance.get("mode") == "ai")
        footer.append(_esc(labels["stats"].format(found=last_run.items_found, new=last_run.items_new, ai=ai,
                                                  total=db.query(models.Item).count())))
    dashboard = _safe_url(config.DASHBOARD_URL)
    if dashboard:
        footer.append(f'<a href="{html.escape(dashboard)}">{_esc(labels["open"])}</a>')
    if footer:
        blocks.append("\n".join(footer))
    return Digest(items=items, opportunities=opportunities, messages=_chunk(blocks), empty=empty,
                  stack_updates=stack_updates, releases=releases, revisit=revisit_now)


def send_digest(db: Session, *, trigger: str = "manual", force: bool = False, transport=None) -> dict:
    """Build and send the digest. Records every attempt; only a fully delivered digest marks its
    items and opportunities as sent. Raises telegram.TelegramError on failure."""
    if not telegram.configured():
        raise telegram.TelegramError("telegram_not_configured")
    digest = build_digest(db)
    if digest.empty and not force:
        return {"sent": 0, "items": 0, "opportunities": 0, "skipped": "nothing_new"}
    try:
        for message in digest.messages:
            telegram.send_message(message, transport=transport)
    except telegram.TelegramError as error:
        db.add(models.DigestLog(trigger=trigger, ok=False, error=error.code))
        db.commit()
        raise
    db.add(models.DigestLog(trigger=trigger, ok=True, messages=len(digest.messages),
                            item_ids=[entry["item_id"] for entry in digest.items],
                            opportunity_titles=[o["title"] for o in digest.opportunities],
                            stack_update_keys=[u["key"] for u in digest.stack_updates],
                            release_keys=[r["key"] for r in digest.releases],
                            revisit_keys=[e["key"] for e in digest.revisit]))
    for sent in digest.opportunities:
        opportunity = db.get(models.Opportunity, sent["id"])
        if opportunity is not None:
            opportunity.digested_at = utcnow()
    db.commit()
    return {"sent": len(digest.messages), "items": len(digest.items), "opportunities": len(digest.opportunities)}


def recent_logs(db: Session, limit: int = 8) -> list[dict]:
    rows = db.query(models.DigestLog).order_by(models.DigestLog.id.desc()).limit(limit).all()
    return [{"sent_at": r.sent_at.isoformat(), "trigger": r.trigger, "ok": r.ok, "error": r.error,
             "items": len(r.item_ids or []), "opportunities": len(r.opportunity_titles or [])} for r in rows]
