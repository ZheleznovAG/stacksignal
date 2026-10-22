"""Evaluate chat retrieval against the accumulated StackSignal database.

The preferred ground truth is the developer's own feedback and per-project decisions. That closes
the loop StackSignal is meant to learn from: find -> inspect -> useful/not relevant or a project
decision -> measure whether later retrieval puts useful things near the top. Projects that do not
yet have manual labels can fall back to strong past AI relevance so the report stays usable while
manual evidence accumulates.

Metrics:
* recall@30: did the model's whole candidate catalogue contain the known useful items?
* precision@10: binary precision against the known-positive set (conservative while labels are sparse)
* MRR: reciprocal rank of the first known useful item
* nDCG@10: whether known useful items are concentrated near the top
* judged precision@10: among manually judged results in the top 10, how many were positive

Run from anywhere with the project venv:
    python stacksignal.py script eval-retrieval
    python stacksignal.py script eval-retrieval --manual-only
"""

import argparse
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import chat, contexts, feedback, models  # noqa: E402
from app.db import SessionLocal  # noqa: E402

RELEVANCE_THRESHOLD = 0.75
GROUND_TRUTH_PER_PROJECT = 20
METRIC_K = 10
# Votes are explicit judgments. For project decisions only actual outcomes train/evaluate taste:
# Trial/Adopt are positive, Ignore is negative; Watch/Assess/Hold are intentionally neutral.
_POSITIVE_DECISIONS = {"trial", "adopt"}
_NEGATIVE_DECISIONS = {"ignore"}


def _value(value) -> str:
    return value.value if hasattr(value, "value") else str(value)


def _ai_analyzed_ids(db) -> set[int]:
    return {row.item_id for row in db.query(models.ItemAnalysisState).all()
            if (row.provenance or {}).get("mode") == "ai"}


def _manual_judgments(db, project: models.Project) -> dict[int, int]:
    """Latest explicit judgment for each item: 1 useful/relevant, -1 rejected/not relevant.

    A project decision is stronger than a lightweight vote. Only Trial/Adopt count as positive
    outcomes and Ignore as a negative outcome; Watch/Assess/Hold are not evidence of fit yet.
    """
    judgments: dict[int, int] = {}
    seen: set[int] = set()
    rows = (db.query(models.ItemFeedback)
            .filter(models.ItemFeedback.kind == "vote", models.ItemFeedback.project_slug == project.slug)
            .order_by(models.ItemFeedback.id.desc()).all())
    for row in rows:
        if row.item_id in seen:
            continue
        seen.add(row.item_id)
        if row.verdict in (-1, 1):
            judgments[row.item_id] = row.verdict

    for decision in db.query(models.ItemProjectDecision).filter_by(project_id=project.id).all():
        status = _value(decision.status)
        if status in _POSITIVE_DECISIONS:
            judgments[decision.item_id] = 1
        elif status in _NEGATIVE_DECISIONS:
            judgments[decision.item_id] = -1
    return judgments


def _ground_truth(db, project: models.Project, ai_ids: set[int], *, manual_only: bool) -> tuple[list[models.Item], str, dict[int, int]]:
    judgments = _manual_judgments(db, project)
    positive_ids = [item_id for item_id, verdict in judgments.items() if verdict == 1]
    if positive_ids:
        by_id = {item.id: item for item in db.query(models.Item).filter(models.Item.id.in_(positive_ids),
                                                                       models.Item.archived.is_(False)).all()}
        # Decisions/votes are already deliberate evidence; newest ItemFeedback ordering is not a useful
        # relevance ordering, so keep a stable id order and evaluate all known positives up to the cap.
        items = [by_id[item_id] for item_id in sorted(positive_ids) if item_id in by_id]
        return items[:GROUND_TRUTH_PER_PROJECT], "manual", judgments
    if manual_only:
        return [], "none", judgments

    rows = (db.query(models.ItemProjectRelevance)
            .filter(models.ItemProjectRelevance.project_id == project.id,
                    models.ItemProjectRelevance.score >= RELEVANCE_THRESHOLD)
            .order_by(models.ItemProjectRelevance.score.desc()).all())
    items = [row.item for row in rows if row.item_id in ai_ids and not row.item.archived]
    return items[:GROUND_TRUTH_PER_PROJECT], "ai-fallback", judgments


def _keyword_question(project: models.Project) -> str | None:
    name_words = {w.lower() for w in (project.name + " " + project.slug.replace("-", " ")).split()}
    picked = [kw for kw in project.keywords if kw.lower() not in name_words]
    if not picked:
        return None
    return "что можно использовать для: " + ", ".join(picked)


