import datetime

from app import models, scoring
from app.utils import utcnow


def _github_item(**overrides) -> models.Item:
    defaults = dict(
        source=models.Source.GITHUB.value,
        external_id="owner/repo",
        name="repo",
        owner="owner",
        url="https://github.com/owner/repo",
        stars=100,
        forks=10,
        downloads=0,
        likes=0,
        source_created_at=utcnow() - datetime.timedelta(days=100),
        source_updated_at=utcnow() - datetime.timedelta(days=1),
        raw={},
    )
    defaults.update(overrides)
    return models.Item(**defaults)


def test_maturity_rewards_license_and_forks_and_stars():
    bare = _github_item(stars=100, forks=5, raw={})
    decorated = _github_item(stars=2000, forks=50, raw={"license": "MIT"})
    assert scoring.compute_maturity(decorated) > scoring.compute_maturity(bare)


def test_maturity_penalizes_archived():
    active = _github_item(raw={"license": "MIT", "archived": False})
    archived = _github_item(raw={"license": "MIT", "archived": True})
    assert scoring.compute_maturity(active) > scoring.compute_maturity(archived)


def test_momentum_without_velocity_uses_since_creation_average():
    # A brand new, already-popular item should score high on momentum even with no
    # snapshot history yet (velocity=None fallback).
    new_and_popular = _github_item(
        stars=5000, source_created_at=utcnow() - datetime.timedelta(days=2)
    )
    old_and_modest = _github_item(
        stars=50, source_created_at=utcnow() - datetime.timedelta(days=1000)
    )
    assert scoring.compute_momentum(new_and_popular, None) > scoring.compute_momentum(old_and_modest, None)


def test_momentum_prefers_real_velocity_over_fallback():
    item = _github_item(stars=100)
    fast_growth = {"stars_per_day": 500.0, "attention_per_day": 0.0, "elapsed_days": 7}
    slow_growth = {"stars_per_day": 1.0, "attention_per_day": 0.0, "elapsed_days": 7}
    assert scoring.compute_momentum(item, fast_growth) > scoring.compute_momentum(item, slow_growth)


def test_momentum_velocity_ignores_negative_rate():
    item = _github_item(stars=100)
    shrinking = {"stars_per_day": -50.0, "attention_per_day": 0.0, "elapsed_days": 7}
    # A negative/near-zero rate should not raise an error and should score low, not negative.
    score = scoring.compute_momentum(item, shrinking)
    assert 0.0 <= score <= 1.0


def test_suggest_status_low_relevance_stays_new():
    assert scoring.suggest_status(momentum=0.9, maturity=0.9, relevance=0.05) == models.Status.NEW


def test_the_system_never_suggests_trial_or_adopt():
    for momentum in (0.0, 0.6, 1.0):
        for maturity in (0.0, 0.7, 1.0):
            for relevance in (0.0, 0.2, 0.5, 0.9, 1.0):
                status = scoring.suggest_status(momentum, maturity, relevance)
                assert status not in (models.Status.TRIAL, models.Status.ADOPT)


def test_suggest_status_high_everything_is_assess():
    assert scoring.suggest_status(momentum=0.6, maturity=0.7, relevance=0.6) == models.Status.ASSESS


def test_suggest_status_high_relevance_low_momentum_is_assess():
    assert scoring.suggest_status(momentum=0.1, maturity=0.7, relevance=0.6) == models.Status.ASSESS


def test_suggest_status_moderate_relevance_is_assess():
    assert scoring.suggest_status(momentum=0.9, maturity=0.9, relevance=0.35) == models.Status.ASSESS


def test_suggest_status_low_but_nonzero_relevance_is_watch():
    assert scoring.suggest_status(momentum=0.9, maturity=0.9, relevance=0.2) == models.Status.WATCH
