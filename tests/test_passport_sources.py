"""No live configuration, authentication, model turns, devices, or app changes."""
import fcntl
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import passport_activity as activity
import passport_sources as sources


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name) / "home"
        self.home.mkdir(mode=0o700)
        self.state = self.home / "Library/Application Support/AI Passport/Bridge"
        self.runtime = self.home / "Applications/青笺.app/Contents/Runtime/python"
        self.runtime.parent.mkdir(parents=True, mode=0o700)
        self.runtime.write_bytes(b"#!/bin/sh\nexit 0\n")
        self.runtime.chmod(0o700)
        self.cursor = self.home / ".cursor/hooks.json"
        self.gemini = self.home / ".gemini/settings.json"

    def tearDown(self):
        self.temporary.cleanup()

    def write(self, path, content):
        path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else json.dumps(content).encode())
        path.chmod(0o600)

    def configure(self, **kwargs):
        return sources.configure_sources(self.state, self.runtime, home=self.home, **kwargs)

    def status(self):
        return sources.source_status(self.state, self.runtime, home=self.home)

    def row(self, provider="cursor"):
        return next(row for row in self.status()["sources"] if row["provider"] == provider)

    def backups(self):
        return list((self.state / activity.HOOK_DIR / sources.BACKUP_DIR).rglob("*.original"))

    def tree(self):
        return {str(p.relative_to(self.home)): (p.read_bytes(), stat.S_IMODE(p.stat().st_mode))
                for p in self.home.rglob("*") if p.is_file()}

    def test_status_is_read_only_and_not_configured(self):
        before = self.tree()
        result = self.status()
        self.assertTrue(result["ok"])
        self.assertEqual(before, self.tree())
        self.assertFalse(self.state.exists())
        row = self.row()
        self.assertEqual(row["status"], "not_configured")
        self.assertFalse(row["events_observed"])
        self.assertEqual(row["auth_status"], "unknown")
        self.assertFalse(row["quota_supported"])

    def test_install_twice_preserves_unrelated_and_no_extra_backup(self):
        cursor = {"version": 1, "other": {"keep": True}, "hooks": {
            "beforeSubmitPrompt": [{"command": "third-party", "failClosed": True}],
            "afterFileEdit": [{"command": "formatter"}]}}
        gemini = {"model": {"name": "leave-this"}, "security": {"unchanged": True},
                  "hooks": {"BeforeAgent": [{"hooks": [{"name": "other", "command": "keep"}]}]}}
        raw_cursor = ("  " + json.dumps(cursor) + "\n\n").encode()
        raw_gemini = (json.dumps(gemini) + "\n").encode()
        self.write(self.cursor, raw_cursor)
        self.write(self.gemini, raw_gemini)
        self.cursor.chmod(0o640)  # Preserve safe read-only group access.
        result = self.configure()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["changed"]["providers"], ["cursor", "gemini"])
        self.assertEqual(result["changed"]["backup_count"], 2)
        self.assertEqual({p.read_bytes() for p in self.backups()}, {raw_cursor, raw_gemini})
        installed_cursor = json.loads(self.cursor.read_bytes())
        installed_gemini = json.loads(self.gemini.read_bytes())
        self.assertEqual(installed_cursor["hooks"]["beforeSubmitPrompt"][0], cursor["hooks"]["beforeSubmitPrompt"][0])
        self.assertEqual(installed_cursor["hooks"]["afterFileEdit"], cursor["hooks"]["afterFileEdit"])
        self.assertEqual(installed_gemini["security"], gemini["security"])
        self.assertEqual(installed_gemini["model"], gemini["model"])
        self.assertEqual(stat.S_IMODE(self.cursor.stat().st_mode), 0o640)
        before = self.tree()
        second = self.configure()
        self.assertEqual(second["changed"], {"providers": [], "support_files": [], "backup_count": 0})
        self.assertEqual(self.tree(), before)
        self.assertEqual(len(self.backups()), 2)
        self.assertEqual(self.row()["status"], "waiting_for_events")

    def test_invalid_second_config_does_not_mutate_anything(self):
        self.write(self.cursor, {"version": 1, "keep": "first"})
        self.write(self.gemini, b'{"hooks": [}\n')
        before = self.tree()
        result = self.configure()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], "invalid_config_json")
        self.assertEqual(self.tree(), before)
        self.assertFalse(self.state.exists())

    def test_selected_source_only_does_not_touch_other_bad_config(self):
        self.write(self.gemini, b"invalid JSON")
        result = self.configure(providers=["cursor"])
        self.assertTrue(result["ok"], result)
        self.assertEqual(self.gemini.read_bytes(), b"invalid JSON")
        self.assertEqual(self.row()["status"], "waiting_for_events")
        self.assertEqual(self.row("gemini")["status"], "error")

    def test_unsupported_version_duplicate_keys_and_disabled_hooks_rejected(self):
        for raw, code in ((b'{"version": 2}', "unsupported_hooks_version"),
                          (b'{"version": true}', "unsupported_hooks_version"),
                          (b'{"version": 1, "version": 1}', "duplicate_config_key"),
                          (b'{"version": NaN}', "invalid_config_json")):
            self.write(self.cursor, raw)
            before = self.tree()
            result = self.configure()
            self.assertEqual(result["error_code"], code)
            self.assertEqual(self.tree(), before)
        self.cursor.unlink()
        self.write(self.gemini, {"hooksConfig": {"enabled": False}, "security": {"unchanged": True}})
        before = self.tree()
        self.assertEqual(self.configure()["error_code"], "hooks_disabled")
        self.assertEqual(self.tree(), before)
        self.assertFalse(self.row("gemini")["config_enabled"])

    def test_symlink_config_and_directory_rejected(self):
        outside = self.home / "untouched.json"
        outside.write_bytes(b"{}")
        self.cursor.parent.mkdir()
        self.cursor.symlink_to(outside)
        result = self.configure()
        self.assertFalse(result["ok"])
        self.assertEqual(outside.read_bytes(), b"{}")
        self.assertFalse(self.state.exists())
        self.cursor.unlink()
        self.cursor.parent.rmdir()
        target = self.home / "redirected"
        target.mkdir()
        self.cursor.parent.symlink_to(target, target_is_directory=True)
        self.assertFalse(self.configure()["ok"])
        self.assertEqual(list(target.iterdir()), [])

    def test_hardlink_fifo_shared_writable_file_or_directory_rejected(self):
        self.write(self.cursor, {})
        self.cursor.chmod(0o666)
        self.assertEqual(self.configure()["error_code"], "unsafe_file")
        self.cursor.chmod(0o600)
        os.link(self.cursor, self.home / "hardlink")
        self.assertEqual(self.configure()["error_code"], "unsafe_file")
        (self.home / "hardlink").unlink()
        self.cursor.unlink()
        os.mkfifo(self.cursor)
        started = time.monotonic()
        self.assertFalse(self.configure()["ok"])
        self.assertLess(time.monotonic() - started, 1)
        self.cursor.unlink()
        self.cursor.parent.chmod(0o777)
        self.assertEqual(self.configure()["error_code"], "unsafe_directory")
        self.assertFalse(self.state.exists())

    def test_shared_receiver_symlink_rejected_before_configs_written(self):
        support = self.state / activity.HOOK_DIR
        support.mkdir(parents=True)
        original = self.home / "receiver-do-not-touch"
        original.write_bytes(b"original")
        (support / "passport_activity.py").symlink_to(original)
        before = self.tree()
        self.assertFalse(self.configure()["ok"])
        self.assertEqual(self.tree(), before)
        self.assertFalse(self.cursor.exists())

    def test_modified_owned_hooks_are_not_misreported_or_silently_enabled(self):
        self.assertTrue(self.configure()["ok"])
        changed = json.loads(self.cursor.read_bytes())
        changed["hooks"]["beforeSubmitPrompt"][0]["failClosed"] = True
        self.write(self.cursor, changed)
        before = self.tree()
        self.assertEqual(self.row()["error_code"], "owned_hook_modified")
        self.assertEqual(self.configure()["error_code"], "owned_hook_modified")
        self.assertEqual(self.tree(), before)

    def test_backup_failure_occurs_before_any_live_mutation(self):
        self.write(self.cursor, {"version": 1, "private_setting": "preserve"})
        original = self.cursor.read_bytes()
        real_replace = sources._replace
        def fail_backup(change, *args, **kwargs):
            if change.label == "backup":
                raise OSError("disk full")
            return real_replace(change, *args, **kwargs)
        with mock.patch.object(sources, "_replace", side_effect=fail_backup):
            result = self.configure()
        self.assertFalse(result["ok"])
        self.assertEqual(self.cursor.read_bytes(), original)
        self.assertFalse(self.gemini.exists())
        for name in sources.SUPPORT_NAMES:
            self.assertFalse((self.state / activity.HOOK_DIR / name).exists())

    def test_missing_and_moved_runtime_are_distinct_and_repairable(self):
        self.assertTrue(self.configure()["ok"])
        original_configs = (self.cursor.read_bytes(), self.gemini.read_bytes())
        previous_path = self.runtime
        moved = self.home / "Applications/New 青笺.app/Contents/Runtime/python"
        moved.parent.mkdir(parents=True)
        previous_path.rename(moved)
        self.assertEqual(self.row()["status"], "needs_repair")
        self.assertFalse(self.row()["runtime_intact"])
        self.assertEqual(self.configure()["error_code"], "invalid_runtime")
        self.runtime = moved
        result = self.configure()
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["changed"]["providers"], [])
        self.assertEqual(result["changed"]["support_files"], ["runtime.path"])
        self.assertEqual(result["changed"]["backup_count"], 1)
        self.assertEqual((self.cursor.read_bytes(), self.gemini.read_bytes()), original_configs)
        self.assertEqual(self.backups()[0].read_bytes(), (str(previous_path) + "\n").encode())
        self.assertTrue(self.row()["runtime_intact"])

    def test_missing_receiver_is_not_live_or_unconfigured(self):
        self.assertTrue(self.configure()["ok"])
        (self.state / activity.HOOK_DIR / "passport_activity.py").unlink()
        row = self.row()
        self.assertTrue(row["configured"])
        self.assertFalse(row["receiver_intact"])
        self.assertEqual(row["status"], "needs_repair")

    def test_changed_support_is_backed_up_and_repaired(self):
        self.assertTrue(self.configure()["ok"])
        receiver = self.state / activity.HOOK_DIR / "passport_activity.py"
        self.write(receiver, b"old receiver\n")
        result = self.configure()
        self.assertTrue(result["ok"])
        self.assertEqual(result["changed"]["support_files"], ["passport_activity.py"])
        self.assertEqual(result["changed"]["backup_count"], 1)
        self.assertEqual(self.backups()[0].read_bytes(), b"old receiver\n")

    def test_concurrent_edit_during_backup_never_overwritten(self):
        self.write(self.cursor, {"version": 1, "keep": "original"})
        real_backup = sources._backup
        edited = b'{"version": 1, "external_edit": true}\n'
        def backup_then_edit(*args):
            count = real_backup(*args)
            self.cursor.write_bytes(edited)
            return count
        with mock.patch.object(sources, "_backup", side_effect=backup_then_edit):
            result = self.configure()
        self.assertEqual(result["error_code"], "concurrent_change")
        self.assertEqual(self.cursor.read_bytes(), edited)
        self.assertFalse(self.gemini.exists())
        self.assertFalse((self.state / activity.HOOK_DIR / "passport_activity.py").exists())

    def test_mid_transaction_failure_rolls_back_existing_and_created_files(self):
        self.assertTrue(self.configure()["ok"])
        # Force a receiver refresh and a new runtime mapping in one transaction.
        self.write(self.state / activity.HOOK_DIR / "passport_activity.py", b"previous receiver")
        baseline = self.tree()
        self.runtime = self.home / "Applications/new-python"
        self.write(self.runtime, b"#!/bin/sh\nexit 0\n")
        self.runtime.chmod(0o700)
        real_replace = sources._replace
        def fail_runtime(change, *args, **kwargs):
            if change.label == "runtime.path" and change.data == (str(self.runtime) + "\n").encode():
                raise OSError("fixed test error")
            return real_replace(change, *args, **kwargs)
        with mock.patch.object(sources, "_replace", side_effect=fail_runtime):
            result = self.configure()
        self.assertFalse(result["ok"])
        for relative, (data, mode) in baseline.items():
            path = self.home / relative
            self.assertEqual(path.read_bytes(), data, relative)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), mode)
        self.assertEqual(len(self.backups()), 2)

    def test_failure_after_replace_rolls_back_this_file_too(self):
        self.write(self.cursor, {"version": 1, "keep": True})
        old = self.cursor.read_bytes()
        real_replace = sources._replace
        def fail_after_cursor(change, *args, **kwargs):
            result = real_replace(change, *args, **kwargs)
            if change.provider == "cursor":
                raise OSError("readback unavailable")
            return result
        with mock.patch.object(sources, "_replace", side_effect=fail_after_cursor):
            result = self.configure()
        self.assertFalse(result["ok"])
        self.assertEqual(self.cursor.read_bytes(), old)
        self.assertFalse(self.gemini.exists())
        for name in sources.SUPPORT_NAMES:
            self.assertFalse((self.state / activity.HOOK_DIR / name).exists())

    def test_rollback_does_not_overwrite_concurrent_user_edit(self):
        self.write(self.cursor, {"version": 1})
        real_replace = sources._replace
        user_edit = b'{"version":1,"user_edit":"preserve"}'
        def replace_then_fail(change, *args, **kwargs):
            if change.provider == "gemini":
                self.cursor.write_bytes(user_edit)
                raise OSError("fixed test error")
            return real_replace(change, *args, **kwargs)
        with mock.patch.object(sources, "_replace", side_effect=replace_then_fail):
            result = self.configure()
        self.assertEqual(result["error_code"], "rollback_incomplete")
        self.assertEqual(self.cursor.read_bytes(), user_edit)

    def test_lock_is_bounded_and_does_not_change_config(self):
        self.state.mkdir(parents=True, mode=0o700)
        lock = self.state / ".sources-setup.lock"
        self.write(lock, b"")
        with lock.open("rb+") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            with mock.patch.object(sources, "LOCK_TIMEOUT", 0.02):
                result = self.configure()
        self.assertEqual(result["error_code"], "setup_busy")
        self.assertFalse(self.cursor.exists())
        self.assertFalse(self.gemini.exists())

    def test_fresh_expired_and_unconfigured_observation_are_distinct(self):
        now = time.time_ns() // 1_000_000
        payload = {"hook_event_name": "beforeSubmitPrompt", "conversation_id": "private", "generation_id": "generation"}
        self.assertTrue(activity.receive_payload("cursor", json.dumps(payload).encode(), self.state, now))
        row = self.row()
        self.assertFalse(row["configured"])
        self.assertTrue(row["events_observed"])
        self.assertEqual(row["event_freshness"], "fresh")
        self.assertEqual(row["status"], "not_configured")
        self.assertTrue(self.configure()["ok"])
        self.assertEqual(self.row()["status"], "live")
        self.assertIsNone(self.row()["desktop_linked"])  # CLI and App use shared hook schema.
        with mock.patch.object(sources.time, "time_ns", return_value=(now + activity.TTL_MS + 1) * 1_000_000):
            row = self.row()
        self.assertTrue(row["configured"])
        self.assertTrue(row["events_observed"])
        self.assertEqual(row["event_freshness"], "expired")
        self.assertEqual(row["status"], "expired")
        self.assertEqual(row["activity_state"], "unknown")

    def test_malformed_event_does_not_claim_live_or_zero(self):
        self.assertTrue(self.configure()["ok"])
        record = self.state / activity.ACTIVITY_DIR / ("cursor-" + "a" * 64 + ".json")
        self.write(record, {"secret": "do not propagate"})
        row = self.row()
        self.assertEqual(row["event_freshness"], "invalid")
        self.assertEqual(row["status"], "error")
        self.assertEqual(row["activity_state"], "unknown")
        self.assertFalse(row["events_observed"])
        self.assertNotIn("secret", json.dumps(row))

    def test_installed_apps_do_not_imply_desktop_linked_or_authorized(self):
        for name in ("Cursor.app", "Gemini.app", "Grok Bot.app"):
            (self.home / "Applications" / name).mkdir()
        cli = self.home / ".local/bin/cursor-agent"
        self.write(cli, b"#!/bin/sh\nexit 99\n")
        cli.chmod(0o700)
        cursor = self.row()
        self.assertTrue(cursor["app_installed"])
        self.assertTrue(cursor["cli_available"])
        self.assertTrue(cursor["desktop_supported"])
        self.assertIsNone(cursor["desktop_linked"])
        self.assertEqual(cursor["auth_status"], "unknown")
        self.assertEqual(cursor["connection_kind"], "desktop_and_cli_hooks")
        gemini = self.row("gemini")
        self.assertTrue(gemini["app_installed"])
        self.assertFalse(gemini["desktop_supported"])
        self.assertFalse(gemini["desktop_linked"])
        self.assertEqual(gemini["auth_status"], "unknown")
        self.assertEqual(gemini["connection_kind"], "cli_hooks")
        self.assertEqual([row["provider"] for row in self.status()["sources"]], ["cursor", "gemini"])

    def test_scope_and_unsupported_provider_rejected_without_changes(self):
        before = self.tree()
        self.assertEqual(self.configure(providers=["grok"])["error_code"], "unsupported_provider")
        self.assertEqual(sources.configure_sources(self.home.parent / "outside", self.runtime, home=self.home)["error_code"], "state_outside_home")
        self.assertEqual(sources.configure_sources(self.home / ".cursor/Bridge", self.runtime, home=self.home)["error_code"], "unsafe_state_directory")
        self.assertEqual(self.tree(), before)

    def test_backups_support_and_created_configs_are_private(self):
        self.write(self.cursor, {"version": 1})
        self.assertTrue(self.configure()["ok"])
        for path in (self.state / activity.HOOK_DIR).rglob("*"):
            expected = 0o700 if path.is_dir() or path.name == "passport-activity" else 0o600
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), expected, path.name)
        self.assertEqual(stat.S_IMODE(self.gemini.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
