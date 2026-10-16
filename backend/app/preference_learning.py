"""Learn a small, local project preference profile from the developer's own judgments.

The profile is intentionally conservative:
* only explicit per-project votes/decisions are training evidence;
* at least two positive AND two negative embedded examples are required;
* already-judged items are never boosted by the learned profile (this is for discovering new items);
* the signal only reranks an existing candidate pool, so it cannot drag unrelated catalogue items into a query;
* embeddings stay local in SQLite and no model call is made here.

This is not a recommender model. It is a weak extra retrieval vote that says "among otherwise plausible
candidates, these look more like things I kept than things I rejected".
"""

from collections import defaultdict
from collections.abc import Iterable

from sqlalchemy.orm import Session

from . import embeddings, models

MIN_POSITIVE_EXAMPLES = 2
MIN_NEGATIVE_EXAMPLES = 2
MAX_EXAMPLES_PER_SIDE = 30
MAX_RESULTS = 50
MIN_POSITIVE_SIMILARITY = 0.30
MIN_AFFINITY = 0.02

# Only outcomes that actually say something about fit train the preference model.
# WATCH/ASSESS are curiosity/work-in-progress states, and HOLD is deliberately neutral: none of them
# should teach the recommender that similar items are desirable or undesirable. Explicit 👍/👎 votes
# remain strong judgments and are handled in ``project_judgments`` above the decision override.
_POSITIVE_DECISIONS = {models.Status.TRIAL, models.Status.ADOPT}
_NEGATIVE_DECISIONS = {models.Status.IGNORE}


def _status(value) -> models.Status | None:
    if isinstance(value, models.Status):
        return value
    try:
        return models.Status(value)
    except ValueError:
        return None


def project_judgments(db: Session, project_ids: Iterable[int]) -> dict[int, int]:
    """Return item -> {-1, +1} for unambiguous explicit judgments in the selected projects.

    Latest votes win within a project. A project decision is stronger than a lightweight vote. If an item has
    contradictory judgments across multiple mentioned projects, it is omitted instead of guessing which context wins.
    """
    ids = set(project_ids)
    if not ids:
        return {}
    projects = db.query(models.Project).filter(models.Project.id.in_(ids)).all()
    slugs = {project.id: project.slug for project in projects}
    if not slugs:
        return {}

    per_project: dict[tuple[int, int], int] = {}
    slug_to_id = {slug: project_id for project_id, slug in slugs.items()}
    seen_votes: set[tuple[int, str]] = set()
    rows = (db.query(models.ItemFeedback)
            .filter(models.ItemFeedback.kind == "vote", models.ItemFeedback.project_slug.in_(slug_to_id))
            .order_by(models.ItemFeedback.id.desc()).all())
    for row in rows:
        key = (row.item_id, row.project_slug)
        if key in seen_votes:
            continue
        seen_votes.add(key)
        if row.verdict in (-1, 1):
            per_project[(row.item_id, slug_to_id[row.project_slug])] = row.verdict
        # verdict=0 deliberately clears older votes: seen_votes prevents them resurfacing.

    decisions = db.query(models.ItemProjectDecision).filter(models.ItemProjectDecision.project_id.in_(ids)).all()
    for decision in decisions:
        status = _status(decision.status)
        key = (decision.item_id, decision.project_id)
        if status in _POSITIVE_DECISIONS:
            per_project[key] = 1
        elif status in _NEGATIVE_DECISIONS:
            per_project[key] = -1
        else:
            per_project.pop(key, None)

    by_item: dict[int, set[int]] = defaultdict(set)
    for (item_id, _project_id), verdict in per_project.items():
        by_item[item_id].add(verdict)
    return {item_id: next(iter(verdicts)) for item_id, verdicts in by_item.items() if len(verdicts) == 1}


def _centroid(vectors: list[list[float]]) -> list[float]:
    averaged = [sum(values) / len(vectors) for values in zip(*vectors)]
    norm = sum(value * value for value in averaged) ** 0.5
    return [value / norm for value in averaged] if norm else averaged


def _embedded_examples(db: Session, judgments: dict[int, int]) -> tuple[list[list[float]], list[list[float]]]:
    if not judgments:
        return [], []
    rows = db.query(models.ItemEmbedding).filter(models.ItemEmbedding.item_id.in_(judgments)).all()
    positives = [row.vector for row in rows if judgments[row.item_id] == 1][:MAX_EXAMPLES_PER_SIDE]
    negatives = [row.vector for row in rows if judgments[row.item_id] == -1][:MAX_EXAMPLES_PER_SIDE]
    return positives, negatives


def preference_scores(db: Session, project_ids: Iterable[int], candidate_ids: Iterable[int]) -> dict[int, float]:
    """Return affinity for *unjudged* candidates, or {} until enough balanced evidence exists.

    Affinity is cosine(candidate, positive centroid) - cosine(candidate, negative centroid). A candidate also has to be
    meaningfully similar to the positive profile; merely being unlike rejected items is not enough.
    """
    candidates = set(candidate_ids)
    if not candidates:
        return {}
    judgments = project_judgments(db, project_ids)
    positives, negatives = _embedded_examples(db, judgments)
    if len(positives) < MIN_POSITIVE_EXAMPLES or len(negatives) < MIN_NEGATIVE_EXAMPLES:
        return {}

    positive_centroid = _centroid(positives)
    negative_centroid = _centroid(negatives)
    unseen = candidates - set(judgments)
    if not unseen:
        return {}

    scored: dict[int, float] = {}
    rows = db.query(models.ItemEmbedding).filter(models.ItemEmbedding.item_id.in_(unseen)).all()
    for row in rows:
        positive_similarity = embeddings.cosine(row.vector, positive_centroid)
        affinity = positive_similarity - embeddings.cosine(row.vector, negative_centroid)
        if positive_similarity >= MIN_POSITIVE_SIMILARITY and affinity >= MIN_AFFINITY:
            scored[row.item_id] = affinity
    return scored


def preference_ranking(db: Session, project_ids: Iterable[int], candidate_ids: Iterable[int]) -> list[int]:
    scores = preference_scores(db, project_ids, candidate_ids)
    return [item_id for item_id, _score in sorted(scores.items(), key=lambda pair: pair[1], reverse=True)[:MAX_RESULTS]]


def profile_stats(db: Session, project_ids: Iterable[int]) -> dict[str, int | bool]:
    judgments = project_judgments(db, project_ids)
    positives, negatives = _embedded_examples(db, judgments)
    positive = sum(verdict == 1 for verdict in judgments.values())
    negative = sum(verdict == -1 for verdict in judgments.values())
    return {
        "positive": positive,
        "negative": negative,
        "judged": positive + negative,
        "embedded_positive": len(positives),
        "embedded_negative": len(negatives),
        "needed_positive": max(0, MIN_POSITIVE_EXAMPLES - len(positives)),
        "needed_negative": max(0, MIN_NEGATIVE_EXAMPLES - len(negatives)),
        "active": len(positives) >= MIN_POSITIVE_EXAMPLES and len(negatives) >= MIN_NEGATIVE_EXAMPLES,
    }
