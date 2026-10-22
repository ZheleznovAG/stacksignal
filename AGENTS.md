# Working on StackSignal

Read [docs/STATUS.md](docs/STATUS.md) first for the checkpoint and next task, then
[docs/MISSION.md](docs/MISSION.md) and [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
[docs/AUDIT.md](docs/AUDIT.md) records verified findings and limits. If present,
`AGENTS.local.md` contains private installation notes; it is intentionally ignored by Git.

## Commands

Run from the repository root with Python 3.11+ (`python3` where that is its executable name):

```text
python stacksignal.py setup --dev
python stacksignal.py run
python stacksignal.py doctor
python stacksignal.py test
python stacksignal.py setup --browser
python stacksignal.py test --browser
python scripts/public_release.py --check
python scripts/check_docs.py
```

Use `python stacksignal.py script NAME ...` for maintenance and agent workflows;
see [docs/AGENT-WORKFLOWS.md](docs/AGENT-WORKFLOWS.md). `exec -- ...` forwards arguments
to the environment's Python. No shell activation, PowerShell, Bash, Node or frontend build is required.

## Invariants

- FastAPI and the native JavaScript dashboard run as one local process on loopback. There is no authentication; multi-worker or public hosting needs a separate design.
- Project descriptions, current stack and needs drive matching. Votes are optional calibration.
- Separate heuristic matches, AI judgments and measured experiment outcomes. Do not report generated text as verified integration or a successful test.
- Preserve user decisions, votes, manual requirements, evidence and history. Re-analysis owns derived data only. Changed requirements make assessments/results stale.
- Change SQLAlchemy models together with an Alembic revision; migration tests check schema drift. Startup runs migrations automatically.
- Keep network/model calls outside database write transactions. Collection commits stage checkpoints; file-based agents validate fingerprints before applying results.
- Tests use scratch databases, fictional profiles, disabled AI/scheduler/embeddings and blocked external network. Do not use a real installation as a test fixture or trigger collection, AI or Telegram as an incidental check.
- Personal state belongs in the configured private directory or ignored `data/`. Never publish `.env`, databases, account exports, agent inputs/results, private notes or real screenshots.
- Use the Python entry point for new operational commands; isolate any unavoidable OS integration at the deployment boundary.
- Preserve pre-existing working-tree changes. Check `git status` and `git log` to determine what is actually committed/published.

Public source ZIPs intentionally contain no `.git`; inspect that snapshot and report publication
state as unknown rather than assuming it matches a remote branch.

Update `docs/STATUS.md` when the checkpoint or next task changes. Keep durable architecture
in `docs/ARCHITECTURE.md`, user instructions in `docs/usage.md`, and machine details in private notes.
