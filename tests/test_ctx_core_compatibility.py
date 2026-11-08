#!/usr/bin/env python3

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))

import ctx_core
import ctx_daily_digest
import ctx_to_obsidian_sessions


def event_record(ordinal, session_id="session-1", provider_session_id="native-1"):
    return {
        "schema_version": 1,
        "record_type": "event_range_event",
        "generation_id": "generation-1",
        "ordinal": ordinal,
        "event": {
            "ctx_session_id": session_id,
            "provider": "pi",
            "provider_session_id": provider_session_id,
        },
    }


def completion(items, terminal, cursor=None):
    return {
        "schema_version": 1,
        "record_type": "event_range_completion",
        "generation_id": "generation-1",
        "terminal": terminal,
        "truncated": not terminal,
        "next_cursor": cursor,
        "usage": {"items": items},
    }


def jsonl(*records):
    return "".join(json.dumps(record) + "\n" for record in records)


class CoreEnumerationTests(unittest.TestCase):
    def test_calendar_bounds_are_exact_and_dst_aware(self):
        start, end = ctx_core.calendar_date_bounds(
            "2026-10-25", "2026-10-25", ZoneInfo("Europe/London")
        )
        self.assertEqual(start, "2026-10-24T23:00:00.000Z")
        self.assertEqual(end, "2026-10-26T00:00:00.000Z")

    def test_jsonl_requires_terminal_completion(self):
        with self.assertRaises(ctx_core.CtxError):
            ctx_core._parse_jsonl_page(jsonl(event_record(0)))

    def test_iter_events_follows_cursor_without_truncation(self):
        outputs = [
            jsonl(event_record(0, "session-1"), completion(1, False, "cursor-1")),
            jsonl(event_record(0, "session-2", "native-2"), completion(1, True)),
        ]
        with mock.patch.object(ctx_core, "run_ctx", side_effect=outputs) as runner:
            events = list(
                ctx_core.iter_events(
                    "ctx",
                    "2026-11-01T00:00:00.000Z",
                    "2026-11-02T00:00:00.000Z",
                    invocation_limit=1,
                )
            )
        self.assertEqual([event["ctx_session_id"] for event in events], ["session-1", "session-2"])
        self.assertNotIn("--cursor", runner.call_args_list[0].args[1])
        self.assertIn("--cursor", runner.call_args_list[1].args[1])
        self.assertIn("cursor-1", runner.call_args_list[1].args[1])

    def test_session_refs_are_unique_and_deterministic(self):
        events = [
            {"ctx_session_id": "b", "provider": "pi", "provider_session_id": "pb"},
            {"ctx_session_id": "a", "provider": "pi", "provider_session_id": "pa"},
            {"ctx_session_id": "b", "provider": "pi", "provider_session_id": "pb"},
        ]
        with mock.patch.object(ctx_core, "iter_events", return_value=iter(events)):
            refs = ctx_core.session_refs_for_window("ctx", "start", "end")
        self.assertEqual([ref.ctx_session_id for ref in refs], ["a", "b"])

    def test_session_summary_uses_complete_event_count_and_earliest_timestamp(self):
        ref = ctx_core.SessionRef("session-1", "pi", "native-1")
        payload = {
            "payload_type": "session_transcript",
            "ctx_session_id": "session-1",
            "provider": "pi",
            "provider_session_id": "native-1",
            "events": [
                {"occurred_at": "2026-11-02T01:02:03.004Z"},
                {"occurred_at": "2026-11-01T23:59:59.999Z"},
            ],
        }
        with mock.patch.object(ctx_core, "run_ctx", return_value=json.dumps(payload)):
            summary = ctx_core.session_summary("ctx", ref)
        self.assertEqual(summary.event_count, 2)
        self.assertEqual(summary.started_at_ms, 1793577599999)


