"""One-time data fixes, recorded in applied_migrations so each runs exactly once."""

import re

from sqlalchemy.orm import Session

from . import models

RESET_HEURISTIC_STATUS = "reset_heuristic_auto_status_v1"
RESET_NOTE = "Reset: the auto-suggestion came from keyword matching only"
CAP_AUTO_STATUS = "cap_auto_status_at_assess_v1"
ARCHIVE_LOW_QUALITY = "archive_low_quality_items_v1"
CAP_NOTE = "Reset: only you can move an item to Trial or Adopt"


def reset_heuristic_auto_statuses(db: Session) -> int:
    """Return items to `New` whose status was only ever auto-suggested from keyword matching.

    Earlier versions advanced the radar from heuristic relevance, so junk like a `Trial` on an
    unrelated framework looked like a decision. Statuses you set yourself (any history entry that
    is not an auto-suggestion) and AI-backed suggestions are left alone."""
    reset = 0
    for item in db.query(models.Item).filter(models.Item.status != models.Status.NEW).all():
        last = item.status_history[-1] if item.status_history else None
        if last is None or not last.note.startswith("Auto-suggested"):
            continue
        state = db.get(models.ItemAnalysisState, item.id)
        if state is not None and state.provenance.get("mode") == "ai":
            continue
        item.status = models.Status.NEW
        item.status_updated_at = models.now()
        db.add(models.StatusHistory(item_id=item.id, status=models.Status.NEW, note=RESET_NOTE))
        reset += 1
    return reset


def cap_auto_statuses_at_assess(db: Session) -> int:
    """Move items to `Assess` whose Trial/Adopt was only ever auto-suggested. Those two statuses
    mean "I am trying / using it", which no score can know; anything the user set stays."""
    changed = 0
    for item in db.query(models.Item).filter(models.Item.status.in_([models.Status.TRIAL, models.Status.ADOPT])).all():
        last = item.status_history[-1] if item.status_history else None
        if last is None or not last.note.startswith("Auto-suggested"):
            continue
        item.status = models.Status.ASSESS
        item.status_updated_at = models.now()
        db.add(models.StatusHistory(item_id=item.id, status=models.Status.ASSESS, note=CAP_NOTE))
        changed += 1
    return changed


def archive_low_quality_items(db: Session) -> int:
    """Archive (not delete) items that fail the quality gate and that nobody has engaged with.
    Before the gate existed, the newest-models list filled the database with experiments
    ("test", "…-seed-42") that had no likes and no downloads."""
    from . import quality
    ai = {s.item_id for s in db.query(models.ItemAnalysisState).all() if s.provenance.get("mode") == "ai"}
    archived = 0
    for item in db.query(models.Item).filter(models.Item.archived.is_(False)).all():
        if quality.has_engagement(item, item.id in ai):
            continue
        if not quality.gate(quality.item_payload(item))[0]:
            item.archived = True
            archived += 1
    return archived


def split_analysis_fingerprints(db: Session) -> int:
    """Analyses made before the split carry one combined fingerprint. Those that are still current are
    converted to the two-part form (content + one per project) so they are not analysed again."""
    from .enrichment import content_fingerprint, item_fingerprint, project_fingerprint
    projects = db.query(models.Project).all()
    project_fps = {p.slug: project_fingerprint(p) for p in projects}
    converted = 0
    for state in db.query(models.ItemAnalysisState).all():
        provenance = state.provenance or {}
        old = provenance.get("item_fingerprint")
        item = db.get(models.Item, state.item_id)
        if provenance.get("mode") != "ai" or not old or item is None or "content_fingerprint" in provenance:
            continue
        if old == item_fingerprint(item, projects):
            state.provenance = {**{k: v for k, v in provenance.items() if k != "item_fingerprint"},
                                "content_fingerprint": content_fingerprint(item),
                                "project_fingerprints": dict(project_fps)}
            converted += 1
    return converted


