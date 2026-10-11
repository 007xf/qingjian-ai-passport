"""Anonymous statusLine fixtures only; never probe the installed Claude CLI."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

TOOLS = Path(__file__).parents[1] / "tools"
spec = importlib.util.spec_from_file_location("passport_claude", TOOLS / "passport_claude.py")
claude = importlib.util.module_from_spec(spec)
spec.loader.exec_module(claude)


class ClaudeQuotaTests(unittest.TestCase):
    NOW = 1_800_000_000_000
    ACCOUNT = "a" * 64

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state, self.profile = self.root / "state", self.root / "anonymous-profile"
        self.probe = mock.patch.object(claude, "_identity", return_value=self.ACCOUNT).start()
        self.addCleanup(mock.patch.stopall)

    def payload(self, percent=25, five_reset=None, seven_reset=None, session="fixture-session"):
        return {"session_id": session, "rate_limits": {
            "five_hour": {"used_percentage": percent, "resets_at": five_reset or self.NOW // 1000 + 3600},
            "seven_day": {"used_percentage": 50, "resets_at": seven_reset or self.NOW // 1000 + 86400}},
            "context_window": {"used_percentage": 99}, "credentials": "never-persist-this",
            "transcript_path": "/private/anonymous/transcript.jsonl", "prompt": "never-persist-this"}

    def capture(self, payload=None, now=None):
        return claude.capture_statusline(self.payload() if payload is None else payload, self.state,
                                        self.profile, self.NOW if now is None else now)

    def collect(self, now=None, refresh=False):
        return claude.collect_claude_quota(self.state, self.profile, self.NOW if now is None else now, refresh)

    def test_documented_used_percentages_and_resets_are_selected(self):
        value = self.capture()
        self.assertTrue(value["available"])
        self.assertEqual(value["status"], "fresh")
        self.assertEqual(value["source"], "claude_code_statusline")
        self.assertEqual(value["five_hour_used_percent"], 25)
        self.assertEqual(value["seven_day_used_percent"], 50)
        self.assertEqual(value["five_hour_reset_at_ms"], self.NOW + 3600000)
        self.assertEqual(value["expires_at_ms"], self.NOW + 180000)
        self.assertTrue(value["current_auth_confirmed"])
        self.assertFalse(value["account_verified"])
        self.assertTrue(value["account_unverified"])

    def test_private_cache_contains_only_anonymous_selected_data(self):
        self.capture()
        path = self.state / claude.CACHE_NAME
        text = path.read_text()
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        for secret in ("never-persist-this", "fixture-session", str(self.profile), "transcript", "context_window", "prompt"):
            self.assertNotIn(secret, text)
        self.assertEqual(set(json.loads(text)), {"version", "source", "profile_scope", "session_scope",
                         "account_scope", "observed_at_ms", "expires_at_ms", "windows"})

    def test_repeated_capture_and_refresh_do_not_extend_source_time(self):
        first = self.capture()
        later = self.capture(now=self.NOW + 60000)
        reread = self.collect(now=self.NOW + 90000, refresh=True)
        self.assertEqual(later["observed_at_ms"], first["observed_at_ms"])
        self.assertEqual(reread["expires_at_ms"], first["expires_at_ms"])
        self.assertEqual(reread["status"], "cached")
        expired = self.capture(now=self.NOW + 180000)
        self.assertFalse(expired["available"])
        self.assertEqual(expired["status"], "stale")

    def test_changed_quota_can_capture_a_new_observation(self):
        self.capture()
        changed = self.capture(self.payload(percent=26), self.NOW + 120000)
        self.assertEqual(changed["observed_at_ms"], self.NOW + 120000)
        self.assertEqual(changed["five_hour_used_percent"], 26)

    def test_expired_window_drop_does_not_refresh_unchanged_weekly_observation(self):
        payload = self.payload(five_reset=self.NOW // 1000 + 60)
        first = self.capture(payload)
        del payload["rate_limits"]["five_hour"]
        dropped = self.capture(payload, self.NOW + 61000)
        self.assertEqual(dropped["observed_at_ms"], first["observed_at_ms"])
        self.assertEqual(dropped["expires_at_ms"], first["expires_at_ms"])
        self.assertIsNone(dropped["five_hour_used_percent"])
        self.assertEqual(dropped["seven_day_used_percent"], 50)
        reread = self.collect(self.NOW + 181000)
        self.assertFalse(reread["available"])
        self.assertEqual(reread["observed_at_ms"], first["observed_at_ms"])

    def test_one_window_reset_does_not_expire_the_other_before_its_ttl(self):
        first = self.capture(self.payload(five_reset=self.NOW // 1000 + 60))
        self.assertEqual(first["expires_at_ms"], self.NOW + 180000)
        value = self.collect(self.NOW + 61000)
        self.assertTrue(value["available"])
        self.assertLess(value["five_hour_reset_at_ms"], self.NOW + 61000)
        self.assertGreater(value["seven_day_reset_at_ms"], self.NOW + 61000)
        self.assertEqual(value["observed_at_ms"], first["observed_at_ms"])

    def test_missing_window_before_its_reset_also_does_not_refresh_retained_window(self):
        payload = self.payload()
        first = self.capture(payload)
        del payload["rate_limits"]["five_hour"]
        value = self.capture(payload, self.NOW + 10000)
        self.assertTrue(value["available"])
        self.assertEqual(value["observed_at_ms"], first["observed_at_ms"])
        self.assertEqual(value["expires_at_ms"], first["expires_at_ms"])
        self.assertIsNone(value["five_hour_used_percent"])

    def test_real_remaining_window_usage_or_reset_change_can_capture_new_time(self):
        for key, value in (("used_percentage", 51), ("resets_at", self.NOW // 1000 + 2 * 86400)):
            with self.subTest(key=key):
                payload = self.payload(five_reset=self.NOW // 1000 + 60, session=key)
                self.capture(payload)
                del payload["rate_limits"]["five_hour"]
                payload["rate_limits"]["seven_day"][key] = value
                updated = self.capture(payload, self.NOW + 61000)
                self.assertEqual(updated["observed_at_ms"], self.NOW + 61000)
                self.assertEqual(updated["expires_at_ms"], self.NOW + 241000)

    def test_legacy_shorter_cache_expiry_is_never_extended_on_window_drop(self):
        payload = self.payload(five_reset=self.NOW // 1000 + 60)
        self.capture(payload)
        path = self.state / claude.CACHE_NAME
        saved = json.loads(path.read_text())
        saved["expires_at_ms"] = self.NOW + 60000
        path.write_text(json.dumps(saved))
        del payload["rate_limits"]["five_hour"]
        value = self.capture(payload, self.NOW + 61000)
        self.assertFalse(value["available"])
        self.assertEqual(value["observed_at_ms"], self.NOW)
        self.assertEqual(value["expires_at_ms"], self.NOW + 60000)

    def test_missing_quota_does_not_create_zero_or_renew_old_capture(self):
        first = self.capture()
        before = (self.state / claude.CACHE_NAME).read_bytes()
        missing = self.capture({"session_id": "fixture-session", "context_window": {"used_percentage": 10}}, self.NOW + 1000)
        self.assertFalse(missing["available"])
        self.assertIsNone(missing["five_hour_used_percent"])
        self.assertEqual((self.state / claude.CACHE_NAME).read_bytes(), before)
        self.assertEqual(self.collect()["observed_at_ms"], first["observed_at_ms"])

    def test_each_official_window_may_be_absent_and_zero_is_known(self):
        payload = {"session_id": "fixture-session", "rate_limits": {
            "five_hour": {"used_percentage": 0, "resets_at": self.NOW // 1000 + 60}}}
        value = self.capture(payload)
        self.assertEqual(value["five_hour_used_percent"], 0)
        self.assertIsNone(value["seven_day_used_percent"])
        self.assertTrue(value["available"])
        self.assertEqual(value["expires_at_ms"], self.NOW + 60000)
        expired = self.collect(self.NOW + 60000)
        self.assertFalse(expired["available"])
        self.assertEqual(expired["five_hour_used_percent"], 0)

    def test_invalid_percentage_or_reset_cannot_replace_valid_cache(self):
        self.capture()
        before = (self.state / claude.CACHE_NAME).read_bytes()
        for key, invalid in (("used_percentage", True), ("used_percentage", -1), ("used_percentage", 101),
                             ("used_percentage", float("nan")), ("resets_at", True), ("resets_at", "1800000100")):
            with self.subTest(key=key, invalid=invalid):
                payload = self.payload()
                payload["rate_limits"]["five_hour"][key] = invalid
                self.assertFalse(self.capture(payload)["available"])
                self.assertEqual((self.state / claude.CACHE_NAME).read_bytes(), before)

    def test_future_capture_clock_is_unknown_on_collection(self):
        self.capture(now=self.NOW + 10000)
        value = self.collect(self.NOW)
        self.assertFalse(value["available"])
        self.assertEqual(value["observed_at_ms"], 0)

    def test_known_account_switch_and_signout_cannot_reuse_cache(self):
        self.capture()
        self.probe.return_value = "b" * 64
        switched = self.collect()
        self.assertFalse(switched["available"])
        self.assertIsNone(switched["five_hour_used_percent"])
        self.assertEqual(switched["error_code"], "account_changed")
        self.probe.side_effect = claude.QuotaError("not_signed_in")
        self.assertIsNone(self.collect()["five_hour_used_percent"])

    def test_unreadable_identity_is_only_a_last_reading_without_renewal(self):
        first = self.capture()
        self.probe.side_effect = claude.QuotaError("identity_unavailable")
        value = self.collect(self.NOW + 10000)
        self.assertFalse(value["available"])
        self.assertEqual(value["status"], "last_reading")
        self.assertEqual(value["five_hour_used_percent"], 25)
        self.assertEqual(value["expires_at_ms"], first["expires_at_ms"])
        self.assertIsNone(value["account_scope"])

    def test_unverified_capture_cannot_later_be_bound_to_known_identity(self):
        self.probe.side_effect = claude.QuotaError("identity_unavailable")
        value = self.capture()
        self.assertFalse(value["available"])
        self.probe.side_effect = None
        self.assertEqual(self.collect()["error_code"], "account_unverified")
        repeated = self.capture(now=self.NOW + 10000)
        self.assertFalse(repeated["available"])
        self.assertEqual(repeated["observed_at_ms"], value["observed_at_ms"])

    def test_known_session_is_not_rebound_after_account_switch(self):
        self.capture()
        self.probe.return_value = "b" * 64
        value = self.capture(self.payload(percent=26), self.NOW + 10000)
        self.assertFalse(value["available"])
        self.assertEqual(value["error_code"], "session_account_unverified")
        self.assertEqual(self.collect()["error_code"], "account_changed")
        new_session = self.capture(self.payload(percent=26, session="new-fixture-session"), self.NOW + 20000)
        self.assertTrue(new_session["available"])

    def test_profile_change_cannot_reuse_cache(self):
        self.capture()
        value = claude.collect_claude_quota(self.state, self.root / "different-profile", self.NOW)
        self.assertFalse(value["available"])
        self.assertIsNone(value["five_hour_used_percent"])

    def test_account_change_during_capture_is_rejected(self):
        self.probe.side_effect = [self.ACCOUNT, "b" * 64]
        value = self.capture()
        self.assertEqual(value["error_code"], "account_changed")
        self.assertFalse((self.state / claude.CACHE_NAME).exists())

    def test_unsafe_or_corrupt_cache_is_unknown(self):
        self.capture()
        path = self.state / claude.CACHE_NAME
        path.chmod(0o644)
        self.assertFalse(self.collect()["available"])
        path.chmod(0o600)
        path.write_text('{"version":1,"version":1}')
        self.assertFalse(self.collect()["available"])

    def test_silent_cli_capture_selects_metadata_without_model_calls(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "path", [str(TOOLS), *sys.path]), \
                mock.patch.object(claude.sys, "stdin", io.StringIO(json.dumps(self.payload()))), \
                contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = claude.main(["--state-dir", str(self.state), "--claude-home", str(self.profile), "capture"])
        self.assertEqual(result, 0)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "")
        self.assertNotIn("never-persist-this", (self.state / claude.CACHE_NAME).read_text())


class IdentityProbeTests(unittest.TestCase):
    def test_gui_path_can_find_the_standard_native_install_without_running_it(self):
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            executable = home / ".local/bin/claude"
            executable.parent.mkdir(parents=True)
            executable.write_text("anonymous executable fixture")
            executable.chmod(0o700)
            with mock.patch.object(claude.shutil, "which", return_value=None), \
                    mock.patch.object(claude.Path, "home", return_value=home), \
                    mock.patch.object(claude.subprocess, "run") as run:
                self.assertEqual(claude._claude_executable(), str(executable))
            run.assert_not_called()

    def test_only_documented_non_model_metadata_command_runs(self):
        profile = Path("/anonymous/profile")
        payload = {"loggedIn": True, "authMethod": "claude.ai", "configDirectory": str(profile),
                   "email": "fixture@example.invalid", "orgId": "fixture-org", "accessToken": "must-not-leave-memory"}
        with mock.patch.object(claude.shutil, "which", return_value="/fixture/claude"), \
                mock.patch.object(claude.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(payload).encode())) as run:
            scope = claude._identity(profile)
        self.assertEqual(len(scope), 64)
        self.assertEqual(run.call_args.args[0], ["/fixture/claude", "auth", "status"])
        self.assertEqual(run.call_args.kwargs["stderr"], subprocess.DEVNULL)
        self.assertEqual(run.call_args.kwargs["env"]["CLAUDE_CONFIG_DIR"], str(profile))
        self.assertNotIn("fixture", scope)

    def test_non_subscription_missing_identity_and_profile_mismatch_fail_closed(self):
        profile = Path("/anonymous/profile")
        base = {"loggedIn": True, "authMethod": "claude.ai", "configDirectory": str(profile),
                "email": "fixture@example.invalid", "orgId": "fixture-org"}
        for change in ({"authMethod": "api_key"}, {"email": None}, {"orgId": []},
                       {"configDirectory": "/anonymous/other"}, {"loggedIn": False}):
            with self.subTest(change=change), mock.patch.object(claude.shutil, "which", return_value="/fixture/claude"), \
                    mock.patch.object(claude.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps({**base, **change}).encode())):
                with self.assertRaises(claude.QuotaError):
                    claude._identity(profile)

    def test_published_nullable_orgid_does_not_disable_a_personal_account(self):
        profile = Path("/anonymous/profile")
        # Shape from the official authStatus claude.ai branch, where the CLI
        # serializes emailAddress/organizationUuid with a null fallback.
        payload = {"loggedIn": True, "authMethod": "claude.ai", "apiProvider": "firstParty",
                   "configDirectory": str(profile), "email": "fixture@example.invalid",
                   "orgId": None, "orgName": None, "subscriptionType": "pro"}
        with mock.patch.object(claude.shutil, "which", return_value="/fixture/claude"), \
                mock.patch.object(claude.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, json.dumps(payload).encode())):
            scope = claude._identity(profile)
            now = ClaudeQuotaTests.NOW
            statusline = {"session_id": "fixture-session", "rate_limits": {
                "five_hour": {"used_percentage": 25, "resets_at": now // 1000 + 3600}}}
            with tempfile.TemporaryDirectory() as folder:
                captured = claude.capture_statusline(statusline, folder, profile, now)
                collected = claude.collect_claude_quota(folder, profile, now + 1000)
        self.assertEqual(len(scope), 64)
        self.assertTrue(captured["available"])
        self.assertTrue(collected["available"])
        self.assertEqual(collected["account_scope"], scope)


if __name__ == "__main__":
    unittest.main()
