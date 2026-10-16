# StackSignal user guide

**A personal technology radar.** It watches GitHub, Hugging Face, arXiv and the npm and PyPI registries and tells you which new things matter
for *your* projects — and why. Not "what is popular", but **"what changed that I could actually use"**.

- **Reads the news for you.** Every day it collects fast-growing repositories and models and scores how quickly
  attention is growing (*momentum*) and how safe they are to depend on (*maturity*).
- **Knows your projects.** Each item is matched against your project profiles, with a reason
  ("OCR directly supports importing photographed text into the memorization workflow").
- **Is honest about what it read.** An AI reads the most promising items; the rest are ranked by fast
  keyword matching. Every card says which.
- **Learns your taste.** 👍/👎 per project and notes. Your votes outrank the AI and survive re-analysis.
- **Remembers.** A decision board with history, a searchable capability map, AI briefings, and a chat that
  answers from everything collected — in Russian or English, with sources.
- **Comes to you.** A short **Telegram digest** after the daily run: the few new items that really matter, never
  repeated. You don't have to remember to open a dashboard.

> Local, single-user tool: the dashboard runs on `http://localhost:8000`, data lives in one SQLite file.

---

## Contents

[Quick start](#quick-start) · [Your daily routine](#your-daily-routine-10-minutes) · [The screens](#the-screens) ·
[Teaching it your taste](#teaching-it-your-taste) · [Telegram digest](#telegram-digest) · [How the AI part works](#how-the-ai-part-works) ·
[Configuration](#configuration) · [The AI gateway](#the-ai-gateway) · [Words you'll meet](#words-youll-meet) ·
[Data and privacy](#data-and-privacy) · [FAQ](#faq--troubleshooting) · [Development](#development)

---

## Quick start

Requires Python 3.11+ (tested on 3.14) on Windows, macOS or Linux. There is no Node or build step.

```powershell
.\run.ps1          # or ./run.sh from Git Bash
```

On the first run the script creates `.venv`, installs the dependencies at the exact versions in
`backend/constraints.txt` and copies `.env.example` to `.env`; later runs just start the server (dependencies
are reinstalled only when the requirements change, or with `.\run.ps1 -Update` / `./run.sh --update`). Open **http://localhost:8000**, then:

1. Click **Собрать** (top right). A run takes about 10–15 minutes with the AI, a few without it.
2. Open **Сегодня** (Today): what is new and relevant to your projects, and what waits for a decision.
3. **Настройки → Справка** in the app explains every screen and word.

The interface is in Russian; this README names each control in Russian with a translation.

**Works without any AI setup.** Without the AI gateway everything runs in *keyword mode* (the pill in the
top bar says *AI выкл*): collection, scoring, radar and feedback all work; only the summaries, reasons,
capabilities, opportunities and the chat need the AI. See [The AI gateway](#the-ai-gateway).

**Your projects.** New installations start with no project profiles. Open the
**Проекты** section and add your own or scan your Projects folder: relevance is only as good as the keywords you put there
(`local inference, ocr, speech to text` — not just `android`). Your edits and deletions persist.

---

## Your daily routine (10 minutes)

1. **Собрать** — or do nothing: while the server is running it does this by itself every day at
   08:00 local time (`DAILY_RUN_TIME`) and sends the [Telegram digest](#telegram-digest).
2. **Сегодня** → skim the new cards. **👍** what is useful, **👎** what is noise; click a card to read it.
3. On the card, record a **decision per project** (*Assess* / *Trial* / *Rejected*, with a goal and a verdict),
   or work through a project's inbox on **Проекты → project → Решения**.
4. Read the **briefings** at the bottom of Сегодня.
5. Before a technical decision → **Chat** ("what should I use for local speech recognition?") or
   **Обзор → Способности** (Capabilities).

**Even easier:** set up the [Telegram digest](#telegram-digest) once, and on most days steps 2–4 collapse into
reading one message on your phone; open the dashboard only when something in it is worth acting on.

---

## The screens

The interface is in Russian and has five sections (on a phone they move to a bottom bar). The **AI** pill in the
header shows whether the AI gateway is ready right now; **Собрать** starts a collection.

### Сегодня (Today) — start here
One screen answers "what is new for me since last time":

- **AI coverage** — how many current, non-archived items the AI has read, including analyses made
  outside collection runs by a local agent. The same total appears in **Настройки → Сбор данных**;
  **AI за этот сбор** in the run history counts only analyses performed during that particular run.
- **the last run** — when, how much was found, how long it took (click it for the stages);
- **new for your projects** — items the AI read this week that are strongly relevant (60%+) to a project you have
  not decided on yet, filterable by project, with 👍/👎 right on the card;
- **inbox** — per project, how many relevant items still wait for a decision;
- **briefings** and **changes**: updates to your stack, new releases of what you track, and decisions worth another look.

It warns you when something is off: the AI gateway is not ready, or the last run finished but the AI read nothing
(everything silently fell back to keywords).

Collection saves its run record immediately and checkpoints discovery, scoring, entity linking, enrichment,
opportunities, capabilities and embeddings separately. A later failure rolls back only the unfinished stage:
`partial` means earlier checkpoints remain available; `failed` means a fatal error before the first checkpoint.
The stage list records where the error occurred. Every accepted item gets heuristic scoring before AI candidates
are prioritized by relevance and momentum; only the AI calls are budgeted, with no 200-item cutoff.

### Item card
Click any item anywhere and its card opens on the right: status, summary, numbers (stars, downloads, momentum,
maturity), releases and capabilities, linked items (the same technology on another registry), **who it is useful
for** with the reason and 👍/👎 per project, **decisions per project**, notes and the full history. The **AI** badge
means the AI read and scored the item; without it the score is a crude keyword overlap (a common word can score high).

### Проекты (Projects)
Projects grouped by attention (tier). Open one to get its workspace with four tabs:

- **Релевантное** — items ranked by relevance to this project, each with the reason;
- **Решения** — the project's decision board plus its inbox and "worth another look";
- **Поиск** — the search queries collection runs for it, and what each has paid off;
- **Профиль** — description, keywords, stack and needs; edit or import them from the project's folder.

The profiles items are matched against: name, description, keywords. This is the one place where *you* tell
StackSignal what you care about.

**Experiments and revisiting.** A decision can also hold a **success criterion** ("20 fps on a mid phone") and
a **revisit when** note ("a GPU build appears"). When you decide, StackSignal snapshots the item, so later it can
answer *what changed since I decided*: new stable releases, a real shift in momentum or maturity, and **new
alternatives** — items found after your decision that share a capability with it and that the AI found strongly
relevant to the same project (candidates that might remove the reason you said no). They appear on the project's **Решения** tab and on Сегодня
under "Worth another look" and, for rejected or paused decisions, once in the Telegram digest ("Worth another
look") until the evidence changes. For what you use or are trying, the same evidence is a heads-up. No AI call is
needed for this; it only uses what is already stored.

**Contexts and attention.** Profiles have a type (project, lab, infrastructure, experiment, goal,
personal system or reference project), a lifecycle and an independent search switch. Active contexts get
up to 3 digest items; watch/incubate contexts get at most 1; dormant/archived contexts get none. Turning
search off preserves existing finds, decisions and dependency monitoring. Archived contexts keep their
history and receive no new automatic searches or analysis. The legacy API `tier` is derived from lifecycle.

**Quick decisions.** Today offers “Изучить”, “Пробую”, “Использую”, “Не подходит” and “Позже” next to
the named project. These save a decision for that project; the success message offers undo and a link
to add a reason. Rejection and neutral interactions do not raise an author's priority or promote them
to watched status. Likes and trial/adopt decisions count positively, at most once per repository.

**Import from the project's folder.** In **Изменить** (Edit) enter the folder of the real project and press *Сканировать* (Scan).
StackSignal reads only dependency files (requirements, pyproject, package.json, Gradle, csproj, Unity manifest,
Cargo, go.mod, Dockerfile) and the README, up to two levels down. It never reads source code or `.env`, and
nothing is saved until you review the proposal and press *Применить к проекту* (Apply). You get the project's **stack** (what it
already uses) and, if you tick *also ask the AI*, a description, keywords and **needs** (what it lacks or
plans). The AI is told both, so it prefers items that fill a need or replace part of the stack and stops
suggesting what the project already uses. Applying changes only that project's profile, so only relevance to
it is redone, in short calls.

**Add several local projects.** On **Проекты**, press **Сканировать папку**. The default is the parent
`Projects` folder next to StackSignal; you can enter another folder. Its immediate subfolders are scanned
using the same README/manifest reader, without AI. Existing profiles are matched by folder path, name
or slug and shown as already added. Select the new projects, expand **Проверить профиль** to edit their
names, descriptions, keywords, needs or stack, and press **Добавить выбранные**. Nothing is saved during
scanning, and existing profiles are preserved. Each import saves the reviewed profile, source folder and
structured dependencies together. Repeating the import skips existing projects. At most 100 subfolders
are checked per scan; hidden/generated folders and directory links are skipped.

Dependency declarations are stored separately from the summary stack: ecosystem, full package key (including
Maven group or Go module path), declared constraint, manifest and scope. Different manifests and runtime/dev
declarations stay separate. Scans do not read lockfiles yet, so `resolved_version` stays empty instead of treating
a constraint as an installed version. Existing `stack_packages` are backfilled by an Alembic migration and kept
as a compatibility field; a rescan supplies declarations that older scans discarded.

**Releases of what you track.** For the GitHub repositories *you* did something about — a status you set, a
decision for a project, a vote, a note, or Add URL — StackSignal looks up the latest releases every run
(`REFRESH_RELEASES_PER_RUN` per run, the ones checked longest ago first). A status the system suggested on its own
does not count: with dozens of those the digest would be a list of nightly builds. A new release shows up on the
Сегодня ("New releases of what you track"), on the item's card (🏷 v1.2.0 · 3 days ago) and once in the Telegram
digest (stable releases from the last week, with the first lines of the notes). Nightly builds and alpha/beta/rc
tags are not counted as news.

**Stack updates.** The dependencies a scan recognised (PyPI and npm) are looked up every run; a new
latest version shows up as *fastapi 0.140.0 → 0.141.1 · used by StackSignal* on Сегодня under *changes* (major
versions are marked) and once in the Telegram digest under "Updates to your stack". Dormant projects are not
watched; other ecosystems (Maven, NuGet, Cargo…) are stored but not looked up.

The project's **Профиль** tab and `GET /api/stack-watch?project_id=…` show declared, resolved (when known), and
latest versions separately. Update kinds (`major`, `minor`, `patch`) compare a known resolved version with latest;
unknown versions and non-numeric/pre-release versions are not guessed. Registry change notifications still show
the previous registry version, explicitly labelled as such. `GET /api/stack-updates` also includes the declarations
of the projects using that package.

**Search queries.** Each query shows what it has paid off (`3 new in 5 runs`). One that finds nothing new
for four runs in a row rests 2, 4, 8, then 14 days, so the budget goes to queries that produce.
Explicit needs receive extra weight in heuristic discovery. AI query generation receives needs and the existing
stack and asks for English technical search terms, even for Russian profiles. Generated queries carry a profile
fingerprint and their basis; editing the description, keywords, needs or stack makes old queries visibly stale.
Collection refreshes the heuristics and replaces AI searches only after a successful, complete response. Gateway
failure keeps the old AI searches usable. Manual queries are preserved, as are toggles and history for searches
that survive regeneration.

### Обзор (Explore)
Everything found, not only for your projects, in four tabs.

**Находки (Finds)** — by growth: **Fast growing**, **New** (first seen in the last two weeks), **Popular**,
**Experimental** (young, low maturity). Filter by source, or to AI-analysed items only.

**Add a link.** Found something yourself — in an article, a chat, a colleague's message? Paste a GitHub
repository, Hugging Face model, arXiv paper, npm or PyPI package link at the top of **Находки**. It is fetched, scored and read by the AI
like any collected item, but it skips the quality filter (you chose it) and goes on the radar as *Watch*, so
it is never archived. Adding a known item again only refreshes it and never demotes your status.
A GitHub profile link (`github.com/<login>`) adds that author to the watched authors instead.

**Your GitHub stars.** With `GITHUB_LOGIN` set, every collection run reads the repositories you starred and
the accounts you follow (public endpoints, two requests for a typical account). A starred repository is
kept regardless of the quality gate and goes on the radar as *Watch* the first time it is seen starred —
a status you change later is never overridden, and its card says "Звезда на GitHub". The owners of
starred repositories and the people you follow become watched authors; unfollows and unstars are mirrored.

**Авторы (watched authors).** Search finds what matches a query; authors find what a person who already
made something useful for you made next. About once a week each watched author's own repositories are
read (metadata only, one request per author, `AUTHOR_CHECKS_PER_RUN` authors per run) and remembered,
and a few enter the radar as ordinary *New* items for the usual scoring and AI read — at most
`AUTHOR_PROMOTE_PER_AUTHOR` per author and `AUTHOR_PROMOTE_PER_RUN` per run. New repositories go first (a
bare one needs a description or a few stars, an organization's needs `AUTHOR_NEW_ORG_MIN_STARS`), then
older ones with `AUTHOR_MIN_STARS` stars pushed within two years; forks, archived repositories, dotfiles,
taps and examples never do. The card of such an item names the author. An author's yield — how many of
their repositories the AI found relevant to a project, how many you acted on — decides who is checked
first; an owner with two or more relevant finds is suggested as a candidate (checked monthly) and becomes
watched once you act on one of their repositories. **Обзор → Авторы** lists them with that yield; "Не
интересен" stops the checks.

**Sources.** - **GitHub repositories** and **Hugging Face models**: the worldwide "hot" lists plus searches written for each of your projects.
- **arXiv papers**: only through project searches (arXiv has no popularity signal, and a global feed of new papers is noise). A paper is stored because of the project it might matter to, and the AI reads its abstract. Searches look at the last 120 days; one or two words are searched as a phrase, longer queries as all their words together.
- **npm packages**: through project searches (created automatically for a project whose imported stack has npm packages) with monthly downloads as the traction signal, and by refreshing packages you track.
- **PyPI packages**: PyPI has no search, so they come in with **Add URL** and are refreshed like the rest.
Every paper or package is kept only if it passes a quality gate (an abstract worth reading; enough downloads), and is archived, never deleted, when nobody has touched it for a month. The source filter on **Находки** narrows by source, and a project's **Поиск** tab shows the queries of every source.

**Способности (Capabilities)** — "what can these tools actually do?"

Items are tagged with reusable abilities (*local inference*, *browser automation*, *speaker diarization*…).
Each card shows how many items offer it, which of your projects it matters to (from the relevance of those
items) and the strongest items. **Спросить чат** (Ask chat) jumps to the chat with a ready question. Because the AI names
abilities freely, near-duplicates are merged after each run (or press **Объединить синонимы**, Merge synonyms).

**Брифинги (Briefings)** — short AI notes.

After each run the AI looks across items and writes a few notes per project: where several new things
together suggest revisiting a decision. This is the "what changed and what does it mean for me" screen.

Opportunities **persist**. A note that rests on mostly the same items as an earlier one is the *same*
opportunity seen again: it keeps its *first seen* date, counts the runs that confirmed it and shows how much
evidence has piled up (**Evidence** lists the exact items). One that stops coming back **fades** after two
weeks. Mark one **Done** when you acted on it or **Dismiss** it; a dismissed opportunity stays dismissed even
when the signal returns, and the Telegram digest only carries active ones it has not sent before. Without the
AI you get short keyword-based placeholders, which give way as soon as real notes are written.

**Общий радар (Overall radar)** — each item's general status.

Every item has a status and every change is remembered with a date. The system may suggest a status on its
own only up to **Assess** (marked *auto*, and only from an AI analysis). **Trial** and **Adopt** mean what
*you* do, so only you set them (*set by you*). Keyword matches never move the radar.

**Decisions per project.** The overall radar shows an item's general status, but what matters is *for which
project*: a new local model can be on trial for one project and rejected for another. In any item card press
**＋ Решение для проекта** (Decision for a project), pick the project and a status (Watch, Assess, Trial, Adopt, Paused, Rejected) and
write a **goal** (what you want to try) and later a **verdict** (what came out of it, or why not). Every change
is kept in the history with its reason, so months later you can answer "why did we drop it?". A project's **Решения**
tab shows its board: your decisions plus an **Inbox** of strongly relevant items you
have not decided on yet (only AI-read ones, once the AI has read anything). Items you already decided on for a
project are not repeated in its Telegram digest.

### Чат (Chat)
Ask in Russian or English. Retrieval understands Cyrillic and word endings ("для Строки"), notices which
project you mean, searches capabilities, your notes and your votes ("why did I skip X?"), and the answer
cites the items it used. It says so when the radar has nothing relevant.

In a big catalogue (300+ items) the chat first narrows the candidates with a full-text index (SQLite FTS5, BM25) plus semantic embeddings, project relevance, freshness and momentum, then combines those independent rankings with RRF. When a project has enough of your own judgments, a deliberately weak local preference signal also participates. It only reorders already-plausible, still-unjudged candidates; it cannot pull unrelated technologies into the result.
The FTS index is a disposable in-memory SQLite cache with its own transaction, rebuilt after restart; refreshing it
never commits the application's data. Embedding helpers also leave transaction ownership to their caller.

### Настройки (Settings)
- **Сбор данных** — AI gateway status with a *Check* button, and the recent runs with how long each stage took and
  what it did (items read by the AI vs. by keywords).
- **Дайджест** — set up, preview and send the [Telegram digest](#telegram-digest). **Preview** shows exactly what
  would be sent right now, without sending anything.
- **Внешний вид** — light, dark or system theme.
- **Справка** — the same explanations as this README, inside the app, with live numbers.

### On a phone
---

## Teaching it your taste

| You do | What happens |
| --- | --- |
| 👍 on a project line | Relevance is raised to at least 0.9. A `New` item moves to `Assess`. |
| 👎 on a project line | Relevance drops below every threshold: the item leaves *Relevant* and *Opportunities*. |
| Click the same vote again | Cleared: the AI's own score and reason come back. |
| **Note** | Free text, kept forever. The chat can find it later. |
| Change the status dropdown | The card says *set by you*; the system never overwrites it. |

Votes live in their own table and are re-applied on top of **every** new analysis, so the AI can re-score an
item but cannot undo your decision. Your recent votes and notes are also sent to the AI as examples, so later
analyses lean towards your taste.

StackSignal also learns a small **local retrieval preference profile** per project. It activates only after at least
**2 positive and 2 negative judgments with embeddings**. Positive evidence is Useful/Trial/Adopt;
negative evidence is Not relevant/Ignore; Watch/Assess/Hold remain neutral. The profile compares the embedding centroid of things you kept with the
centroid of things you rejected, and gives similar *unseen* candidates a weak extra ranking vote. Judged items are
never boosted by their own training label, and the profile only reranks candidates the normal retrieval/relevance
already admitted. This keeps sparse early feedback from taking over the radar.

---

## Telegram digest

Every day after the automatic run, StackSignal can send you **one short Telegram message**: the best new items
per project, each with the AI's reason, plus fresh opportunity briefings and a link to the dashboard.

**Set it up once (about 3 minutes):**

1. In Telegram open **@BotFather**, send `/newbot`, follow the prompts, copy the **token**.
2. Open your new bot and press **Start** (send it any word) — a bot cannot write to you first.
3. Put the token in `.env` as `TELEGRAM_BOT_TOKEN=...` and restart StackSignal.
4. Open **Настройки → Дайджест**, press **Узнать мой chat id** (Find my chat id), put the number in `.env` as `TELEGRAM_CHAT_ID=...`,
   restart again. Press **Отправить сейчас** (Send now) to check it.

**What goes into a digest — deliberately strict, so it never becomes noise:**

- only items an **AI analysis** scored at least `DIGEST_MIN_RELEVANCE` (default 60%) — keyword-only matches are
  used only while the database has no AI analysis at all;
- never something you marked *Ignore* or voted 👎, and **never the same item twice**;
- at most 3 items per project and `DIGEST_MAX_ITEMS` (8) in total, best first, plus up to 3 opportunity
  briefings you have not seen in a digest before;
- **nothing new and relevant = no message.** (*Send now* still sends a "quiet day" note, so you can test.)

The daily run happens at `DAILY_RUN_TIME` (default `08:00`, your computer's local time) and only if the computer
and StackSignal are running then; a run delayed by a sleeping computer still happens within 6 hours. Otherwise
press **Собрать** — the digest is sent after scheduled runs and by **Send now**.

**Start automatically (Windows).** `scripts\autostart.ps1 -Install` registers a per-user task
that starts StackSignal at logon without a terminal window and checks it every five minutes.
It does not launch, restart or contact AI Gateway. `-Status` shows the supervisor log;
`-Remove` uninstalls the task. Logs are in `data/logs`.

**Manual agent analysis.** Gateway calls are disabled by default (`AI_ENABLED=0`), including
health checks, collection analysis and chat generation. Collection still saves items for analysis.
Run an agent manually when you want to process the backlog:

```powershell
.venv\Scripts\python.exe backend\scripts\agent_analysis.py status
.venv\Scripts\python.exe backend\scripts\agent_analysis.py next --items 10 --batches 1
```

The agent reads `data/agent-analysis/context.json` and the exported batch, writes
`batch-<n>.result.json`, then applies its result:

```powershell
.venv\Scripts\python.exe backend\scripts\agent_analysis.py apply data\agent-analysis\batch-0001.json
```

**Behind a block?** If Telegram is unreachable from your network, set `TELEGRAM_PROXY` (for example
`socks5://127.0.0.1:10808`, the port of a local v2rayN/Clash). The bot token is never written to logs or shown
in the dashboard.

---

## How the AI part works

```
GitHub / Hugging Face --> collect --> score (momentum, maturity) --> keyword relevance for EVERY item
                                                                            |
        ranked by relevance x momentum                                      v
                  |                                       AI reads the best ~20, in batches of 5
                  +-------------------------------------> (summary, reason, capabilities,
                                                            relevance per project)
                                                                            |
        your votes and notes are applied on top  <--------------------------+
                                                                            v
                                   opportunities briefing - capability synonym merge - radar suggestions
```

- **Every item** gets a fast keyword pass. **Each run the AI additionally reads ~20 items** — the ones most
  worth it (relevance × momentum, not raw popularity) — in batches of 5, and skips items it has already read
  (unless their description or your project profiles changed). Coverage grows run after run; the counter in
  the top bar shows it.
- Per run the AI budget is `AI_MAX_CALLS_PER_RUN` = 6 gateway calls: 4 for item batches, one for the
  opportunities briefing, one for merging capability synonyms.
- StackSignal never talks to a model provider directly. It speaks to an **AI gateway** over HTTP with
  idempotency keys, a content-addressed cache, a per-run budget and a circuit breaker. If the gateway is down
  the run finishes in keyword mode and nothing is lost.

---

## Configuration

Copy `.env.example` to `.env` (the run script does it for you). All keys are optional.

| Variable | Default | Meaning |
| --- | --- | --- |
| `AI_BASE_URL` | `http://127.0.0.1:8787` | Where the AI gateway listens. |
| `AI_API_KEY` | *(empty)* | The gateway's key. Empty = keyword mode. On Windows, if empty and the URL is the default, the `GATEWAY_API_KEY` user environment variable is used. |
| `AI_ENABLED` | `0` | `1` enables gateway-backed AI when a key is configured; `0` keeps the local/heuristic mode. |
| `EMBEDDINGS_ENABLED` | `1` | `0` turns off local semantic search for the chat (see below); keyword/FTS search still works. Needs no key: the model is a one-time ~23 MB download into `data/models/` on first use. |
| `AI_MAX_CALLS_PER_RUN` | `6` | Hard cap on gateway calls per run (see above). |
| `AI_RELEVANCE_BATCH_SIZE` | `10` | Items per *relevance-only* AI call, used when you edit a project profile: only that project's relevance is redone, in short calls, and summaries and the other projects' scores stay. |
| `AI_BATCH_SIZE` | `5` | Items per AI call (1–20). One call costs a fixed 20–40 s plus time proportional to its output; much larger batches risk timeouts. |
| `AI_REQUEST_TIMEOUT_MS` | `300000` | Timeout sent to the gateway. **The gateway clamps it to its own `GATEWAY_TIMEOUT_MS`**, so start the gateway with at least this. |
| `AI_TIMEOUT` | `330` | Client-side deadline for one call, in seconds. |
| `GITHUB_TOKEN` | *(empty)* | Optional; raises the GitHub rate limit from 60 to 5000 requests/hour. |
| `GITHUB_LOGIN` | *(empty)* | Optional; your GitHub login. Starred repositories go on the radar as Watch; starred owners and followed accounts become watched authors. |
| `AUTHOR_CHECKS_PER_RUN` | `25` | Watched authors whose repositories are read per run (one request each); 0 disables author checks. |
| `AUTHOR_CHECK_DAYS` / `AUTHOR_CANDIDATE_CHECK_DAYS` | `7` / `30` | How often a watched / suggested author is checked again. |
| `AUTHOR_PROMOTE_PER_RUN` / `AUTHOR_PROMOTE_PER_AUTHOR` | `10` / `3` | How many author repositories may enter the radar per run / per author per check. |
| `AUTHOR_MIN_STARS` / `AUTHOR_NEW_DAYS` / `AUTHOR_NEW_ORG_MIN_STARS` | `25` / `120` / `10` | Stars an older repository needs; age below which a repository is new; stars a new organization repository needs. |
| `DISCOVERY_ENABLED` | `1` | `0` turns off the searches written for your projects (only the worldwide lists are collected). |
| `GITHUB_QUERIES_PER_RUN` | `24` with a token, `8` without | Project searches on GitHub per run; the ones that ran longest ago go first. A query that finds nothing new for 4 runs in a row rests 2, 4, 8, then 14 days. |
| `GITHUB_SEARCH_PAGES` | `3` with a token, `1` without | Result pages (30 repositories each, best by stars first) read per GitHub search. More pages reach deeper into a crowded topic. |
| `GITHUB_SEARCH_PAUSE` | `2.2` / `6.5` | Seconds between two GitHub searches, to stay under its search limit (30/min with a token, 10/min without). |
| `HF_QUERIES_PER_RUN` | `24` | Project searches on Hugging Face per run. |
| `REFRESH_GITHUB_PER_RUN` / `REFRESH_HF_PER_RUN` | `100` (token) or `15` / `60` | How many already-tracked items (the ones you or the AI care about) get fresh numbers per run. |
| `CHAT_FTS_MIN_ITEMS` | `300` | From this many items the chat picks candidates through the full-text index; smaller catalogues score every item. |
| `REFRESH_RELEASES_PER_RUN` | `60` with a token, `10` without | Tracked GitHub repositories checked for new releases per run. |
| `STACK_WATCH_PER_RUN` | `60` | Dependencies of your projects looked up on PyPI/npm per run (the ones checked longest ago first). |
| `ARXIV_QUERIES_PER_RUN` | `12` | arXiv searches per run (spaced 3 s apart, as arXiv asks). `ARXIV_DAYS` (`120`) is how recent a paper must be. |
| `NPM_QUERIES_PER_RUN` | `8` | npm searches per run. `REFRESH_NPM_PER_RUN` / `REFRESH_PYPI_PER_RUN` (`20`) refresh tracked packages. |
| `STALE_DAYS` | `30` | Items nobody touched and no run has seen for this long are archived (hidden, never deleted). |
| `COLLECT_WINDOW_DAYS` | `30` | How far back "new" repositories are looked for. |
| `DAILY_RUN_TIME` | `08:00` | Local time of the automatic daily run (the digest follows it). |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | *(empty)* | Both needed for the [digest](#telegram-digest). |
| `TELEGRAM_PROXY` | *(empty)* | Only if Telegram is blocked: `socks5://…` or `http://…`. |
| `DIGEST_LANG` | `ru` | `ru` or `en` — the digest's own labels. |
| `AI_TEXT_LANG` | `ru` | `ru` or `en` — language of AI-written texts (summaries, reasons, briefings). Product names and technical terms stay in English; capability names, search queries and other identifiers are always English. Changing it re-analyses items in the new language over the next runs. |
| `DIGEST_MIN_RELEVANCE` / `DIGEST_MAX_ITEMS` | `0.6` / `8` | How relevant an item must be, and how many go into one digest. |
| `DIGEST_ENABLED` | `1` | `0` keeps the daily run but stops the digest. |

---

## The AI gateway

AI support is optional and disabled by default. The gateway is a separate service;
no gateway implementation, provider account or model is included in this repository.
StackSignal expects a specific HTTP API, not an arbitrary provider's completion endpoint.

Set `AI_ENABLED=1`, `AI_BASE_URL` and `AI_API_KEY` in `.env`, then restart StackSignal.
The client authenticates with `Authorization: Bearer ...` and uses these endpoints:

| Endpoint | Contract |
| --- | --- |
| `GET /v1/profiles` | HTTP 200 JSON with a string `routing_version` and routing/profile metadata |
| `GET /readyz` | HTTP 200 or 503 JSON with a boolean `ready`, optional `code` and `dispatch` |
| `POST /v1/generate` | JSON generation request with an `Idempotency-Key`; completed output or queued/running response |
| `GET /v1/responses/{id}` | Poll a queued response; IDs start with `resp_` |

Completed generation responses have `status: completed` and an `output` array.
See `backend/app/ai_client.py`, `backend/app/ai_analysis.py` and their fake transports
in `backend/tests/` for the exact request, output and error handling contracts.
Choose a gateway/provider appropriate for your requirements and review its data handling.
Without a gateway, use keyword matching or the local agent export/apply workflows.

---

## Words you'll meet

| Word | Meaning |
| --- | --- |
| **Momentum** | How fast attention is growing *right now* (stars/likes per day, recency) — not total popularity. |
| **Maturity** | How safe it is to depend on: age, license, activity, adoption. |
| **Relevance** | How useful an item is for one project, 0–100%, with a reason. |
| **AI / keywords** | Whether the AI read the item, or it is ranked by keyword overlap only. |
| **Capability** | A reusable ability ("browser automation") that items and projects can be compared on. |
| **Status** | New → Watch → Assess → Trial → Adopt, or Hold / Ignore. *New*: not triaged · *Watch*: keep an eye on it · *Assess*: worth a proper look · *Trial*: trying it in a project · *Adopt*: using it · *Hold*: not now · *Ignore*: not for me. |

---

## Data and privacy

- Everything lives in `data/stacksignal.db` (SQLite, gitignored): items, snapshots, votes, notes, the AI's
  raw responses and its cache. **Back it up** with `.venv\Scripts\python.exe backend\scripts\backup.py`
  (a consistent copy into `data/backups/`, safe while the app runs). Do not just copy the `.db` file while
  StackSignal is running: in WAL mode recent writes sit in `stacksignal.db-wal`, so the copy can be stale or
  torn. To restore, stop the app, put the copy in place of `data/stacksignal.db` and delete the `-wal`/`-shm` files.
- The server binds to `127.0.0.1` only and has **no authentication** — a deliberate single-user tradeoff.
  Do not expose it to a network without adding auth first. There is no CORS policy, on purpose. Because
  other websites open in the same browser can still reach `127.0.0.1`, the app also refuses requests whose
  `Host` is not local (`ALLOWED_HOSTS`, against DNS rebinding) and state-changing requests from another site,
  and it cannot be framed (`app/security.py`).
- `.env` (keys) is gitignored; only `.env.example` is committed.
- The entire `data/` directory is ignored by Git, including agent batches, profiles, logs and backups.
- A new installation starts empty. Add your profiles through **Projects**, manually or by scanning a folder.
  Project descriptions are sent to the configured AI gateway or agent when those analyses run.

---

## FAQ / troubleshooting

**The top bar says "AI выкл" (AI off).** No `AI_API_KEY`, or `AI_ENABLED=0`. The app still works.

**"AI не готов" (AI not ready), or Сегодня warns that the AI read nothing.** The gateway is not reachable or not
ready; **Настройки → Сбор данных** names the reason. Check `http://127.0.0.1:8787/readyz`. If the gateway reports `provider_recovery_required`, restart it (it stops
itself after an ambiguous browser run) and run again.

**A run takes minutes.** Normal: each AI call goes through a real browser (~20–40 s). A run is about
10–15 minutes. A chat message is one call too and waits behind a running collection.

**Why is my favourite item marked "keywords"?** The AI reads ~20 items per run, best first, so it will get
there. 👍 it, or ask the chat about it.

**Why did the radar suddenly get emptier?** Early versions let keyword matching set statuses, which put junk
in *Trial*. Those automatic statuses were reset once (statuses you set were kept), and the system now suggests
at most *Assess*, and only from an AI analysis.

**The digest does not arrive.** Open **Настройки → Дайджест**: it names the problem. *Rejected the token* — check
`TELEGRAM_BOT_TOKEN`. *Bot cannot write to you* / *chat not found* — open the bot in Telegram, press **Start**,
check `TELEGRAM_CHAT_ID`. *Cannot reach Telegram* — set `TELEGRAM_PROXY`. Nothing arrives on a quiet day — that is
by design; **Preview** shows what a run would send. Nothing arrives at 08:00 if the computer was off.

**I want to start over.** Stop the server and delete `data/stacksignal.db`; it is recreated on the next start.

---

## Development

```
backend/app/
  collectors/             GitHub, Hugging Face, arXiv, npm, PyPI: fetch + normalize
  history.py, scoring.py  snapshot-based growth velocity; momentum / maturity; status suggestion (max Assess)
  enrichment.py           IDF-weighted keyword relevance + batched AI analysis, item fingerprints
  ai_client.py            HTTP adapter for the gateway: retries, polling, deadlines, idempotency
  ai_analysis.py          content-addressed cache, per-run budget with reserve, circuit breaker
  capabilities.py         overview, AI synonym consolidation, pruning
  feedback.py             votes/notes, precedence over analysis, calibration examples
  opportunities.py        AI briefings          chat.py  multilingual, project-aware, source-citing chat
  pipeline.py             one collection run    scheduler.py  daily run at DAILY_RUN_TIME + digest
  digest.py, telegram.py  strict digest selection/formatting; minimal Bot API client (token never logged)
  seed.py, maintenance.py versioned one-time migrations (profiles, radar clean-ups)
  decisions.py            per-project decisions (goal, criteria, verdict, revisit-when, history) and their AI calibration examples
  revisit.py              what changed since you decided: releases, shifts, new alternatives
  intake.py, authors.py   Add URL; GitHub stars, follows and watched authors
  profile_import.py       project profile from its real folder (manifests + README)
  project_intelligence.py turns the project description + current stack into a technical model and structured
                          needs, priorities, constraints, acceptance criteria, search terms and prerequisite edges
  project_model.py        goals -> subsystems -> current capabilities -> gaps snapshot, source fingerprint/staleness,
                          and the gap context used to explain why a discovered technology matters
  needs.py                need graph: ready/blocked ordering, unlocks, gap linkage and bounded need-priority ranking signal
  discovery.py, queries.py, quality.py   project searches that learn, the quality gate, refresh of tracked items
  search_index.py         FTS5/BM25 candidate index for the chat
  embeddings.py           local sentence embeddings (all-MiniLM-L6-v2, ONNX) for semantic chat retrieval
  retrieval.py            Reciprocal Rank Fusion (RRF): combines BM25, embeddings, project relevance and freshness
  preference_learning.py  optional secondary calibration from positive/negative decisions; the primary signal is the project profile + needs
  entities.py             links a PyPI/npm package to its GitHub repository (one technology, not two items)
  stack_watch.py          new versions of the packages your projects depend on (PyPI, npm)
  release_watch.py        new releases of the GitHub repositories you track
  models.py, db.py        SQLite (WAL, foreign keys enforced) via SQLAlchemy; schema managed by Alembic (backend/alembic/)
  serializers.py          model -> API dict, shared by the routers below; ser_items() loads a list in batched queries
  security.py             local-only guard: allowed hosts, no cross-site writes, no framing
  routers/                one file per resource (today, projects, items, discover, relevant, radar, opportunities,
                          queries, capabilities, digest, collect, chat, health); main.py just includes them
  main.py                 the FastAPI app: lifespan, router registration, serves frontend/
frontend/                 no-build dashboard: index.html, styles.css (design tokens, light/dark) and js/ —
                          native ES modules: main.js (router), api, store, ui primitives, components/, views/
screenshots/         fictional demo screenshot
scripts/                  supervisor.ps1 + autostart.ps1 (Windows autostart and watchdog)
backend/scripts/          backup.py (consistent copy of the database), eval_retrieval.py
                          (Recall@30 / Precision@10 / MRR / nDCG@10; manual votes/decisions are preferred ground truth),
                          preference_report.py (per-project learning readiness and learned inbox candidates),
                          need_graph_report.py (what to do now vs what is blocked by prerequisites),
                          project_model_report.py (goals/subsystems/gaps and whether the model is stale)
                          agent_relevance.py (resumable, project-scoped relevance refresh through local agents)
```

**Tests** (no network — outside connections fail the test — in-memory and temporary file-backed SQLite;
`AI_ENABLED=0` and `EMBEDDINGS_ENABLED=0` are forced so nothing hits a real gateway or downloads the embedding model):

```powershell
.venv\Scripts\python.exe -m pip install -r backend\requirements-dev.txt
cd backend
..\.venv\Scripts\python.exe -m pytest
```

To measure retrieval on the accumulated real database (not the in-memory test DB):

```powershell
.venv\Scripts\python.exe backend\scripts\eval_retrieval.py
.venv\Scripts\python.exe backend\scripts\eval_retrieval.py --manual-only
.venv\Scripts\python.exe backend\scripts\preference_report.py
.venv\Scripts\python.exe backend\scripts\need_graph_report.py --project stacksignal
.venv\Scripts\python.exe backend\scripts\project_model_report.py --project stacksignal
```

The evaluator prefers your own per-project votes and decisions as ground truth; strong historical AI
relevance is only a temporary fallback while manual labels accumulate. This is the feedback loop for
retrieval changes: compare Recall@30, Precision@10, MRR and nDCG@10 before/after ranking changes.

To update relevance after adding or changing projects without rewriting item summaries, use
`backend/scripts/agent_relevance.py plan --projects slug1,slug2`, then `export` with the same arguments.
Each `data/agent-relevance/batch-NNNN.json` contains its prompt, profiles and items. Have the local agent
write its sibling `.result.json`, then run `apply <batch-path> --provider claude-code --model <actual-model>`.
`status --projects slug1,slug2` reports pending pairs and open batches; repeated exports and applies resume
completed work. Changed content or profiles are skipped for a fresh batch. User votes, decisions,
summaries and other projects' judgments are preserved. Scoped AI relevance survives keyword collection
and appears in the project's AI-only results with a separate relevance badge; it does not mark a keyword
summary as AI-written or inflate global full-analysis coverage.

Python 3.11 or newer is required (`tomllib`). GitHub Actions runs the tests on every push
(`.github/workflows/tests.yml`); `backend/constraints.txt` pins the exact versions they ran with — install with
`pip install -r backend/requirements-dev.txt -c backend/constraints.txt` for a reproducible environment.

**Browser tests** (`backend/e2e/`): the real server on a scratch database, driven by Chromium — every section
loads, a Russian project is created, edited and deleted, the item drawer takes a vote and a decision, the chat
answers; any console error fails the test. No AI, scheduler or network.

```powershell
.venv\Scripts\python.exe -m pip install -r backend\requirements-e2e.txt -c backend\constraints.txt
.venv\Scripts\python.exe -m playwright install chromium
cd backend
..\.venv\Scripts\python.exe -m pytest e2e
```

A model change needs an Alembic revision (`cd backend; ..\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "..."`):
`tests/test_migrations.py` compares the migrated schema with the models column by column and fails otherwise.

They cover scoring and snapshot velocity, the project migration, relevance ranges, the AI client (against
`httpx.MockTransport`) and its cache / circuit-breaker state machine, batching and candidate selection,
capability consolidation, feedback precedence and survival, the chat, the radar clean-ups, the Telegram digest
(selection, escaping, delivery and failures against a fake Bot API, the token never leaking, the schedule) and the
HTTP API (FastAPI `TestClient` on an in-memory database).

**Adding a source:** write `collectors/<name>.py` with `collect() -> list[dict]` in the shape of `github.py`
and register it in `pipeline.COLLECTORS`. GitHub, Hugging Face, arXiv, npm and PyPI are already implemented.

**Bootstrap profiles:** the legacy migration hook is empty in the public distribution. Tests provide fictional
examples explicitly; do not add personal profiles to the application source.

**Changing the database schema:** edit the model in `models.py`, then generate a migration from `backend/`
(`..\.venv\Scripts\python.exe -m alembic revision --autogenerate -m "..."`) and review the generated file before
committing it — autogenerate is a diff, not a guarantee. It runs automatically on the next start (`init_db()`
calls `alembic upgrade head`); there is no manual step. `tests/test_migrations.py` fails if a model changes
without a matching revision.


### Project understanding and optional preference calibration
The project description is the primary personalization contract. When you explicitly run **Переосмыслить проект**,
StackSignal reads the description together with the scanned stack/dependencies and builds an inspectable technical
model: **goals → subsystems → capabilities already evidenced by the project → gaps → needs**. Absence from the stack
is not treated as proof of a missing capability. The model stores a fingerprint of the exact profile it was built
from; after the description, stack or dependency declarations change the **Модель** tab marks it stale until the
next analysis instead of silently using an old understanding as if it were current.

Each gap records current vs desired state, the subsystem and goals it affects, and the inferred needs that close it.
Those links travel into Radar, so a candidate can be explained as “closes this need, which closes this project gap
for this subsystem/goal” rather than only by a relevance percentage. Existing hand-written or README-imported needs
remain authoritative and are never overwritten by the inference pass. Open a project → **Модель** or **Потребности**
→ **Переосмыслить проект** to rebuild the derived model.

StackSignal also infers a conservative prerequisite graph between needs. A dependency is created only when one
capability really has to exist before another can be useful; the prompt explicitly forbids inventing a linear
roadmap. Open needs are therefore split into **ready now** and **blocked**. A ready foundation that unlocks
several later needs gets a small bounded priority bonus, while technologies that only address a blocked future
layer are down-ranked until its prerequisites are satisfied. Marking the prerequisite need as satisfied unlocks
the next layer automatically.

In project Radar, an already-relevant technology gets a bounded extra signal when it visibly addresses a
high-priority ready need. This lets a tool that closes today's blocker rank above a merely popular or generally
interesting tool. No 👍/👎 labels are required for this behavior.

The **Дополнительная калибровка** panel under **Решения** is optional. After 2 positive and 2 negative embedded
examples it adds a much smaller taste/history signal. Trial/Adopt and Ignore are training outcomes;
Watch/Assess/Hold are neutral.

```powershell
.\.venv\Scripts\python.exe backend\scripts\eval_retrieval.py --manual-only --compare-learning
```
