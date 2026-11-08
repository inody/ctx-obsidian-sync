#!/usr/bin/env python3
"""Small, strict helpers for the ctx 2.4 Core/Tantivy JSON APIs."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional, Set, Tuple
from zoneinfo import ZoneInfo


class CtxError(RuntimeError):
    """Raised when ctx fails or violates its documented JSON contract."""


@dataclass(frozen=True)
class SessionRef:
    ctx_session_id: str
    provider: str
    provider_session_id: Optional[str]


@dataclass(frozen=True)
class SessionSummary:
    ctx_session_id: str
    provider: str
    provider_session_id: Optional[str]
    started_at_ms: int
    event_count: int
    cwd: Optional[str] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ctx_session_id": self.ctx_session_id,
            "provider": self.provider,
            "provider_session_id": self.provider_session_id,
            "started_at_ms": self.started_at_ms,
            "event_count": self.event_count,
            "cwd": self.cwd,
        }


def run_ctx(ctx_bin: str, args: List[str]) -> str:
    proc = subprocess.run([ctx_bin, *args], capture_output=True, text=True)
    if proc.returncode != 0:
        detail = proc.stderr.strip()
        raise CtxError(
            "ctx {} failed (rc={}): {}".format(" ".join(args), proc.returncode, detail)
        )
    return proc.stdout


def _utc_millis(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def calendar_date_bounds(since: str, until: str, tz: ZoneInfo) -> Tuple[str, str]:
    """Return exact UTC RFC3339 bounds for inclusive local calendar dates."""
    start = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=tz)
    last = datetime.strptime(until, "%Y-%m-%d").replace(tzinfo=tz)
    if last < start:
        raise ValueError("--until must not be earlier than --since")
    return _utc_millis(start), _utc_millis(last + timedelta(days=1))


def _parse_jsonl_page(output: str) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    completion: Optional[Dict[str, Any]] = None
    generation_id: Optional[str] = None
    expected_ordinal = 0

    for line_number, raw in enumerate(output.splitlines(), 1):
        if not raw.strip():
            raise CtxError("ctx list events returned a blank JSONL record at line {}".format(line_number))
        try:
            record = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CtxError("ctx list events returned invalid JSONL at line {}: {}".format(line_number, exc))
        if not isinstance(record, dict):
            raise CtxError("ctx list events returned a non-object JSONL record")
        record_type = record.get("record_type")
        if record_type == "event_range_event":
            if completion is not None:
                raise CtxError("ctx list events returned an event after completion")
            if record.get("ordinal") != expected_ordinal:
                raise CtxError("ctx list events returned noncontiguous ordinals")
            expected_ordinal += 1
            event = record.get("event")
            if not isinstance(event, dict):
                raise CtxError("ctx list events returned an event record without an event object")
            current_generation = record.get("generation_id")
            if not isinstance(current_generation, str) or not current_generation:
                raise CtxError("ctx list events omitted the generation id")
            if generation_id is None:
                generation_id = current_generation
            elif generation_id != current_generation:
                raise CtxError("ctx list events mixed Core generations")
            events.append(event)
        elif record_type == "event_range_completion":
            if completion is not None:
                raise CtxError("ctx list events returned multiple completion records")
            completion = record
            current_generation = record.get("generation_id")
            if not isinstance(current_generation, str) or not current_generation:
                raise CtxError("ctx list events completion omitted the generation id")
            if generation_id is not None and generation_id != current_generation:
                raise CtxError("ctx list events completion changed Core generation")
            generation_id = current_generation
        else:
            raise CtxError("ctx list events returned unknown record_type {!r}".format(record_type))

    if completion is None:
        raise CtxError("ctx list events ended without a completion record")
    usage = completion.get("usage")
    if not isinstance(usage, dict) or usage.get("items") != len(events):
        raise CtxError("ctx list events completion usage does not match emitted events")
    return events, completion


def iter_events(
    ctx_bin: str,
    since_rfc3339: str,
    until_rfc3339: str,
    provider: Optional[str] = None,
    *,
    content: str = "none",
    invocation_limit: int = 1000,
) -> Iterator[Dict[str, Any]]:
    """Enumerate a complete event window, following every opaque cursor."""
    if invocation_limit <= 0:
        raise ValueError("invocation_limit must be positive")
    cursor: Optional[str] = None
    seen_cursors: Set[str] = set()
    pinned_generation: Optional[str] = None

    while True:
        args = [
            "list", "events",
            "--since", since_rfc3339,
            "--until", until_rfc3339,
            "--direction", "ascending",
            "--content", content,
            "--limit", str(invocation_limit),
            "--format", "jsonl",
        ]
        if provider:
            args.extend(["--provider", provider])
        if cursor:
            args.extend(["--cursor", cursor])
        events, completion = _parse_jsonl_page(run_ctx(ctx_bin, args))
        generation = completion.get("generation_id")
        if pinned_generation is None:
            pinned_generation = generation
        elif pinned_generation != generation:
            raise CtxError("ctx list events continuation changed Core generation")
        for event in events:
            yield event

        terminal = completion.get("terminal")
        truncated = completion.get("truncated")
        next_cursor = completion.get("next_cursor")
        if terminal is True:
            if truncated is True or next_cursor is not None:
                raise CtxError("ctx list events returned inconsistent terminal completion")
            return
        if truncated is not True or not isinstance(next_cursor, str) or not next_cursor:
            raise CtxError("ctx list events was nonterminal without a usable continuation cursor")
        if next_cursor in seen_cursors:
            raise CtxError("ctx list events repeated a continuation cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor


def session_refs_for_window(
    ctx_bin: str,
    since_rfc3339: str,
    until_rfc3339: str,
    provider: Optional[str] = None,
) -> List[SessionRef]:
    by_id: Dict[str, SessionRef] = {}
    for event in iter_events(ctx_bin, since_rfc3339, until_rfc3339, provider):
        session_id = event.get("ctx_session_id")
        event_provider = event.get("provider")
        if not isinstance(session_id, str) or not session_id:
            continue
        if not isinstance(event_provider, str) or not event_provider:
            raise CtxError("ctx list events returned a session event without provider metadata")
        provider_session_id = event.get("provider_session_id")
        if provider_session_id is not None and not isinstance(provider_session_id, str):
            raise CtxError("ctx list events returned an invalid provider session id")
        previous = by_id.get(session_id)
        if previous is not None:
            if previous.provider != event_provider:
                raise CtxError("ctx list events returned inconsistent session providers")
            if (
                previous.provider_session_id is not None
                and provider_session_id is not None
                and previous.provider_session_id != provider_session_id
            ):
                raise CtxError("ctx list events returned inconsistent provider session ids")
            provider_session_id = previous.provider_session_id or provider_session_id
        by_id[session_id] = SessionRef(session_id, event_provider, provider_session_id)
    return [by_id[key] for key in sorted(by_id)]


def _timestamp_ms(event: Dict[str, Any]) -> Optional[int]:
    value = event.get("occurred_at_ms")
    if isinstance(value, int):
        return value
    stamp = event.get("occurred_at")
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CtxError("ctx show session returned invalid occurred_at: {}".format(exc))
    return int(parsed.timestamp() * 1000)


def session_summary(ctx_bin: str, ref: SessionRef) -> SessionSummary:
    output = run_ctx(
        ctx_bin,
        ["show", "session", ref.ctx_session_id, "--mode", "full", "--format", "json"],
    )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as exc:
        raise CtxError("ctx show session returned invalid JSON: {}".format(exc))
    if not isinstance(payload, dict) or payload.get("payload_type") != "session_transcript":
        raise CtxError("ctx show session returned an unexpected payload")
    if payload.get("ctx_session_id") != ref.ctx_session_id:
        raise CtxError("ctx show session returned a different session id")
    if payload.get("truncated") is not None:
        raise CtxError("ctx show session unexpectedly truncated a complete transcript")
    events = payload.get("events")
    if not isinstance(events, list):
        raise CtxError("ctx show session omitted its events array")
    if any(not isinstance(event, dict) for event in events):
        raise CtxError("ctx show session returned a non-object event")
    timestamps = [_timestamp_ms(event) for event in events]
    timed = [value for value in timestamps if value is not None]
    if not timed:
        raise CtxError("ctx show session had no timestamped events")
    provider = payload.get("provider")
    provider_session_id = payload.get("provider_session_id")
    if not isinstance(provider, str) or not provider:
        raise CtxError("ctx show session omitted provider metadata")
    if provider_session_id is not None and not isinstance(provider_session_id, str):
        raise CtxError("ctx show session returned an invalid provider session id")
    if provider != ref.provider:
        raise CtxError("ctx show session provider disagrees with event enumeration")
    if (
        ref.provider_session_id is not None
        and provider_session_id != ref.provider_session_id
    ):
        raise CtxError("ctx show session provider session id disagrees with event enumeration")
    return SessionSummary(
        ctx_session_id=ref.ctx_session_id,
        provider=provider,
        provider_session_id=provider_session_id,
        started_at_ms=min(timed),
        event_count=len(events),
    )


def session_summaries_for_dates(
    ctx_bin: str,
    since: str,
    until: str,
    provider: Optional[str],
    tz: ZoneInfo,
) -> List[Dict[str, Any]]:
    start, end = calendar_date_bounds(since, until, tz)
    refs = session_refs_for_window(ctx_bin, start, end, provider)
    summaries = [session_summary(ctx_bin, ref).as_dict() for ref in refs]
    return sorted(summaries, key=lambda row: (int(row["started_at_ms"]), row["ctx_session_id"]))
