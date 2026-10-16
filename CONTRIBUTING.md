# Contributing

StackSignal is a local, single-user technology radar. The backend is Python/FastAPI
with SQLite and Alembic; the frontend uses native JavaScript modules with no build step.
The interface is currently in Russian.

## Development

Start with `./run.sh` or `.\run.ps1`, then open http://localhost:8000.
Python 3.11 or newer is required. Keep credentials in your local `.env` and runtime
data in `data/`; neither belongs in a commit, issue, screenshot or test fixture.

Install the test dependencies into the environment created by the launcher:

```sh
python -m pip install -r backend/requirements-dev.txt -c backend/constraints.txt
python -m pip install -r backend/requirements-e2e.txt -c backend/constraints.txt
python -m playwright install chromium
cd backend
python -m pytest -q
python -m pytest e2e -q
```

Activate `.venv` first, or replace `python` with `.venv/bin/python` on Unix or
`.venv\Scripts\python.exe` on Windows (adjust the path after changing directory).
Backend tests block outside network access. Browser tests start their own server
and scratch database; they do not need an AI gateway or live account credentials.

## Pull requests

- Describe the problem, resulting behavior and checks you ran.
- Add regression coverage for changes to persistence, ranking, imports or security.
- Use fictional profiles and deterministic fake HTTP responses in tests.
- Preserve user votes, decisions, edits and analysis provenance when changing migrations.
- Keep AI-backed judgments distinguishable from heuristic matching.
- Update the relevant documentation when changing setup or configuration.

Before submitting, run `python scripts/public_release.py --check`. The scanner is
a useful baseline; review the diff yourself for personal information and secrets.
Dependency upgrades should update both requirements and `backend/constraints.txt`.

Contributions are made under the project's [MIT license](LICENSE).
