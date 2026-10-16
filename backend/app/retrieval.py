"""Small, dependency-free helpers for combining independent retrieval rankings.

StackSignal deliberately keeps keyword/FTS, semantic embeddings, project relevance and freshness
as separate signals. Reciprocal Rank Fusion (RRF) lets those signals vote on the final order
without pretending that a BM25 score, cosine similarity and a project relevance score live on the
same numeric scale.
"""

from collections.abc import Iterable, Mapping


DEFAULT_RRF_K = 60


def reciprocal_rank_fusion(rankings: Mapping[str, Iterable[int]], *, weights: Mapping[str, float] | None = None,
                           k: int = DEFAULT_RRF_K) -> dict[int, float]:
    """Return ``item_id -> fused score`` for best-first rankings.

    Duplicate ids inside one ranking count once (at their first position). Empty rankings are fine.
    ``k=60`` is the conventional conservative RRF constant: top positions matter, while one source
    cannot completely swamp another just because its native score has a larger numeric range.
    """
    if k < 1:
        raise ValueError("RRF k must be positive")
    weights = weights or {}
    scores: dict[int, float] = {}
    for name, ranking in rankings.items():
        weight = float(weights.get(name, 1.0))
        if weight <= 0:
            continue
        seen: set[int] = set()
        rank = 0
        for item_id in ranking:
            if item_id in seen:
                continue
            seen.add(item_id)
            rank += 1
            scores[item_id] = scores.get(item_id, 0.0) + weight / (k + rank)
    return scores
