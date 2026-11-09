#!/usr/bin/env python3
"""Generate Obsidian session notes from the local ctx Core index.

Pipeline:
  1. enumerate a complete calendar window with cursor-safe `ctx list events`
  2. obtain stable metadata and event counts from `ctx show session --format json`
  3. render each session as markdown: YAML frontmatter + cleaned
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
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from ctx_config import ConfigError, load_config, require_vault, resolve_timezone
from ctx_core import CtxError, run_ctx, session_summaries_for_dates

HOME = Path.home()
DEFAULT_CTX_BIN = str(HOME / ".local" / "bin" / "ctx")
DEFAULT_SESSIONS_DIR = "ctx-sessions"
DEFAULT_PROJECTS_DIR = "03_Projects"
DEFAULT_STATE = str(HOME / ".local" / "state" / "ctx-obsidian-sync" / "synced.json")


@dataclass(frozen=True)
class ProjectRef:
    project_id: str
    note: str
    roots: Tuple[str, ...]


def yaml_scalar(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"\"", "'"}:
        return value[1:-1]
    return value


def project_from_note(path: Path) -> Optional[ProjectRef]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if not lines or lines[0].strip() != "---":
        return None
    project_id = ""
    roots: List[str] = []
    list_key = ""
    for line in lines[1:]:
        if line.strip() == "---":
            break
        stripped = line.strip()
        if stripped.startswith("-") and list_key == "roots":
            root = yaml_scalar(stripped[1:]).rstrip("/")
            if root:
                roots.append(root)
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()
        list_key = key if not value else ""
        if key == "project_id":
            project_id = yaml_scalar(value)
    if not project_id:
        return None
    return ProjectRef(project_id=project_id, note=path.stem, roots=tuple(roots))


def load_projects(projects_dir: Path) -> List[ProjectRef]:
    if not projects_dir.exists():
        return []
    projects: List[ProjectRef] = []
    ids: Dict[str, Path] = {}
    roots: Dict[str, Path] = {}
    for path in sorted(projects_dir.glob("*.md")):
        project = project_from_note(path)
        if project is None:
            continue
        if project.project_id in ids:
            raise CtxError(
                "duplicate project_id {} in {} and {}".format(
                    project.project_id, ids[project.project_id], path
                )
            )
        ids[project.project_id] = path
        for root in project.roots:
            if root in roots:
                raise CtxError("duplicate project root {} in {} and {}".format(root, roots[root], path))
            roots[root] = path
        projects.append(project)
    return projects


def project_for_cwd(cwd: Optional[str], projects: List[ProjectRef]) -> Optional[ProjectRef]:
    if not cwd:
        return None
    matches = [
        (len(root), project)
        for project in projects
        for root in project.roots
        if cwd == root or cwd.startswith(root + "/")
    ]
    if not matches:
        return None
    longest = max(length for length, _project in matches)
    winners = {project for length, project in matches if length == longest}
    if len(winners) != 1:
        raise CtxError("ambiguous project roots for cwd {}".format(cwd))
    return next(iter(winners))


def find_ctx_bin(explicit: Optional[str]) -> str:
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


def short_path(path: Optional[str]) -> str:
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
    if row.get("project_id"):
        lines.append(f"project_id: {row['project_id']}")
        lines.append(f'project: "[[{row["project_note"]}]]"')
    lines.append(f"session_id: {row['ctx_session_id']}")
    if row.get("provider_session_id"):
        lines.append(f"provider_session_id: {row['provider_session_id']}")
    lines.append("---")
    return "\n".join(lines)


def note_filename(row: dict, pc_id: str, tz: ZoneInfo) -> str:
    date = dt_from_ms(int(row["started_at_ms"]), tz).strftime("%Y-%m-%d")
    sid8 = row["ctx_session_id"][:8]
    return f"{date}__{pc_id}__{row['provider']}__{sid8}.md"


def fetch_sessions(ctx_bin: str, since: str, until: str, provider: Optional[str], tz: ZoneInfo) -> List[dict]:
    return session_summaries_for_dates(ctx_bin, since, until, provider, tz)


def load_state(path: Path) -> dict:
    if not path.exists():
        return {"synced": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CtxError("cannot read sync state {}: {}".format(path, exc))
    if not isinstance(state, dict) or not isinstance(state.get("synced", {}), dict):
        raise CtxError("sync state has an invalid shape: {}".format(path))
    return state


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def render_note(ctx_bin: str, row: dict, pc_id: str, tz: ZoneInfo, max_body_chars: int) -> Optional[str]:
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
    p.add_argument("--timezone", help="IANA TZ for day boundary (default: CTX_OBSIDIAN_TZ, then the system zone).")
    p.add_argument("--since", help="Start date YYYY-MM-DD (default: today in TZ).")
    p.add_argument("--until", help="End date YYYY-MM-DD inclusive (default: --since).")
    p.add_argument("--provider", help="Filter to one provider (pi, codex, claude, opencode, ...).")
    p.add_argument("--pc-id", default=socket.gethostname().split(".")[0], help="PC identifier (default: hostname -s).")
    p.add_argument("--write", action="store_true", help="Write notes to the vault (default: dry-run to stdout).")
    p.add_argument("--vault", help="Vault root (default: OBSIDIAN_VAULT from the environment or config file).")
    p.add_argument("--sessions-dir", default=DEFAULT_SESSIONS_DIR, help=f"Folder under vault (default: {DEFAULT_SESSIONS_DIR}).")
    p.add_argument("--projects-dir", default=DEFAULT_PROJECTS_DIR, help=f"Project notes folder under vault (default: {DEFAULT_PROJECTS_DIR}).")
    p.add_argument("--state", default=DEFAULT_STATE, help=f"Synced-state JSON path (default: {DEFAULT_STATE}).")
    p.add_argument("--max-body-chars", type=int, default=8000, help="Truncate body to ~N chars (default: 8000). Full transcript stays in the ctx DB.")
    return p


def run_dry(ctx_bin: str, sessions: List[dict], pc_id: str, tz: ZoneInfo, label: str, max_body_chars: int) -> int:
    parts: List[str] = []
    failed = 0
    for row in sessions:
        try:
            note = render_note(ctx_bin, row, pc_id, tz, max_body_chars)
        except CtxError as exc:
            print(f"warn: skip {row['ctx_session_id']}: {exc}", file=sys.stderr)
            failed += 1
            continue
        if note is None:
            continue
        parts.append(note.rstrip())
    sys.stdout.write("\n\n---\n\n".join(parts) + ("\n" if parts else ""))
    print(f"[dry-run] {len(parts)} note(s) failed={failed} {label}", file=sys.stderr)
    return 1 if failed else 0


def note_frontmatter(path: Path) -> Dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    if not lines or lines[0].strip() != "---":
        return {}
    meta: Dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip("\"'")
    return meta


def replace_project_frontmatter(path: Path, project: Optional[ProjectRef]) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return False
    try:
        end = next(i for i, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration:
        return False

    header = [
        line for line in lines[1:end]
        if line.split(":", 1)[0].strip() not in {"project_id", "project"}
    ]
    if project is not None:
        insert_at = len(header)
        for i, line in enumerate(header):
            if line.split(":", 1)[0].strip() == "cwd":
                insert_at = i + 1
                break
        header[insert_at:insert_at] = [
            "project_id: {}".format(project.project_id),
            'project: "[[{}]]"'.format(project.note),
        ]
    updated = "\n".join(["---", *header, "---", *lines[end + 1:]])
    if text.endswith("\n"):
        updated += "\n"
    if updated == text:
        return False
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(updated, encoding="utf-8")
    tmp.replace(path)
    return True


def refresh_project_links(sessions_dir: Path, projects: List[ProjectRef]) -> Tuple[int, int]:
    updated = failed = 0
    if not sessions_dir.exists():
        return updated, failed
    for path in sorted(sessions_dir.glob("*.md")):
        meta = note_frontmatter(path)
        cwd = meta.get("cwd")
        if not cwd:
            continue
        try:
            project = project_for_cwd(cwd, projects)
            if replace_project_frontmatter(path, project):
                updated += 1
        except (CtxError, OSError) as exc:
            print("warn: cannot refresh project link for {}: {}".format(path, exc), file=sys.stderr)
            failed += 1
    return updated, failed


def index_existing_notes(sessions_dir: Path) -> Tuple[Dict[str, List[Path]], Dict[Tuple[str, str], List[Path]], Dict[Path, Dict[str, str]]]:
    by_session: Dict[str, List[Path]] = {}
    by_provider_session: Dict[Tuple[str, str], List[Path]] = {}
    metadata: Dict[Path, Dict[str, str]] = {}
    if not sessions_dir.exists():
        return by_session, by_provider_session, metadata
    for path in sorted(sessions_dir.glob("*.md")):
        meta = note_frontmatter(path)
        metadata[path] = meta
        session_id = meta.get("session_id")
        if session_id:
            by_session.setdefault(session_id, []).append(path)
        provider = meta.get("provider")
        provider_session_id = meta.get("provider_session_id")
        if provider and provider_session_id:
            by_provider_session.setdefault((provider, provider_session_id), []).append(path)
    return by_session, by_provider_session, metadata


def unique_note(paths: List[Path], identity: str) -> Optional[Path]:
    unique = sorted(set(paths))
    if len(unique) > 1:
        raise CtxError("multiple existing notes match {}".format(identity))
    return unique[0] if unique else None


def run_write(ctx_bin: str, sessions: List[dict], pc_id: str, tz: ZoneInfo, label: str,
              sessions_dir: Path, state_path: Path, max_body_chars: int,
              projects: Optional[List[ProjectRef]] = None) -> int:
    state = load_state(state_path)
    synced = state.setdefault("synced", {})
    reconciled_ids = state.setdefault("reconciled_session_ids", {})
    projects = projects or []
    sessions_dir.mkdir(parents=True, exist_ok=True)
    by_session, by_provider_session, metadata = index_existing_notes(sessions_dir)
    written = updated = reconciled = skipped_synced = skipped_empty = failed = 0
    for row in sessions:
        sid = row["ctx_session_id"]
        ev_count = int(row.get("event_count") or 0)
        try:
            path = unique_note(by_session.get(sid, []), "ctx session {}".format(sid))
            if path is None and row.get("provider_session_id"):
                native_key = (str(row["provider"]), str(row["provider_session_id"]))
                path = unique_note(
                    by_provider_session.get(native_key, []),
                    "provider session {}/{}".format(*native_key),
                )
        except CtxError as exc:
            print(f"warn: skip {sid}: {exc}", file=sys.stderr)
            failed += 1
            continue

        previous_meta = metadata.get(path, {}) if path else {}
        previous_sid = previous_meta.get("session_id")
        render_cwd = short_path(row.get("cwd")) or previous_meta.get("cwd", "")
        try:
            project = project_for_cwd(render_cwd, projects)
        except CtxError as exc:
            print(f"warn: skip {sid}: {exc}", file=sys.stderr)
            failed += 1
            continue
        desired_project_id = project.project_id if project else ""
        desired_project_link = "[[{}]]".format(project.note) if project else ""
        prev = synced.get(sid)
        if (
            path is not None
            and previous_sid == sid
            and isinstance(prev, dict)
            and prev.get("event_count") == ev_count
            and previous_meta.get("project_id", "") == desired_project_id
            and previous_meta.get("project", "") == desired_project_link
        ):
            skipped_synced += 1
            continue

        render_row = dict(row)
        if not render_row.get("cwd") and previous_meta.get("cwd"):
            render_row["cwd"] = previous_meta["cwd"]
        if project:
            render_row["project_id"] = project.project_id
            render_row["project_note"] = project.note
        try:
            note = render_note(ctx_bin, render_row, pc_id, tz, max_body_chars)
        except CtxError as exc:
            print(f"warn: skip {sid}: {exc}", file=sys.stderr)
            failed += 1
            continue
        if note is None:
            skipped_empty += 1
            continue

        existed = path is not None
        if path is None:
            path = sessions_dir / note_filename(render_row, pc_id, tz)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(note, encoding="utf-8")
        tmp.replace(path)
        synced[sid] = {
            "synced_at": datetime.now(timezone.utc).isoformat(),
            "event_count": ev_count,
            "provider": row["provider"],
            "provider_session_id": row.get("provider_session_id"),
            "note": path.name,
        }
        if previous_sid and previous_sid != sid:
            reconciled_ids[previous_sid] = sid
            reconciled += 1
        new_meta = dict(previous_meta)
        new_meta.update({
            "session_id": sid,
            "provider": str(row["provider"]),
            "provider_session_id": str(row.get("provider_session_id") or ""),
            "project_id": desired_project_id,
            "project": desired_project_link,
        })
        metadata[path] = new_meta
        by_session.setdefault(sid, []).append(path)
        if row.get("provider_session_id"):
            by_provider_session.setdefault(
                (str(row["provider"]), str(row["provider_session_id"])), []
            ).append(path)
        if existed:
            updated += 1
        else:
            written += 1
    save_state(state_path, state)
    print(
        f"[write] written={written} updated={updated} reconciled={reconciled} "
        f"skipped_synced={skipped_synced} skipped_empty={skipped_empty} "
        f"failed={failed} dir={sessions_dir} {label}",
        file=sys.stderr,
    )
    return 1 if failed else 0


def main() -> int:
    args = build_parser().parse_args()
    config = load_config()
    args.timezone = args.timezone or resolve_timezone(config)
    tz = ZoneInfo(args.timezone)
    sessions_dir: Optional[Path] = None
    projects: List[ProjectRef] = []
    project_refresh_failed = 0
    if args.write:
        # Fail before querying ctx when there is nowhere to write.
        try:
            vault = require_vault(args.vault, config)
            sessions_dir = vault / args.sessions_dir
            projects_dir = vault / args.projects_dir
            projects = load_projects(projects_dir)
            if projects_dir.exists():
                refreshed, project_refresh_failed = refresh_project_links(sessions_dir, projects)
                print(
                    "[projects] definitions={} refreshed={} failed={}".format(
                        len(projects), refreshed, project_refresh_failed
                    ),
                    file=sys.stderr,
                )
        except (ConfigError, CtxError, OSError) as exc:
            print(f"ctx_to_obsidian_sessions.py: {exc}", file=sys.stderr)
            return 2
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
    except (CtxError, ValueError) as exc:
        print(f"ctx_to_obsidian_sessions.py: query failed: {exc}", file=sys.stderr)
        return 1

    if not sessions:
        print(f"No sessions {label}.", file=sys.stderr)
        return 1 if project_refresh_failed else 0

    if sessions_dir is not None:
        try:
            rc = run_write(
                ctx_bin, sessions, args.pc_id, tz, label, sessions_dir,
                Path(args.state).expanduser(), args.max_body_chars, projects,
            )
            return 1 if rc or project_refresh_failed else 0
        except (CtxError, OSError) as exc:
            print(f"ctx_to_obsidian_sessions.py: write failed: {exc}", file=sys.stderr)
            return 1
    return run_dry(ctx_bin, sessions, args.pc_id, tz, label, args.max_body_chars)


if __name__ == "__main__":
    raise SystemExit(main())
