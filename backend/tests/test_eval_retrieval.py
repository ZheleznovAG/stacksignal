import importlib.util
from pathlib import Path
from types import SimpleNamespace


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "eval_retrieval.py"
spec = importlib.util.spec_from_file_location("eval_retrieval", SCRIPT)
eval_retrieval = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(eval_retrieval)


def _items(*ids):
    return [SimpleNamespace(id=item_id) for item_id in ids]


def test_metrics_reward_early_relevant_results_and_manual_judgments():
    metrics = eval_retrieval._metrics(
        _items(3, 9, 1, 8, 2, 7, 6, 5, 4, 10),
        {1, 2, 3},
        {3: 1, 9: -1, 1: 1},
    )

    assert metrics["recall"] == 1.0
    assert metrics["precision"] == 0.3
    assert metrics["mrr"] == 1.0
    assert 0.0 < metrics["ndcg"] <= 1.0
    assert metrics["judged_precision"] == 2 / 3
    assert metrics["judged_count"] == 3


def test_metrics_report_missing_truth():
    metrics = eval_retrieval._metrics(_items(10, 20), {1, 2}, {})

    assert metrics["recall"] == 0.0
    assert metrics["mrr"] == 0.0
    assert set(metrics["miss_ids"]) == {1, 2}
