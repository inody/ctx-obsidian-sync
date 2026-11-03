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
link_script "$repo_dir/bin/ctx_to_obsidian_sessions.py" "$HOME/bin/ctx_to_obsidian_sessions.py"
link_script "$repo_dir/README.md" "$HOME/bin/README.ctx-obsidian-sync.md"

plist_target="$HOME/Library/LaunchAgents/com.dinoue.ctx-obsidian-sync.plist"
plist_tmp="$(mktemp)"
trap 'rm -f "$plist_tmp"' EXIT
python3 - "$repo_dir/launchd/com.dinoue.ctx-obsidian-sync.plist.in" "$plist_tmp" "$HOME" <<'PY'
from pathlib import Path
from xml.sax.saxutils import escape
import sys
source, target, home = map(Path, sys.argv[1:])
text = source.read_text(encoding="utf-8").replace("__HOME__", escape(str(home)))
target.write_text(text, encoding="utf-8")
PY
plutil -lint "$plist_tmp" >/dev/null
mkdir -p "$(dirname "$plist_target")"
if [[ ! -e "$plist_target" ]] || ! cmp -s "$plist_tmp" "$plist_target"; then
  if [[ -e "$plist_target" ]]; then
    cp "$plist_target" "${plist_target}.backup-${stamp}"
    echo "backup  ${plist_target}.backup-${stamp}"
  fi
  cp "$plist_tmp" "$plist_target"
  echo "wrote   $plist_target"
else
  echo "ok      $plist_target"
fi

if $load_agent; then
  domain="gui/$(id -u)"
  launchctl bootout "$domain/com.dinoue.ctx-obsidian-sync" >/dev/null 2>&1 || true
  launchctl bootstrap "$domain" "$plist_target"
  echo "loaded  $domain/com.dinoue.ctx-obsidian-sync"
else
  echo "LaunchAgent was not reloaded. Run '$0 --load' when you want to load/reload it."
fi
