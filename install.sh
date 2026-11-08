#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
stamp="$(date +%Y%m%d-%H%M%S)"
load_agent=false

if [[ "${1:-}" == "--load" ]]; then
  load_agent=true
elif [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--load]" >&2
  exit 2
fi

link_script() {
  local source=$1 target=$2 backup
  mkdir -p "$(dirname "$target")"

  if [[ -L "$target" ]] && [[ "$(python3 -c 'import os,sys; print(os.path.realpath(sys.argv[1]))' "$target")" == "$source" ]]; then
    echo "ok      $target"
    return
  fi

  if [[ -e "$target" || -L "$target" ]]; then
    if [[ ! -L "$target" ]] && cmp -s "$source" "$target"; then
      rm -f "$target"
    else
      backup="${target}.backup-${stamp}"
      mv "$target" "$backup"
      echo "backup  $backup"
    fi
  fi

  ln -s "$source" "$target"
  echo "linked  $target -> $source"
}

link_script "$repo_dir/bin/ctx_obsidian_sync.sh" "$HOME/bin/ctx_obsidian_sync.sh"
link_script "$repo_dir/bin/ctx_core.py" "$HOME/bin/ctx_core.py"
link_script "$repo_dir/bin/ctx_to_obsidian_sessions.py" "$HOME/bin/ctx_to_obsidian_sessions.py"
link_script "$repo_dir/bin/ctx_daily_digest.sh" "$HOME/bin/ctx_daily_digest.sh"
link_script "$repo_dir/bin/ctx_daily_digest.py" "$HOME/bin/ctx_daily_digest.py"
link_script "$repo_dir/README.md" "$HOME/bin/README.ctx-obsidian-sync.md"

render_plist() {
  local source=$1 target=$2 tmp
  tmp="$(mktemp)"
  python3 - "$source" "$tmp" "$HOME" <<'PY'
from pathlib import Path
from xml.sax.saxutils import escape
import sys
source, target, home = map(Path, sys.argv[1:])
text = source.read_text(encoding="utf-8").replace("__HOME__", escape(str(home)))
target.write_text(text, encoding="utf-8")
PY
  plutil -lint "$tmp" >/dev/null
  mkdir -p "$(dirname "$target")"
  if [[ ! -e "$target" ]] || ! cmp -s "$tmp" "$target"; then
    if [[ -e "$target" ]]; then
      cp "$target" "${target}.backup-${stamp}"
      echo "backup  ${target}.backup-${stamp}"
    fi
    cp "$tmp" "$target"
    echo "wrote   $target"
  else
    echo "ok      $target"
  fi
  rm -f "$tmp"
}

sync_plist="$HOME/Library/LaunchAgents/com.dinoue.ctx-obsidian-sync.plist"
digest_plist="$HOME/Library/LaunchAgents/com.dinoue.ctx-daily-digest.plist"
render_plist "$repo_dir/launchd/com.dinoue.ctx-obsidian-sync.plist.in" "$sync_plist"
render_plist "$repo_dir/launchd/com.dinoue.ctx-daily-digest.plist.in" "$digest_plist"

if $load_agent; then
  domain="gui/$(id -u)"
  for label in com.dinoue.ctx-obsidian-sync com.dinoue.ctx-daily-digest; do
    launchctl bootout "$domain/$label" >/dev/null 2>&1 || true
  done
  launchctl bootstrap "$domain" "$sync_plist"
  launchctl bootstrap "$domain" "$digest_plist"
  echo "loaded  $domain/com.dinoue.ctx-obsidian-sync"
  echo "loaded  $domain/com.dinoue.ctx-daily-digest"
else
  echo "LaunchAgents were not reloaded. Run '$0 --load' when you want to load/reload them."
fi