SPLIT_FINGERPRINTS = "split_analysis_fingerprints_v1"
MARK_HEURISTIC_OPPORTUNITIES = "mark_heuristic_opportunities_v1"
ARXIV_DEFAULT_QUERIES = "arxiv_default_queries_v1"
PURGE_ORPHANS = "purge_orphans_v1"


def purge_orphans(db: Session) -> int:
    """Rows about a project that no longer exists (votes, opportunities, discoveries, decision history), left by
    deletions made before a project's data was removed together with it."""
    slugs = {p.slug for p in db.query(models.Project)}
    ids = {p.id for p in db.query(models.Project)}
    removed = 0
    for model in (models.ItemFeedback, models.Opportunity, models.ItemDiscovery):
        for row in db.query(model).all():
            slug = row.project_slug or ""
            if slug and slug not in slugs and (model is not models.ItemFeedback or row.kind == "vote"):
                db.delete(row)
                removed += 1
    for row in db.query(models.StatusHistory).filter(models.StatusHistory.project_id.isnot(None)).all():
        if row.project_id not in ids:
            db.delete(row)
            removed += 1
    return removed


def add_arxiv_default_queries(db: Session) -> int:
    """Projects that already had queries get arXiv ones too (new projects get them with all the others)."""
    from . import queries
    return queries.add_source_defaults(db, db.query(models.Project).all(), ("arxiv",))


def mark_heuristic_opportunities(db: Session) -> int:
    """Opportunities written before they persisted have no origin; the keyword-based ones are recognisable
    by their generated title, and are marked so they can give way to real AI notes."""
    marked = 0
    for opportunity in db.query(models.Opportunity).all():
        if re.match(r"^\d+ relevant items for ", opportunity.title or "") and opportunity.origin != "heuristic":
            opportunity.origin = "heuristic"
            marked += 1
    return marked


def apply_pending(db: Session) -> dict:
    done = {}
    if db.query(models.AppliedMigration).filter_by(name=RESET_HEURISTIC_STATUS).one_or_none() is None:
        done[RESET_HEURISTIC_STATUS] = reset_heuristic_auto_statuses(db)
        db.add(models.AppliedMigration(name=RESET_HEURISTIC_STATUS))
        db.commit()
    if db.query(models.AppliedMigration).filter_by(name=CAP_AUTO_STATUS).one_or_none() is None:
        done[CAP_AUTO_STATUS] = cap_auto_statuses_at_assess(db)
        db.add(models.AppliedMigration(name=CAP_AUTO_STATUS))
        db.commit()
    if db.query(models.AppliedMigration).filter_by(name=ARCHIVE_LOW_QUALITY).one_or_none() is None:
        done[ARCHIVE_LOW_QUALITY] = archive_low_quality_items(db)
        db.add(models.AppliedMigration(name=ARCHIVE_LOW_QUALITY))
        db.commit()
    if db.query(models.AppliedMigration).filter_by(name=SPLIT_FINGERPRINTS).one_or_none() is None:
        done[SPLIT_FINGERPRINTS] = split_analysis_fingerprints(db)
        db.add(models.AppliedMigration(name=SPLIT_FINGERPRINTS))
        db.commit()
    if db.query(models.AppliedMigration).filter_by(name=MARK_HEURISTIC_OPPORTUNITIES).one_or_none() is None:
        done[MARK_HEURISTIC_OPPORTUNITIES] = mark_heuristic_opportunities(db)
        db.add(models.AppliedMigration(name=MARK_HEURISTIC_OPPORTUNITIES))
        db.commit()
    if db.query(models.AppliedMigration).filter_by(name=ARXIV_DEFAULT_QUERIES).one_or_none() is None:
        done[ARXIV_DEFAULT_QUERIES] = add_arxiv_default_queries(db)
        db.add(models.AppliedMigration(name=ARXIV_DEFAULT_QUERIES))
        db.commit()
    if db.query(models.AppliedMigration).filter_by(name=PURGE_ORPHANS).one_or_none() is None:
        done[PURGE_ORPHANS] = purge_orphans(db)
        db.add(models.AppliedMigration(name=PURGE_ORPHANS))
        db.commit()
    return done
