from app import retrieval


def test_rrf_rewards_consensus_without_comparing_native_score_scales():
    scores = retrieval.reciprocal_rank_fusion({
        "keyword": [1, 2, 3],
        "semantic": [3, 4, 1],
    })

    assert scores[1] > scores[2]  # appears in both rankings
    assert scores[3] > scores[4]  # appears in both rankings


def test_rrf_respects_source_weights_and_ignores_duplicates():
    weighted = retrieval.reciprocal_rank_fusion(
        {"project": [10, 10, 20], "momentum": [20, 10]},
        weights={"project": 2.0, "momentum": 0.1},
    )

    assert weighted[10] > weighted[20]
    # A duplicate in one ranking must not get a second vote.
    deduped = retrieval.reciprocal_rank_fusion({"x": [1, 1, 2]})
    assert deduped[1] == 1 / 61
    assert deduped[2] == 1 / 62


def test_rrf_rejects_invalid_k():
    try:
        retrieval.reciprocal_rank_fusion({"x": [1]}, k=0)
    except ValueError as error:
        assert "positive" in str(error)
    else:
        raise AssertionError("expected ValueError")
