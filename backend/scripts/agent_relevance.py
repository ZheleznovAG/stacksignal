"""Plan, export and apply scoped project relevance analysis through agent-readable files."""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import agent_relevance  # noqa: E402
from app.db import SessionLocal  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("plan", "status", "export"):
        command = commands.add_parser(name)
        command.add_argument("--projects", action="append", required=True,
                             help="Project slugs; repeat the flag or separate slugs with commas")
        command.add_argument("--work-dir", type=Path, default=agent_relevance.WORK_DIR)
        if name != "status":
            command.add_argument("--items", type=int, default=100)
        if name == "export":
            command.add_argument("--batches", type=int, default=1)
    command = commands.add_parser("apply")
    command.add_argument("batch", type=Path)
    command.add_argument("--result", type=Path)
    command.add_argument("--model", default="")
    command.add_argument("--provider", default=agent_relevance.PROVIDER)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    try:
        with SessionLocal() as db:
            if args.command == "apply":
                result = agent_relevance.apply(db, args.batch, result_path=args.result,
                                               model=args.model, provider=args.provider)
            elif args.command == "export":
                paths = agent_relevance.export(db, projects=args.projects, items_per_batch=args.items,
                                                batches=args.batches, work_dir=args.work_dir)
                result = {"work_dir": str(args.work_dir), "batches": [str(p) for p in paths]}
            elif args.command == "plan":
                result = agent_relevance.plan(db, projects=args.projects, items_per_batch=args.items,
                                              work_dir=args.work_dir)
            else:
                result = agent_relevance.status(db, projects=args.projects, work_dir=args.work_dir)
    except (ValueError, FileNotFoundError) as error:
        raise SystemExit(f"rejected: {error}") from error
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
