#!/usr/bin/env python3
"""User configuration shared by the ctx-obsidian-sync scripts.

Settings come from, in decreasing precedence:

  1. command-line flags of the individual scripts
  2. environment variables (set, even to an empty string)
  3. the config file, ``$CTX_OBSIDIAN_SYNC_CONFIG`` or
     ``${XDG_CONFIG_HOME:-~/.config}/ctx-obsidian-sync/config``
  4. built-in defaults

The config file uses the environment-variable names as keys, one ``KEY=value``
per line. ``bin/ctx_config.sh`` implements the same rules for the shell
wrappers, so a value means the same thing to bash and to Python. The file is
parsed, never executed.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Dict, Mapping, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Keep in sync with CTX_CONFIG_KEYS in ctx_config.sh and with config.example.
CONFIG_KEYS = frozenset({
    "OBSIDIAN_VAULT",
    "CTX_OBSIDIAN_TZ",
    "CTX_OBSIDIAN_DIARY_DIR",
    "CTX_OBSIDIAN_DIARY_TEMPLATE",
    "CTX_OBSIDIAN_LAUNCHD_PREFIX",
    "CTX_DAILY_DIGEST_HOST",
    "CTX_DAILY_DIGEST_MODEL",
    "CTX_DAILY_DIGEST_LANGUAGE",
    "CTX_DAILY_DIGEST_PROMPT_FILE",
    "CTX_DAILY_DIGEST_HEADING",
    "CTX_DAILY_DIGEST_DIARY_OFFSET_DAYS",
    "CTX_DAILY_DIGEST_UPDATE_PROJECT_STATUS",
    "CTX_DAILY_DIGEST_EXCLUDE_PROVIDERS",
})

DEFAULT_DIARY_DIR = "Diary"
DEFAULT_LAUNCHD_PREFIX = "local"
DEFAULT_DIGEST_LANGUAGE = "en"

_LINE_RE = re.compile(r"^(?:export\s+)?([A-Z_][A-Z0-9_]*)=(.*)$")


class ConfigError(RuntimeError):
    pass


def config_path(environ: Optional[Mapping[str, str]] = None) -> Path:
    env = os.environ if environ is None else environ
    explicit = env.get("CTX_OBSIDIAN_SYNC_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    base = env.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base).expanduser() / "ctx-obsidian-sync" / "config"


def _parse_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    else:
        # Unquoted values may carry a trailing " # comment".
        value = re.split(r"\s#", value, maxsplit=1)[0].rstrip()
    if value == "~" or value.startswith("~/"):
        value = str(Path.home()) + value[1:]
    return value


def parse_config(text: str, *, source: str = "config") -> Dict[str, str]:
    values: Dict[str, str] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _LINE_RE.match(stripped)
        if not match:
            print(f"warn: {source}:{number}: ignoring malformed line", file=sys.stderr)
            continue
        key, raw = match.groups()
        if key not in CONFIG_KEYS:
            print(f"warn: {source}:{number}: ignoring unknown key {key}", file=sys.stderr)
            continue
        values[key] = _parse_value(raw)
    return values


def load_config(path: Optional[Path] = None) -> Dict[str, str]:
    path = config_path() if path is None else path
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as exc:
        print(f"warn: cannot read {path}: {exc}", file=sys.stderr)
        return {}
    return parse_config(text, source=str(path))


def setting(
    name: str,
    config: Mapping[str, str],
    default: Optional[str] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Optional[str]:
    env = os.environ if environ is None else environ
    if name in env:
        return env[name]
    if name in config:
        return config[name]
    return default


def _valid_zone(name: str) -> bool:
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def local_timezone_name(environ: Optional[Mapping[str, str]] = None) -> str:
    """Best-effort IANA name of the system timezone; UTC when unknown."""
    env = os.environ if environ is None else environ
    tz = (env.get("TZ") or "").lstrip(":")
    if tz and _valid_zone(tz):
        return tz
    try:
        target = os.path.realpath("/etc/localtime")
    except OSError:
        target = ""
    if "zoneinfo/" in target:
        name = target.split("zoneinfo/", 1)[1]
        if _valid_zone(name):
            return name
    return "UTC"


def resolve_timezone(config: Mapping[str, str], environ: Optional[Mapping[str, str]] = None) -> str:
    env = os.environ if environ is None else environ
    for name in ("CTX_OBSIDIAN_TZ", "AI_OBSIDIAN_TZ"):
        if env.get(name):
            return env[name]
    if config.get("CTX_OBSIDIAN_TZ"):
        return config["CTX_OBSIDIAN_TZ"]
    return local_timezone_name(env)


def require_vault(explicit: Optional[str], config: Mapping[str, str]) -> Path:
    vault = explicit or setting("OBSIDIAN_VAULT", config)
    if not vault:
        raise ConfigError(
            "Obsidian vault is not configured: pass --vault, set OBSIDIAN_VAULT, "
            f"or add OBSIDIAN_VAULT=... to {config_path()}"
        )
    return Path(vault).expanduser()


def main(argv: list[str]) -> int:
    """Tiny CLI used by install.sh: ``ctx_config.py get KEY [DEFAULT]`` / ``path``."""
    if len(argv) >= 2 and argv[0] == "get" and argv[1] in CONFIG_KEYS:
        value = setting(argv[1], load_config(), argv[2] if len(argv) > 2 else "")
        print(value or "")
        return 0
    if argv == ["path"]:
        print(config_path())
        return 0
    print("usage: ctx_config.py get KEY [DEFAULT] | path", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
