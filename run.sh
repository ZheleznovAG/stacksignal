#!/usr/bin/env bash
# Starts StackSignal. Dependencies are installed on the first run and again only when
# backend/requirements.txt or backend/constraints.txt change (or with --update), always at the exact
# versions in constraints.txt -- so starting the app never changes the environment or needs PyPI.
set -euo pipefail
root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$root"

if [ ! -d "$root/.venv" ]; then
  echo "Creating virtual environment..."
  if command -v python3 >/dev/null 2>&1; then
    python3 -m venv "$root/.venv"
  else
    python -m venv "$root/.venv"
  fi
fi

if [ -f "$root/.venv/Scripts/python.exe" ]; then
  PY="$root/.venv/Scripts/python.exe"
else
  PY="$root/.venv/bin/python"
fi

requirements="$root/backend/requirements.txt"
constraints="$root/backend/constraints.txt"
stamp="$root/.venv/.deps-stamp"
wanted="$(cat "$requirements" "$constraints" | cksum)"
installed="$(cat "$stamp" 2>/dev/null || true)"
if [ "${1:-}" = "--update" ] || [ "$installed" != "$wanted" ]; then
  echo "Installing dependencies (pinned by backend/constraints.txt)..."
  "$PY" -m pip install --quiet -r "$requirements" -c "$constraints"
  echo "$wanted" > "$stamp"
fi

if [ ! -f "$root/.env" ]; then
  cp "$root/.env.example" "$root/.env"
  echo "Created .env from .env.example. Add AI_API_KEY (from your ai-gateway) and GITHUB_TOKEN there for full functionality; both are optional."
fi

echo "Starting StackSignal on http://localhost:8000 ..."
# Bound to 127.0.0.1, not 0.0.0.0: this app has no authentication, so it must not be
# reachable from other devices on the network by default. See README's Security section
# before changing this.
"$PY" -m uvicorn app.main:app --app-dir "$root/backend" --host 127.0.0.1 --port 8000
