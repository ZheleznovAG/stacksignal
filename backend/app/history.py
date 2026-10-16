"""Real, time-normalized growth rates, independent of how often collection happens.

Naively diffing "current stars" against "stars at the previous collection run" is wrong:
the delta means something completely different if runs are five minutes apart versus a
day apart. Instead we record a snapshot of each item's metrics on every run and diff
against the snapshot closest to N days ago, dividing by the *actual* elapsed time.
"""

import datetime

from sqlalchemy.orm import Session

from . import models
from .utils import utcnow

SNAPSHOT_RETENTION_DAYS = 60
MIN_ELAPSED_DAYS_FOR_VELOCITY = 0.5  # below this, a rate estimate is too noisy to trust


def record_snapshot(db: Session, item: models.Item) -> None:
    db.add(
        models.ItemSnapshot(
            item_id=item.id,
            stars=item.stars,
            forks=item.forks,
            downloads=item.downloads,
            likes=item.likes,
        )
    )


def prune_snapshots(db: Session, item_id: int) -> None:
    cutoff = utcnow() - datetime.timedelta(days=SNAPSHOT_RETENTION_DAYS)
    db.query(models.ItemSnapshot).filter(
        models.ItemSnapshot.item_id == item_id,
        models.ItemSnapshot.captured_at < cutoff,
    ).delete()


def recent_velocity(db: Session, item: models.Item, window_days: int = 7) -> dict | None:
    """Per-day growth rate over roughly the last `window_days`, based on the actual
    elapsed time to the baseline snapshot used. Returns None if there isn't yet enough
    snapshot history to say anything meaningful (first time this item is seen, or the
    only snapshot available is from moments ago)."""
    cutoff = utcnow() - datetime.timedelta(days=window_days)

    baseline = (
        db.query(models.ItemSnapshot)
        .filter(models.ItemSnapshot.item_id == item.id, models.ItemSnapshot.captured_at <= cutoff)
        .order_by(models.ItemSnapshot.captured_at.desc())
        .first()
    )
    if baseline is None:
        baseline = (
            db.query(models.ItemSnapshot)
            .filter(models.ItemSnapshot.item_id == item.id)
            .order_by(models.ItemSnapshot.captured_at.asc())
            .first()
        )
    if baseline is None:
        return None

    elapsed_days = (utcnow() - baseline.captured_at).total_seconds() / 86400.0
    if elapsed_days < MIN_ELAPSED_DAYS_FOR_VELOCITY:
        return None

    stars_per_day = (item.stars - baseline.stars) / elapsed_days
    # Hugging Face "downloads" is a rolling ~30-day window, not a cumulative counter, so its
    # difference is not "downloads gained": it can fall just because a big day left the window.
    # Only the relative change of the window is used, bounded; likes are cumulative and counted as is.
    window_change = max(-1.0, min((item.downloads - baseline.downloads) / max(baseline.downloads, 1000), 2.0))
    attention_per_day = ((item.likes - baseline.likes) * 5 + window_change * 20) / elapsed_days

    return {
        "stars_per_day": stars_per_day,
        "attention_per_day": attention_per_day,
        "elapsed_days": elapsed_days,
    }
