"""Need pilot: seed, plan, run, export, apply or review; no source collection or Telegram delivery."""
import argparse
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config, need_analysis, need_pilot, needs
from app.ai_analysis import with_language
from app.db import SessionLocal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["seed", "plan", "run", "export", "apply", "review"])
    parser.add_argument("--batch", type=Path)
    parser.add_argument("--result", type=Path)
    parser.add_argument("--provider", default="local-agent")
    parser.add_argument("--model", default="")
    parser.add_argument("--max-batches", type=int, choices=range(1, 7), default=1)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    work = config.DATA_DIR / "need-pilot"
    with SessionLocal() as db:
        if args.command == "seed":
            result = need_pilot.seed(db)
        elif args.command == "run":
            result = need_analysis.run(db, max_batches=args.max_batches)
        elif args.command == "apply":
            if not args.batch or not args.result:
                parser.error("apply requires --batch and --result")
            batch = json.loads(args.batch.read_text(encoding="utf-8"))
            data = json.loads(args.result.read_text(encoding="utf-8"))
            result = need_analysis.apply_result(db, batch, data,
                {"mode": "ai", "provider": args.provider, "model": args.model, "prompt_version": need_analysis.VERSION})
        elif args.command == "review":
            result = needs.overview(db, pilot_only=True)
            work.mkdir(exist_ok=True)
            (work / "review.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        else:
            result = need_analysis.plan(db)
            batches = result.pop("batches")
            if args.command == "export":
                folder = work / uuid.uuid4().hex[:12]
                folder.mkdir(parents=True)
                (folder / "prompt.txt").write_text(with_language(need_analysis.PROMPT, '"rationale"'), encoding="utf-8")
                result["files"] = []
                for index, batch in enumerate(batches, 1):
                    path = folder / f"batch-{index:02d}.json"
                    path.write_text(json.dumps(batch, ensure_ascii=False, indent=2), encoding="utf-8")
                    result["files"].append(str(path))
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
