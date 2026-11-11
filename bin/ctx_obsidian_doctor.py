#!/usr/bin/env python3
"""Validate the Obsidian-side contract used by ctx-obsidian-sync."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Mapping, Optional

from ctx_config import ConfigError, load_config, require_vault, setting
from ctx_core import CtxError
from ctx_to_obsidian_sessions import (
    DEFAULT_PROJECTS_DIR,
    DEFAULT_SESSIONS_DIR,
    load_projects,
)

START_MARKER = "<!-- ctx-daily-summary:start -->"
END_MARKER = "<!-- ctx-daily-summary:end -->"
PROJECT_STATUS_START = "<!-- ctx-project-status:start -->"
PROJECT_STATUS_END = "<!-- ctx-project-status:end -->"


def check_vault(
    vault: Path,
    config: Mapping[str, str],
    *,
    projects_dir: str = DEFAULT_PROJECTS_DIR,
    sessions_dir: str = DEFAULT_SESSIONS_DIR,
    diary_template: Optional[str] = None,
    out=sys.stdout,
) -> int:
    errors = 0

    if not vault.is_dir():
        print(f"[error] vault directory does not exist: {vault}", file=out)
        return 1
    print(f"[ok] vault: {vault}", file=out)

    project_path = vault / projects_dir
    try:
        projects = load_projects(project_path)
    except (CtxError, OSError) as exc:
        print(f"[error] project definitions: {exc}", file=out)
        errors += 1
    else:
        print(f"[ok] project definitions: {len(projects)} in {project_path}", file=out)
        for project in projects:
            if not project.roots and not project.root_prefixes:
                print(
                    f"[warn] project {project.project_id} has no roots or root_prefixes",
                    file=out,
                )
        project_status_enabled = (
            (setting("CTX_DAILY_DIGEST_UPDATE_PROJECT_STATUS", config, "false") or "")
            .strip()
            .lower()
            in {"1", "true", "yes", "on"}
        )
        if project_status_enabled:
            marker_errors = 0
            for project in projects:
                note_path = project_path / f"{project.note}.md"
                text = note_path.read_text(encoding="utf-8", errors="replace")
                ordered = text.find(PROJECT_STATUS_START) < text.find(PROJECT_STATUS_END)
                before_locations = text.find("## Current status") < text.find("## Project locations")
                if (
                    text.count(PROJECT_STATUS_START) != 1
                    or text.count(PROJECT_STATUS_END) != 1
                    or not ordered
                    or not before_locations
                ):
                    print(
                        f"[error] Project status markers or section order are invalid: {note_path}",
                        file=out,
                    )
                    marker_errors += 1
            errors += marker_errors
            if marker_errors == 0:
                print(f"[ok] Project status markers: {len(projects)} note(s)", file=out)

    session_path = vault / sessions_dir
    if session_path.is_dir():
        count = sum(1 for _path in session_path.glob("*.md"))
        print(f"[ok] session output: {count} note(s) in {session_path}", file=out)
    else:
        print(f"[warn] session output directory does not exist: {session_path}", file=out)

    template_setting = diary_template
    if template_setting is None:
        template_setting = setting("CTX_OBSIDIAN_DIARY_TEMPLATE", config)
    if not template_setting:
        print("[warn] diary template is not configured", file=out)
    else:
        template_path = Path(template_setting).expanduser()
        if not template_path.is_absolute():
            template_path = vault / template_path
        try:
            text = template_path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"[error] diary template cannot be read: {template_path}: {exc}", file=out)
            errors += 1
        else:
            start_count = text.count(START_MARKER)
            end_count = text.count(END_MARKER)
            ordered = text.find(START_MARKER) < text.find(END_MARKER)
            if start_count != 1 or end_count != 1 or not ordered:
                print(
                    "[error] diary template must contain exactly one ordered "
                    f"summary marker pair: {template_path}",
                    file=out,
                )
                errors += 1
            else:
                print(f"[ok] diary summary markers: {template_path}", file=out)

    if errors:
        print(f"[result] failed with {errors} error(s)", file=out)
        return 1
    print("[result] ok", file=out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate project definitions, session output, and diary markers."
    )
    parser.add_argument("--vault", help="Vault root (default: OBSIDIAN_VAULT from config).")
    parser.add_argument(
        "--projects-dir",
        default=DEFAULT_PROJECTS_DIR,
        help=f"Project directory under the vault (default: {DEFAULT_PROJECTS_DIR}).",
    )
    parser.add_argument(
        "--sessions-dir",
        default=DEFAULT_SESSIONS_DIR,
        help=f"Session directory under the vault (default: {DEFAULT_SESSIONS_DIR}).",
    )
    parser.add_argument(
        "--diary-template",
        help="Diary template path, absolute or vault-relative (default: configured value).",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = load_config()
    try:
        vault = require_vault(args.vault, config)
    except ConfigError as exc:
        print(f"ctx_obsidian_doctor.py: {exc}", file=sys.stderr)
        return 2
    return check_vault(
        vault,
        config,
        projects_dir=args.projects_dir,
        sessions_dir=args.sessions_dir,
        diary_template=args.diary_template,
    )


if __name__ == "__main__":
    raise SystemExit(main())
