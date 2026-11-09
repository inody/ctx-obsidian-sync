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
link_script "$repo_dir/bin/ctx_config.py" "$HOME/bin/ctx_config.py"
link_script "$repo_dir/bin/ctx_config.sh" "$HOME/bin/ctx_config.sh"
link_script "$repo_dir/bin/ctx_to_obsidian_sessions.py" "$HOME/bin/ctx_to_obsidian_sessions.py"
link_script "$repo_dir/bin/ctx_obsidian_doctor.py" "$HOME/bin/ctx_obsidian_doctor.py"
link_script "$repo_dir/bin/ctx_daily_digest.sh" "$HOME/bin/ctx_daily_digest.sh"
link_script "$repo_dir/bin/ctx_daily_digest.py" "$HOME/bin/ctx_daily_digest.py"
link_script "$repo_dir/README.md" "$HOME/bin/README.ctx-obsidian-sync.md"

# User settings live outside the repository. Create a commented template on
# first install; an existing file is never modified.
# shellcheck source=bin/ctx_config.sh
source "$repo_dir/bin/ctx_config.sh"
config_file="$(ctx_config_path)"
if [[ ! -e "$config_file" ]]; then
  mkdir -p "$(dirname "$config_file")"
  cp "$repo_dir/config.example" "$config_file"
  echo "created $config_file (edit it to set OBSIDIAN_VAULT and other options)"
else
  echo "ok      $config_file"
fi
ctx_config_load

label_prefix="${CTX_OBSIDIAN_LAUNCHD_PREFIX:-local}"
if [[ ! "$label_prefix" =~ ^[A-Za-z0-9][A-Za-z0-9.-]*$ ]]; then
  echo "invalid CTX_OBSIDIAN_LAUNCHD_PREFIX: $label_prefix" >&2
  exit 2
fi
sync_label="$label_prefix.ctx-obsidian-sync"
digest_label="$label_prefix.ctx-daily-digest"

render_plist() {
  local source=$1 target=$2 tmp
  tmp="$(mktemp)"
  python3 - "$source" "$tmp" "$HOME" "$label_prefix" <<'PY'
from pathlib import Path
from xml.sax.saxutils import escape
import sys
source, target, home, prefix = sys.argv[1:]
text = (
    Path(source).read_text(encoding="utf-8")
    .replace("__HOME__", escape(home))
    .replace("__LABEL_PREFIX__", escape(prefix))
)
Path(target).write_text(text, encoding="utf-8")
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

agents_dir="$HOME/Library/LaunchAgents"
sync_plist="$agents_dir/$sync_label.plist"
digest_plist="$agents_dir/$digest_label.plist"
render_plist "$repo_dir/launchd/ctx-obsidian-sync.plist.in" "$sync_plist"
render_plist "$repo_dir/launchd/ctx-daily-digest.plist.in" "$digest_plist"

# Agents installed earlier under another label prefix would run the same jobs
# twice. Report them instead of deleting files this run did not create.
for stale in "$agents_dir"/*.ctx-obsidian-sync.plist "$agents_dir"/*.ctx-daily-digest.plist; do
  [[ -e "$stale" ]] || continue
  [[ "$stale" == "$sync_plist" || "$stale" == "$digest_plist" ]] && continue
  stale_label="$(basename "$stale" .plist)"
  echo "warning: another agent is installed under a different label: $stale" >&2
  echo "         remove it with: launchctl bootout gui/$(id -u)/$stale_label; rm '$stale'" >&2
done

if $load_agent; then
  if [[ -z "${OBSIDIAN_VAULT:-}" ]]; then
    echo "OBSIDIAN_VAULT is not set; edit $config_file before running '$0 --load'." >&2
    exit 1
  fi
  domain="gui/$(id -u)"
  for label in "$sync_label" "$digest_label"; do
    launchctl bootout "$domain/$label" >/dev/null 2>&1 || true
  done
  launchctl bootstrap "$domain" "$sync_plist"
  launchctl bootstrap "$domain" "$digest_plist"
  echo "loaded  $domain/$sync_label"
  echo "loaded  $domain/$digest_label"
else
  echo "LaunchAgents were not reloaded. Run '$0 --load' when you want to load/reload them."
fi
