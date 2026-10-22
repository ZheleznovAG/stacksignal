# StackSignal

[![Tests](https://github.com/ZheleznovAG/stacksignal/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/ZheleznovAG/stacksignal/actions/workflows/tests.yml)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

**Find useful technology for your projects, test it, and keep the evidence behind your decisions.**

StackSignal is a local technology radar for open-source tools, models and papers.
It matches discoveries to your project's needs and existing stack, prepares a small
experiment brief, and stores the outcome alongside your decision.

**Project needs → candidates → experiment → evidence → decision**

[Русская версия](README.ru.md) · [Documentation](docs/README.md) · [Project state](docs/STATUS.md) · [Contributing](CONTRIBUTING.md)

![Project workspace with fictional demo data](docs/screenshots/projects-demo.png)

## Quick start

Requires **Python 3.11+** with `venv` and `pip`. Clone the repository or download its source:

```text
git clone https://github.com/ZheleznovAG/stacksignal.git
cd stacksignal
python stacksignal.py run
```

Open **http://localhost:8000**. Use `python3` where that is your Python command.
The launcher creates `.venv`, installs constrained dependencies and creates `.env`
from the example if absent. The first installation needs internet access; subsequent
starts reuse the environment when dependency files have not changed.

The same Python entry point supports Windows, Linux and macOS. No shell activation
or frontend build is needed. See [operations](docs/OPERATIONS.md) for setup and troubleshooting.

## From a project to a decision

A new installation starts empty. The interface is currently in Russian.

1. **Add your projects.** Open **Проекты → Сканировать папку**, review the suggested
   profiles and import the projects you want to track. You can also create a profile manually.
2. **Describe the need.** Record the current stack, constraints and a concrete acceptance
   criterion, such as extracting text from a fixed set of sample documents offline.
3. **Find candidates.** Press **Собрать** and review discoveries in **Сегодня** or the
   project workspace. Heuristic matches and AI judgments have separate provenance.
4. **Test a candidate.** Under **Потребности**, prepare a check and copy or download
   its Markdown brief for Claude or another agent in the target project.
5. **Keep the result.** Record the outcome and evidence. Closing a need and adopting
   a tool are explicit decisions; changed requirements make earlier results stale.

Collection and keyword matching work without AI. AI is disabled by default and uses
an independent gateway or [file-based agent workflow](docs/AGENT-WORKFLOWS.md).
The gateway is not bundled; a model API needs an adapter to its contract.

## What works today

| Area | Available behavior |
| --- | --- |
| Discovery | GitHub, Hugging Face, arXiv, npm and PyPI; project queries, release and dependency monitoring |
| Project context | Reviewed folder scans, stack and dependency imports, technical models, needs and prerequisite graphs |
| Matching | Heuristics, AI analysis and project-specific relevance with visible provenance |
| Search | Questions over collected knowledge, keyword/FTS search and optional local semantic search |
| Decisions | Project-specific decisions, notes, history and reasons to revisit a choice |
| Experiments | Frozen briefs, recorded outcomes and evidence, explicit confirmation and stale-result handling |
| Routine use | Daily collection while the service is running and an optional Telegram digest |

This is an early-stage **single-user local application**: Python/FastAPI, SQLite/Alembic
and native JavaScript modules. Run one process on loopback; there is no authentication.
Candidate selection for needs is currently lexical; semantic retrieval is available in
chat. Agents execute outside StackSignal, with briefs and results exchanged manually.
The [current state](docs/STATUS.md) distinguishes implemented behavior from the next tasks.

## Your data and configuration

Settings are documented in [.env.example](.env.example). Databases, credentials, project
paths, agent batches and backups are private runtime data. `STACKSIGNAL_PRIVATE_DIR`
can keep the private `.env` and `data/` outside the checkout. Moving existing state
requires a separate migration while the service is stopped.

```text
python stacksignal.py doctor
python stacksignal.py backup
```

Backup snapshots the configured SQLite database; preserve other private files separately.
Collectors contact their source APIs, and analysis shares selected project/content data
with the configured gateway or agent. Optional local semantic search downloads its model
on first use; `EMBEDDINGS_ENABLED=0` disables it. Read [operations](docs/OPERATIONS.md)
and [security](SECURITY.md). Repository screenshots and fixtures use fictional data.

## Documentation and development

The [documentation index](docs/README.md) directs users, contributors and agents to the
right guide. Detailed guides and project handoff documents are currently in Russian.

| Start with | Purpose |
| --- | --- |
| [Mission](docs/MISSION.md) | The problem, intended users and measure of a useful outcome |
| [Current state](docs/STATUS.md) | Implemented features, limits and the next product task |
| [Architecture](docs/ARCHITECTURE.md) | Components, data ownership and transaction boundaries |
| [Agent instructions](AGENTS.md) | Joining the project and preserving its invariants |
| [Audit](docs/AUDIT.md) | Verified findings, validation and publication checks |

```text
python stacksignal.py setup --dev
python stacksignal.py test
python scripts/public_release.py --check
python scripts/check_docs.py
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for browser tests, migrations and secret checks,
and the [publication guide](docs/public-release.md) for source snapshots and Git history.

## License

[MIT](LICENSE). Third-party packages, models and collected content retain their own licenses and terms.
