"""Show whether each project has enough manual evidence for local preference learning.

No AI call and no writes. Once a project has at least two positive and two negative embedded judgments,
StackSignal uses that profile as a weak extra ranking signal for unseen chat/Radar candidates.

    .venv\\Scripts\\python.exe backend\\scripts\\preference_report.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import contexts, decisions, models, preference_learning  # noqa: E402
from app.db import SessionLocal  # noqa: E402


def main() -> None:
    db = SessionLocal()
    try:
        projects = db.query(models.Project).filter(contexts.attended_filter()).order_by(models.Project.name).all()
        print(f"{'project':<26} {'+':>3} {'-':>3} {'emb+':>5} {'emb-':>5}  learning")
        for project in projects:
            stats = preference_learning.profile_stats(db, [project.id])
            state = "ACTIVE" if stats["active"] else "waiting"
            print(f"{project.slug:<26} {stats['positive']:>3} {stats['negative']:>3} "
                  f"{stats['embedded_positive']:>5} {stats['embedded_negative']:>5}  {state}")
            if stats["active"]:
                inbox = decisions.undecided_for(db, project)
                affinities = preference_learning.preference_scores(db, [project.id], [row.item_id for row in inbox])
                top = [(row.item.name, affinities[row.item_id]) for row in inbox if row.item_id in affinities][:5]
                if top:
                    print("    learned inbox: " + ", ".join(f"{name} ({score:+.2f})" for name, score in top))
        print("\nActivation threshold: 2 positive + 2 negative judgments with embeddings per project.")
        print("Positive: Useful/Trial/Adopt. Negative: Not relevant/Ignore. Watch/Assess/Hold are neutral.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
