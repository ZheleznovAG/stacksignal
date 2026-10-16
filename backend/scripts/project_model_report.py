from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import models, project_model  # noqa: E402
from app.db import SessionLocal  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Show StackSignal's current structured understanding of one project.")
    parser.add_argument("--project", required=True, help="Project slug")
    args = parser.parse_args()
    db = SessionLocal()
    try:
        project = db.query(models.Project).filter_by(slug=args.project).one_or_none()
        if project is None:
            print(f"Project not found: {args.project}", file=sys.stderr)
            return 2
        model = project_model.serialise(db, project)
        if model is None:
            print(f"{project.name}: no technical model yet. Use Projects -> Model -> Build model.")
            return 1
        print(f"{project.name} [{project.slug}]")
        print(f"stale: {model['stale']}  updated: {model['updated_at']}  prompt: {model['prompt_version']}")
        print(f"\n{model['summary']}\n")
        print("GOALS")
        for row in sorted(model["goals"], key=lambda x: -x.get("priority", 2)):
            print(f"  P{row.get('priority', 2)} {row.get('key')}: {row.get('title')}")
            if row.get("outcome"):
                print(f"      -> {row['outcome']}")
        print("\nSUBSYSTEMS")
        for row in model["subsystems"]:
            print(f"  [{row.get('status', 'unknown')}] {row.get('key')}: {row.get('title')}")
            if row.get("current_capabilities"):
                print("      has: " + "; ".join(row["current_capabilities"]))
        print("\nGAPS")
        for row in sorted(model["gaps"], key=lambda x: -x.get("priority", 2)):
            print(f"  P{row.get('priority', 2)} {row.get('key')}: {row.get('title')}")
            if row.get("current_state") or row.get("desired_state"):
                print(f"      {row.get('current_state', '?')} -> {row.get('desired_state', '?')}")
            if row.get("needs"):
                print("      needs: " + "; ".join(n.get("title", "") for n in row["needs"]))
        if model["risks"]:
            print("\nRISKS")
            for row in model["risks"]:
                print(f"  - {row.get('title')}: {row.get('why', '')}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
