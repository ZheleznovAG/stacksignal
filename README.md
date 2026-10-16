# StackSignal

**A local technology radar for your projects.** StackSignal collects open-source tools,
models and papers, matches them to your project needs, and keeps the reasoning and your
decisions together. The dashboard runs on your computer and stores its data in SQLite.

![Project workspace with fictional demo data](docs/screenshots/projects-demo.png)

## What it does

- Collects from GitHub, Hugging Face, arXiv, npm and PyPI; tracks momentum and maturity.
- Imports project profiles and dependency declarations from README files and manifests.
  Scan your `Projects` folder to review and add several projects at once.
- Matches candidates to project goals, existing capabilities, gaps and needs.
- Preserves per-project votes, decisions, notes and experiment evidence across re-analysis.
- Shows a radar, relevant items, capability map, briefings and search/chat with sources.
- Supports optional AI enrichment through a separately configured gateway or local agent.
  Cards distinguish heuristic matches, full AI analyses and project-specific AI relevance.
- Offers scheduled collection, dependency/release monitoring and an optional Telegram digest.

**Status:** under active development. The interface is currently in Russian. This is a
single-user desktop service; it has no authentication and should remain on loopback.
Collection and keyword relevance work without an AI gateway or paid AI account.

## Quick start

Requires **Python 3.11+**. There is no Node.js or frontend build step.

Windows PowerShell, from the extracted or cloned project folder:

```powershell
.\run.ps1
```

macOS / Linux / Git Bash:

```sh
bash run.sh
```

The launcher creates `.venv`, installs dependencies constrained by
`backend/constraints.txt`, copies `.env.example` to `.env` if needed, and starts
**http://localhost:8000**. The first installation requires internet access.

1. Open **Проекты → Сканировать папку** (Projects → Scan folder), review the proposals,
   and add your projects. You can also create a profile manually. New installations start empty.
2. Add concrete needs and keywords; review the imported stack and dependency declarations.
3. Press **Собрать** (Collect), then open **Сегодня** (Today) or a project's **Радар**.
4. Vote on candidates and record decisions. Your feedback survives later analysis.

Collection time depends on query budgets, rate limits and AI configuration. While the
server is running, daily collection defaults to 08:00 local time. Set
`SCHEDULER_ENABLED=0` in `.env` for manual collection only.

## Optional services

Configuration is documented in [.env.example](.env.example) and the
[user guide](docs/usage.md#configuration).

| Setting | Purpose |
| --- | --- |
| `GITHUB_TOKEN` | Optional public API access with a higher request budget |
| `GITHUB_LOGIN` | Optional discovery from your public stars and follows |
| `AI_ENABLED=1`, `AI_BASE_URL`, `AI_API_KEY` | Enable a separately operated AI gateway |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Enable delivery of the configured digest |
| `EMBEDDINGS_ENABLED=0` | Disable local semantic retrieval and its first-use model download |

The gateway is not bundled. It must implement the HTTP contract described in the
[gateway guide](docs/usage.md#the-ai-gateway); an arbitrary model API URL is insufficient.
AI analysis is disabled by default. Local agents can process exported batches through
`backend/scripts/agent_analysis.py` and `backend/scripts/agent_relevance.py`; see the
[development guide](docs/usage.md#development).

## Data and privacy

Runtime state is stored under `data/`, including the database, project paths, agent
prompts/results and backups. The entire directory and `.env` are ignored by Git.
Project profiles and collected content are shared with the configured gateway or agent
when those analyses run. Collectors contact their respective external APIs.

Create a consistent backup while the app is running:

```powershell
.\.venv\Scripts\python.exe backend\scripts\backup.py
```

On Unix, use `.venv/bin/python`. See [Security](SECURITY.md) for the local deployment
model and reporting vulnerabilities. Screenshots in this repository use fictional profiles
and synthetic metrics; they contain no personal installation data.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for setup and tests. The backend uses FastAPI,
SQLAlchemy, SQLite and Alembic; the frontend uses native JavaScript modules served by
the same process. Tests use isolated databases and fake external services.

```sh
# In an activated .venv, from the repository root:
python -m pip install -r backend/requirements-dev.txt -c backend/constraints.txt
python -m pip install -r backend/requirements-e2e.txt -c backend/constraints.txt
python -m playwright install chromium
cd backend
python -m pytest -q
python -m pytest e2e -q
```

Check source before sharing it, then create a snapshot without runtime data or Git history:

```sh
python scripts/public_release.py --check
python scripts/public_release.py --output dist/stacksignal-public.zip
```

Run these commands from the repository root. The [publication guide](docs/public-release.md)
explains how to create a public repository from a personal checkout without exposing its
old history. Automated credential checks are best-effort and still require human review.

## License

[MIT](LICENSE). Third-party packages, downloaded models and collected content retain
their respective licenses and terms; the project license does not relicense them.
