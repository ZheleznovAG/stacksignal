"""Print the inferred need order for one project (or all projects)."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import models, needs  # noqa: E402
from app.db import SessionLocal  # noqa: E402


def show(project, plan):
    print(f"\n{project.name} [{project.slug}]")
    print("  NOW")
    if not plan["ready"]:
        print("    -")
    for row in plan["ready"]:
        suffix = f"; unlocks {', '.join(x['title'] for x in row['unlocks'])}" if row["unlocks"] else ""
        print(f"    p{row['priority']}  {row['title']}{suffix}")
    print("  LATER / BLOCKED")
    if not plan["blocked"]:
        print("    -")
    for row in plan["blocked"]:
        blockers = ", ".join(x["title"] for x in row["blocked_by"])
        print(f"    p{row['priority']}  {row['title']} <- {blockers}")
    if plan["satisfied"]:
        print(f"  satisfied: {plan['satisfied']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", help="Project slug; omit to print every project")
    args = parser.parse_args()
    with SessionLocal() as db:
        query = db.query(models.Project)
        if args.project:
            query = query.filter_by(slug=args.project)
        projects = query.order_by(models.Project.name).all()
        if not projects:
            raise SystemExit("Project not found")
        for project in projects:
            show(project, needs.project_plan(db, project.id))


if __name__ == "__main__":
    main()
