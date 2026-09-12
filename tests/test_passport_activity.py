"""Synthetic hook inputs and temporary files only; no AI turns/user settings/USB."""
import datetime as dt
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/passport_activity.py"
spec = importlib.util.spec_from_file_location("activity_under_test", SCRIPT)
activity = importlib.util.module_from_spec(spec)
spec.loader.exec_module(activity)
NOW = 1_800_000_000_000


def cursor(event="beforeSubmitPrompt", generation="generation-1", **extra):
    return dict(conversation_id="private-conversation", generation_id=generation,
                hook_event_name=event, **extra)


def gemini(event="BeforeAgent", at=NOW, **extra):
    return dict(session_id="private-session", hook_event_name=event,
                timestamp=dt.datetime.fromtimestamp(at / 1000, dt.timezone.utc).isoformat(), **extra)


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = self.root / "Bridge"

    def tearDown(self):
        self.temporary.cleanup()

    def receive(self, provider, payload, now=NOW):
        return activity.receive_payload(provider, json.dumps(payload).encode(), self.state, now)

    def records(self):
        return [json.loads(path.read_text()) for path in
                (self.state / activity.ACTIVITY_DIR).glob("*.json")]

    def error(self, provider="cursor"):
        return json.loads((self.state / activity.ACTIVITY_DIR / ".metadata" / f"{provider}.json").read_text())

    def run_receiver(self, raw, provider="cursor", timeout=3):
        return subprocess.run([sys.executable, str(SCRIPT), "receive", "--provider", provider,
                               "--state-dir", str(self.state)], input=raw, capture_output=True,
                              timeout=timeout)

    def test_cursor_working_then_stop(self):
        self.assertTrue(self.receive("cursor", cursor(model="gpt-5.5")))
        record = self.records()[0]
        self.assertEqual(record["state"], "working")
        self.assertEqual(record["state_until_ms"], NOW + 180_000)
        self.assertTrue(self.receive("cursor", cursor("stop", status="completed"), NOW + 2))
        self.assertEqual(self.records()[0]["state"], "idle")
        self.assertEqual(self.records()[0]["model"], "")  # Unknown is not an old model.

    def test_privacy_allowlist_and_hashing(self):
        secret = "DO-NOT-STORE-/Users/private/key-auth@example.net"
        self.receive("cursor", cursor(prompt=secret, command=secret, cwd=secret,
                     transcript_path=secret, user_email=secret, auth=secret,
                     tool_input={"command": secret}, output=secret, model=secret))
        record = self.records()[0]
        self.assertEqual(set(record), activity.RECORD_FIELDS)
        self.assertEqual(record["model"], "")
        saved = "".join(path.read_text() for path in self.state.rglob("*.json"))
        self.assertNotIn(secret, saved)
        self.assertNotIn("private-conversation", saved)
        self.assertNotIn("generation-1", saved)
        self.assertRegex(record["session_id_hash"], r"^[a-f0-9]{64}$")

    def test_model_ascii_and_length(self):
        for value in ("a" * 33, "模型", "secret@example.com", "/Users/sh", "has spaces", 3):
            self.assertEqual(activity.normalize_event("cursor", cursor(model=value), NOW)["model"], "")
        self.assertEqual(activity.normalize_event("cursor", cursor(model="a" * 32), NOW)["model"], "a" * 32)

    def test_structured_model_preferred(self):
        record = activity.normalize_event("cursor", cursor(model="legacy", model_id="gpt-5.5"), NOW)
        self.assertEqual(record["model"], "gpt-5.5")

    def test_cursor_parallel_generations_and_session_end(self):
        self.receive("cursor", cursor(generation="one"))
        self.receive("cursor", cursor(generation="two"), NOW + 1)
        self.receive("cursor", dict(cursor(generation="three"), conversation_id="different"), NOW + 2)
        self.assertEqual(len(self.records()), 3)
        self.receive("cursor", cursor("stop", generation="one", status="completed"), NOW + 3)
        self.assertEqual(sum(r["state"] == "working" for r in self.records()), 2)
        self.receive("cursor", cursor("sessionEnd"), NOW + 4)
        self.assertEqual(sum(r["state"] == "working" for r in self.records()), 1)

    def test_cursor_missing_generation_is_not_guessed(self):
        payload = cursor()
        del payload["generation_id"]
        self.assertFalse(self.receive("cursor", payload))
        self.assertEqual(self.error()["error_code"], "missing_generation")

    def test_cursor_stop_cannot_be_revived_by_late_tool_or_start(self):
        self.receive("cursor", cursor("stop", status="completed"))
        for event in ("preToolUse", "postToolUse", "beforeSubmitPrompt"):
            self.assertFalse(self.receive("cursor", cursor(event), NOW + 1))
        self.assertEqual(self.records()[0]["state"], "idle")
        self.assertTrue(self.receive("cursor", cursor(generation="new"), NOW + 2))

    def test_cursor_tool_error_does_not_mean_entire_agent_error(self):
        self.receive("cursor", cursor("postToolUseFailure", failure_type="error", error_message="PRIVATE"))
        self.assertEqual(self.records()[0]["state"], "working")
        self.receive("cursor", cursor("stop", status="error"), NOW + 1)
        self.assertEqual(self.records()[0]["state"], "error")

    def test_invalid_stop_is_not_guessed_idle(self):
        self.assertFalse(self.receive("cursor", cursor("stop", status="unknown")))
        self.assertEqual(self.error()["error_code"], "invalid_stop_status")

    def test_gemini_permission_lifecycle(self):
        self.receive("gemini", gemini())
        self.receive("gemini", gemini("Notification", NOW + 1, notification_type="ToolPermission",
                     message="PRIVATE", details={"file": "PRIVATE"}), NOW + 1)
        self.assertEqual(self.records()[0]["state"], "waiting")
        self.receive("gemini", gemini("AfterTool", NOW + 2), NOW + 2)
        self.assertEqual(self.records()[0]["state"], "working")
        self.receive("gemini", gemini("AfterAgent", NOW + 3, prompt_response="PRIVATE"), NOW + 3)
        self.assertEqual(self.records()[0]["state"], "idle")
        self.assertNotIn("PRIVATE", json.dumps(self.records()))

    def test_gemini_other_notification_is_ignored(self):
        self.assertFalse(self.receive("gemini", gemini("Notification", notification_type="Other")))
        self.assertFalse(self.state.exists())

    def test_gemini_late_event_cannot_reopen_until_new_turn(self):
        self.receive("gemini", gemini("AfterAgent"))
        self.assertFalse(self.receive("gemini", gemini("AfterTool", NOW + 1), NOW + 1))
        self.assertTrue(self.receive("gemini", gemini("BeforeAgent", NOW + 2), NOW + 2))
        self.assertEqual(self.records()[0]["state"], "working")

    def test_gemini_session_end_cannot_reopen(self):
        self.receive("gemini", gemini("SessionEnd"))
        self.assertFalse(self.receive("gemini", gemini("BeforeAgent", NOW + 1), NOW + 1))

    def test_timestamp_replay_cannot_refresh_lease(self):
        payload = gemini()
        self.receive("gemini", payload)
        self.assertFalse(self.receive("gemini", payload, NOW + 50_000))
        self.assertEqual(self.records()[0]["state_until_ms"], NOW + 180_000)

    def test_older_event_does_not_overwrite_newer(self):
        self.receive("gemini", gemini("BeforeTool", NOW + 2), NOW + 2)
        self.assertFalse(self.receive("gemini", gemini("AfterAgent", NOW), NOW + 3))
        self.assertEqual(self.records()[0]["state"], "working")

    def test_untrusted_timestamp_is_rejected(self):
        for at in (NOW - 180_001, NOW + 5001):
            self.assertFalse(self.receive("gemini", gemini(at=at)))
        self.assertFalse(self.receive("gemini", dict(gemini(), timestamp="not a date")))
        self.assertEqual(self.records(), [])

    def test_private_modes(self):
        self.receive("cursor", cursor())
        for path in self.state.rglob("*"):
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.is_dir() else 0o600)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)

    def test_symlink_state_directory_rejected(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.state.symlink_to(outside, target_is_directory=True)
        self.assertFalse(self.receive("cursor", cursor()))
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlink_record_not_followed(self):
        record = activity.normalize_event("cursor", cursor(), NOW)
        directory = self.state / activity.ACTIVITY_DIR
        directory.mkdir(parents=True)
        target = self.root / "untouched"
        target.write_text("PRIVATE")
        (directory / f"cursor-{record['session_id_hash']}.json").symlink_to(target)
        self.assertFalse(self.receive("cursor", cursor()))
        self.assertEqual(target.read_text(), "PRIVATE")

    def test_metadata_never_logs_payload_or_exception_paths(self):
        self.assertFalse(activity.receive_payload("cursor", b'{"secret": "PRIVATE",', self.state, NOW))
        self.assertEqual(self.error(), {"error_code": "invalid_json", "error_count": 1,
                                        "observed_at_ms": NOW})

    def test_oversized_and_deep_json_fail_open(self):
        for raw in (b"x" * (activity.MAX_INPUT + 1), b"[" * 2000 + b"]" * 2000):
            result = self.run_receiver(raw)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(json.loads(result.stdout), {})
            self.assertEqual(result.stderr, b"")

    def test_payload_command_is_never_executed(self):
        target = self.root / "must-not-exist"
        result = self.run_receiver(json.dumps(cursor(command=f"touch '{target}'")).encode())
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"{}\n")
        self.assertFalse(target.exists())

    def test_pipe_without_eof_is_bounded(self):
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, b"{")
            started = time.monotonic()
            with self.assertRaisesRegex(activity.ActivityError, "stdin_timeout"):
                activity.read_bounded_stdin(read_fd, timeout=0.03)
            self.assertLess(time.monotonic() - started, 0.2)
        finally:
            os.close(read_fd)
            os.close(write_fd)

    def test_contended_lock_fails_open_with_bounded_wait(self):
        directory = self.state / activity.ACTIVITY_DIR
        directory.mkdir(parents=True)
        with (directory / ".lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            started = time.monotonic()
            result = self.run_receiver(json.dumps(cursor()).encode())
            self.assertLess(time.monotonic() - started, 2)
            self.assertEqual(result.stdout, b"{}\n")
            self.assertEqual(self.error()["error_code"], "lock_timeout")

    def test_concurrent_processes_preserve_distinct_runs(self):
        processes = []
        for index in range(8):
            proc = subprocess.Popen([sys.executable, str(SCRIPT), "receive", "--provider", "cursor",
                                     "--state-dir", str(self.state)], stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            proc.stdin.write(json.dumps(cursor(generation=str(index))).encode())
            proc.stdin.close()
            processes.append(proc)
        for proc in processes:
            proc.wait(timeout=4)
            self.assertEqual(proc.stdout.read(), b"{}\n")
            self.assertEqual(proc.stderr.read(), b"")
            proc.stdout.close()
            proc.stderr.close()
        self.assertEqual(len(self.records()), 8)

    def test_record_limit_and_reclaim_only_expired(self):
        with mock.patch.object(activity, "MAX_RECORDS", 2):
            self.receive("cursor", cursor(generation="one"))
            self.receive("cursor", cursor(generation="two"))
            self.assertFalse(self.receive("cursor", cursor(generation="three")))
            self.assertEqual(self.error()["error_code"], "record_capacity")
            self.receive("cursor", cursor("stop", generation="one", status="completed"), NOW + 1)
            self.assertTrue(self.receive("cursor", cursor(generation="three"), NOW + activity.TTL_MS + 2))
            self.assertEqual(len(self.records()), 2)

    def test_unapproved_record_fields_are_rejected(self):
        record = activity.normalize_event("cursor", cursor(), NOW)
        record["prompt"] = "PRIVATE"
        with self.assertRaisesRegex(activity.ActivityError, "invalid_record"):
            activity.persist_event(self.state, record, NOW)


class InstallPreviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.state = self.root / "Application Support/AI Passport/Bridge"
        self.launcher = self.state / activity.HOOK_DIR / "passport-activity"

    def tearDown(self):
        self.temporary.cleanup()

    def test_cursor_merge_preserves_existing_and_is_idempotent(self):
        existing = {"version": 1, "other": {"keep": True}, "hooks": {
            "beforeSubmitPrompt": [{"command": "existing-hook", "failClosed": True}],
            "afterFileEdit": [{"command": "formatter"}]}}
        original = json.dumps(existing)
        merged = activity.merge_hook_config("cursor", existing, self.launcher)
        self.assertEqual(json.dumps(existing), original)
        self.assertEqual(merged["hooks"]["beforeSubmitPrompt"][0], existing["hooks"]["beforeSubmitPrompt"][0])
        self.assertEqual(merged["hooks"]["afterFileEdit"], existing["hooks"]["afterFileEdit"])
        self.assertEqual(activity.merge_hook_config("cursor", merged, self.launcher), merged)
        self.assertFalse(merged["hooks"]["stop"][0]["failClosed"])

    def test_gemini_merge_preserves_all_settings_and_matcher(self):
        existing = {"model": {"name": "keep-model"}, "security": {"keep": True},
                    "hooks": {"BeforeAgent": [{"hooks": [{"name": "existing", "command": "keep"}]}]}}
        merged = activity.merge_hook_config("gemini", existing, self.launcher)
        self.assertEqual(merged["model"], existing["model"])
        self.assertEqual(merged["security"], existing["security"])
        self.assertEqual(merged["hooks"]["BeforeAgent"][0], existing["hooks"]["BeforeAgent"][0])
        self.assertEqual(merged["hooks"]["Notification"][0]["matcher"], "ToolPermission")
        self.assertEqual(activity.merge_hook_config("gemini", merged, self.launcher), merged)

    def test_malformed_existing_hooks_not_overwritten(self):
        for provider, existing in (("cursor", {"version": 2}), ("gemini", {"hooks": []}),
                                   ("cursor", {"hooks": {"stop": "wrong"}})):
            with self.assertRaises(activity.ActivityError):
                activity.merge_hook_config(provider, existing, self.launcher)

    def test_prepare_changes_only_isolated_review(self):
        cursor_path = self.root / ".cursor/hooks.json"
        gemini_path = self.root / ".gemini/settings.json"
        gemini_path.parent.mkdir()
        original = b'{"model": {"name": "existing"}, "custom": true}\n'
        gemini_path.write_bytes(original)
        manifest = activity.prepare_install(self.root / "review", sys.executable, self.state,
                                           cursor_path, gemini_path)
        self.assertTrue(manifest["prepared_only"])
        self.assertFalse(cursor_path.exists())
        self.assertEqual(gemini_path.read_bytes(), original)
        self.assertFalse(self.state.exists())
        self.assertIsNone(manifest["configs"][0]["original_sha256"])
        self.assertIsNotNone(manifest["configs"][1]["original_sha256"])
        for entry in manifest["configs"]:
            self.assertFalse(Path(entry["backup_path"]).exists())
        for path in (self.root / "review").iterdir():
            self.assertEqual(path.stat().st_mode & 0o777, 0o700 if path.name == "passport-activity" else 0o600)

    def test_review_must_not_be_live_state(self):
        with self.assertRaisesRegex(activity.ActivityError, "review_directory_not_isolated"):
            activity.prepare_install(self.state / "review", sys.executable, self.state)

    def make_launcher(self, runtime):
        support = self.launcher.parent
        support.mkdir(parents=True)
        self.launcher.write_text(activity.launcher_text(support, self.state))
        self.launcher.chmod(0o700)
        (support / "runtime.path").write_text(runtime + "\n")
        shutil.copyfile(SCRIPT, support / "passport_activity.py")

    def test_launcher_runtime_path_is_data_not_shell(self):
        marker = self.root / "DO-NOT-CREATE"
        self.make_launcher(f"/missing;touch {marker}")
        result = subprocess.run([str(self.launcher), "cursor"], input=b"{}", capture_output=True, timeout=2)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"{}\n")
        self.assertEqual(result.stderr, b"")
        self.assertFalse(marker.exists())

    def test_stable_launcher_with_bundled_or_current_python(self):
        self.make_launcher(sys.executable)
        result = subprocess.run([str(self.launcher), "cursor"], input=json.dumps(cursor()).encode(),
                                capture_output=True, timeout=3)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, b"{}\n")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(len(list((self.state / activity.ACTIVITY_DIR).glob("*.json"))), 1)

    def test_missing_runtime_mapping_is_quiet_and_fail_open(self):
        self.make_launcher(sys.executable)
        (self.launcher.parent / "runtime.path").unlink()
        result = subprocess.run([str(self.launcher), "gemini"], input=b"{}", capture_output=True, timeout=2)
        self.assertEqual(result.stdout, b"{}\n")
        self.assertEqual(result.stderr, b"")
        self.assertEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
