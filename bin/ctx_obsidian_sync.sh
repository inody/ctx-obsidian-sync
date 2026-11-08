#!/bin/bash
# Refresh the ctx index from local agent history and render new sessions
# into Obsidian ctx-sessions/ notes. Runs from launchd hourly.
set -euo pipefail

export PATH="$HOME/.local/bin:$PATH"
export TZ="${CTX_OBSIDIAN_TZ:-${AI_OBSIDIAN_TZ:-Europe/London}}"  # keep `date` and Python on the same TZ
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"

# Keep CTX in the non-daemon operating model and run one explicit finite Core
# refresh. CTX 1.3 auto-discovers Pi at ~/.pi/agent/sessions, so no duplicate
# exact-path import is needed. Valid records still commit when individual records
# or independent sources fail; log only aggregate diagnostics, never source text.
ctx index mode manual --format json >/dev/null
import_result=$(mktemp)
trap 'rm -f "$import_result"' EXIT INT TERM
ctx import --all --format json --progress none >"$import_result"
python3 - "$import_result" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    result = json.load(handle)
if result.get("schema_version") != 2 or result.get("outcome") == "failure":
    raise SystemExit("ctx import did not publish a usable Core result")
totals = result.get("totals", {})
print(
    "[ctx import] outcome={} sessions={} rejected_records={} failed_sources={}".format(
        result.get("outcome"),
        totals.get("current_indexed_sessions", 0),
        totals.get("rejected_records", 0),
        totals.get("failed_sources", 0),
    ),
    file=sys.stderr,
)
PY

# Scan the last 3 days so sessions that crossed midnight or arrived late are
# not missed. Idempotent: already-synced ctx_session_ids are skipped via state.
# BSD date (launchd/system /bin/date) vs GNU date (homebrew gnubin on PATH):
SINCE=$(date -v-2d +%Y-%m-%d 2>/dev/null || date -d "2 days ago" +%Y-%m-%d)
UNTIL=$(date +%Y-%m-%d)

python3 "$script_dir/ctx_to_obsidian_sessions.py" --write --since "$SINCE" --until "$UNTIL"
