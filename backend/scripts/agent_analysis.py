"""Work through the AI analysis backlog with a local agent instead of the gateway (app/agent_analysis.py).

    python stacksignal.py script agent-analysis status
    python stacksignal.py script agent-analysis next [--items 10] [--batches 1]
    python stacksignal.py script agent-analysis show path/from/export/batch-0001.json
    python stacksignal.py script agent-analysis apply path/from/export/batch-0001.json [--model NAME]
    python stacksignal.py script agent-analysis release

`next` writes data/agent-analysis/context.json (prompt + shared payload) and batch files; the agent
writes each answer next to its batch as batch-<n>.result.json; `apply` validates and stores it.
Safe while StackSignal is running: every write is one short transaction.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import agent_analysis  # noqa: E402
from app.db import SessionLocal  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    nxt = commands.add_parser("next")
    nxt.add_argument("--items", type=int, default=10)
    nxt.add_argument("--batches", type=int, default=1)
    show = commands.add_parser("show")
    show.add_argument("batch", type=Path)
    app = commands.add_parser("apply")
    app.add_argument("batch", type=Path)
    app.add_argument("--result", type=Path)
    app.add_argument("--model", default="")
    app.add_argument("--provider", default=agent_analysis.PROVIDER,
                     help="Agent identity stored in provenance (for example codex)")
    app.add_argument("--refresh", action="store_true", help="Reassess already analysed items in this batch")
    commands.add_parser("release")
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")

    with SessionLocal() as db:
        if args.command == "status":
            print(json.dumps(agent_analysis.status(db), ensure_ascii=False))
        elif args.command == "next":
            paths = agent_analysis.export(db, items_per_batch=args.items, batches=args.batches)
            print(f"context: {agent_analysis.WORK_DIR / 'context.json'}")
            for path in paths:
                print(f"batch: {path}")
            if not paths:
                print("backlog is empty")
        elif args.command == "show":
            print(agent_analysis.describe(args.batch))
        elif args.command == "apply":
            try:
                stats = agent_analysis.apply(db, args.batch, result_path=args.result, model=args.model,
                                             provider=args.provider, refresh=args.refresh)
            except (ValueError, FileNotFoundError) as error:  # pydantic's ValidationError is a ValueError
                raise SystemExit(f"rejected, nothing stored: {error}")
            print(json.dumps(stats))
        else:
            print(f"released {agent_analysis.release()} batch(es)")


if __name__ == "__main__":
    main()
