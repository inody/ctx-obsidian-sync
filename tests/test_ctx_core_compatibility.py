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

import ctx_config
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


def isolated_env(home):
    """Environment that cannot see the developer's own config or settings."""
    env = {
        key: value for key, value in os.environ.items()
        if key not in ctx_config.CONFIG_KEYS
        and key not in {"AI_OBSIDIAN_TZ", "TZ", "XDG_CONFIG_HOME", "CTX_OBSIDIAN_SYNC_CONFIG"}
    }
    env["HOME"] = str(home)
    return env


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


class ProjectLinkTests(unittest.TestCase):
    def test_project_notes_map_cwd_by_longest_root(self):
        projects = [
            ctx_to_obsidian_sessions.ProjectRef("parent", "prj_parent", ("~/work",)),
            ctx_to_obsidian_sessions.ProjectRef("child", "prj_child", ("~/work/child",)),
        ]
        project = ctx_to_obsidian_sessions.project_for_cwd("~/work/child/src", projects)
        self.assertEqual(project.project_id, "child")

    def test_refresh_adds_project_metadata_without_touching_body(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            projects_dir = root / "03_Projects"
            sessions_dir = root / "ctx-sessions"
            projects_dir.mkdir()
            sessions_dir.mkdir()
            (projects_dir / "prj_nlmfsp.md").write_text(
                "---\nproject_id: nlmfsp\nroots:\n  - \"~/Dropbox/univ/nlmfsp\"\n---\n",
                encoding="utf-8",
            )
            note = sessions_dir / "session.md"
            note.write_text(
                "---\ndate: 2026-01-01\ncwd: ~/Dropbox/univ/nlmfsp/latex\nsession_id: s1\n---\n\nbody\n",
                encoding="utf-8",
            )
            projects = ctx_to_obsidian_sessions.load_projects(projects_dir)
            updated, failed = ctx_to_obsidian_sessions.refresh_project_links(
                sessions_dir, projects
            )
            self.assertEqual((updated, failed), (1, 0))
            text = note.read_text(encoding="utf-8")
            self.assertIn("project_id: nlmfsp", text)
            self.assertIn('project: "[[prj_nlmfsp]]"', text)
            self.assertTrue(text.endswith("\n\nbody\n"))

    def test_duplicate_project_root_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            projects_dir = Path(temp)
            for name, project_id in (("a.md", "a"), ("b.md", "b")):
                (projects_dir / name).write_text(
                    "---\nproject_id: {}\nroots:\n  - ~/same\n---\n".format(project_id),
                    encoding="utf-8",
                )
            with self.assertRaises(ctx_core.CtxError):
                ctx_to_obsidian_sessions.load_projects(projects_dir)


class WrapperImportOutcomeTests(unittest.TestCase):
    def run_wrapper(self, outcome, import_rc):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            script_dir = home / "scripts"
            script_dir.mkdir()
            wrapper = script_dir / "ctx_obsidian_sync.sh"
            shutil.copy2(ROOT / "bin" / "ctx_obsidian_sync.sh", wrapper)
            shutil.copy2(ROOT / "bin" / "ctx_config.sh", script_dir / "ctx_config.sh")
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
            env = isolated_env(home)
            env.update({
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


CONFIG_SAMPLE = """
# comment
OBSIDIAN_VAULT=~/Notes/My Vault
export CTX_OBSIDIAN_TZ="Asia/Tokyo"
CTX_OBSIDIAN_DIARY_DIR=Journal  # trailing comment
CTX_DAILY_DIGEST_HOST='host#1'
CTX_DAILY_DIGEST_DIARY_OFFSET_DAYS=1
CTX_DAILY_DIGEST_LANGUAGE=en
CTX_DAILY_DIGEST_LANGUAGE=ja
UNKNOWN_KEY=1
not a setting
"""


class ConfigTests(unittest.TestCase):
    def test_python_parser(self):
        with mock.patch("sys.stderr"):
            values = ctx_config.parse_config(CONFIG_SAMPLE)
        self.assertEqual(values, {
            "OBSIDIAN_VAULT": str(Path.home() / "Notes" / "My Vault"),
            "CTX_OBSIDIAN_TZ": "Asia/Tokyo",
            "CTX_OBSIDIAN_DIARY_DIR": "Journal",
            "CTX_DAILY_DIGEST_HOST": "host#1",
            "CTX_DAILY_DIGEST_DIARY_OFFSET_DAYS": "1",
            "CTX_DAILY_DIGEST_LANGUAGE": "ja",
        })

    def test_shell_loader_matches_python_and_env_wins(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            config = home / ".config" / "ctx-obsidian-sync" / "config"
            config.parent.mkdir(parents=True)
            config.write_text(CONFIG_SAMPLE, encoding="utf-8")
            env = isolated_env(home)
            env["CTX_DAILY_DIGEST_HOST"] = ""  # explicitly empty env overrides the file
            script = (
                f'source "{ROOT / "bin" / "ctx_config.sh"}"; ctx_config_load; '
                'for k in OBSIDIAN_VAULT CTX_OBSIDIAN_TZ CTX_OBSIDIAN_DIARY_DIR '
                'CTX_DAILY_DIGEST_HOST CTX_DAILY_DIGEST_DIARY_OFFSET_DAYS '
                'CTX_DAILY_DIGEST_LANGUAGE UNKNOWN_KEY; '
                'do printf "%s=%s\\n" "$k" "${!k-<unset>}"; done'
            )
            proc = subprocess.run(
                ["/bin/bash", "-c", script], capture_output=True, text=True, env=env, check=True
            )
        self.assertEqual(proc.stdout.splitlines(), [
            f"OBSIDIAN_VAULT={home / 'Notes' / 'My Vault'}",
            "CTX_OBSIDIAN_TZ=Asia/Tokyo",
            "CTX_OBSIDIAN_DIARY_DIR=Journal",
            "CTX_DAILY_DIGEST_HOST=",
            "CTX_DAILY_DIGEST_DIARY_OFFSET_DAYS=1",
            "CTX_DAILY_DIGEST_LANGUAGE=ja",
            "UNKNOWN_KEY=<unset>",
        ])
        self.assertIn("unknown key UNKNOWN_KEY", proc.stderr)

    def test_precedence_and_timezone(self):
        config = {"OBSIDIAN_VAULT": "/from/config", "CTX_OBSIDIAN_TZ": "Asia/Tokyo"}
        self.assertEqual(ctx_config.setting("OBSIDIAN_VAULT", config, environ={}), "/from/config")
        self.assertEqual(
            ctx_config.setting("OBSIDIAN_VAULT", config, environ={"OBSIDIAN_VAULT": "/env"}), "/env"
        )
        self.assertEqual(ctx_config.resolve_timezone(config, environ={}), "Asia/Tokyo")
        self.assertEqual(
            ctx_config.resolve_timezone(config, environ={"AI_OBSIDIAN_TZ": "Europe/Paris"}),
            "Europe/Paris",
        )
        self.assertEqual(
            ctx_config.resolve_timezone({}, environ={"TZ": "America/New_York"}), "America/New_York"
        )

    def test_missing_vault_is_an_error(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(ctx_config.ConfigError):
                ctx_config.require_vault(None, {})


class DigestHostTests(unittest.TestCase):
    def run_digest_wrapper(self, host_setting):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            script_dir = home / "scripts"
            script_dir.mkdir()
            for name in ("ctx_daily_digest.sh", "ctx_config.sh"):
                shutil.copy2(ROOT / "bin" / name, script_dir / name)
            marker = home / "digest-ran"
            (script_dir / "ctx_daily_digest.py").write_text(
                "import os, pathlib\npathlib.Path(os.environ['DIGEST_MARKER']).write_text('ok')\n",
                encoding="utf-8",
            )
            env = isolated_env(home)
            env["DIGEST_MARKER"] = str(marker)
            if host_setting is not None:
                config = home / ".config" / "ctx-obsidian-sync" / "config"
                config.parent.mkdir(parents=True)
                config.write_text(f"CTX_DAILY_DIGEST_HOST={host_setting}\n", encoding="utf-8")
            proc = subprocess.run(
                ["/bin/bash", str(script_dir / "ctx_daily_digest.sh")],
                capture_output=True, text=True, env=env,
            )
            return proc, marker.exists()

    def test_unset_host_runs_everywhere(self):
        proc, ran = self.run_digest_wrapper(None)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertTrue(ran)

    def test_other_host_is_skipped(self):
        proc, ran = self.run_digest_wrapper("no-such-host-xyz")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(ran)
        self.assertIn("[skip]", proc.stderr)


class AgentRuntimeTests(unittest.TestCase):
    def test_finds_newest_fnm_node_outside_launchd_path(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            older = home / ".local/share/fnm/node-versions/v20.1.0/installation/bin"
            newer = home / ".local/share/fnm/node-versions/v25.3.0/installation/bin"
            older.mkdir(parents=True)
            newer.mkdir(parents=True)
            (older / "node").write_text("", encoding="utf-8")
            (newer / "node").write_text("", encoding="utf-8")
            with mock.patch.object(ctx_daily_digest, "HOME", home), mock.patch.dict(
                os.environ, {"PATH": "/usr/bin:/bin"}, clear=True
            ):
                env = ctx_daily_digest.agent_runtime_env()
            self.assertEqual(env["PATH"].split(os.pathsep)[0], str(newer))
            self.assertTrue(env["PATH"].endswith("/usr/bin:/bin"))


class DigestLanguageTests(unittest.TestCase):
    def test_prompt_file_placeholder(self):
        self.assertEqual(ctx_daily_digest.digest_prompt("LOG", "before {{source}} after"), "before LOG after")
        self.assertEqual(ctx_daily_digest.digest_prompt("LOG", "intro\n"), "intro\nLOG")

    def test_languages_have_distinct_headings(self):
        self.assertEqual(ctx_daily_digest.LANGUAGES["ja"].heading, "## AIエージェントとの作業まとめ")
        self.assertTrue(ctx_daily_digest.LANGUAGES["en"].prompt.startswith("You are"))

    def test_configured_heading_replaces_model_h2(self):
        result = ctx_daily_digest.normalize_digest_heading(
            "## AIエージェントとの作業まとめ\n\n本文", "## Yesterday's Summary"
        )
        self.assertEqual(result, "## Yesterday's Summary\n\n本文")


if __name__ == "__main__":
    unittest.main()
