# ctx → Obsidian session sync and daily digest

`ctx-obsidian-sync` exports coding-agent conversations indexed by [ctx](https://ctx.rs) into an Obsidian vault. It can also ask [Pi](https://pi.dev) to turn one day of conversations into a short diary summary.

It is:

- a set of local Python and shell scripts;
- a one-way exporter from the ctx 2.4.1 Core index to Markdown;
- an optional pair of macOS LaunchAgents for hourly session export and a daily digest.

It is **not** an Obsidian plugin, a transcript backup system, a bidirectional sync tool, or a replacement for ctx. It does not install ctx, Pi, Obsidian, or a model provider. It never imports Obsidian edits back into ctx.

## Who this is for

This project is intended for macOS users who:

- already use ctx 2.4.1 to index local Codex, Claude, Pi, OpenCode, or other supported agent histories;
- want readable session notes in an Obsidian vault;
- optionally want one AI-generated daily summary;
- are comfortable configuring and inspecting local command-line tools and LaunchAgents.

The Python scripts may work when run manually on other Unix-like systems, but the installer and background-service instructions are macOS-specific.

## What problem it solves

Agent transcripts normally remain split across provider-specific history directories. This project gives them a consistent, searchable Markdown representation in Obsidian:

- one generated note per ctx session under `<vault>/ctx-sessions/`;
- stable frontmatter containing the date, host, provider, session identifiers, and an optional project link;
- incremental rewriting while a session gains events;
- reconciliation by provider-native session ID if a ctx migration changes the public ctx session ID;
- an optional marker-delimited summary in `<vault>/<diary-dir>/YYYY-MM-DD.md`.

On several computers, each computer can export session notes into the same synchronized vault. One designated computer can then generate the diary digest from both its exact local ctx transcripts and session notes produced elsewhere.

## Data flow and safety model

```text
local agent histories
        |
        | ctx import --all
        v
~/.ctx Core/Tantivy lexical index
        |
        | ctx list events + ctx show session
        v
<vault>/ctx-sessions/*.md
        |
        | optional: extract one local calendar day
        | and send it to Pi's configured model provider
        v
<vault>/<diary-dir>/YYYY-MM-DD.md
```

Important boundaries:

1. **ctx remains the source of truth.** The exporter reads ctx's Core APIs and does not modify provider histories.
2. **Session export does not invoke Pi or another model provider.** It runs ctx locally and writes Markdown. That Markdown may still leave the machine if the vault is synchronized by Dropbox, iCloud, Git, or another service.
3. **Daily digest generation is not local-only unless your Pi model is local.** Conversation excerpts are sent through Pi to the configured model provider.
4. **Generated session notes are replaceable output.** A matching note can be rewritten in full; do not keep manual edits in `ctx-sessions/`.
5. **Diary edits outside the markers are preserved.** Only the block between `<!-- ctx-daily-summary:start -->` and `<!-- ctx-daily-summary:end -->` is replaced.
6. **Failures are conservative.** Invalid or ambiguous ctx output stops or skips the affected export instead of guessing. A digest with duplicate or unbalanced markers is not written.

## Prerequisites

Required for session export:

- macOS for `install.sh` and the included LaunchAgents;
- Python 3.9 or newer, including `zoneinfo`;
- ctx **2.4.1**, available on `PATH` or at `~/.local/bin/ctx`;
- an initialized ctx index;
- an existing Obsidian vault.

Additionally required for the daily digest:

- Pi, available on `PATH` or at `~/.pi/agent/bin/pi`;
- a working Pi model/provider configuration.

Install ctx and Pi using their upstream instructions. This repository is tied to the machine-readable import and Core API contracts in ctx 2.4.1; other ctx versions are not supported unless verified separately.

Initialize ctx without its background daemon, then select manual indexing:

```bash
ctx setup --no-daemon --format json
ctx index mode manual
```

The hourly wrapper repeats `ctx index mode manual` and performs one finite `ctx import --all`. It does not require semantic indexing or an embedding model.

## Quick start

The following installs and tests the complete setup before enabling background jobs.

```bash
git clone https://github.com/inody/ctx-obsidian-sync.git ~/ctx-obsidian-sync
cd ~/ctx-obsidian-sync
./install.sh
```

The installer creates this file on first run and never overwrites it:

```text
~/.config/ctx-obsidian-sync/config
```

Edit it and set at least the vault path:

```sh
OBSIDIAN_VAULT=~/Dropbox/Obsidian/MyVault
```

Run the session job once in the foreground:

```bash
~/bin/ctx_obsidian_sync.sh
```

That command refreshes ctx and writes sessions with events from today and the previous two local calendar days. Check `<vault>/ctx-sessions/` before continuing.

If you want daily summaries, first inspect the source that would be sent to Pi, then test a dry run. Both commands default to yesterday:

```bash
python3 ~/bin/ctx_daily_digest.py --source-only
python3 ~/bin/ctx_daily_digest.py
```

The first command does not call Pi. The second calls Pi but does not change the vault. When the output is acceptable, write it once:

```bash
python3 ~/bin/ctx_daily_digest.py --write
```

Finally, load both LaunchAgents:

```bash
cd ~/ctx-obsidian-sync
./install.sh --load
```

Keep the cloned repository at the same path after installation: files in `~/bin/` are symlinks to this checkout. If you move the checkout, rerun `install.sh` from its new location.

## Configuration

The normal configuration file is:

```text
${XDG_CONFIG_HOME:-~/.config}/ctx-obsidian-sync/config
```

For a manually launched process, `CTX_OBSIDIAN_SYNC_CONFIG` can name another file. The supplied LaunchAgents do not set `CTX_OBSIDIAN_SYNC_CONFIG` or `XDG_CONFIG_HOME`, so they normally use `~/.config/ctx-obsidian-sync/config`.

The format is one `KEY=value` assignment per line. It is parsed, not executed. Quoted values are accepted, an initial `~/` expands to the home directory, and an unquoted ` # comment` is ignored. Shell expressions such as `$HOME` are not expanded.

Precedence is:

1. command-line option, when the Python command has one;
2. environment variable, even when explicitly empty;
3. configuration file;
4. built-in default.

| Key | Default | Meaning |
| --- | --- | --- |
| `OBSIDIAN_VAULT` | none | Vault root. Required for writes, all digest modes, and `install.sh --load`. |
| `CTX_OBSIDIAN_TZ` | system timezone, then UTC if it cannot be identified | IANA timezone used for calendar-day boundaries. |
| `CTX_OBSIDIAN_DIARY_DIR` | `Diary` | Diary directory under the vault. |
| `CTX_OBSIDIAN_DIARY_TEMPLATE` | none | Vault-relative template for a new diary. Literal `{{date:YYYY-MM-DD}}` is replaced. |
| `CTX_OBSIDIAN_LAUNCHD_PREFIX` | `local` | LaunchAgent label prefix. Read by `install.sh`; rerun it after changing this value. |
| `CTX_DAILY_DIGEST_HOST` | empty | If set, only the host whose `hostname -s` matches this value runs the scheduled digest. |
| `CTX_DAILY_DIGEST_DIARY_OFFSET_DAYS` | `0` | Diary date relative to the summarized conversation date. Use `1` to put yesterday's digest in today's diary. |
| `CTX_DAILY_DIGEST_MODEL` | Pi default | Model pattern or ID passed to `pi --model`. |
| `CTX_DAILY_DIGEST_LANGUAGE` | `en` | Built-in prompt and heading language: `en` or `ja`. |
| `CTX_DAILY_DIGEST_PROMPT_FILE` | none | File containing custom digest instructions. |
| `CTX_DAILY_DIGEST_HEADING` | language-specific heading | Heading prepended when Pi's output does not start with it. Set `""` to disable. |

Example:

```sh
OBSIDIAN_VAULT=~/Dropbox/Obsidian/MyVault
CTX_OBSIDIAN_TZ=Europe/London
CTX_OBSIDIAN_DIARY_DIR=Journal
CTX_OBSIDIAN_DIARY_TEMPLATE=Templates/daily.md
CTX_DAILY_DIGEST_HOST=my-laptop
CTX_DAILY_DIGEST_DIARY_OFFSET_DAYS=1
CTX_DAILY_DIGEST_LANGUAGE=en
CTX_DAILY_DIGEST_HEADING="## Yesterday's Summary"
```

`AI_OBSIDIAN_TZ` remains accepted as a legacy **environment variable**, not as a configuration-file key. `TZ` is also considered when no ctx-specific timezone is set.

### Custom digest prompts

Set `CTX_DAILY_DIGEST_PROMPT_FILE` to replace the built-in instructions. Put `{{source}}` where the extracted conversation log should appear; if the placeholder is absent, the log is appended.

Changing the prompt, model, or heading does not change the source hash used to skip unchanged days. Regenerate an existing day explicitly:

```bash
python3 ~/bin/ctx_daily_digest.py --write --force --date 2026-11-06
```

## Routine commands and workflows

### Session notes

Preview today's notes from the current ctx index without touching the vault:

```bash
python3 ~/bin/ctx_to_obsidian_sessions.py
```

Preview one provider:

```bash
python3 ~/bin/ctx_to_obsidian_sessions.py --provider pi
```

Write an inclusive date range:

```bash
python3 ~/bin/ctx_to_obsidian_sessions.py \
  --write --since 2026-01-01 --until 2026-01-31
```

Refresh ctx and export the rolling three-day window exactly as the hourly LaunchAgent does:

```bash
~/bin/ctx_obsidian_sync.sh
```

The exporter records event counts in `~/.local/state/ctx-obsidian-sync/synced.json`. An unchanged session is skipped; a session with more events is rewritten. By default, the body of each note is shortened at approximately 8,000 characters. A truncated note points back to `ctx show session <id>` for the complete transcript.

Before querying ctx in write mode, the exporter scans project notes under `<vault>/03_Projects/`. A project note with `project_id` and `roots` can classify sessions by exact `cwd` or a descendant directory. Optional `root_prefixes` classify disposable experiment workspaces that share a deterministic naming prefix:

```yaml
---
type: project
project_id: example-project
roots:
  - "~/work/example-project"
  - "/Users/another-user/work/example-project"
root_prefixes:
  - "~/work/example-project-"
  - "~/work/example-project_"
---
```

Matching session notes receive:

```yaml
project_id: example-project
project: "[[prj_example-project]]"
```

Existing generated session notes are refreshed in place without changing their transcript body. Longest-match selection applies across exact roots and prefixes, so a nested exact root can override a broader campaign prefix. Duplicate project IDs, duplicate roots, duplicate prefixes, or equal-length ambiguity stop the write instead of guessing. Prefixes are plain string prefixes, not regular expressions or AI classification. Use `--projects-dir` if project notes live somewhere other than `03_Projects`.

For a historical backfill, use `ctx import --all` if the index is not current, then run the Python exporter with explicit dates:

```bash
ctx import --all --format json --progress none
python3 ~/bin/ctx_to_obsidian_sessions.py \
  --write --since 2025-01-01 --until 2025-12-31
```

### Daily digest

Print only the extracted source; do not call Pi:

```bash
python3 ~/bin/ctx_daily_digest.py --source-only --date 2026-11-06
```

Call Pi and print the digest without writing:

```bash
python3 ~/bin/ctx_daily_digest.py --date 2026-11-06
```

Write or refresh a summarized conversation date:

```bash
python3 ~/bin/ctx_daily_digest.py --write --date 2026-11-06
```

With the default offset `0`, this writes to `2026-11-06.md`. With `CTX_DAILY_DIGEST_DIARY_OFFSET_DAYS=1`, it writes the November 6 digest to `2026-11-07.md`.

The digest uses exact local ctx transcripts when available and falls back to synced `ctx-sessions` notes, including notes from other computers. Messages are selected by event timestamp in the configured timezone, not by the session's start date.

If the destination diary does not exist, the script uses the configured template and substitutes the destination date. Without a template it creates frontmatter plus a minimal `## Memo` section. In `--write` mode, a summarized day with no agent messages is recorded in state without creating or modifying a diary note.

For a next-day section, set the offset to `1`, set `CTX_DAILY_DIGEST_HEADING` to the desired heading, and place the digest markers near the top of the diary template. The model's first Markdown H2 is normalized to the configured heading, so a built-in prompt cannot leave a duplicate language-specific heading. Rolling lookback refreshes skip dates previously tracked with another offset, preventing an offset change from silently migrating older summaries.

### Multiple computers

On every computer:

1. use the same Obsidian vault through your chosen synchronization service;
2. install and run the hourly session exporter;
3. give the computers distinct hostnames if you want filenames and `pc` frontmatter to distinguish them.

Choose one digest coordinator and put its short hostname in every computer's config:

```sh
CTX_DAILY_DIGEST_HOST=my-laptop
```

A nonmatching host exits successfully before calling Pi or touching the diary. Manual Python digest commands are not restricted by this setting; the restriction is implemented by `ctx_daily_digest.sh`.

## launchd and background operation

`install.sh` links the scripts into `~/bin/`, renders two plists into `~/Library/LaunchAgents/`, and creates the config template. Existing different files are moved or copied to timestamped `.backup-*` files.

With the default label prefix, the agents are:

| Label | Schedule | Command | Logs |
| --- | --- | --- | --- |
| `local.ctx-obsidian-sync` | at load, then every 3,600 seconds | `~/bin/ctx_obsidian_sync.sh` | `/tmp/ctx-obsidian-sync.out`, `/tmp/ctx-obsidian-sync.err` |
| `local.ctx-daily-digest` | 03:10 local time | `~/bin/ctx_daily_digest.sh` | `/tmp/ctx-daily-digest.out`, `/tmp/ctx-daily-digest.err` |

The daily wrapper always checks yesterday. It also revisits the two preceding dates only when they already exist in `daily-digests.json`, which catches late imports or delayed vault synchronization without performing an unexpected historical backfill.

Configuration changes are read on the next run. The exception is `CTX_OBSIDIAN_LAUNCHD_PREFIX`, because labels and plist filenames are rendered at install time.

Inspect the agents, substituting your configured prefix when necessary:

```bash
launchctl print "gui/$(id -u)/local.ctx-obsidian-sync"
launchctl print "gui/$(id -u)/local.ctx-daily-digest"
```

Reload both after changing the checkout or launchd prefix:

```bash
cd ~/ctx-obsidian-sync
./install.sh --load
```

To run only session export in the background, do not use `--load`; bootstrap only its rendered plist. This example uses the default prefix, so adjust the filename if you changed it:

```bash
launchctl bootstrap "gui/$(id -u)" \
  "$HOME/Library/LaunchAgents/local.ctx-obsidian-sync.plist"
```

## Recovery and uninstall

### Rebuild session export state

Back up the state file and rerun the desired date range:

```bash
mv ~/.local/state/ctx-obsidian-sync/synced.json \
  ~/.local/state/ctx-obsidian-sync/synced.json.backup
python3 ~/bin/ctx_to_obsidian_sessions.py \
  --write --since 2026-01-01 --until 2026-01-31
```

Existing notes are matched by ctx session ID and, when available, provider-native session ID. If more than one note matches the same identity, the exporter fails closed; remove or repair the duplicate before retrying.

### Regenerate a digest

```bash
python3 ~/bin/ctx_daily_digest.py --write --force --date 2026-11-06
```

The wrapper uses `~/.local/state/ctx-obsidian-sync/daily-digest.lock/` to prevent concurrent scheduled runs. It automatically removes a lock whose recorded process is no longer running. Do not remove the lock while a digest process is active.

If digest markers are duplicated or unbalanced, repair the diary manually so that it contains either no markers or exactly one start/end pair.

### Unload and remove this project

First determine the prefix used when the plists were rendered. The following uses the default `local`:

```bash
prefix=local
domain="gui/$(id -u)"
launchctl bootout "$domain/$prefix.ctx-obsidian-sync" 2>/dev/null || true
launchctl bootout "$domain/$prefix.ctx-daily-digest" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$prefix.ctx-obsidian-sync.plist"
rm -f "$HOME/Library/LaunchAgents/$prefix.ctx-daily-digest.plist"
```

Inspect `~/bin/` first, then remove the symlinks installed by this repository:

```bash
ls -l ~/bin/ctx_* ~/bin/README.ctx-obsidian-sync.md
rm -f \
  ~/bin/ctx_obsidian_sync.sh \
  ~/bin/ctx_core.py \
  ~/bin/ctx_config.py \
  ~/bin/ctx_config.sh \
  ~/bin/ctx_to_obsidian_sessions.py \
  ~/bin/ctx_daily_digest.sh \
  ~/bin/ctx_daily_digest.py \
  ~/bin/README.ctx-obsidian-sync.md
```

Optionally remove local configuration and state after backing them up:

```bash
rm -rf ~/.config/ctx-obsidian-sync
rm -rf ~/.local/state/ctx-obsidian-sync
```

Uninstalling does not remove ctx, Pi, their histories or credentials, generated `ctx-sessions` notes, diary summary blocks, or timestamped backups made by the installer.

## Security and privacy

- Agent transcripts and generated notes can contain source code, prompts, secrets, paths, and personal data. Review the destination vault and its synchronization policy.
- Session export does not use a model. Daily digest generation sends selected transcript text to Pi's configured provider.
- ctx has its own provider-discovery, storage, and analytics configuration. This project changes only ctx's indexing mode; review `~/.ctx/config.toml` separately.
- The digest invokes Pi with `--no-tools --no-session`, so Pi tools are disabled and the digest is not saved as a Pi session. The script does not pass `--no-extensions`, `--no-mcp`, `--no-skills`, or `--no-context-files`; audit your Pi installation if third-party extensions or context loading are a concern.
- The built-in prompt tells the model to treat transcript content as untrusted data, but no prompt can guarantee resistance to prompt injection. Inspect with `--source-only` and test without `--write` before enabling automation.
- Configuration, state, notes, and logs use the permissions produced by your account and `umask`; this project does not encrypt them or tighten permissions.
- The repository contains no transcripts, ctx database, generated notes, credentials, or machine-local configuration. Keep those files out of commits.

## Limitations

- ctx 2.4.1 is the only supported API version.
- Scheduled installation is macOS-only.
- The hourly job scans a rolling three-day event window, not all history. Use explicit dates for a backfill.
- Session-note bodies are truncated by default; ctx retains the complete transcript.
- Digest extraction also shortens long events and caps the assembled input (`--max-event-chars` defaults to 6,000 and `--max-input-chars` to 180,000).
- `ctx-sessions/` is generated output. Matching notes may be overwritten, and the scheduled workflow does not provide a configurable sessions-directory setting.
- Diary content is protected only outside the marker-delimited block.
- Vault synchronization and conflict resolution are delegated to Dropbox, iCloud, Git, or whichever service you use.
- Built-in digest prompts are English and Japanese only. Other languages require a custom prompt file.
- Hostnames are part of generated filenames. Renaming a computer changes the identifier used for future notes.
- The exporter uses the first eight characters of the ctx session ID in new filenames. A prefix collision on the same date, host, and provider is not specially handled.

## Troubleshooting

### `OBSIDIAN_VAULT is not set`

Edit `~/.config/ctx-obsidian-sync/config` and use a real path or a leading `~/`. Do not use `$HOME`, because the config parser does not perform shell expansion.

### `ctx binary not found` or ctx JSON contract errors

Confirm the supported version and location:

```bash
ctx --version
command -v ctx
```

The scripts search `PATH` and then `~/.local/bin/ctx`. The expected version is 2.4.1.

### No session notes were written

Run the complete foreground wrapper and inspect stderr:

```bash
~/bin/ctx_obsidian_sync.sh
```

Then check the date range and timezone with a dry run:

```bash
python3 ~/bin/ctx_to_obsidian_sessions.py \
  --since 2026-11-01 --until 2026-11-08
```

The hourly wrapper covers only today and the previous two days. Historical sessions need an explicit range.

If the refresh itself fails, run the exact import separately:

```bash
ctx import --all --format json --progress none
```

The wrapper continues when ctx publishes a usable generation with isolated record rejections or independent source failures. It stops when no usable Core generation is published.

### LaunchAgent is missing or failing

Check the rendered plist, launchd state, and logs:

```bash
plutil -lint ~/Library/LaunchAgents/local.ctx-obsidian-sync.plist
launchctl print "gui/$(id -u)/local.ctx-obsidian-sync"
tail -n 100 /tmp/ctx-obsidian-sync.err

tail -n 100 /tmp/ctx-daily-digest.err
```

If the repository moved, rerun `install.sh`. If the label prefix changed, unload and remove the old labels explicitly; the installer warns about them but does not delete them.

### Digest says `[skip]`

Common reasons are:

- the current host does not match `CTX_DAILY_DIGEST_HOST`;
- the day has no extracted agent messages;
- the source hash is unchanged and the diary already contains markers;
- an older lookback date has never been tracked.

Use a manual command with `--force` when regeneration is intentional. `CTX_DAILY_DIGEST_HOST` affects only the shell wrapper, not direct Python commands.

### Digest generation fails

Check Pi independently and then run a dry digest:

```bash
pi --version
pi
python3 ~/bin/ctx_daily_digest.py --date 2026-11-06
```

Typical causes are missing provider authentication, an invalid model name, an unreadable custom prompt, a timeout, or unavailable synchronized session notes. For launchd jobs, the digest also adds common Pi, fnm, nvm, Volta, mise, asdf, Homebrew, and `/usr/local` runtime directories to `PATH`, because GUI jobs do not load interactive shell version-manager setup.

### Duplicate session match or duplicate digest markers

These are intentional fail-closed conditions. Back up the affected files, remove the duplicate identity or repair the marker pair, and rerun the command.

## Development and tests

Run the test suite from the repository root:

```bash
python3 -m unittest discover tests
```

The tests use temporary directories and fake ctx binaries. They do not read the user's config file, ctx index, or Obsidian vault.

Useful syntax and template checks:

```bash
python3 -m py_compile bin/*.py
bash -n install.sh bin/*.sh
plutil -lint launchd/*.plist.in
```

These checks do not read the user's ctx index, configuration, or vault.

## License

[MIT](LICENSE)
