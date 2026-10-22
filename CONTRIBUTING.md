# Contributing

Read [AGENTS.md](AGENTS.md) and [docs/STATUS.md](docs/STATUS.md) for the current checkpoint.
Use the [documentation index](docs/README.md) to find user and operational guides.
StackSignal is a local, single-user application: Python/FastAPI, SQLite/Alembic and native
JavaScript modules with no frontend build. The UI is currently in Russian.

## Development

Python 3.11+ is required. From the repository root:

```text
python stacksignal.py setup --dev
python stacksignal.py run --reload
```

The launcher selects the environment interpreter; shell activation is unnecessary.
Use `python3` where that is the installed Python command. Existing `.env` files are preserved.

## Validation

```text
python stacksignal.py test
python stacksignal.py setup --browser
python stacksignal.py test --browser
python stacksignal.py exec -- -m pip check
python scripts/public_release.py --check
python scripts/check_docs.py
```

Backend tests block external network, disable AI/model download and use scratch databases.
Browser tests run Chromium against a separate server/database. They need no live credentials.
On Linux, Chromium may need OS libraries: `python stacksignal.py exec -- -m playwright install-deps chromium`.
CI provisions those libraries before browser tests.

Run selected backend tests with, for example:

```text
python stacksignal.py test -- tests/test_needs.py -k stale
python stacksignal.py test --browser -- -k experiment
```

Additional Python tooling uses `python stacksignal.py exec -- ...`; maintenance commands
use `python stacksignal.py script NAME ...`. These preserve argument boundaries and use the
project environment from any supported OS.

## Data and migrations

- Keep credentials, personal profiles, local paths and actual agent artifacts outside source/tests.
- Use fictional fixtures and explicit fake transports. Never evaluate a test against a personal database.
- Preserve user-owned votes, decisions, manual needs, evidence and history during re-analysis.
- Keep heuristic matches, AI judgments and experiment measurements distinguishable.
- Add an Alembic revision when models change. Migration tests compare the schema with the models.
- For autogeneration, point `ALEMBIC_DATABASE_URL` in a temporary process environment at an initialized
  scratch SQLite database, then use `python stacksignal.py exec -- -m alembic -c backend/alembic.ini revision --autogenerate -m "description"`.
  Review the generated migration and remove the override before running the application.
- Keep remote calls outside write transactions; respect collection checkpoints and AI budgets.

## Documentation and changes

Record the resulting behavior and appropriate validation. Update `docs/STATUS.md` when the
checkpoint or next task changes; keep architecture in `docs/ARCHITECTURE.md`, operations in
`docs/OPERATIONS.md`, and user workflows in `docs/usage.md`. Private installation details belong
in ignored `AGENTS.local.md`. Add regression coverage for persistence, migration, ranking,
import and security changes. Dependency changes update requirements and `backend/constraints.txt`.

Keep the English and Russian README overviews aligned, and link new guides from
`docs/README.md`. Keep dated audit results distinct from current feature and publication state.

Public source checks are best-effort; review the diff for personal data before publication.
With Gitleaks installed, also check the staged changes and all reachable Git history:

```text
gitleaks git --staged --redact
gitleaks git --log-opts="--all" --redact
```

The checked-in configuration extends the default rules. Its only exception matches the
exact public tokenizer checksum in `backend/app/embeddings.py`; other keys and files
remain subject to the default checks. Never scan private runtime data into a public report.

Contributions are made under the project's [MIT license](LICENSE).
