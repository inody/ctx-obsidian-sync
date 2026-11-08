#!/bin/bash
# Summarize the previous day and refresh already-tracked recent days when their
# synced conversations changed. Intended to run from launchd at 03:10 local time.
set -u

export PATH="$HOME/.local/bin:$HOME/.pi/agent/bin:$PATH"
export TZ="${CTX_OBSIDIAN_TZ:-${AI_OBSIDIAN_TZ:-Europe/London}}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
state_dir="$HOME/.local/state/ctx-obsidian-sync"
lock_dir="$state_dir/daily-digest.lock"
mkdir -p "$state_dir"

# mkdir is atomic. Recover a stale lock left by a killed process.
if ! mkdir "$lock_dir" 2>/dev/null; then
  if [[ -f "$lock_dir/pid" ]]; then
    old_pid=$(cat "$lock_dir/pid" 2>/dev/null || true)
    if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
      echo "ctx daily digest is already running (pid=$old_pid)" >&2
      exit 0
    fi
  fi
  rm -rf "$lock_dir"
  if ! mkdir "$lock_dir" 2>/dev/null; then
    echo "could not acquire daily digest lock: $lock_dir" >&2
    exit 1
  fi
fi
printf '%s\n' "$$" > "$lock_dir/pid"
trap 'rm -rf "$lock_dir"' EXIT INT TERM

bsd_day() {
  date -v-"$1"d +%Y-%m-%d 2>/dev/null || date -d "$1 days ago" +%Y-%m-%d
}

rc=0
# Yesterday is always considered. The older dates are refreshed only after they
# have been tracked once, which catches late Dropbox/session updates without
# unexpectedly backfilling historical diaries on first installation.
for offset in 1 2 3; do
  day=$(bsd_day "$offset")
  extra=()
  if [[ "$offset" -gt 1 ]]; then
    extra+=(--only-if-tracked)
  fi
  python3 "$script_dir/ctx_daily_digest.py" --write --date "$day" "${extra[@]}" || rc=1
done

exit "$rc"
