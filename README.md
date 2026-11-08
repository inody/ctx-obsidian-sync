# ctx → Obsidian session sync

Sync local coding-agent history (Codex, Claude, Pi, OpenCode) into Obsidian using [ctx](https://ctx.rs) as the local index.

The repository contains only the sync code and LaunchAgent template. It does not contain the ctx database, agent transcripts, generated Obsidian notes, credentials, or machine-local state.

## Flow

```text
agent history --ctx import--> ~/.ctx/work.sqlite
    --ctx_to_obsidian_sessions.py--> <vault>/ctx-sessions/*.md
    --ctx_daily_digest.py + Pi--> <vault>/01_Diary/YYYY-MM-DD.md
```

Each device uses `hostname -s` as its `pc` identifier. Notes from multiple devices can coexist in a Dropbox-synced vault. The session sync runs hourly. A separate LaunchAgent runs at 03:10 local time and summarizes the previous calendar day into a marker-delimited diary section.

## Requirements

- macOS for the included LaunchAgent
- Python 3 with `zoneinfo`
- `ctx` installed at `~/.local/bin/ctx` or available on `PATH`
- Pi installed at `~/.pi/agent/bin/pi` or available on `PATH`
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
gh auth login  # only when this device is not already authenticated
gh repo clone inody/ctx-obsidian-sync ~/ctx-obsidian-sync
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
- `CTX_DAILY_DIGEST_MODEL`: optional Pi model override; the configured Pi default is used otherwise

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

Dry-run a daily digest without changing the vault:

```bash
python3 ~/bin/ctx_daily_digest.py --date 2026-11-06
```

Write or refresh one day manually:

```bash
python3 ~/bin/ctx_daily_digest.py --write --date 2026-11-06
```

The daily job uses exact local `ctx` transcripts when available and falls back to Dropbox-synced `ctx-sessions` notes for other computers. It filters messages by their event timestamp rather than the session start date. Pi runs with `--no-tools --no-session`, so digest generation cannot edit files through tools and does not create another history session. Only text between `ctx-daily-summary` markers is replaced; hand-written diary content is preserved. If the diary does not exist, it is created from `Template/diary.md`.

The daily state tracks source hashes. Yesterday is always checked, while the two preceding dates are refreshed only after they have been tracked once, allowing late session/Dropbox updates without an initial historical backfill.

## Local data not tracked by Git

- ctx index: `~/.ctx/work.sqlite`
- session sync state: `~/.local/state/ctx-obsidian-sync/synced.json`
- daily digest state and lock: `~/.local/state/ctx-obsidian-sync/daily-digests.json`, `daily-digest.lock/`
- generated notes: `<vault>/ctx-sessions/` and the marker-delimited sections in `<vault>/01_Diary/`
- source agent transcripts and credentials

Each note contains at most 8000 body characters by default. The full transcript remains available through `ctx show session <id>`.