def _metrics(items: list[models.Item], truth_ids: set[int], judgments: dict[int, int]) -> dict:
    ranked_ids = [item.id for item in items]
    hits = [index + 1 for index, item_id in enumerate(ranked_ids) if item_id in truth_ids]
    top = ranked_ids[:METRIC_K]
    dcg = sum(1.0 / math.log2(rank + 1) for rank, item_id in enumerate(top, start=1) if item_id in truth_ids)
    ideal_hits = min(len(truth_ids), METRIC_K)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    judged = [judgments[item_id] for item_id in top if item_id in judgments and judgments[item_id] != 0]
    return {
        "recall": len(hits) / len(truth_ids) if truth_ids else 0.0,
        "precision": sum(1 for item_id in top if item_id in truth_ids) / METRIC_K,
        "mrr": 1.0 / hits[0] if hits else 0.0,
        "ndcg": dcg / idcg if idcg else 0.0,
        "judged_precision": (sum(1 for verdict in judged if verdict == 1) / len(judged)) if judged else None,
        "judged_count": len(judged),
        "miss_ids": [item_id for item_id in truth_ids if item_id not in ranked_ids],
    }


def evaluate(db, *, manual_only: bool = False, compare_learning: bool = False) -> dict:
    ai_ids = _ai_analyzed_ids(db)
    projects = db.query(models.Project).filter(contexts.attended_filter()).order_by(models.Project.name).all()
    report = {"projects": [], "queries": []}

    for project in projects:
        truth, source, judgments = _ground_truth(db, project, ai_ids, manual_only=manual_only)
        if not truth:
            continue
        truth_ids = {item.id for item in truth}
        questions = [("named", f"что нового полезного для {project.name}?")]
        keyword_q = _keyword_question(project)
        if keyword_q:
            questions.append(("keyword", keyword_q))

        project_rows = []
        for style, question in questions:
            ranked = chat._retrieve(db, question, learn_from_feedback=True)
            metrics = _metrics(ranked, truth_ids, judgments)
            if compare_learning:
                baseline = _metrics(chat._retrieve(db, question, learn_from_feedback=False), truth_ids, judgments)
                metrics["baseline"] = {key: baseline[key] for key in ("recall", "precision", "mrr", "ndcg",
                                                                       "judged_precision", "judged_count")}
            metrics.update({"style": style, "question": question})
            report["queries"].append(metrics)
            project_rows.append(metrics)

        by_id = {item.id: item.name for item in truth}
        report["projects"].append({
            "project": project.slug,
            "ground_truth": len(truth_ids),
            "source": source,
            "manual_judgments": len(judgments),
            "queries": project_rows,
            "truth_names": by_id,
        })
    return report


def _avg(rows: list[dict], key: str) -> float:
    return sum(row[key] for row in rows) / len(rows) if rows else 0.0


def _print(report: dict) -> None:
    print(f"{'project':<24} {'gt':>3}  {'source':<11} {'style':<7} {'R@30':>6} {'P@10':>6} {'MRR':>6} {'nDCG':>6} {'judged P':>9}")
    for project in report["projects"]:
        for i, row in enumerate(project["queries"]):
            judged = f"{row['judged_precision']:.0%}/{row['judged_count']}" if row["judged_precision"] is not None else "n/a"
            name = project["project"] if i == 0 else ""
            print(f"{name:<24} {project['ground_truth']:>3}  {project['source']:<11} {row['style']:<7} "
                  f"{row['recall']:>6.0%} {row['precision']:>6.0%} {row['mrr']:>6.2f} {row['ndcg']:>6.2f} {judged:>9}")
            if row.get("baseline"):
                base = row["baseline"]
                print(f"    learning delta: P@{METRIC_K} {row['precision'] - base['precision']:+.0%}, "
                      f"MRR {row['mrr'] - base['mrr']:+.2f}, nDCG {row['ndcg'] - base['ndcg']:+.2f}")
            if row["miss_ids"]:
                misses = [project["truth_names"].get(item_id, str(item_id)) for item_id in row["miss_ids"]]
                print(f"    misses: {misses}")

    rows = report["queries"]
    print(f"\nOverall across {len(rows)} evaluated questions:")
    print(f"  recall@{chat.TOP_K}: {_avg(rows, 'recall'):.1%}")
    print(f"  precision@{METRIC_K}: {_avg(rows, 'precision'):.1%}  (conservative while manual labels are sparse)")
    print(f"  MRR: {_avg(rows, 'mrr'):.3f}")
    print(f"  nDCG@{METRIC_K}: {_avg(rows, 'ndcg'):.3f}")
    judged_rows = [row for row in rows if row["judged_precision"] is not None]
    if judged_rows:
        print(f"  judged precision@{METRIC_K}: {_avg(judged_rows, 'judged_precision'):.1%} "
              f"({sum(row['judged_count'] for row in judged_rows)} judged top-{METRIC_K} results)")
    manual_projects = sum(project["source"] == "manual" for project in report["projects"])
    print(f"  manual-ground-truth projects: {manual_projects}/{len(report['projects'])}")
    if not manual_projects:
        print("\nNo project has a positive manual vote/decision yet. The report is using AI relevance as a temporary")
        print("fallback; start voting/deciding on candidates, then rerun with --manual-only for the real learning loop.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manual-only", action="store_true", help="Evaluate only projects with explicit user labels")
    parser.add_argument("--compare-learning", action="store_true",
                        help="For every query also run retrieval with preference learning disabled and print deltas")
    args = parser.parse_args()
    session = SessionLocal()
    try:
        _print(evaluate(session, manual_only=args.manual_only, compare_learning=args.compare_learning))
    finally:
        session.close()
