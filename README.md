# ctx → Obsidian session sync

Sync local coding-agent history (Codex, Claude, Pi, OpenCode) into Obsidian using [ctx](https://ctx.rs) 2.4.1's local Core/Tantivy index.

The repository contains only the sync code and LaunchAgent template. It does not contain the ctx database, agent transcripts, generated Obsidian notes, credentials, or machine-local state.

## Flow

```text
agent history --ctx import--> ~/.ctx/search/lexical (immutable Core/Tantivy generations)
    --ctx list events / ctx show session--> <vault>/ctx-sessions/*.md
    --ctx_daily_digest.py + Pi--> <vault>/<diary-dir>/YYYY-MM-DD.md
```

The pre-v0.26 `~/.ctx/work.sqlite` Store is not migrated or queried by CTX 2.4.1. If it still exists after an upgrade, it is an inert owner-managed backup rather than the active index.

Each device uses `hostname -s` as its `pc` identifier. Notes from multiple devices can coexist in a Dropbox-synced vault. The session sync runs hourly. A separate LaunchAgent runs at 03:10 local time and summarizes the previous calendar day into a marker-delimited diary section.

## Requirements

- macOS for the included LaunchAgent
- Python 3.9 or newer with `zoneinfo`
- `ctx` 2.4.1 installed at `~/.local/bin/ctx` or available on `PATH`
- [Pi](https://pi.dev) installed at `~/.pi/agent/bin/pi` or available on `PATH` (only needed for the daily digest)
- An Obsidian vault, configured with `OBSIDIAN_VAULT` (see [Configuration](#configuration))

Install ctx and Pi separately:

```bash
curl -fsSL https://ctx.rs/install | sh
curl -fsSL https://pi.dev/install.sh | sh
```

See the [ctx](https://ctx.rs) and [Pi](https://pi.dev) sites for other installation methods. Configure a model provider for Pi (run `pi` once interactively) before enabling the daily digest.

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

Run `install.sh` once without `--load` to create the config file, set at least `OBSIDIAN_VAULT` in it, then run `install.sh --load`.

The installer:

- links the session-sync and daily-digest scripts into `~/bin/`
- creates `~/.config/ctx-obsidian-sync/config` from [`config.example`](config.example) if it does not exist (an existing file is never changed)
- renders both LaunchAgents with the current home directory and the configured label prefix
- loads both LaunchAgents only when `--load` is supplied, and refuses to load them while `OBSIDIAN_VAULT` is unset
- preserves differing existing files as timestamped backups, and warns about agents left over under a different label prefix

The LaunchAgents call the scripts by absolute path. To run them by hand as shown below, add `~/bin` to your `PATH` (for example `export PATH="$HOME/bin:$PATH"` in `~/.zshrc`), or call them as `python3 ~/bin/<script>`.

## Configuration

Settings are read from `${XDG_CONFIG_HOME:-~/.config}/ctx-obsidian-sync/config` (or the file named by `CTX_OBSIDIAN_SYNC_CONFIG`). Each line is `KEY=value`, using the same names as the environment variables. The file is parsed, not executed. Precedence is: command-line flag, then environment variable, then config file, then built-in default. The LaunchAgents read the file at run time, so changes apply on the next run without reinstalling, except for the label prefix.

| Key | Default | Purpose |
| --- | --- | --- |
| `OBSIDIAN_VAULT` | none, required for writing | Vault root |
| `CTX_OBSIDIAN_TZ` | system timezone | IANA timezone for calendar days; `AI_OBSIDIAN_TZ` is still accepted as a legacy environment variable |
| `CTX_OBSIDIAN_DIARY_DIR` | `Diary` | Diary folder under the vault |
| `CTX_OBSIDIAN_DIARY_TEMPLATE` | none | Vault-relative template for new diary notes |
| `CTX_OBSIDIAN_LAUNCHD_PREFIX` | `local` | LaunchAgent labels become `<prefix>.ctx-obsidian-sync` and `<prefix>.ctx-daily-digest`; read by `install.sh` only |
| `CTX_DAILY_DIGEST_HOST` | none (all hosts) | Only this `hostname -s` runs the scheduled digest |
| `CTX_DAILY_DIGEST_MODEL` | Pi default | Pi model for the digest |
| `CTX_DAILY_DIGEST_LANGUAGE` | `en` | Built-in digest prompt and headings: `en` or `ja` |
| `CTX_DAILY_DIGEST_PROMPT_FILE` | none | Your own digest instructions (see below) |
| `CTX_DAILY_DIGEST_HEADING` | language heading | Heading prepended to Pi's output if missing; empty disables it |

Example:

```sh
OBSIDIAN_VAULT=~/Dropbox/Obsidian/MyVault
CTX_OBSIDIAN_TZ=Europe/London
CTX_OBSIDIAN_DIARY_DIR=Journal
CTX_OBSIDIAN_DIARY_TEMPLATE=Templates/daily.md
CTX_DAILY_DIGEST_HOST=my-laptop
```

### Digest language and prompt

The daily digest is written in English by default. Set `CTX_DAILY_DIGEST_LANGUAGE=ja` for the built-in Japanese prompt and headings. For any other language or style, write your own instructions to a file and point `CTX_DAILY_DIGEST_PROMPT_FILE` at it; put `{{source}}` where the day's conversation log should go, or leave it out to have the log appended. Set `CTX_DAILY_DIGEST_HEADING` to match the heading your prompt asks for. The digest is regenerated when the extracted log changes; after editing only the prompt, run `ctx_daily_digest.py --write --force --date ...` to refresh a day.

## Manual use

Dry-run today (no vault access needed):

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

The daily job enumerates the day's local Core events with the same cursor-safe `ctx list events` helper, uses exact local `ctx show session` transcripts when available, and falls back to Dropbox-synced `ctx-sessions` notes for other computers. It filters messages by their event timestamp rather than the session start date. Pi runs with `--no-tools --no-session`, so digest generation cannot edit files through tools and does not create another history session. Only text between `ctx-daily-summary` markers is replaced; hand-written diary content is preserved. If the diary does not exist, it is created from `CTX_OBSIDIAN_DIARY_TEMPLATE` when set, otherwise as a minimal note with journal frontmatter.

On multiple computers, run the hourly session sync everywhere but allow the daily digest on only one coordinator: set `CTX_DAILY_DIGEST_HOST` to that machine's `hostname -s` in the config file on every computer. Other hosts then exit successfully without calling Pi or touching the Diary. When the setting is absent, every host runs the digest. Manual `ctx_daily_digest.py` commands remain available on every host.

The daily state tracks source hashes. Yesterday is always checked, while the two preceding dates are refreshed only after they have been tracked once, allowing late session/Dropbox updates without an initial historical backfill.

## Local data not tracked by Git

- active ctx index: immutable generations under `~/.ctx/search/lexical/`
- optional inert pre-v0.26 Store/backup: `~/.ctx/work.sqlite` (not read by CTX 2.4.1)
- session sync state: `~/.local/state/ctx-obsidian-sync/synced.json`
- daily digest state and lock: `~/.local/state/ctx-obsidian-sync/daily-digests.json`, `daily-digest.lock/`
- configuration: `~/.config/ctx-obsidian-sync/config`
- generated notes: `<vault>/ctx-sessions/` and the marker-delimited sections in `<vault>/<diary-dir>/`
- source agent transcripts and credentials

Each note contains at most 8000 body characters by default. The full transcript remains available through `ctx show session <id>`.

## Development

Run the tests from the repository root:

```bash
python3 -m unittest discover tests
```

The tests use temporary directories and fake `ctx` binaries; they do not read your config file, ctx index, or vault.

## License

[MIT](LICENSE)
