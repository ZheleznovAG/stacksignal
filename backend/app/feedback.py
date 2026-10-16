"""The user's own judgments about items, kept apart from anything the analysis produces.

A *vote* says an item is useful (1), not relevant (-1) or cleared (0) for one project; the
latest vote per (item, project) wins. A *note* is free text about the item. Votes take
precedence over the model's relevance score — and keep doing so after the item is analysed
again, because the analysis rewrites relevance rows but never touches this table:

  useful        -> score raised to at least BOOST_SCORE
  not relevant  -> score lowered to at most SUPPRESS_SCORE (drops out of Relevant/Opportunities)
  cleared       -> the analysis' own score (stored as base_score) is restored

Recent votes are also fed back to the model as examples so future analyses lean towards the
user's taste, and the chat sees votes and notes ("why did I skip X?").
"""

from sqlalchemy.orm import Session

from . import models

BOOST_SCORE = 0.9
SUPPRESS_SCORE = 0.02
PREFIX_USEFUL = "You marked this useful. "
PREFIX_NOT_RELEVANT = "You marked this not relevant. "
MAX_EXAMPLES_PER_SIDE = 5
NOTE_MAX_LENGTH = 2000
VERDICT_LABELS = {1: "useful", -1: "not_relevant"}


def current_votes(item: models.Item) -> dict[str, int]:
    """{project slug: 1 | -1} — the latest non-cleared vote per project."""
    votes: dict[str, int] = {}
    for row in item.feedback:  # ordered by id, so later rows overwrite earlier ones
        if row.kind == "vote":
            votes[row.project_slug] = row.verdict
    return {slug: verdict for slug, verdict in votes.items() if verdict}


def notes(item: models.Item) -> list[models.ItemFeedback]:
    return [row for row in item.feedback if row.kind == "note"]


def effective_score(base: float, verdict: int) -> float:
    if verdict == 1:
        return max(base, BOOST_SCORE)
    if verdict == -1:
        return min(base, SUPPRESS_SCORE)
    return base


def _strip_prefix(text: str) -> str:
    for prefix in (PREFIX_USEFUL, PREFIX_NOT_RELEVANT, PREFIX_USEFUL.strip(), PREFIX_NOT_RELEVANT.strip()):
        if text.startswith(prefix):
            return text[len(prefix):]
    return text


def apply_votes(db: Session, item: models.Item, *, projects: list[models.Project] | None = None,
                relevances: list[models.ItemProjectRelevance] | None = None) -> None:
    """Make the item's relevance rows reflect its current votes. Idempotent."""
    db.flush()
    votes = current_votes(item)
    if relevances is None:
        relevances = db.query(models.ItemProjectRelevance).filter_by(item_id=item.id).all()
    if projects is None:
        projects = db.query(models.Project).all()
    rows = {row.project_id: row for row in relevances}
    for project in projects:
        verdict = votes.get(project.slug, 0)
        row = rows.get(project.id)
        if row is None:
            if verdict == 1:  # the user vouches for a match the analysis never produced
                db.add(models.ItemProjectRelevance(item_id=item.id, project_id=project.id, base_score=0.0,
                                                   score=BOOST_SCORE, rationale=PREFIX_USEFUL.strip()))
            continue
        if verdict == 0:
            if row.base_score is not None:
                if row.base_score == 0.0 and not _strip_prefix(row.rationale):
                    db.delete(row)  # a row that only existed because of the vote
                else:
                    row.score = row.base_score
                    row.rationale = _strip_prefix(row.rationale)
            continue
        base = row.base_score if row.base_score is not None else row.score
        row.base_score = base
        row.score = effective_score(base, verdict)
        prefix = PREFIX_USEFUL if verdict == 1 else PREFIX_NOT_RELEVANT
        row.rationale = prefix + _strip_prefix(row.rationale)
    db.flush()
    db.expire(item, ["relevances"])


def record_vote(db: Session, item: models.Item, project_slug: str, verdict: int) -> None:
    if verdict not in (-1, 0, 1):
        raise ValueError("verdict must be -1, 0 or 1")
    if db.query(models.Project).filter_by(slug=project_slug).one_or_none() is None:
        raise ValueError("Unknown project")
    db.add(models.ItemFeedback(item_id=item.id, kind="vote", project_slug=project_slug, verdict=verdict))
    db.flush()
    db.expire(item, ["feedback"])
    if verdict == 1 and item.status == models.Status.NEW:
        item.status = models.Status.ASSESS  # the user just told us it is worth a look
        item.status_updated_at = models.now()
        db.add(models.StatusHistory(item_id=item.id, status=models.Status.ASSESS,
                                    note=f"Marked useful for {project_slug}"))
    apply_votes(db, item)


def record_note(db: Session, item: models.Item, text: str) -> models.ItemFeedback:
    text = (text or "").strip()
    if not text:
        raise ValueError("Note is empty")
    if len(text) > NOTE_MAX_LENGTH:
        raise ValueError(f"Note is longer than {NOTE_MAX_LENGTH} characters")
    row = models.ItemFeedback(item_id=item.id, kind="note", note=text)
    db.add(row)
    db.flush()
    db.expire(item, ["feedback"])
    return row


def examples(db: Session) -> list[dict]:
    """Recent judgments (newest first, cleared votes ignored) as few-shot calibration for the
    model: up to MAX_EXAMPLES_PER_SIDE useful and not-relevant ones."""
    seen: set[tuple[int, str]] = set()
    picked: dict[int, list[models.ItemFeedback]] = {1: [], -1: []}
    live = {p.slug for p in db.query(models.Project)}  # a vote about a deleted project teaches nothing
    for row in db.query(models.ItemFeedback).filter_by(kind="vote").order_by(models.ItemFeedback.id.desc()):
        if row.project_slug not in live:
            continue
        key = (row.item_id, row.project_slug)
        if key in seen:
            continue
        seen.add(key)  # an older vote for the same pair is superseded, whatever the newer one is
        if row.verdict in picked and len(picked[row.verdict]) < MAX_EXAMPLES_PER_SIDE:
            picked[row.verdict].append(row)
    out = []
    for verdict in (1, -1):
        for row in picked[verdict]:
            item = db.get(models.Item, row.item_id)
            if item is None:
                continue
            entry = {"project": row.project_slug, "verdict": VERDICT_LABELS[verdict], "item": item.name,
                     "summary": (item.llm_summary or item.description or "")[:140]}
            last_note = next((n.note for n in reversed(notes(item))), "")
            if last_note:
                entry["note"] = last_note[:200]
            out.append(entry)
    from . import decisions  # your explicit decisions (with reasons) calibrate the model too
    return out + decisions.examples(db)
