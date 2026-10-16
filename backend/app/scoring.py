"""Heuristic scoring: momentum (how fast it's growing) and maturity (how safe to bet on).

These run on every collection pass, independent of whether the LLM enrichment step is
available, so the Radar and Discover views always have something to sort by.
"""

import datetime
import math

from . import models
from .utils import utcnow


def _age_days(dt: datetime.datetime | None) -> float:
    if dt is None:
        return 365.0
    delta = utcnow() - dt
    return max(delta.total_seconds() / 86400.0, 0.5)


def compute_momentum(item: models.Item, velocity: dict | None = None) -> float:
    """0..1, roughly: how fast is attention accumulating right now.

    Prefers a real measured per-day rate (`velocity`, from history.recent_velocity —
    diffed against an actual past snapshot over actual elapsed time). Falls back to a
    since-creation average (volume / age) only when no snapshot history exists yet,
    e.g. the very first time an item is seen.
    """
    age = _age_days(item.source_created_at)
    push_age = _age_days(item.source_updated_at)
    if item.source == models.Source.GITHUB.value:
        recency_bonus = 1.0 if push_age <= 7 else (0.5 if push_age <= 30 else 0.0)
    else:
        recency_bonus = 1.0 if push_age <= 14 else (0.5 if push_age <= 45 else 0.0)

    if velocity is not None:
        rate = velocity["stars_per_day"] if item.source == models.Source.GITHUB.value else velocity["attention_per_day"]
        rate = max(rate, 0.0)
    else:
        volume = item.stars if item.source == models.Source.GITHUB.value else item.likes * 5 + item.downloads / 100.0
        rate = volume / age  # "units of attention" per day since creation, as a first estimate

    # log-compress so a handful of huge outliers don't flatten everything else
    rate_score = min(math.log1p(rate) / math.log1p(200), 1.0)
    new_bonus = 0.2 if age <= 30 else 0.0
    return round(min(rate_score * 0.7 + recency_bonus * 0.1 + new_bonus, 1.0), 3)


def compute_maturity(item: models.Item) -> float:
    """0..1, roughly: how safe is this to depend on."""
    age = _age_days(item.source_created_at)
    age_score = min(age / 365.0, 1.0)  # a year old = fully "aged in"

    signal = 0.0
    raw = item.raw or {}
    if item.source == models.Source.GITHUB.value:
        if raw.get("license"):
            signal += 0.25
        if not raw.get("archived"):
            signal += 0.1
        if item.forks >= 20:
            signal += 0.15
        if item.stars >= 1000:
            signal += 0.2
    else:
        if item.downloads >= 10000:
            signal += 0.3
        if item.likes >= 100:
            signal += 0.2
        if raw.get("library_name"):
            signal += 0.1
        if raw.get("license") and item.source in (models.Source.NPM.value, models.Source.PYPI.value):
            signal += 0.1

    return round(min(age_score * 0.3 + signal, 1.0), 3)


def suggest_status(momentum: float, maturity: float, relevance: float) -> models.Status:
    """What the system may suggest on its own. It stops at Assess ("worth a proper look"):
    Trial ("I am trying it in a project") and Adopt ("I use it") are facts about what the user
    does, which no score can know, so only the user can set them. momentum and maturity stay in
    the signature for callers but no longer decide the outcome."""
    if relevance < 0.15:
        return models.Status.NEW
    if relevance >= 0.3:
        return models.Status.ASSESS
    return models.Status.WATCH
