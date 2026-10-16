---
name: analyze-backlog
description: Work off StackSignal's AI analysis backlog yourself instead of the AI gateway - read batches of collected items, write the same JSON analysis the gateway would (summary, why interesting, capabilities, per-project relevance) and store it. Use when the user asks to analyse the backlog, process unanalysed items, or replace the gateway for a while. Optional argument - how many items to process (default 50).
---

# Analyse the backlog

StackSignal stores items that no AI has read yet. `backend/scripts/agent_analysis.py` hands them out in
batches with exactly the prompt and payload the gateway gets, validates your answer with the same schema,
and stores it as an ordinary AI analysis (provider `claude-code`), so the pipeline never redoes it.

Run every command from the repository root. Target: the number of items in the argument, default 50.

## Loop

1. `.venv\Scripts\python.exe backend\scripts\agent_analysis.py status` - backlog size, open batches.
   If there are open batches from an interrupted session, answer those first (or `release` them).
2. `.venv\Scripts\python.exe backend\scripts\agent_analysis.py next --items 10` - prints the paths of
   `context.json` and a new `batch-NNNN.json`.
3. Read `data/agent-analysis/context.json` once per session (re-read only if `next` was run after a long
   pause): `system_prompt` is your instruction set; `projects`, `known_capabilities` and `user_feedback`
   are the shared part of the input.
4. `.venv\Scripts\python.exe backend\scripts\agent_analysis.py show data\agent-analysis\batch-NNNN.json`
   prints the batch's items compactly (id, kind, owner/name, numbers, description, topics) - the rest of
   the input. Read the raw batch JSON only if you need a field `show` leaves out.
5. Write the answer to the batch's `result_file` (same folder): one JSON object `{"results": [...]}`, no
   prose, no fences, exactly as `system_prompt` describes.
6. `.venv\Scripts\python.exe backend\scripts\agent_analysis.py apply data\agent-analysis\batch-NNNN.json --model <your model id>`
   - `rejected, nothing stored: ...` - fix the answer file and run `apply` again.
   - Otherwise it prints counts; `changed`/`unanswered` items simply go back to the backlog.
7. Repeat from step 2 until the target is reached, then run `status` and report what was done.

## How to answer well

The `system_prompt` rules are the contract. In practice:

- Text fields (`summary`, `why_interesting`, every `rationale`) in the language the prompt asks for
  (Russian by default), one short sentence each, under 30 words. Keep names, code identifiers and terms of
  art in their original form.
- `summary` says what the item actually is, from its description, topics and kind - never invent features.
  An empty or vague description gets a modest, honest summary.
- `capabilities`: 1-4 short generic names, reusing `known_capabilities` whenever one fits.
- `relevance`: at most 3 projects, only with score >= 0.3, only slugs from `projects`. Judge against each
  project's description, keywords, `stack` (already used - do not recommend it) and `needs` (gaps - score
  these higher). Most items are relevant to nothing: an empty `relevance` is the common, correct answer.
  Calibrate to `user_feedback` when present.
- Every item of the batch appears exactly once with its `item_id` unchanged.

## Larger runs

For more than ~100 items, run several workers side by side: `next` never hands out an item that is already
in an open batch. Each worker (for example a subagent) follows the loop above with its own batches and
passes its own model id to `--model`. Check the first few applied results yourself before scaling up.
