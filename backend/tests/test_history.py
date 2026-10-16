import datetime

from app import history, models
from app.utils import utcnow


def _make_item(db_session, **overrides) -> models.Item:
    defaults = dict(
        source=models.Source.GITHUB.value,
        external_id="owner/repo",
        name="repo",
        owner="owner",
        url="https://github.com/owner/repo",
        stars=100,
        forks=0,
        downloads=0,
        likes=0,
        raw={},
    )
    defaults.update(overrides)
    item = models.Item(**defaults)
    db_session.add(item)
    db_session.flush()
    return item


def test_no_snapshots_returns_none(db_session):
    item = _make_item(db_session)
    assert history.recent_velocity(db_session, item) is None


def test_velocity_uses_real_elapsed_time_not_run_cadence(db_session):
    item = _make_item(db_session, stars=800)
    # Baseline snapshot backdated 8 days, as if collection ran daily since then.
    snapshot = models.ItemSnapshot(item_id=item.id, stars=100, forks=0, downloads=0, likes=0)
    db_session.add(snapshot)
    db_session.flush()
    snapshot.captured_at = utcnow() - datetime.timedelta(days=8)
    db_session.flush()

    velocity = history.recent_velocity(db_session, item, window_days=7)
    assert velocity is not None
    # (800 - 100) stars over ~8 days ~= 87.5/day, not (800-100)/one-run-interval.
    assert 80 <= velocity["stars_per_day"] <= 95


def test_velocity_none_when_only_snapshot_is_seconds_old(db_session):
    item = _make_item(db_session, stars=100)
    history.record_snapshot(db_session, item)
    db_session.flush()

    assert history.recent_velocity(db_session, item) is None


def test_prune_snapshots_removes_only_old_rows(db_session):
    item = _make_item(db_session)
    old = models.ItemSnapshot(item_id=item.id, stars=1, forks=0, downloads=0, likes=0)
    recent = models.ItemSnapshot(item_id=item.id, stars=2, forks=0, downloads=0, likes=0)
    db_session.add_all([old, recent])
    db_session.flush()
    old.captured_at = utcnow() - datetime.timedelta(days=90)
    db_session.flush()

    history.prune_snapshots(db_session, item.id)
    db_session.flush()

    remaining = db_session.query(models.ItemSnapshot).filter_by(item_id=item.id).all()
    assert len(remaining) == 1
    assert remaining[0].stars == 2
