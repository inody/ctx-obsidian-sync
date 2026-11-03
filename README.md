# ctx → Obsidian session sync

Sync local coding-agent history (Codex, Claude, Pi, OpenCode) into Obsidian using [ctx](https://ctx.rs) as the local index.

The repository contains only the sync code and LaunchAgent template. It does not contain the ctx database, agent transcripts, generated Obsidian notes, credentials, or machine-local state.

## Flow

```text
agent history --ctx import--> ~/.ctx/work.sqlite
    --ctx_to_obsidian_sessions.py--> <vault>/ctx-sessions/*.md
```

Each device uses `hostname -s` as its `pc` identifier. Notes from multiple devices can coexist in a Dropbox-synced vault.

## Requirements

- macOS for the included LaunchAgent
- Python 3 with `zoneinfo`
- `ctx` installed at `~/.local/bin/ctx` or available on `PATH`
- An Obsidian vault; default: `~/Dropbox/Obsidian/ObsidianVault`

Install ctx separately:

```bash
curl -fsSL https://ctx.rs/install | sh
```

Disable ctx analytics locally if desired:

```toml
# ~/.ctx/config.toml
[analytics]
enabled = false
```

## Install

```bash
git clone git@github.com:inody/ctx-obsidian-sync.git ~/ctx-obsidian-sync
~/ctx-obsidian-sync/install.sh --load
```

The installer:

- links the two executable scripts into `~/bin/`
- renders the LaunchAgent with the current home directory
- loads the LaunchAgent only when `--load` is supplied
- preserves differing existing files as timestamped backups

Environment overrides:

- `OBSIDIAN_VAULT`: vault root
- `CTX_OBSIDIAN_TZ` or `AI_OBSIDIAN_TZ`: timezone, default `Europe/London`

## Manual use

Dry-run today:

```bash
python3 ~/bin/ctx_to_obsidian_sessions.py
```

Write a date range:

```bash
python3 ~/bin/ctx_to_obsidian_sessions.py \
  --write --since 2026-01-01 --until "$(date +%Y-%m-%d)"
```

Initial or manual import:

```bash
ctx import --all --partial
ctx import --provider pi --path ~/.pi/agent/sessions --partial
```

The wrapper scans the most recent three calendar days each hour. Sync is idempotent: the local state file records each session's event count so an in-progress session is rewritten only when it changes.

## Local data not tracked by Git

- ctx index: `~/.ctx/work.sqlite`
- sync state: `~/.local/state/ctx-obsidian-sync/synced.json`
- generated notes: `<vault>/ctx-sessions/`
- source agent transcripts and credentials

Each note contains at most 8000 body characters by default. The full transcript remains available through `ctx show session <id>`.
