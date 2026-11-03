#!/usr/bin/env python3
"""Generate Obsidian session notes from the local ctx index.

Pipeline:
  1. query ctx_sessions for the target day(s) (TZ-aware) via `ctx sql`
  2. render each session as markdown: YAML frontmatter + cleaned
     `ctx show session --mode full --format markdown` body

The ctx index is the single source of truth. This script only reads it.

Modes:
  dry-run (default): print notes to stdout, do NOT touch the vault.
  --write:           write one note per session to <vault>/<sessions-dir>/
                     and record synced ctx_session_ids in a state file so
                     re-runs are idempotent.

Examples:
  # dry-run, pi only, today
  python3 ctx_to_obsidian_sessions.py --provider pi
  # write all providers for a range
  python3 ctx_to_obsidian_sessions.py --write --since 2026-05-19 --until 2026-07-03
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HOME = Path.home()
DEFAULT_CTX_BIN = str(HOME / ".local" / "bin" / "ctx")
DEFAULT_TZ = os.environ.get("AI_OBSIDIAN_TZ") or os.environ.get("CTX_OBSIDIAN_TZ") or "Europe/London"
DEFAULT_VAULT = os.environ.get("OBSIDIAN_VAULT") or str(HOME / "Dropbox" / "Obsidian" / "ObsidianVault")
DEFAULT_SESSIONS_DIR = "ctx-sessions"
DEFAULT_STATE = str(HOME / ".local" / "state" / "ctx-obsidian-sync" / "synced.json")


def find_ctx_bin(explicit: str | None) -> str:
    if explicit:
        if Path(explicit).exists():
            return explicit
        raise FileNotFoundError(f"ctx binary not found at {explicit}")
    for d in os.environ.get("PATH", "").split(os.pathsep):
        p = Path(d) / "ctx"
        if p.exists():
            return str(p)
    if Path(DEFAULT_CTX_BIN).exists():
        return DEFAULT_CTX_BIN
    raise FileNotFoundError("ctx binary not found on PATH or at ~/.local/bin/ctx")


def run_ctx(ctx_bin: str, args: list[str]) -> str:
    proc = subprocess.run([ctx_bin, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ctx {' '.join(args)} failed (rc={proc.returncode}):\n{proc.stderr}")
    return proc.stdout


def ctx_sql_rows(ctx_bin: str, sql: str) -> list[dict]:
    out = run_ctx(ctx_bin, ["sql", sql, "--format", "json", "--max-rows", "5000"])
    data = json.loads(out)
    cols = data.get("columns", [])
    return [dict(zip(cols, row)) for row in data.get("rows", [])]


def session_markdown(ctx_bin: str, ctx_session_id: str) -> str:
    return run_ctx(
        ctx_bin,
        ["show", "session", ctx_session_id, "--mode", "full", "--format", "markdown"],
    )


def clean_body(body: str) -> str:
    """Strip the ctx header block (title + meta list) and per-event ctx_event_id lines.

    The ctx `show session` markdown starts with:
        # <provider> session <id>

        - ctx_session_id: `...`
        - provider: `...`
        ...
    followed by `## <role> - <type> - <ts>` event sections, each preceded by a
    `ctx_event_id: ` line. We drop everything before the first `## ` heading,
    and remove each `ctx_event_id:` line plus the blank line that follows it.
    """
    raw = body.split("\n")
    out: list[str] = []
    in_header = True
    i, n = 0, len(raw)
    while i < n:
        line = raw[i]
        if in_header:
            if line.startswith("## "):
                in_header = False
                out.append(line)
            i += 1
            continue
        if line.startswith("ctx_event_id:"):
            i += 1
            if i < n and raw[i].strip() == "":
                i += 1
            continue
        out.append(line)
        i += 1
    while out and out[0].strip() == "":
        out.pop(0)
    while out and out[-1].strip() == "":
        out.pop()
    return "\n".join(out)


def truncate_body(body: str, max_chars: int, session_id: str) -> str:
    """Truncate to ~max_chars on a line boundary; keep the full transcript in ctx."""
    if max_chars <= 0 or len(body) <= max_chars:
        return body
    cut = body[:max_chars]
    last_nl = cut.rfind("\n")
    if last_nl > max_chars // 2:
        cut = cut[:last_nl]
    return (
        cut.rstrip()
        + f"\n\n> [!info] Truncated at ~{max_chars} chars. "
        f"Full transcript: `ctx show session {session_id}`"
    )


def short_path(path: str | None) -> str:
    if not path:
        return ""
    text = str(path)
    home = str(HOME)
    if text == home:
        return "~"
    if text.startswith(home + "/"):
        return "~/" + text[len(home) + 1:]
    return text


def dt_from_ms(ms: int, tz: ZoneInfo) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz)


def build_frontmatter(row: dict, pc_id: str, tz: ZoneInfo) -> str:
    started = dt_from_ms(int(row["started_at_ms"]), tz)
    lines = ["---"]
    lines.append(f"date: {started.strftime('%Y-%m-%d')}")
    lines.append(f"pc: {pc_id}")
    lines.append(f"provider: {row['provider']}")
    lines.append(f"started: {started.isoformat()}")
    if row.get("cwd"):
        lines.append(f"cwd: {short_path(row['cwd'])}")
    lines.append(f"session_id: {row['ctx_session_id']}")
    if row.get("provider_session_id"):
        lines.append(f"provider_session_id: {row['provider_session_id']}")
    lines.append("---")
    return "\n".join(lines)


def note_filename(row: dict, pc_id: str, tz: ZoneInfo) -> str:
    date = dt_from_ms(int(row["started_at_ms"]), tz).strftime("%Y-%m-%d")
    sid8 = row["ctx_session_id"][:8]
    return f"{date}__{pc_id}__{row['provider']}__{sid8}.md"


def day_range_ms(day_str: str, tz: ZoneInfo) -> tuple[int, int]:
    day = datetime.strptime(day_str, "%Y-%m-%d").replace(tzinfo=tz)
    start_ms = int(day.timestamp() * 1000)
    end_ms = int((day + timedelta(days=1)).timestamp() * 1000)
    return start_ms, end_ms


def fetch_sessions(ctx_bin: str, since: str, until: str, provider: str | None, tz: ZoneInfo) -> list[dict]:
    start_ms, _ = day_range_ms(since, tz)
    _, end_ms = day_range_ms(until, tz)
    clauses = [f"started_at_ms >= {start_ms}", f"started_at_ms < {end_ms}"]
    if provider:
        clauses.append(f"provider = '{provider}'")
    where = " AND ".join(clauses)
    sql = (
        "SELECT s.ctx_session_id, s.provider, s.provider_session_id, s.started_at_ms, s.cwd, "
        "(SELECT COUNT(*) FROM ctx_events e WHERE e.ctx_session_id = s.ctx_session_id) AS event_count "
        f"FROM ctx_sessions s WHERE {where} ORDER BY s.started_at_ms ASC"
    )
    return ctx_sql_rows(ctx_bin, sql)


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"synced": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"synced": {}}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def render_note(ctx_bin: str, row: dict, pc_id: str, tz: ZoneInfo, max_body_chars: int) -> str | None:
    """Return the note text, or None if the session has no body (empty)."""
    body = clean_body(session_markdown(ctx_bin, row["ctx_session_id"]))
    if not body.strip():
        return None
    body = truncate_body(body, max_body_chars, row["ctx_session_id"])
    fm = build_frontmatter(row, pc_id, tz)
    return fm + "\n\n" + body.rstrip() + "\n"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Render ctx sessions as Obsidian markdown notes.")
    p.add_argument("--ctx-bin", help="Path to ctx binary (default: search PATH / ~/.local/bin/ctx).")
    p.add_argument("--timezone", default=DEFAULT_TZ, help=f"TZ for day boundary (default: {DEFAULT_TZ}).")
    p.add_argument("--since", help="Start date YYYY-MM-DD (default: today in TZ).")
    p.add_argument("--until", help="End date YYYY-MM-DD inclusive (default: --since).")
    p.add_argument("--provider", help="Filter to one provider (pi, codex, claude, opencode, ...).")
    p.add_argument("--pc-id", default=socket.gethostname().split(".")[0], help="PC identifier (default: hostname -s).")
    p.add_argument("--write", action="store_true", help="Write notes to the vault (default: dry-run to stdout).")
    p.add_argument("--vault", default=DEFAULT_VAULT, help=f"Vault root (default: {DEFAULT_VAULT}).")
    p.add_argument("--sessions-dir", default=DEFAULT_SESSIONS_DIR, help=f"Folder under vault (default: {DEFAULT_SESSIONS_DIR}).")
    p.add_argument("--state", default=DEFAULT_STATE, help=f"Synced-state JSON path (default: {DEFAULT_STATE}).")
    p.add_argument("--max-body-chars", type=int, default=8000, help="Truncate body to ~N chars (default: 8000). Full transcript stays in the ctx DB.")
    return p


def run_dry(ctx_bin: str, sessions: list[dict], pc_id: str, tz: ZoneInfo, label: str, max_body_chars: int) -> int:
    parts: list[str] = []
    for row in sessions:
        try:
            note = render_note(ctx_bin, row, pc_id, tz, max_body_chars)
        except RuntimeError as exc:
            print(f"warn: skip {row['ctx_session_id']}: {exc}", file=sys.stderr)
            continue
        if note is None:
            continue
        parts.append(note.rstrip())
    sys.stdout.write("\n\n---\n\n".join(parts) + ("\n" if parts else ""))
    print(f"[dry-run] {len(parts)} note(s) {label}", file=sys.stderr)
    return 0


def run_write(ctx_bin: str, sessions: list[dict], pc_id: str, tz: ZoneInfo, label: str,
              sessions_dir: Path, state_path: Path, max_body_chars: int) -> int:
    state = load_state(state_path)
    synced = state.setdefault("synced", {})
    sessions_dir.mkdir(parents=True, exist_ok=True)
    written = updated = skipped_synced = skipped_empty = failed = 0
    for row in sessions:
        sid = row["ctx_session_id"]
        ev_count = int(row.get("event_count") or 0)
        prev = synced.get(sid)
        if isinstance(prev, dict) and prev.get("event_count") == ev_count:
            skipped_synced += 1
            continue
        try:
            note = render_note(ctx_bin, row, pc_id, tz, max_body_chars)
        except RuntimeError as exc:
            print(f"warn: skip {sid}: {exc}", file=sys.stderr)
            failed += 1
            continue
        if note is None:
            skipped_empty += 1
            continue
        path = sessions_dir / note_filename(row, pc_id, tz)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(note, encoding="utf-8")
        tmp.replace(path)
        synced[sid] = {"synced_at": datetime.now(timezone.utc).isoformat(), "event_count": ev_count}
        if prev is None:
            written += 1
        else:
            updated += 1
    save_state(state_path, state)
    print(
        f"[write] written={written} updated={updated} skipped_synced={skipped_synced} "
        f"skipped_empty={skipped_empty} failed={failed} dir={sessions_dir} {label}",
        file=sys.stderr,
    )
    return 0


def main() -> int:
    args = build_parser().parse_args()
    tz = ZoneInfo(args.timezone)
    today = datetime.now(tz).strftime("%Y-%m-%d")
    since = args.since or today
    until = args.until or since
    label = f"for {since}..{until}{' provider=' + args.provider if args.provider else ''} pc={args.pc_id} tz={args.timezone}"

    try:
        ctx_bin = find_ctx_bin(args.ctx_bin)
    except FileNotFoundError as exc:
        print(f"ctx_to_obsidian_sessions.py: {exc}", file=sys.stderr)
        return 1

    try:
        sessions = fetch_sessions(ctx_bin, since, until, args.provider, tz)
    except (RuntimeError, json.JSONDecodeError) as exc:
        print(f"ctx_to_obsidian_sessions.py: query failed: {exc}", file=sys.stderr)
        return 1

    if not sessions:
        print(f"No sessions {label}.", file=sys.stderr)
        return 0

    if args.write:
        sessions_dir = Path(args.vault).expanduser() / args.sessions_dir
        return run_write(ctx_bin, sessions, args.pc_id, tz, label, sessions_dir, Path(args.state).expanduser(), args.max_body_chars)
    return run_dry(ctx_bin, sessions, args.pc_id, tz, label, args.max_body_chars)


if __name__ == "__main__":
    raise SystemExit(main())
