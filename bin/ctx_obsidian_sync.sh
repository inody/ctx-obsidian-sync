#!/bin/bash
# Refresh the ctx index from local agent history and render new sessions
# into Obsidian ctx-sessions/ notes. Runs from launchd hourly.
set -u

export PATH="$HOME/.local/bin:$PATH"
export TZ="${CTX_OBSIDIAN_TZ:-${AI_OBSIDIAN_TZ:-Europe/London}}"  # keep `date` and Python on the same TZ
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

# Refresh the ctx index (read-only against provider files). pi needs an
# explicit path because ctx 0.18's auto-discovery looks at a stale path.
# --partial keeps one bad row from sinking a whole source: a single provider
# event hash conflict silently stalled the claude import from 2026-07-28.
ctx import --all --partial
ctx import --provider pi --path "$HOME/.pi/agent/sessions" --partial

# Scan the last 3 days so sessions that crossed midnight or arrived late are
# not missed. Idempotent: already-synced ctx_session_ids are skipped via state.
# BSD date (launchd/system /bin/date) vs GNU date (homebrew gnubin on PATH):
SINCE=$(date -v-2d +%Y-%m-%d 2>/dev/null || date -d "2 days ago" +%Y-%m-%d)
UNTIL=$(date +%Y-%m-%d)

exec python3 "$script_dir/ctx_to_obsidian_sessions.py" --write --since "$SINCE" --until "$UNTIL"
