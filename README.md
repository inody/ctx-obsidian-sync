# ctx → Obsidian session sync

Sync local coding-agent history (Codex, Claude, Pi, OpenCode) into Obsidian using [ctx](https://ctx.rs) 2.4.1's local Core/Tantivy index.

The repository contains only the sync code and LaunchAgent template. It does not contain the ctx database, agent transcripts, generated Obsidian notes, credentials, or machine-local state.

## Flow

```text
agent history --ctx import--> ~/.ctx/search/lexical (immutable Core/Tantivy generations)
    --ctx list events / ctx show session--> <vault>/ctx-sessions/*.md
    --ctx_daily_digest.py + Pi--> <vault>/01_Diary/YYYY-MM-DD.md
```

The pre-v0.26 `~/.ctx/work.sqlite` Store is not migrated or queried by CTX 2.4.1. If it still exists after an upgrade, it is an inert owner-managed backup rather than the active index.

Each device uses `hostname -s` as its `pc` identifier. Notes from multiple devices can coexist in a Dropbox-synced vault. The session sync runs hourly. A separate LaunchAgent runs at 03:10 local time and summarizes the previous calendar day into a marker-delimited diary section.

## Requirements

- macOS for the included LaunchAgent
- Python 3.9 or newer with `zoneinfo`
- `ctx` 2.4.1 installed at `~/.local/bin/ctx` or available on `PATH`
- Pi installed at `~/.pi/agent/bin/pi` or available on `PATH`
- An Obsidian vault; default: `~/Dropbox/Obsidian/ObsidianVault`

Install ctx separately:

```bash
curl -fsSL https://ctx.rs/install | sh
```

Set up CTX once, keep indexing manual, and disable analytics locally if desired:

```bash
ctx index mode manual
ctx setup --no-daemon --format json
ctx import --all --format json --progress none
ctx index wait --lexical --format json
```

```toml
# ~/.ctx/config.toml
[indexing]
mode = "manual"

[analytics]
enabled = false
```

Manual mode removes persistent CTX daemon supervision. Each hourly sync performs one explicit finite `ctx import --all`; semantic search remains disabled and no embedding model is needed.

## Install

```bash
gh auth login  # only when this device is not already authenticated
gh repo clone inody/ctx-obsidian-sync ~/ctx-obsidian-sync
~/ctx-obsidian-sync/install.sh --load
```

The installer:

- links the session-sync and daily-digest scripts into `~/bin/`
- renders both LaunchAgents with the current home directory
- loads both LaunchAgents only when `--load` is supplied
- preserves differing existing files as timestamped backups

Environment overrides:

- `OBSIDIAN_VAULT`: vault root
- `CTX_OBSIDIAN_TZ` or `AI_OBSIDIAN_TZ`: timezone, default `Europe/London`
- `CTX_DAILY_DIGEST_MODEL`: optional Pi model override; the configured Pi default is used otherwise
- `CTX_DAILY_DIGEST_HOST`: hostname allowed to write the shared Diary; the included LaunchAgent designates `dimbp`

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

Initial or manual refresh:

```bash
ctx index mode manual
ctx import --all --format json --progress none
ctx index wait --lexical --format json
```

CTX 2.4.1 auto-discovers Pi at `~/.pi/agent/sessions`; the hourly wrapper does not import that path a second time. Imports preserve valid records when isolated records are malformed and expose rejections/source failures in their result. The wrapper evaluates the machine-readable publication outcome, so a nonzero command status caused only by independent source failures does not discard a usable published Core generation.

The wrapper scans the most recent three calendar days each hour. Session discovery uses exact RFC3339 day bounds with cursor-safe `ctx list events` enumeration, then obtains complete event counts and metadata from `ctx show session --format json`. Sync is idempotent: the local state file records each session's event count so an in-progress session is rewritten only when it changes. If Core session IDs differ after migration, the writer reconciles an existing note by provider-native session ID and reuses its filename instead of creating a duplicate note set.

Dry-run a daily digest without changing the vault:

```bash
python3 ~/bin/ctx_daily_digest.py --date 2026-11-06
```

Write or refresh one day manually:

```bash
python3 ~/bin/ctx_daily_digest.py --write --date 2026-11-06
```

The daily job enumerates the day's local Core events with the same cursor-safe `ctx list events` helper, uses exact local `ctx show session` transcripts when available, and falls back to Dropbox-synced `ctx-sessions` notes for other computers. It filters messages by their event timestamp rather than the session start date. Pi runs with `--no-tools --no-session`, so digest generation cannot edit files through tools and does not create another history session. Only text between `ctx-daily-summary` markers is replaced; hand-written diary content is preserved. If the diary does not exist, it is created from `Template/diary.md`.

On multiple computers, run the hourly session sync everywhere but allow the daily digest on only one coordinator. The included LaunchAgent sets `CTX_DAILY_DIGEST_HOST=dimbp`; other hosts exit successfully without calling Pi or touching the Diary. Manual `ctx_daily_digest.py` commands remain available on every host.

The daily state tracks source hashes. Yesterday is always checked, while the two preceding dates are refreshed only after they have been tracked once, allowing late session/Dropbox updates without an initial historical backfill.

## Local data not tracked by Git

- active ctx index: immutable generations under `~/.ctx/search/lexical/`
- optional inert pre-v0.26 Store/backup: `~/.ctx/work.sqlite` (not read by CTX 2.4.1)
- session sync state: `~/.local/state/ctx-obsidian-sync/synced.json`
- daily digest state and lock: `~/.local/state/ctx-obsidian-sync/daily-digests.json`, `daily-digest.lock/`
- generated notes: `<vault>/ctx-sessions/` and the marker-delimited sections in `<vault>/01_Diary/`
- source agent transcripts and credentials

Each note contains at most 8000 body characters by default. The full transcript remains available through `ctx show session <id>`.