class ReconciliationTests(unittest.TestCase):
    def test_changed_core_id_reuses_existing_provider_session_note(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            sessions_dir = root / "ctx-sessions"
            sessions_dir.mkdir()
            old_path = sessions_dir / "2026-11-01__pc__pi__oldid.md"
            old_path.write_text(
                "---\nprovider: pi\nsession_id: old-id\nprovider_session_id: native-1\ncwd: ~/repo\n---\n\nold\n",
                encoding="utf-8",
            )
            state_path = root / "state.json"
            state_path.write_text(
                json.dumps({"synced": {"old-id": {"event_count": 1}}}), encoding="utf-8"
            )
            sessions = [{
                "ctx_session_id": "new-id",
                "provider": "pi",
                "provider_session_id": "native-1",
                "started_at_ms": 1793520000000,
                "event_count": 2,
                "cwd": None,
            }]

            def fake_render(_ctx, row, _pc, _tz, _limit):
                return "---\nprovider: pi\nsession_id: {}\nprovider_session_id: native-1\ncwd: {}\n---\n\nnew\n".format(
                    row["ctx_session_id"], row["cwd"]
                )

            with mock.patch.object(ctx_to_obsidian_sessions, "render_note", side_effect=fake_render):
                rc = ctx_to_obsidian_sessions.run_write(
                    "ctx", sessions, "pc", ZoneInfo("Europe/London"), "test",
                    sessions_dir, state_path, 8000,
                )
            self.assertEqual(rc, 0)
            self.assertEqual(sorted(path.name for path in sessions_dir.glob("*.md")), [old_path.name])
            self.assertIn("session_id: new-id", old_path.read_text(encoding="utf-8"))
            state = json.loads(state_path.read_text(encoding="utf-8"))
            self.assertEqual(state["reconciled_session_ids"]["old-id"], "new-id")
            self.assertEqual(state["synced"]["new-id"]["note"], old_path.name)

    def test_ambiguous_provider_identity_fails_closed(self):
        with self.assertRaises(ctx_core.CtxError):
            ctx_to_obsidian_sessions.unique_note([Path("a"), Path("b")], "provider session")


class WrapperImportOutcomeTests(unittest.TestCase):
    def run_wrapper(self, outcome, import_rc):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            script_dir = home / "scripts"
            script_dir.mkdir()
            wrapper = script_dir / "ctx_obsidian_sync.sh"
            shutil.copy2(ROOT / "bin" / "ctx_obsidian_sync.sh", wrapper)
            marker = home / "exporter-ran"
            (script_dir / "ctx_to_obsidian_sessions.py").write_text(
                "import os, pathlib\npathlib.Path(os.environ['SYNC_MARKER']).write_text('ok')\n",
                encoding="utf-8",
            )
            bin_dir = home / ".local" / "bin"
            bin_dir.mkdir(parents=True)
            fake_ctx = bin_dir / "ctx"
            fake_ctx.write_text(
                """#!/bin/bash
if [[ "$1" == "index" ]]; then
  exit 0
fi
if [[ "$1" == "import" ]]; then
  printf '{"schema_version":2,"outcome":"%s","totals":{"current_indexed_sessions":3,"rejected_records":1,"failed_sources":1}}\\n' "$FAKE_IMPORT_OUTCOME"
  exit "$FAKE_IMPORT_RC"
fi
exit 2
""",
                encoding="utf-8",
            )
            fake_ctx.chmod(0o755)
            env = os.environ.copy()
            env.update({
                "HOME": str(home),
                "FAKE_IMPORT_OUTCOME": outcome,
                "FAKE_IMPORT_RC": str(import_rc),
                "SYNC_MARKER": str(marker),
            })
            proc = subprocess.run(
                [str(wrapper)], capture_output=True, text=True, env=env
            )
            return proc, marker.exists()

    def test_published_partial_import_continues_to_exporter(self):
        proc, exporter_ran = self.run_wrapper(
            "completed_with_rejections_and_source_failures", 1
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(exporter_ran)
        self.assertIn("command_rc=1", proc.stderr)

    def test_failed_import_stops_before_exporter(self):
        proc, exporter_ran = self.run_wrapper("failure", 1)
        self.assertNotEqual(proc.returncode, 0)
        self.assertFalse(exporter_ran)


class DigestParsingTests(unittest.TestCase):
    def test_ctx_iso_markdown_heading_is_parsed(self):
        text = "## user - message - 2026-11-08T06:29:26.207Z\n\nhello\n"
        entries = ctx_daily_digest.entries_from_markdown(
            text,
            target_date="2026-11-08",
            tz=ZoneInfo("Europe/London"),
            provider="pi",
            pc="pc",
            link="note",
            session_id="session-1",
            max_event_chars=100,
        )
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].body, "hello")


if __name__ == "__main__":
    unittest.main()
