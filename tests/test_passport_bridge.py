#!/usr/bin/env python3
"""Host tests: real counter parsing, cycle policy, encoding and USB protocol fake."""
import importlib.util
import io
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("passport_bridge", Path(__file__).parents[1] / "tools/passport_bridge.py")
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)


def iso(second):
    return bridge.dt.datetime.fromtimestamp(second, bridge.dt.timezone.utc).isoformat()


def meta(sid, created, parent=None):
    payload = {"id": sid, "timestamp": iso(created)}
    if parent:
        payload["forked_from_id"] = parent
    return {"type": "session_meta", "payload": payload}


def count(at, total, last, limits=None):
    return {"timestamp": iso(at), "type": "event_msg", "payload": {
        "type": "token_count", "info": {"total_token_usage": {"total_tokens": total},
        "last_token_usage": {"total_tokens": last}}, "rate_limits": limits}}


def write_events(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x) + "\n" for x in events))


def write_auth(home, account="account-a", subject="subject-a"):
    home.mkdir(parents=True, exist_ok=True)
    payload = bridge.base64.urlsafe_b64encode(json.dumps({"sub": subject}).encode()).decode().rstrip("=")
    credential = "header." + payload + ".private-credential"
    path = home / "auth.json"
    path.write_text(json.dumps({"auth_mode": "chatgpt", "tokens": {
        "account_id": account, "id_token": credential,
        "access_token": credential, "refresh_token": "private-refresh"}}))
    path.chmod(0o600)


class CodexAccountIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "profile"
        self.state = self.root / "state"
        self.now = bridge.time.time()
        self.args = SimpleNamespace(state_dir=str(self.state), codex_home=str(self.home),
                                    codex_cli=None, no_quota=False, refresh_quota=False)
        write_auth(self.home)

    def quota(self, account="hashed-a", used=27):
        return {"source": "official_app_server", "observed_at": self.now,
                "account_hash": account, "limits": {"rateLimitsByLimitId": {"codex": {
                    "limitId": "codex", "primary": {"usedPercent": used,
                    "windowDurationMins": 10080, "resetsAt": int(self.now) + 86400}}}}}

    def prime(self):
        with mock.patch.object(bridge, "app_server_quota", return_value=self.quota()):
            return bridge.read_tokens(self.args)

    def test_same_current_account_can_reuse_fresh_quota_cache(self):
        self.prime()
        with mock.patch.object(bridge, "app_server_quota") as read:
            result = bridge.read_tokens(self.args)
        read.assert_not_called()
        self.assertTrue(result["account_verified"])
        self.assertEqual(result["account_hash"], "hashed-a")

    def test_account_switch_forces_live_read_even_inside_sixty_seconds(self):
        self.prime()
        write_auth(self.home, "account-b", "subject-b")
        with mock.patch.object(bridge, "app_server_quota", return_value=self.quota("hashed-b", 63)) as read:
            result = bridge.read_tokens(self.args)
        read.assert_called_once_with(None, codex_home=self.home.resolve())
        self.assertEqual(result["account_hash"], "hashed-b")
        self.assertEqual(result["codex_quota"]["w1_used_percent"], 63)

    def test_switch_followed_by_network_failure_does_not_return_old_quota(self):
        self.prime()
        write_auth(self.home, "account-b", "subject-b")
        with mock.patch.object(bridge, "app_server_quota", side_effect=bridge.BridgeError("offline")):
            result = bridge.read_tokens(self.args)
        self.assertFalse(result["account_verified"])
        self.assertIsNone(result["account_hash"])
        self.assertFalse(result["codex_quota_ready"])

    def test_sign_out_does_not_reuse_fresh_cache_or_last_account_identity(self):
        self.prime()
        (self.home / "auth.json").unlink()
        with mock.patch.object(bridge, "app_server_quota", side_effect=bridge.CodexAuthError("not signed in")):
            result = bridge.read_tokens(self.args)
        self.assertFalse(result["account_verified"])
        self.assertIsNone(result["account_hash"])
        self.assertFalse(result["codex_quota_ready"])

    def test_explicit_rejected_live_auth_cannot_fall_back_to_same_context_cache(self):
        self.prime()
        self.args.refresh_quota = True
        with mock.patch.object(bridge, "app_server_quota", side_effect=bridge.CodexAuthError("not signed in")):
            result = bridge.read_tokens(self.args)
        self.assertFalse(result["account_verified"])
        self.assertFalse(result["codex_quota_ready"])

    def test_sign_out_while_live_read_is_in_flight_discards_response(self):
        def response(*args, **kwargs):
            (self.home / "auth.json").unlink()
            return self.quota()
        with mock.patch.object(bridge, "app_server_quota", side_effect=response):
            result = bridge.read_tokens(self.args)
        self.assertFalse(result["account_verified"])
        self.assertIsNone(result["account_hash"])

    def test_auth_context_is_stable_across_refresh_and_distinct_per_profile(self):
        before = bridge.codex_auth_context(self.home)
        raw = json.loads((self.home / "auth.json").read_text())
        raw["tokens"]["refresh_token"] = "new-private-refresh"
        raw["tokens"]["access_token"] = "new-private-access"
        (self.home / "auth.json").write_text(json.dumps(raw))
        self.assertEqual(bridge.codex_auth_context(self.home), before)
        other = self.root / "other-profile"
        write_auth(other)
        self.assertNotEqual(bridge.codex_auth_context(other), before)
        self.assertEqual(len(before), 64)

    def test_missing_local_auth_keychain_mode_requires_live_account_every_time(self):
        (self.home / "auth.json").unlink()
        with mock.patch.object(bridge, "app_server_quota", return_value=self.quota()) as read:
            self.assertTrue(bridge.read_tokens(self.args)["account_verified"])
            self.assertTrue(bridge.read_tokens(self.args)["account_verified"])
        self.assertEqual(read.call_count, 2)

    def test_partial_credentials_do_not_authorize_old_account_cache(self):
        self.prime()
        auth = self.home / "auth.json"
        raw = json.loads(auth.read_text())
        raw["tokens"].pop("access_token")
        auth.write_text(json.dumps(raw))
        self.assertIsNone(bridge.codex_auth_context(self.home))
        with mock.patch.object(bridge, "app_server_quota", side_effect=bridge.BridgeError("offline")):
            result = bridge.read_tokens(self.args)
        self.assertFalse(result["account_verified"])
        self.assertFalse(result["codex_quota_ready"])

    def test_cache_contains_no_credential_subject_or_plain_account(self):
        self.prime()
        text = (self.state / "02 quota.json").read_text()
        for forbidden in ("private-credential", "private-refresh", "subject-a", "account-a", "id_token", "access_token"):
            self.assertNotIn(forbidden, text)

    def test_custom_profile_is_passed_to_app_server_environment(self):
        class StopProbe(Exception):
            pass
        with mock.patch.object(bridge.subprocess, "Popen", side_effect=StopProbe) as start:
            with self.assertRaises(StopProbe):
                bridge.app_server_quota("/unused/codex", codex_home=self.home)
        self.assertEqual(start.call_args.kwargs["env"]["CODEX_HOME"], str(self.home.resolve()))
        self.assertEqual(start.call_args.args[0], ["/unused/codex", "app-server"])

    def test_codex_explicit_binary_has_priority_over_discovery(self):
        with mock.patch.object(bridge.Path, "is_file") as check:
            self.assertEqual(bridge.find_codex_cli("/chosen/codex"), "/chosen/codex")
        check.assert_not_called()

    def test_codex_user_app_is_found_before_old_path_cli(self):
        binary = self.root / "Applications/Codex.app/Contents/Resources/codex"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o700)
        (binary.parents[1] / "Info.plist").write_bytes(bridge.plistlib.dumps({"CFBundleIdentifier": "com.openai.codex"}))
        with mock.patch.object(bridge.Path, "home", return_value=self.root), \
                mock.patch.object(bridge.shutil, "which", return_value="/old/codex") as which:
            self.assertEqual(bridge.find_codex_cli(app_roots=(self.root / "Applications",)), str(binary))
        which.assert_not_called()

    def test_codex_path_is_used_when_no_standard_app_exists(self):
        with mock.patch.object(bridge.Path, "is_file", return_value=False), \
                mock.patch.object(bridge.shutil, "which", return_value="/custom/bin/codex"):
            self.assertEqual(bridge.find_codex_cli(app_roots=(self.root / "Applications",)), "/custom/bin/codex")

    def test_codex_missing_installation_has_actionable_error(self):
        with mock.patch.object(bridge.Path, "is_file", return_value=False), \
                mock.patch.object(bridge.shutil, "which", return_value=None):
            with self.assertRaisesRegex(bridge.BridgeError, "未找到 Codex"):
                bridge.find_codex_cli(app_roots=(self.root / "Applications",))

    def make_codex_app(self, root, name, bundle_id="com.openai.codex"):
        app = root / name
        binary = app / "Contents/Resources/codex"
        binary.parent.mkdir(parents=True)
        binary.write_text("#!/bin/sh\n")
        binary.chmod(0o700)
        (app / "Contents/Info.plist").write_bytes(bridge.plistlib.dumps({"CFBundleIdentifier": bundle_id}))
        return binary

    def test_renamed_codex_is_discovered_in_each_application_root(self):
        for location in ("user", "system"):
            with self.subTest(location=location):
                root = self.root / location
                binary = self.make_codex_app(root, "ChatGPT.app")
                with mock.patch.object(bridge.shutil, "which", return_value="/old/codex") as which:
                    self.assertEqual(bridge.find_codex_cli(app_roots=(root,)), str(binary))
                which.assert_not_called()

    def test_normal_chatgpt_and_misnamed_non_codex_bundle_are_not_selected(self):
        root = self.root / "Applications"
        self.make_codex_app(root, "ChatGPT.app", "com.openai.chat")
        self.make_codex_app(root, "Codex.app", "com.example.other")
        with mock.patch.object(bridge.shutil, "which", return_value="/fallback/codex"):
            self.assertEqual(bridge.find_codex_cli(app_roots=(root,)), "/fallback/codex")

    def test_verified_standard_codex_precedes_renamed_bundles(self):
        user, system = self.root / "user", self.root / "system"
        self.make_codex_app(user, "Renamed.app")
        binary = self.make_codex_app(system, "Codex.app")
        self.assertEqual(bridge.find_codex_cli(app_roots=(user, system)), str(binary))

    def test_invalid_or_missing_bundle_identity_is_skipped(self):
        root = self.root / "Applications"
        binary = self.make_codex_app(root, "Codex.app")
        info = binary.parents[1] / "Info.plist"
        for data in (b"<plist>broken", b"not a plist", bridge.plistlib.dumps([])):
            with self.subTest(data=data):
                info.write_bytes(data)
                with mock.patch.object(bridge.shutil, "which", return_value="/fallback/codex"):
                    self.assertEqual(bridge.find_codex_cli(app_roots=(root,)), "/fallback/codex")
        info.unlink()
        self.assertIsNone(bridge.codex_app_cli(binary.parents[2]))

    def test_gui_path_keeps_existing_order_and_adds_node_runtime_directories(self):
        class StopProbe(Exception):
            pass
        original = "/chosen/node:/usr/bin:/bin"
        with mock.patch.dict(bridge.os.environ, {"PATH": original}, clear=True), \
                mock.patch.object(bridge.subprocess, "Popen", side_effect=StopProbe) as start:
            with self.assertRaises(StopProbe):
                bridge.app_server_quota("/custom/cli/codex")
            self.assertEqual(bridge.os.environ["PATH"], original)
        paths = start.call_args.kwargs["env"]["PATH"].split(bridge.os.pathsep)
        self.assertEqual(paths[:3], original.split(bridge.os.pathsep))
        self.assertIn("/custom/cli", paths)
        self.assertIn("/opt/homebrew/bin", paths)
        self.assertIn("/usr/local/bin", paths)

    def test_child_path_does_not_duplicate_an_existing_homebrew_directory(self):
        class StopProbe(Exception):
            pass
        with mock.patch.dict(bridge.os.environ, {"PATH": "/opt/homebrew/bin:/usr/bin"}, clear=True), \
                mock.patch.object(bridge.subprocess, "Popen", side_effect=StopProbe) as start:
            with self.assertRaises(StopProbe):
                bridge.app_server_quota("/opt/homebrew/bin/codex")
        self.assertEqual(start.call_args.kwargs["env"]["PATH"].split(bridge.os.pathsep).count("/opt/homebrew/bin"), 1)

    def test_symlink_or_malformed_auth_never_authorizes_cache_shortcut(self):
        auth = self.home / "auth.json"
        original = self.home / "original.json"
        auth.rename(original)
        auth.symlink_to(original)
        self.assertIsNone(bridge.codex_auth_context(self.home))
        auth.unlink()
        auth.write_text("malformed sensitive content")
        self.assertIsNone(bridge.codex_auth_context(self.home))

    def test_different_profile_does_not_mix_counters_and_original_cache_is_preserved(self):
        first = self.root / "first"
        second = self.root / "second"
        write_events(first / "sessions/a.jsonl", [meta("a", 100), count(110, 25, 25)])
        write_events(second / "sessions/b.jsonl", [meta("b", 100), count(120, 60, 60)])
        # Simulate an existing pre-isolation cache. It must retain its records.
        original = bridge.CounterCache(self.state)
        original.scan(first)
        original.db.close()
        selected = bridge.CounterCache(self.state, source_root=second)
        selected.scan(second)
        self.assertNotEqual(selected.state_dir, self.state)
        self.assertEqual(selected.totals(0)["lifetime_tokens"], 60)
        self.assertEqual(selected.totals(0)["last_token_event_at"], 120)
        selected.db.close()
        original = bridge.CounterCache(self.state, source_root=first)
        self.assertEqual(original.state_dir, self.state)
        self.assertEqual(original.totals(0)["lifetime_tokens"], 25)
        original.db.close()

    def test_empty_first_profile_stays_bound_when_next_profile_has_logs(self):
        original = bridge.CounterCache(self.state, source_root=self.home)
        original.db.close()
        other = self.root / "other"
        write_events(other / "sessions/a.jsonl", [meta("a", 100), count(110, 30, 30)])
        selected = bridge.CounterCache(self.state, source_root=other)
        selected.scan(other)
        self.assertNotEqual(selected.state_dir, self.state)
        selected.db.close()
        original = bridge.CounterCache(self.state, source_root=self.home)
        self.assertEqual(original.totals(0)["lifetime_tokens"], 0)
        original.db.close()

    def test_custom_profile_uses_separate_quota_and_cycle_files(self):
        self.prime()
        original_quota = (self.state / "02 quota.json").read_bytes()
        other = self.root / "other"
        write_auth(other, "account-b", "subject-b")
        self.args.codex_home = str(other)
        with mock.patch.object(bridge, "app_server_quota", return_value=self.quota("hashed-b", 66)):
            result = bridge.read_tokens(self.args)
        self.assertEqual(result["account_hash"], "hashed-b")
        self.assertEqual((self.state / "02 quota.json").read_bytes(), original_quota)
        profiles = list((self.state / "11 profiles").iterdir())
        self.assertEqual(len(profiles), 1)
        self.assertTrue((profiles[0] / "02 quota.json").is_file())
        self.assertTrue((profiles[0] / "03 cycle hashed-b.json").is_file())


class CounterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.cache = bridge.CounterCache(self.root / "state")

    def tearDown(self):
        self.cache.db.close()
        self.temp.cleanup()

    def test_cumulative_not_summed_and_archived_copy_deduplicated(self):
        events = [meta("a", 100), count(110, 10, 10), count(120, 25, 15), count(120, 25, 15)]
        write_events(self.root / "sessions/a.jsonl", events)
        write_events(self.root / "archived_sessions/a.jsonl", events)
        self.cache.scan(self.root)
        result = self.cache.totals(115)
        self.assertEqual(result["lifetime_tokens"], 25)
        self.assertEqual(result["cycle_tokens"], 15)
        self.assertEqual(result["session_count"], 1)
        self.assertEqual(self.cache.scan(self.root)["files_scanned"], 0)

    def test_incremental_append_and_partial_tail(self):
        path = self.root / "sessions/a.jsonl"
        write_events(path, [meta("a", 100), count(110, 10, 10)])
        self.cache.scan(self.root)
        with path.open("a") as stream:
            stream.write(json.dumps(count(120, 30, 20)))
        self.assertTrue(self.cache.scan(self.root)["scan_incomplete"])
        self.assertEqual(self.cache.totals(100)["cycle_tokens"], 10)
        with path.open("a") as stream:
            stream.write("\n")
        self.cache.scan(self.root)
        self.assertEqual(self.cache.totals(100)["cycle_tokens"], 30)

    def test_large_picture_does_not_hide_later_counters(self):
        path = self.root / "sessions/a.jsonl"
        write_events(path, [meta("a", 100), {"type":"response_item", "payload":{"image":"x"*(8*1024*1024)}}, count(120, 30, 30)])
        result = self.cache.scan(self.root)
        self.assertEqual(result["malformed_records"], 0)
        self.assertIsNone(result["newest_uncertain_mtime"])
        self.assertEqual(self.cache.totals(100)["cycle_tokens"], 30)

    def test_oversized_counter_remains_uncertain(self):
        path = self.root / "sessions/a.jsonl"
        event = count(120, 30, 30)
        event["payload"]["extra"] = "x"*(8*1024*1024)
        write_events(path, [meta("a", 100), event])
        result = self.cache.scan(self.root)
        self.assertEqual(result["malformed_records"], 1)
        self.assertIsNotNone(result["newest_uncertain_mtime"])

    def test_old_picture_false_positive_rescanned_not_blindly_cleared(self):
        path = self.root / "sessions/a.jsonl"
        write_events(path, [meta("a", 100), count(120, 30, 30)])
        self.cache.scan(self.root)
        self.cache.db.execute("UPDATE files SET malformed=1")
        self.cache.db.execute("PRAGMA user_version=0")
        self.cache.db.commit()
        self.cache.db.close()
        self.cache = bridge.CounterCache(self.root / "state")
        result = self.cache.scan(self.root)
        self.assertEqual(result["files_scanned"], 1)
        self.assertEqual(result["malformed_records"], 0)
        self.assertEqual(self.cache.totals(100)["cycle_tokens"], 30)

    def test_record_type_uses_top_level_metadata_only(self):
        self.assertEqual(bridge.counter_record_kind(b'{"timestamp":"2026", "type":"response_item", "payload":'), "response_item")
        self.assertIsNone(bridge.counter_record_kind(b'{"payload":{"type":"response_item"},"type":"event_msg"}'))
        self.assertIsNone(bridge.counter_record_kind(b'{"type": ["response_item"]}'))

    def test_fork_copies_inherited_metadata_and_events(self):
        original = [meta("parent", 100), count(110, 10, 10), count(120, 25, 15)]
        write_events(self.root / "sessions/parent.jsonl", original)
        fork = [meta("child", 125, "parent"), *original, count(130, 30, 5)]
        write_events(self.root / "sessions/child.jsonl", fork)
        self.cache.scan(self.root)
        self.assertEqual(self.cache.totals(100)["lifetime_tokens"], 30)
        self.assertEqual(self.cache.totals(126)["cycle_tokens"], 5)

    def test_subagent_new_counter_after_inherited_events(self):
        original = [meta("parent", 100), count(110, 100, 100)]
        write_events(self.root / "sessions/parent.jsonl", original)
        write_events(self.root / "sessions/child.jsonl", [meta("child", 120, "parent"), *original, count(130, 40, 40)])
        self.cache.scan(self.root)
        result = self.cache.totals(120)
        self.assertEqual(result["lifetime_tokens"], 140)
        self.assertEqual(result["cycle_tokens"], 40)

    def test_unavailable_and_invalid_counts_not_zero_measurements(self):
        bad = count(110, -1, -1)
        write_events(self.root / "sessions/a.jsonl", [meta("a", 100), bad, count(120, True, True), count(130, 10, 10)])
        self.cache.scan(self.root)
        result = self.cache.totals(None)
        self.assertIsNone(result["cycle_tokens"])
        self.assertEqual(result["lifetime_tokens"], 10)

    def test_historical_first_total_not_assigned_wholly_to_new_cycle(self):
        write_events(self.root / "sessions/a.jsonl", [meta("a", 100), count(200, 1000, 20)])
        self.cache.scan(self.root)
        result = self.cache.totals(150)
        self.assertEqual(result["lifetime_tokens"], 1000)
        self.assertEqual(result["cycle_tokens"], 20)
        self.assertEqual(result["uncertain_boundaries"], 1)

    def test_only_counter_payloads_persist(self):
        secret = "PRIVATE-CONVERSATION-DO-NOT-INDEX"
        events = [meta("a", 100), {"type": "response_item", "payload": {"content": secret}}, count(110, 10, 10)]
        write_events(self.root / "sessions/a.jsonl", events)
        self.cache.scan(self.root)
        self.assertNotIn(secret, "\n".join(self.cache.db.iterdump()))

    def test_scan_budget_is_explicit(self):
        write_events(self.root / "sessions/a.jsonl", [meta("a", 100), count(110, 10, 10)])
        result = self.cache.scan(self.root, budget=0)
        self.assertTrue(result["scan_incomplete"])

    def test_repeated_log_quota_does_not_refresh_observation_or_allow_out_of_order_values(self):
        quota = {"limit_id": "codex", "primary": {"used_percent": 25, "window_minutes": 10080, "resets_at": 700000}}
        path = self.root / "sessions/a.jsonl"
        write_events(path, [meta("a", 100), count(110, 10, 10, quota), count(130, 20, 10, quota)])
        self.cache.scan(self.root)
        self.assertEqual(self.cache.log_quota()["observed_at"], 110)
        old_quota = {"limit_id": "codex", "primary": {"used_percent": 22, "window_minutes": 10080, "resets_at": 700000}}
        write_events(self.root / "sessions/b.jsonl", [meta("b", 100), count(120, 10, 10, old_quota)])
        self.cache.scan(self.root)
        self.assertEqual(self.cache.log_quota()["limits"]["primary"]["usedPercent"], 25)
        with path.open("a") as stream:
            stream.write(json.dumps(count(150, 30, 10, old_quota)) + "\n")
        self.cache.scan(self.root)
        self.assertEqual(self.cache.log_quota()["observed_at"], 150)
        self.assertEqual(self.cache.log_quota()["limits"]["primary"]["usedPercent"], 22)


class CycleTests(unittest.TestCase):
    def quota(self, start, source="official_app_server", used=50):
        return {"source": source, "limits": {"rateLimitsByLimitId": {"codex": {"primary": {
            "usedPercent": used, "windowDurationMins": 10080, "resetsAt": start + 604800}}}}}

    def test_stage_thresholds(self):
        expected = {0: 0, 77_777_776: 0, 77_777_777: 1, 555_555_554: 1, 555_555_555: 2, 1_000_000_000: 2}
        for value, stage in expected.items():
            self.assertEqual(bridge.stage_for(value), stage)

    def test_custom_threshold_boundaries(self):
        for value, expected in ((9, 0), (10, 1), (29, 1), (30, 2)):
            self.assertEqual(bridge.stage_for(value, 10, 30), expected)
        for config in ({"threshold1": 0}, {"threshold1": 20, "threshold2": 10},
                       {"threshold1": True}, {"threshold2": 2**53}, {"threshold1": "100"}):
            with self.assertRaises(bridge.BridgeError):
                bridge.thresholds(config)

    def test_first_cycle_and_weekly_rollover(self):
        first = bridge.choose_cycle({}, self.quota(1000), 2000)
        self.assertEqual(first["cycle_started_at"], 1000)
        second = bridge.choose_cycle(first, self.quota(605800), 606000)
        self.assertEqual(second["cycle_started_at"], 605800)
        self.assertEqual(second["reset_source"], "weekly_window")

    def test_usage_drop_is_not_card_reset(self):
        first = bridge.choose_cycle({}, self.quota(1000, used=90), 2000)
        second = bridge.choose_cycle(first, self.quota(1000, used=0), 3000)
        self.assertEqual(second["cycle_started_at"], 1000)

    def test_manual_card_reset_and_next_natural_weekly_rollover(self):
        first = bridge.choose_cycle({}, self.quota(1000), 2000)
        manual = bridge.choose_cycle(first, self.quota(1000), 3000, manual=True)
        self.assertEqual(manual["cycle_started_at"], 3000)
        continued = bridge.choose_cycle(manual, self.quota(1000), 4000)
        self.assertEqual(continued["cycle_started_at"], 3000)
        rollover = bridge.choose_cycle(continued, self.quota(605800), 606000)
        self.assertEqual(rollover["cycle_started_at"], 605800)

    def test_changed_deadline_is_only_ambiguity(self):
        first = bridge.choose_cycle({}, self.quota(1000), 2000)
        changed = bridge.choose_cycle(first, self.quota(2000), 3000)
        self.assertEqual(changed["cycle_started_at"], 1000)
        self.assertIn("confirmation", changed["reset_detection"])

    def test_expired_source_cannot_continue_previous_cycle(self):
        first = bridge.choose_cycle({}, self.quota(1000), 2000)
        expired = bridge.choose_cycle(first, None, 606000)
        self.assertIsNone(expired["cycle_started_at"])

    def test_spark_is_not_account_cycle(self):
        limits = {"limit_id": "codex_bengalfox", "secondary": {"window_minutes": 10080, "resets_at": 700000}}
        self.assertIsNone(bridge.weekly_window(limits))

    def test_malformed_quota_is_unavailable(self):
        for limits in (None, [], {"rateLimits": []}, {"rateLimitsByLimitId": []},
                       {"primary": {"windowDurationMins": "10080", "resetsAt": 700000}},
                       {"primary": {"windowDurationMins": 10080, "resetsAt": True}}):
            self.assertIsNone(bridge.weekly_window(limits))


class ConfigTests(unittest.TestCase):
    def test_profile_utf8_limits_and_control_characters(self):
        initial = bridge.validate_config({})
        self.assertEqual(initial["name"], "苍崎青子")
        self.assertEqual(initial["title"], "MISS BLUE")
        self.assertEqual(initial["intro"], "如果你惹怒了我，我将会开启3技能")
        self.assertEqual(initial["threshold1"], 77_777_777)
        self.assertFalse(initial["feature_all"])
        self.assertEqual(initial["feature_mask"], 147)
        for bad in ({"name": "高" * 9}, {"name": "a\nb"}, {"name": "😀"},
                    {"feature_all": 1}, {"feature_mask": 256}, {"feature_mask": True},
                    {"avatar_path": "relative.png"}, {"avatar_default": True, "avatar_path": "/some/image.png"}):
            with self.assertRaises(bridge.BridgeError):
                bridge.validate_config(bad)

    def test_avatar_apz1_byte_order_and_size(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow unavailable in this host interpreter")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "avatar.png"
            Image.new("RGB", (32, 32), (255, 0, 0)).save(path)
            result = bridge.avatar_bytes({"avatar_path": str(path)})
            self.assertEqual(len(result), 8224)
            self.assertEqual(result[:4], b"APZ1")
            self.assertEqual(bridge.AVATAR_HEADER.unpack_from(result)[1:3], (128, 128))
            self.assertEqual(bridge.decode_avatar_bytes(result), b"\x00\xf8" * (128 * 128))

    def test_tall_avatar_preserves_top_bottom_and_black_margins(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow unavailable in this host interpreter")
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "full-body.png"
            image = Image.new("RGB", (64, 128), (0, 255, 0))
            for y in range(8):
                for x in range(64):
                    image.putpixel((x, y), (255, 0, 0))
                    image.putpixel((x, 127-y), (0, 0, 255))
            image.save(path)
            data = bridge.avatar_bytes({"avatar_path": str(path)})
            pixels = bridge.decode_avatar_bytes(data)

            def pixel(x, y):
                i = y * 128 + x
                return int.from_bytes(pixels[i*2:i*2+2], "little")

            self.assertEqual(pixel(64, 0), 0xF800)
            self.assertEqual(pixel(64, 127), 0x001F)
            self.assertEqual(pixel(0, 64), 0)
            self.assertEqual(pixel(127, 64), 0)

    def test_three_stage_paths_validated_before_device_changes(self):
        with self.assertRaises(bridge.BridgeError):
            bridge.validate_config({"avatar_paths": {"3": "/image.png"}})
        with self.assertRaises(bridge.BridgeError):
            bridge.validate_config({"avatar_paths": {"1": "relative.png"}})
        config = bridge.validate_config({"avatar_paths": {"0": "/winter.png", "2": "/red.png"}})
        with mock.patch.object(bridge, "avatar_bytes", side_effect=[b"first", b"last"]) as convert:
            self.assertEqual(bridge.prepare_avatars(config, {"stage": 0}), {"0": b"first", "2": b"last"})
        self.assertEqual(convert.call_count, 2)


class CodexQuotaTests(unittest.TestCase):
    def quota(self, observed=1000, primary=None, secondary=None):
        return bridge.normalized_quota_cache({"source": "official_app_server", "observed_at": observed,
            "account_hash": "hashed-account", "limits": {"rateLimitsByLimitId": {"codex": {
                "limitId": "codex", "primary": primary, "secondary": secondary}}}})

    def window(self, used=25, duration=300, reset=2000):
        return {"usedPercent": used, "windowDurationMins": duration, "resetsAt": reset}

    def test_real_two_windows_preserved_without_hardcoded_duration(self):
        quota = self.quota(primary=self.window(17.5, 60, 3000), secondary=self.window(80, 10080, 700000))
        result = bridge.codex_quota_snapshot(quota, now=1010)
        payload = result["payload"]
        self.assertEqual(payload["w1_used_percent"], 17.5)
        self.assertEqual(payload["w1_duration_min"], 60)
        self.assertEqual(payload["w1_reset_s"], 3000)
        self.assertEqual(payload["w2_used_percent"], 80)
        self.assertEqual(payload["w2_duration_min"], 10080)
        self.assertEqual(payload["w2_reset_s"], 700000)
        self.assertTrue(result["ready"])
        self.assertFalse(result["stale"])
        self.assertEqual(bridge.weekly_window(quota["limits"])["resets_at"], 700000)

    def test_missing_or_invalid_percentage_is_unknown_not_zero(self):
        for value in (None, -1, float("nan"), float("inf"), "23", True):
            with self.subTest(value=value):
                quota = self.quota(primary=self.window(value))
                result = bridge.codex_quota_snapshot(quota, now=1010)
                self.assertIsNone(result["payload"]["w1_used_percent"])
                self.assertEqual(result["payload"]["w1_duration_min"], 300)
                self.assertFalse(result["ready"])
                json.dumps(result, allow_nan=False)
        for value in (0, 100):
            result = bridge.codex_quota_snapshot(self.quota(primary=self.window(value)), now=1010)
            self.assertEqual(result["payload"]["w1_used_percent"], value)
            self.assertTrue(result["ready"])

    def test_overage_is_clamped_but_original_preserved(self):
        result = bridge.codex_quota_snapshot(self.quota(primary=self.window(125.25)), now=1010)
        self.assertEqual(result["payload"]["w1_used_percent"], 100)
        self.assertEqual(result["raw_used_percent"][0], 125.25)

    def test_partial_window_fields_keep_null(self):
        result = bridge.codex_quota_snapshot(self.quota(primary={"usedPercent": 12}), now=1010)
        self.assertEqual(result["payload"]["w1_used_percent"], 12)
        self.assertIsNone(result["payload"]["w1_duration_min"])
        self.assertIsNone(result["payload"]["w1_reset_s"])
        self.assertIsNone(result["payload"]["w2_used_percent"])

    def test_cache_resend_never_renews_expiry(self):
        quota = self.quota(primary=self.window())
        first = bridge.codex_quota_snapshot(quota, now=1001)
        later = bridge.codex_quota_snapshot(quota, now=1200)
        self.assertEqual(first["payload"], later["payload"])
        self.assertEqual(later["payload"]["observed_utc_ms"], 1000000)
        self.assertEqual(later["payload"]["expires_utc_ms"], 1180000)
        self.assertEqual(later["age_seconds"], 200)
        self.assertTrue(later["stale"])

    def test_snapshot_expires_at_first_window_reset(self):
        quota = self.quota(primary=self.window(reset=1050), secondary=self.window(4, 10080, 700000))
        result = bridge.codex_quota_snapshot(quota, now=1051)
        self.assertEqual(result["payload"]["expires_utc_ms"], 1050000)
        self.assertTrue(result["stale"])

    def test_clock_rollback_or_missing_time_cannot_be_fresh(self):
        for observed, now in ((1000, 999), (None, 1010), (float("nan"), 1010)):
            result = bridge.codex_quota_snapshot(self.quota(observed=observed, primary=self.window()), now=now)
            self.assertEqual(result["payload"]["observed_utc_ms"], 0)
            self.assertEqual(result["payload"]["expires_utc_ms"], 0)
            self.assertIsNone(result["payload"]["w1_used_percent"])
            self.assertFalse(result["ready"])
            self.assertTrue(result["stale"])

    def test_spark_bucket_never_fills_core_quota(self):
        limits = {"rateLimitsByLimitId": {"codex_bengalfox": {"limitId": "codex_bengalfox", "primary": self.window(99)}},
                  "rateLimits": {"limitId": "codex_bengalfox", "primary": self.window(99)}}
        self.assertEqual(bridge.normalized_codex_limits(limits), {})
        limits["rateLimitsByLimitId"]["codex"] = {"primary": self.window(12)}
        self.assertEqual(bridge.normalized_codex_limits(limits)["primary"]["usedPercent"], 12)

    def test_old_weekly_only_cache_not_fabricated_primary_window(self):
        old = {"source": "official_app_server", "observed_at": 1000,
               "limits": {"limitId": "codex", "primary": {"windowDurationMins": 10080, "resetsAt": 700000}}}
        result = bridge.codex_quota_snapshot(old, now=1010)
        self.assertTrue(result["legacy_cache"])
        self.assertIsNone(result["payload"]["w1_duration_min"])
        self.assertFalse(result["ready"])
        self.assertEqual(bridge.weekly_window(old["limits"])["resets_at"], 700000)

    def test_local_log_has_real_windows_original_time_and_no_secret_fields(self):
        log = {"source": "local_log", "observed_at": 1000, "limits": {
            "limit_id": "codex", "primary": {"used_percent": 34, "window_minutes": 300, "resets_at": 3000},
            "secondary": None, "email": "secret@example.test", "credits": {"balance": "private"}}}
        result = bridge.codex_quota_snapshot(log, now=1200)
        self.assertEqual(result["payload"]["source"], "local_log")
        self.assertEqual(result["payload"]["w1_used_percent"], 34)
        self.assertEqual(result["payload"]["observed_utc_ms"], 1000000)
        self.assertTrue(result["stale"])
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("private", json.dumps(result))
        self.assertLessEqual(len(json.dumps(result["payload"], separators=(",", ":"))), 512)

    def test_v1_cache_migrates_with_live_read_and_retains_both_windows(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            now = bridge.time.time()
            old = {"source": "official_app_server", "observed_at": now, "account_hash": "test",
                   "limits": {"limitId": "codex", "primary": {"windowDurationMins": 10080, "resetsAt": int(now)+86400}}}
            bridge.atomic_json(root / "02 quota.json", old)
            live = self.quota(observed=now, primary=self.window(14, 60, int(now)+3600),
                              secondary=self.window(22, 10080, int(now)+86400))
            args = SimpleNamespace(state_dir=str(root), codex_home=str(root / "empty-codex"),
                                   no_quota=False, refresh_quota=False, codex_cli=None)
            with mock.patch.object(bridge, "app_server_quota", return_value=live) as read:
                result = bridge.read_tokens(args)
            self.assertEqual(read.call_count, 1)
            saved = json.loads((root / "02 quota.json").read_text())
            self.assertEqual(saved["cache_version"], 2)
            self.assertEqual(saved["limits"]["primary"]["usedPercent"], 14)
            self.assertEqual(saved["limits"]["secondary"]["usedPercent"], 22)
            self.assertEqual(result["codex_quota"]["w1_duration_min"], 60)
            self.assertEqual(result["codex_quota"]["w2_duration_min"], 10080)


class BLEIntegrationTests(unittest.TestCase):
    identifier = "12345678-1234-4234-8234-123456789ABC"

    def test_ble_adapter_rejects_every_avatar_command_before_transport(self):
        device = object.__new__(bridge.BLEDevice)
        device._ble = mock.Mock()
        for command in ("AVATAR DEFAULT", "AVATAR BEGIN 8224 hash 0", "AVATAR DATA 0 AA==", "AVATAR END"):
            with self.subTest(command=command), self.assertRaisesRegex(bridge.BridgeError, "USB"):
                device.request(command, "@AP AVATAR_OK")
        device._ble.request.assert_not_called()

    def test_ble_upload_keeps_cached_art_and_syncs_text_tokens_quota(self):
        device = FakeDevice()
        device.transport = "ble"
        device.current.update(avatar_custom=True, avatar_custom_stage=0,
                              avatar_sha256="previous-art", codex_dashboard_supported=True,
                              codex_observed_utc_ms=0)
        config = bridge.validate_config({"name": "测试者", "threshold1": 10, "threshold2": 20,
                                         "feature_all": False, "feature_mask": 129})
        usage = {"cycle_tokens": 21, "codex_quota": bridge.codex_quota_snapshot(None)["payload"]}
        result = bridge.upload_device(device, config, usage, {"0": bytes(8224), "2": bytes(8224)}, reset_cycle=True)
        commands = [c for c, _ in device.calls]
        self.assertFalse(any(c.startswith("AVATAR") for c in commands))
        self.assertIn("TOKENS 0", commands)
        self.assertIn("TOKENS 21", commands)
        self.assertTrue(any(c.startswith("CODEX ") for c in commands))
        self.assertEqual(result["name"], "测试者")
        self.assertEqual(result["threshold2"], 20)
        self.assertEqual(result["avatar_sha256"], "previous-art")
        self.assertEqual(usage["avatar_sync"], "usb_required")
        self.assertEqual(usage["avatar_sync_stage"], 2)
        self.assertEqual(usage["avatar_pending_usb_stages"], [0, 2])

    def test_ble_default_request_never_clears_cached_image(self):
        for cached_stage, expected in ((0, "usb_required"), (2, "up_to_date")):
            with self.subTest(cached_stage=cached_stage):
                device = FakeDevice()
                device.transport = "ble"
                device.current.update(avatar_custom=True, avatar_custom_stage=cached_stage,
                                      avatar_sha256="keep-this")
                usage = {"cycle_tokens": 42}
                result = bridge.sync_device(device, usage, bridge.validate_config({}), {})
                self.assertFalse(any(c.startswith("AVATAR") for c, _ in device.calls))
                self.assertTrue(result["avatar_custom"])
                self.assertEqual(result["avatar_sha256"], "keep-this")
                self.assertEqual(usage["avatar_sync"], expected)

    def test_ble_matching_cache_is_verified_but_other_stages_stay_pending(self):
        device = FakeDevice()
        device.transport = "ble"
        avatar = bytes(8224)
        device.current.update(avatar_custom=True, avatar_custom_stage=0,
                              avatar_sha256=bridge.hashlib.sha256(avatar).hexdigest())
        usage = {"cycle_tokens": 42}
        bridge.sync_device(device, usage, bridge.validate_config({}), {"0": avatar, "2": avatar})
        self.assertEqual(usage["avatar_sync"], "up_to_date")
        self.assertEqual(usage["avatar_pending_usb_stages"], [2])
        self.assertFalse(any(c.startswith("AVATAR") for c, _ in device.calls))

    def test_ble_missing_image_does_not_block_cli_and_remains_pending_for_usb(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = {"name": "测试者", "avatar_paths": {"0": str(root / "unavailable.png")}}
            config_path = root / "draft.json"
            config_path.write_text(json.dumps(config))
            for action in ("upload", "sync"):
                device = FakeDevice()
                device.transport = "ble"
                args = ["bridge", "--state-dir", str(root / "state"), "--ble-id", self.identifier, action]
                if action == "upload":
                    args += ["--config", str(config_path)]
                usage = {"cycle_tokens": 42, "stage": 0, "cycle_started_at": 1000}
                output = io.StringIO()
                with mock.patch.object(bridge.sys, "argv", args), \
                     mock.patch.object(bridge, "read_tokens", return_value=usage), \
                     mock.patch.object(bridge, "collect_provider_metadata", side_effect=lambda value, args: value), \
                     mock.patch.object(bridge, "BLEDevice", return_value=device), \
                     mock.patch.object(bridge, "Device", side_effect=AssertionError("no USB")), \
                     mock.patch.object(bridge.sys, "stdout", output):
                    self.assertEqual(bridge.main(), 0)
                result = json.loads(output.getvalue())
                self.assertEqual(result["usage"]["avatar_sync"], "usb_required")
                self.assertEqual(result["status"]["tokens"], 42)
                self.assertFalse(any(c.startswith("AVATAR") for c, _ in device.calls))
            saved = bridge.load_json(root / "state/04 device config.json")
            self.assertEqual(saved["avatar_paths"], config["avatar_paths"])
            with self.assertRaises(OSError):
                bridge.prepare_avatars(bridge.validate_config(saved), {"stage": 0})

    def test_ble_unknown_cycle_syncs_unknown_without_avatar_commands(self):
        device = FakeDevice()
        device.transport = "ble"
        usage = {"cycle_tokens": None}
        bridge.sync_device(device, usage, bridge.validate_config({}), {"0": None})
        self.assertIn(("TOKENS UNKNOWN", "@AP TOKENS_OK"), device.calls)
        self.assertEqual(usage["avatar_sync"], "cycle_unknown")
        self.assertEqual(usage["avatar_pending_usb_stages"], [0])
        self.assertFalse(any(c.startswith("AVATAR") for c, _ in device.calls))

    def test_transport_flags_are_mutually_exclusive(self):
        with mock.patch.object(bridge.sys, "stderr", io.StringIO()), self.assertRaises(SystemExit):
            bridge.parser().parse_args(["--port", "/dev/fake", "--ble-id", self.identifier, "status"])

    def test_ble_status_does_not_open_usb_or_read_accounts(self):
        device = FakeDevice()
        device.port, device.transport = "BLE:" + self.identifier, "ble"
        output = io.StringIO()
        with mock.patch.object(bridge.sys, "argv", ["bridge", "--ble-id", self.identifier, "status"]), \
             mock.patch.object(bridge, "BLEDevice", return_value=device) as connect, \
             mock.patch.object(bridge, "Device", side_effect=AssertionError("no USB")), \
             mock.patch.object(bridge, "read_tokens", side_effect=AssertionError("no quota read")), \
             mock.patch.object(bridge.sys, "stdout", output):
            self.assertEqual(bridge.main(), 0)
        connect.assert_called_once_with(self.identifier)
        self.assertEqual(json.loads(output.getvalue())["transport"], "ble")

    def test_ble_scan_returns_candidates_without_connecting(self):
        @dataclass
        class Candidate:
            identifier: str
            name: str
            rssi: int

        async def scan(timeout):
            self.assertEqual(timeout, 5)
            return [Candidate(self.identifier, "QingJian-A1B2C3", -42)]

        module = SimpleNamespace(scan_candidates=scan, BLETransportError=RuntimeError)
        with mock.patch.object(bridge, "_ble_transport_module", return_value=module), \
             mock.patch.object(bridge, "Device", side_effect=AssertionError("no USB")), \
             mock.patch.object(bridge, "BLEDevice", side_effect=AssertionError("no connection")):
            result = bridge.scan_ble_devices(5)
        self.assertEqual(result, {"ok": True, "devices": [{"identifier": self.identifier, "name": "QingJian-A1B2C3", "rssi": -42}]})

    def test_pair_is_usb_only_and_does_not_claim_completed_pairing(self):
        device = FakeDevice()
        output = io.StringIO()
        with mock.patch.object(bridge.sys, "argv", ["bridge", "pair"]), \
             mock.patch.object(bridge, "Device", return_value=device), \
             mock.patch.object(bridge, "BLEDevice", side_effect=AssertionError("must not connect BLE")), \
             mock.patch.object(bridge.sys, "stdout", output):
            self.assertEqual(bridge.main(), 0)
        self.assertIn(("PAIR", "@AP PAIR_OK"), device.calls)
        self.assertEqual(json.loads(output.getvalue())["pairing"], "requested")
        with mock.patch.object(bridge.sys, "argv", ["bridge", "--ble-id", self.identifier, "pair"]), \
             mock.patch.object(bridge, "Device", side_effect=AssertionError("no USB")), \
             mock.patch.object(bridge, "BLEDevice", side_effect=AssertionError("no BLE")), \
             mock.patch.object(bridge.sys, "stdout", io.StringIO()):
            self.assertEqual(bridge.main(), 1)

    def test_ble_exception_is_translated_to_bridge_error(self):
        class TransportError(Exception):
            pass

        transport = SimpleNamespace(BLETransportError=TransportError,
            SyncPassportBLE=mock.Mock(side_effect=TransportError("蓝牙操作超时")))
        with mock.patch.object(bridge, "_ble_transport_module", return_value=transport), \
             self.assertRaisesRegex(bridge.BridgeError, "超时"):
            bridge.BLEDevice(self.identifier)

    def test_switching_transport_and_legacy_marker_migration_do_not_reset_cycle(self):
        usage = {"cycle_started_at": 1000, "account_hash": "account"}
        current = bridge.cycle_sync_marker(usage, {"device_id": "001122334455"})
        self.assertEqual(current["device_id"], "001122334455")
        self.assertNotIn("port", current)
        old_usb = {**usage, "port": "/dev/cu.usbmodem123"}
        self.assertFalse(bridge.cycle_reset_needed(old_usb, current))
        self.assertFalse(bridge.cycle_reset_needed(current, current))
        self.assertFalse(bridge.cycle_reset_needed({**old_usb, "account_hash": None}, current))
        self.assertTrue(bridge.cycle_reset_needed(current, {**current, "cycle_started_at": 2000}))
        self.assertTrue(bridge.cycle_reset_needed(current, {**current, "account_hash": "other"}))
        self.assertTrue(bridge.cycle_reset_needed(current, {**current, "device_id": "000000123456"}))

    def test_legacy_usb_marker_to_ble_sync_does_not_send_zero(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            usage = {"cycle_tokens": 42, "cycle_started_at": 1000, "account_hash": "account",
                     "coverage": {"cycle_scan_incomplete": False}}
            bridge.atomic_json(state / "05 device sync.json", {"cycle_started_at": 1000,
                               "account_hash": "account", "port": "/dev/cu.old-usb"})
            device = FakeDevice()
            device.port, device.transport = "BLE:" + self.identifier, "ble"
            device.current["device_id"] = "001122334455"
            args = ["bridge", "--state-dir", str(state), "--ble-id", self.identifier, "sync"]
            with mock.patch.object(bridge.sys, "argv", args), \
                 mock.patch.object(bridge, "read_tokens", return_value=usage), \
                 mock.patch.object(bridge, "collect_provider_metadata", side_effect=lambda value, args: value), \
                 mock.patch.object(bridge, "BLEDevice", return_value=device), \
                 mock.patch.object(bridge, "Device", side_effect=AssertionError("no USB")), \
                 mock.patch.object(bridge.sys, "stdout", io.StringIO()):
                self.assertEqual(bridge.main(), 0)
            self.assertNotIn(("TOKENS 0", "@AP TOKENS_OK"), device.calls)
            marker = json.loads((state / "05 device sync.json").read_text())
            self.assertEqual(marker["device_id"], "001122334455")
            self.assertNotIn("port", marker)


class GrowthAccountingTests(unittest.TestCase):
    NOW = 1789232000000

    def source(self, count, start=None, **changes):
        return {"cycle_tokens": count, "complete": True, "available": True, "status": "fresh",
                "cycle_start_ms": self.NOW - 1000000 if start is None else start,
                "reset_at_ms": self.NOW + 1000000, "observed_at_ms": self.NOW - 1000,
                "expires_at_ms": self.NOW + 179000, **changes}

    def result(self, codex=30, cursor=70):
        return {"cycle_tokens": codex, "cycle_started_at": (self.NOW - 1000000) // 1000,
                "threshold1": 50, "threshold2": 163885684,
                "codex_token_usage": self.source(codex),
                "cursor_quota": {"token_usage": self.source(cursor, start=self.NOW - 2000000)}}

    def test_sum_uses_each_original_period_and_preserves_codex_independent_count(self):
        value = bridge.combine_growth(self.result(), self.NOW)
        self.assertEqual(value["cycle_tokens"], 30)
        self.assertEqual(value["growth_tokens"], 100)
        self.assertTrue(value["growth_ready"])
        self.assertEqual(value["growth_sources"]["cursor"]["cycle_start_ms"], self.NOW - 2000000)
        self.assertEqual(value["threshold2"], 163885684)
        self.assertEqual(value["stage"], 1)

    def test_stale_cursor_keeps_both_observations_and_does_not_promote(self):
        value = self.result()
        original = self.source(70, observed_at_ms=self.NOW - 300001, expires_at_ms=self.NOW - 1, available=False, status="stale")
        value["cursor_quota"]["token_usage"] = original.copy()
        bridge.combine_growth(value, self.NOW)
        self.assertEqual(value["growth_tokens"], 100)
        self.assertFalse(value["growth_ready"])
        self.assertEqual(value["growth_status"], "stale")
        self.assertIsNone(value["stage"])
        self.assertEqual(value["growth_sources"]["cursor"], original)

    def test_missing_source_is_not_a_fabricated_zero(self):
        value = self.result(); value["cursor_quota"] = {}
        bridge.combine_growth(value, self.NOW)
        self.assertIsNone(value["growth_tokens"])
        self.assertEqual(value["growth_status"], "partial")
        self.assertEqual(value["growth_sources"]["codex"]["cycle_tokens"], 30)

    def test_confirmed_zero_month_is_added_as_real_zero(self):
        value = bridge.combine_growth(self.result(cursor=0), self.NOW)
        self.assertEqual(value["growth_tokens"], 30)
        self.assertTrue(value["growth_ready"])

    def test_new_month_or_week_restarts_but_adding_cursor_to_legacy_marker_does_not(self):
        value = bridge.combine_growth(self.result(), self.NOW)
        current = bridge.cycle_sync_marker(value, {"device_id": "001122334455"})
        legacy = {"cycle_started_at": value["cycle_started_at"], "device_id": "001122334455"}
        self.assertFalse(bridge.cycle_reset_needed(legacy, current))
        next_month = {**current, "cursor_cycle_started_at_ms": current["cursor_cycle_started_at_ms"] + 1000}
        self.assertTrue(bridge.cycle_reset_needed(current, next_month))
        self.assertTrue(bridge.cycle_reset_needed(current, {**current, "cycle_started_at": current["cycle_started_at"] + 1}))

    def test_growth_device_sync_uses_sum_and_existing_device_threshold(self):
        value = bridge.combine_growth(self.result(), self.NOW)
        device = FakeDevice()
        device.current.update(threshold1=50, threshold2=163885684)
        bridge.sync_device(device, value)
        self.assertIn(("TOKENS 100", "@AP TOKENS_OK"), device.calls)
        self.assertNotIn(("TOKENS 30", "@AP TOKENS_OK"), device.calls)
        self.assertEqual(device.current["threshold2"], 163885684)

    def test_stale_growth_does_not_refresh_or_clear_device_last_observation(self):
        value = self.result(); value["cursor_quota"]["token_usage"]["available"] = False
        bridge.combine_growth(value, self.NOW)
        device = FakeDevice(); device.current.update(tokens=999, tokens_known=True)
        bridge.sync_device(device, value)
        self.assertFalse(any(command.startswith("TOKENS") for command, _ in device.calls))
        self.assertEqual(device.current["tokens"], 999)
        self.assertEqual(value["growth_sync"], "waiting_for_sources")

    def test_incomplete_codex_scan_retains_last_complete_count_and_original_time(self):
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            value = {"cycle_tokens": 80, "cycle_started_at": (self.NOW - 1000000) / 1000,
                     "weekly_resets_at": (self.NOW + 1000000) / 1000, "updated_at": self.NOW / 1000,
                     "coverage": {"cycle_scan_incomplete": False}, "last_token_event_at": self.NOW / 1000 - 2}
            first = bridge.codex_token_observation(value, state, "test-account")
            value.update(cycle_tokens=90, updated_at=(self.NOW + 1000) / 1000, coverage={"cycle_scan_incomplete": True})
            after = bridge.codex_token_observation(value, state, "test-account")
        self.assertEqual(after["cycle_tokens"], 80)
        self.assertEqual(after["observed_at_ms"], first["observed_at_ms"])
        self.assertFalse(after["available"])
        self.assertEqual(after["status"], "stale")

    def test_invalid_or_overflow_sum_stays_unknown(self):
        value = self.result(codex=bridge.MAX_DEVICE_TOKENS, cursor=1)
        bridge.combine_growth(value, self.NOW)
        self.assertIsNone(value["growth_tokens"])
        self.assertFalse(value["growth_ready"])


class AvatarCodecTests(unittest.TestCase):
    def setUp(self):
        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow unavailable in this host interpreter")
        self.Image = Image

    def packet(self, raw, width=128, height=128, declared_raw=None, extra=b""):
        compressed = bridge.zlib.compress(raw, 9) + extra
        header = bridge.AVATAR_HEADER.pack(b"APZ1", width, height, len(compressed),
                                           len(raw) if declared_raw is None else declared_raw)
        return (header + compressed).ljust(8224, b"\0")

    def test_gradient_preserves_more_than_sixteen_colors(self):
        image = self.Image.new("RGBA", (128, 128))
        image.putdata([(x * 2, y * 2, (x ^ y) * 2, 255) for y in range(128) for x in range(128)])
        data = bridge.encode_avatar_source(image)
        raw = bridge.decode_avatar_bytes(data)
        colors = {raw[i:i+2] for i in range(0, len(raw), 2)}
        self.assertGreater(len(colors), 100)
        self.assertIn(bridge.AVATAR_HEADER.unpack_from(data)[1], bridge.AVATAR_RESOLUTIONS)

    def test_alpha_composites_onto_black(self):
        image = self.Image.new("RGBA", (128, 128), (255, 255, 255, 0))
        image.putpixel((0, 0), (255, 0, 0, 128))
        image.putpixel((1, 0), (0, 255, 0, 255))
        raw = bridge.rgb565_canvas(image)
        self.assertEqual(raw[:2], (0x8000).to_bytes(2, "little"))
        self.assertEqual(raw[2:4], (0x07E0).to_bytes(2, "little"))
        self.assertEqual(raw[4:6], b"\0\0")

    def test_incompressible_input_chooses_bounded_smaller_resolution(self):
        import random
        rng = random.Random(123)
        image = self.Image.frombytes("RGB", (128, 128), rng.randbytes(128 * 128 * 3)).convert("RGBA")
        data = bridge.encode_avatar_source(image)
        _, width, height, compressed, raw = bridge.AVATAR_HEADER.unpack_from(data)
        self.assertLess(width, 128)
        self.assertEqual(width, height)
        self.assertLessEqual(compressed, 8208)
        self.assertEqual(raw, width * height * 2)
        self.assertEqual(len(bridge.decode_avatar_bytes(data)), 32768)

    def test_exact_floor_nearest_mapping(self):
        raw = b"".join((y * 56 + x).to_bytes(2, "little") for y in range(56) for x in range(56))
        data = self.packet(raw, 56, 56)
        decoded = bridge.decode_avatar_bytes(data)
        for x, y in ((0, 0), (1, 1), (2, 3), (64, 64), (127, 127)):
            expected = (y * 56 // 128) * 56 + x * 56 // 128
            i = (y * 128 + x) * 2
            self.assertEqual(int.from_bytes(decoded[i:i+2], "little"), expected)

    def test_legacy_indexed16_readback(self):
        data = b"\x00\xf8" + bytes(30 + 8192)
        self.assertEqual(bridge.decode_avatar_bytes(data), b"\x00\xf8" * 16384)

    def test_invalid_headers_corruption_truncation_and_output_overflow(self):
        good = self.packet(bytes(32768))
        malformed = [good[:-1], b"APZ2" + good[4:], self.packet(bytes(32768), 129, 129),
                     self.packet(bytes(32766), declared_raw=32768),
                     self.packet(bytes(32770), declared_raw=32768),
                     self.packet(bytes(32768), extra=b"trailing")]
        corrupt = bytearray(good)
        compressed_size = bridge.AVATAR_HEADER.unpack_from(good)[3]
        corrupt[16 + compressed_size - 1] ^= 1
        malformed.append(bytes(corrupt))
        padding = bytearray(good)
        padding[-1] = 1
        malformed.append(bytes(padding))
        overlarge = bytearray(good)
        overlarge[8:12] = (8209).to_bytes(4, "little")
        malformed.append(bytes(overlarge))
        compressed = good[16:16+compressed_size-2]
        truncated_stream = bridge.AVATAR_HEADER.pack(b"APZ1", 128, 128, len(compressed), 32768) + compressed
        malformed.append(truncated_stream.ljust(8224, b"\0"))
        for index, data in enumerate(malformed):
            with self.subTest(index=index), self.assertRaises(bridge.BridgeError):
                bridge.decode_avatar_bytes(data)

    def test_preview_equals_exact_decoded_pixels_and_has_no_usb_access(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source = root / "source.png"
            output = root / "preview.png"
            self.Image.new("RGBA", (80, 128), (140, 75, 224, 128)).save(source)
            arguments = ["bridge", "preview-avatar", "--source", str(source), "--output", str(output)]
            stdout = io.StringIO()
            with mock.patch.object(bridge.sys, "argv", arguments), \
                 mock.patch.object(bridge, "Device", side_effect=AssertionError("must not open USB")), \
                 mock.patch.object(bridge, "read_tokens", side_effect=AssertionError("must not read usage")), \
                 mock.patch.object(bridge.sys, "stdout", stdout):
                self.assertEqual(bridge.main(), 0)
            reply = json.loads(stdout.getvalue())
            self.assertEqual(reply["path"], str(output.resolve()))
            self.assertEqual(reply["format"], "rgb565")
            data = bridge.avatar_bytes({"avatar_path": str(source)})
            expected = bridge.rgb565_image(bridge.decode_avatar_bytes(data))
            with self.Image.open(output) as preview:
                self.assertEqual(preview.size, (128, 128))
                self.assertEqual(preview.tobytes(), expected.tobytes())

    def test_builtin_preview_keeps_native_160_resolution(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            source, output = root / "source.png", root / "preview.png"
            self.Image.new("RGBA", (70, 128), (255, 0, 0, 255)).save(source)
            reply = bridge.preview_avatar(source, output, builtin=True)
            self.assertEqual(reply["source_resolution"], 160)
            with self.Image.open(output) as preview:
                self.assertEqual(preview.size, (160, 160))
                expected = bridge.rgb565_image(bridge.rgb565_canvas(bridge.load_avatar_source(source), 160), 160)
                self.assertEqual(preview.tobytes(), expected.tobytes())
            with self.assertRaises(bridge.BridgeError):
                bridge.preview_avatar(source, source)


class FakeDevice:
    def __init__(self):
        self.calls = []
        self.port = "fake-passport"
        self.current = {"time_synced": True, "tokens": 42, "avatar_custom": False,
                        "threshold1": bridge.FIRST_THRESHOLD, "threshold2": bridge.FINAL_THRESHOLD}

    def close(self):
        pass

    def request(self, command, expected, timeout=5):
        self.calls.append((command, expected))
        if command.startswith("TIME "):
            _, value, offset = command.split()
            self.current.update(time_synced=True, utc_ms=int(value), offset_min=int(offset))
        if command.startswith("TOKENS "):
            value = command.split()[1]
            self.current["tokens_known"] = value != "UNKNOWN"
            if value != "UNKNOWN":
                self.current["tokens"] = int(value)
                self.current["stage"] = bridge.stage_for(int(value), self.current["threshold1"], self.current["threshold2"])
        if command.startswith("THRESHOLDS "):
            _, first, final = command.split()
            self.current.update(threshold1=int(first), threshold2=int(final))
        if command.startswith("CODEX "):
            quota = json.loads(bridge.base64.b64decode(command.split(" ", 1)[1]))
            self.current["codex_observed_utc_ms"] = quota["observed_utc_ms"]
        if command.startswith("BADGE "):
            self.current.update(json.loads(bridge.base64.b64decode(command.split(" ", 1)[1])))
        if command.startswith("FEATURES "):
            _, all_features, mask = command.split()
            self.current.update(feature_all=all_features == "1", feature_mask=int(mask))
        if command.startswith("AVATAR BEGIN "):
            self.current["avatar_sha256"] = command.split()[-2]
            self.current["avatar_custom"] = True
            self.current["avatar_custom_stage"] = int(command.split()[-1])
        if command == "AVATAR DEFAULT":
            self.current["avatar_custom"] = False
        return ""

    def wait_fields(self, expected, timeout=10):
        assert all(self.current.get(k) == v for k, v in expected.items())
        return dict(self.current)

    def status(self):
        return dict(self.current)


class ProtocolTests(unittest.TestCase):
    def test_macos_runtime_uses_no_reset_serial_adapter(self):
        connection = mock.Mock()
        mac_serial = mock.Mock(return_value=connection)
        normal_serial = mock.Mock(side_effect=AssertionError("normal pySerial open can reset C3"))
        modules = {"serial": SimpleNamespace(Serial=normal_serial),
                   "serial.tools": SimpleNamespace(list_ports=mock.Mock()),
                   "passport_serial": SimpleNamespace(MacNoResetSerial=mac_serial)}
        with mock.patch.dict(bridge.sys.modules, modules), mock.patch.object(bridge.sys, "platform", "darwin"):
            device = bridge.Device("/dev/fake-passport")
            device.close()
        mac_serial.assert_called_once_with(port=None, baudrate=115200, timeout=0.1, write_timeout=2)
        connection.open.assert_called_once_with()
        connection.close.assert_called_once_with()
        self.assertEqual(connection.port, "/dev/fake-passport")
        self.assertNotIn("dtr", connection.__dict__)
        self.assertNotIn("rts", connection.__dict__)

    def test_upload_bounded_chunks_and_final_readback(self):
        device = FakeDevice()
        config = bridge.validate_config({"name": "测试者", "feature_all": False, "feature_mask": 129})
        avatar = bytes(8224)
        result = bridge.upload_device(device, config, {"cycle_tokens": 42}, {"0": avatar})
        self.assertEqual(result["name"], "测试者")
        chunks = [c for c, _ in device.calls if c.startswith("AVATAR DATA")]
        self.assertEqual(len(chunks), 33)
        self.assertTrue(all(len(c) < 768 for c, _ in device.calls))
        self.assertTrue(any(c == "TOKENS 42" for c, _ in device.calls))

    def test_unknown_cycle_is_not_published_as_zero(self):
        device = FakeDevice()
        bridge.sync_device(device, {"cycle_tokens": None})
        self.assertTrue(any(c == "TOKENS UNKNOWN" for c, _ in device.calls))
        self.assertFalse(device.current["tokens_known"])

    def test_incomplete_cycle_clears_previously_known_counter(self):
        device = FakeDevice()
        device.current["tokens_known"] = True
        bridge.sync_device(device, {"cycle_tokens": 1234, "coverage": {"cycle_scan_incomplete": True}})
        self.assertTrue(any(c == "TOKENS UNKNOWN" for c, _ in device.calls))
        self.assertFalse(device.current["tokens_known"])

    def test_time_acceptance_waits_for_applied_fresh_readback(self):
        class DelayedTime(FakeDevice):
            def __init__(self):
                super().__init__()
                self.polls = 0

            def status(self):
                self.polls += 1
                if self.polls < 3:
                    # Previous valid-looking clock is stale and in wrong timezone.
                    return {"time_synced": True, "utc_ms": 1, "offset_min": 0}
                return super().status()

        device = DelayedTime()
        with mock.patch.object(bridge.time, "sleep"):
            bridge.sync_device(device, {"cycle_tokens": 42})
        self.assertEqual(device.polls, 3)

    def test_default_avatar_uses_explicit_reset_and_readback(self):
        device = FakeDevice()
        device.current["avatar_custom"] = True
        config = bridge.validate_config({"avatar_default": True})
        bridge.upload_device(device, config, {"cycle_tokens": 42})
        self.assertTrue(any(c == "AVATAR DEFAULT" for c, _ in device.calls))
        self.assertFalse(device.current["avatar_custom"])

    def test_matching_old_fields_are_not_saved_while_busy(self):
        device = bridge.Device.__new__(bridge.Device)
        device.status = mock.Mock(side_effect=[
            {"name": "测试者", "save_busy": True, "last_save_error": 0},
            {"name": "测试者", "save_busy": False, "last_save_error": 0}])
        with mock.patch.object(bridge.time, "sleep"):
            result = device.wait_fields({"name": "测试者"})
        self.assertFalse(result["save_busy"])
        self.assertEqual(device.status.call_count, 2)

    def test_device_save_failure_does_not_report_success(self):
        device = bridge.Device.__new__(bridge.Device)
        device.status = lambda: {"name": "测试者", "save_busy": False, "last_save_error": 123}
        with self.assertRaises(bridge.BridgeError):
            device.wait_fields({"name": "测试者"})

    def test_new_epoch_resets_then_installs_art_before_actual_count(self):
        device = FakeDevice()
        config = bridge.validate_config({"threshold1": 10, "threshold2": 20})
        result = bridge.sync_device(device, {"cycle_tokens": 21}, config,
                                    {"2": bytes(8224)}, reset_cycle=True)
        commands = [c for c, _ in device.calls]
        self.assertLess(commands.index("TOKENS 0"), next(i for i, c in enumerate(commands) if c.startswith("AVATAR BEGIN")))
        self.assertLess(commands.index("AVATAR END"), commands.index("TOKENS 21"))
        self.assertEqual(result["stage"], 2)
        self.assertEqual(result["avatar_custom_stage"], 2)

    def test_threshold_edit_does_not_reset_actual_tokens(self):
        device = FakeDevice()
        result = bridge.sync_device(device, {"cycle_tokens": 42}, {"threshold1": 10, "threshold2": 20})
        commands = [c for c, _ in device.calls]
        self.assertNotIn("TOKENS 0", commands)
        self.assertIn("THRESHOLDS 10 20", commands)
        self.assertEqual(result["tokens"], 42)
        self.assertEqual(result["stage"], 2)

    def test_cached_stage_image_is_not_reuploaded(self):
        device = FakeDevice()
        data = bytes(8224)
        bridge.sync_device(device, {"cycle_tokens": 42}, config={}, prepared={"0": data})
        first_uploads = sum(c.startswith("AVATAR BEGIN") for c, _ in device.calls)
        bridge.sync_device(device, {"cycle_tokens": 43}, config={}, prepared={"0": data})
        self.assertEqual(sum(c.startswith("AVATAR BEGIN") for c, _ in device.calls), first_uploads)

    def test_all_features_preserve_saved_custom_mask(self):
        class PreserveMask(FakeDevice):
            def request(self, command, expected, timeout=5):
                previous = self.current.get("feature_mask", 8)
                result = super().request(command, expected, timeout)
                if command.startswith("FEATURES 1 "):
                    self.current["feature_mask"] = previous
                return result

        device = PreserveMask()
        result = bridge.upload_device(device, bridge.validate_config({"feature_all": True, "feature_mask": 255}), {"cycle_tokens": 42})
        self.assertEqual(result["feature_mask"], 8)

    def test_host_config_only_saved_after_verified_upload(self):
        for success in (False, True):
            with tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                config_path = root / "input.json"
                config_path.write_text(json.dumps({"name": "测试者", "threshold1": 10, "threshold2": 20}))
                device = FakeDevice()
                if not success:
                    device.wait_fields = mock.Mock(side_effect=bridge.BridgeError("保存失败"))
                arguments = ["bridge", "--state-dir", str(root / "state"), "upload", "--config", str(config_path)]
                usage = {"cycle_tokens": 42, "stage": 2, "cycle_started_at": 1000, "coverage": {"cycle_scan_incomplete": False}}
                with mock.patch.object(bridge.sys, "argv", arguments), mock.patch.object(bridge, "read_tokens", return_value=usage), \
                     mock.patch.object(bridge, "Device", return_value=device), mock.patch.object(bridge.sys, "stdout", io.StringIO()):
                    self.assertEqual(bridge.main(), 0 if success else 1)
                saved = root / "state/04 device config.json"
                self.assertEqual(saved.exists(), success)
                if success:
                    self.assertEqual(json.loads(saved.read_text())["threshold2"], 20)

    def test_first_sync_preserves_existing_thresholds_and_custom_avatar(self):
        device = FakeDevice()
        device.current.update(threshold1=10, threshold2=20, avatar_custom=True,
                              avatar_custom_stage=2, avatar_sha256="existing-device-art")
        usage = {"cycle_tokens": 42}
        result = bridge.sync_device(device, usage)
        commands = [c for c, _ in device.calls]
        self.assertFalse(any(c.startswith(("THRESHOLDS ", "AVATAR ", "BADGE ", "FEATURES ")) for c in commands))
        self.assertEqual(result["threshold1"], 10)
        self.assertEqual(result["threshold2"], 20)
        self.assertEqual(result["avatar_sha256"], "existing-device-art")
        self.assertEqual(result["stage"], 2)
        self.assertEqual(usage["stage"], 2)
        self.assertEqual(usage["threshold2"], 20)

    def test_first_sync_cli_does_not_create_default_settings(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            device = FakeDevice()
            device.current.update(threshold1=10, threshold2=20, avatar_custom=True,
                                  avatar_custom_stage=2, avatar_sha256="preserve-me")
            usage = {"cycle_tokens": 42, "stage": 0, "cycle_started_at": 1000,
                     "coverage": {"cycle_scan_incomplete": False}}
            with mock.patch.object(bridge.sys, "argv", ["bridge", "--state-dir", str(root), "sync"]), \
                 mock.patch.object(bridge, "read_tokens", return_value=usage), \
                 mock.patch.object(bridge, "Device", return_value=device), \
                 mock.patch.object(bridge.sys, "stdout", io.StringIO()):
                self.assertEqual(bridge.main(), 0)
            self.assertFalse((root / "04 device config.json").exists())
            self.assertEqual(device.current["avatar_sha256"], "preserve-me")
            self.assertEqual(device.current["threshold2"], 20)

    def test_codex_command_is_capability_gated(self):
        for capability in (None, False, "true"):
            device = FakeDevice()
            if capability is not None:
                device.current["codex_dashboard_supported"] = capability
            usage = {"cycle_tokens": 42}
            bridge.sync_device(device, usage)
            self.assertFalse(any(c.startswith("CODEX ") for c, _ in device.calls))
            self.assertEqual(usage["codex_dashboard_sync"], "unsupported_firmware")

    def test_codex_quota_sent_after_tokens_and_verified_by_observation(self):
        for capability in ({"codex_dashboard_supported": True}, {"capabilities": ["codex_dashboard"]},
                           {"capabilities": {"codex_dashboard": True}}):
            device = FakeDevice()
            device.current.update(capability, codex_observed_utc_ms=0)
            quota = bridge.codex_quota_snapshot(bridge.normalized_quota_cache({
                "source": "official_app_server", "observed_at": 1000, "limits": {
                    "limitId": "codex", "primary": {"usedPercent": 25, "windowDurationMins": 300, "resetsAt": 2000}}}), now=1010)["payload"]
            usage = {"cycle_tokens": 42, "codex_quota": quota}
            result = bridge.sync_device(device, usage)
            commands = [c for c, _ in device.calls]
            codex = next(c for c in commands if c.startswith("CODEX "))
            self.assertGreater(commands.index(codex), commands.index("TOKENS 42"))
            self.assertEqual(json.loads(bridge.base64.b64decode(codex[6:])), quota)
            self.assertEqual(result["codex_observed_utc_ms"], 1000000)
            self.assertEqual(usage["codex_dashboard_sync"], "verified")


class ProviderBridgeTests(unittest.TestCase):
    def test_older_firmware_never_receives_provider_command(self):
        device = mock.Mock()
        usage = {"provider_snapshots": [{"provider": "cursor"}]}
        status = {"provider_dashboard_supported": False}
        self.assertEqual(bridge.sync_provider_dashboards(device, status, usage), status)
        device.request.assert_not_called()
        self.assertEqual(usage["provider_sync"], "unsupported_firmware")

    def test_each_provider_receipt_is_checked_without_merging_windows(self):
        device = mock.Mock()
        device.wait_fields.side_effect = lambda fields: {"provider_dashboard_supported": True, **fields}
        snapshots = [{"provider": p, "update_id": 1789220000000000+i,
                      "state": "unknown", "metric_value": None} for i,p in enumerate(("codex", "cursor", "gemini"))]
        usage = {"provider_snapshots": snapshots}
        bridge.sync_provider_dashboards(device, {"provider_dashboard_supported": True}, usage)
        actual = [json.loads(bridge.base64.b64decode(call.args[0][9:])) for call in device.request.call_args_list]
        self.assertEqual(actual, snapshots)
        self.assertEqual(device.wait_fields.call_args_list,
            [mock.call({s["provider"]+"_activity_update_id": s["update_id"]}) for s in snapshots])
        self.assertEqual(usage["provider_sync"], "verified")

    def test_source_failure_clears_prior_state_with_explicit_unknowns(self):
        args = SimpleNamespace(state_dir="/unused", codex_home="/unused", no_quota=True)
        with mock.patch.dict("sys.modules", {"passport_providers": None}):
            result = bridge.collect_provider_metadata({"cycle_tokens": 10}, args)
        self.assertEqual(result["cycle_tokens"], 10)
        self.assertEqual(len(result["provider_snapshots"]), 3)
        for packet in result["provider_snapshots"]:
            self.assertEqual(len(packet), 13)
            self.assertEqual(packet["state"], "unknown")
            self.assertIsNone(packet["metric_value"])
            self.assertEqual(packet["metric_status"], "unavailable")


class CursorQuotaBridgeTests(unittest.TestCase):
    def snapshot(self):
        return {"observed_at_ms": 1789232000000, "expires_at_ms": 1789232300000,
                "reset_at_ms": 1790383012000, "plan": "Pro+", "cursor_used_percent": 96.6225,
                "other_used_percent": 72.98181818181818, "source": "cursor_app_api"}

    def test_used_percentage_and_source_timestamp_are_preserved(self):
        snapshot = self.snapshot()
        actual = bridge.cursor_quota_payload(snapshot)
        self.assertEqual(actual["cursor_used_percent"], 96.6225)
        self.assertEqual(actual["other_used_percent"], snapshot["other_used_percent"])
        self.assertEqual(actual["observed_utc_ms"], snapshot["observed_at_ms"])
        self.assertEqual(actual["expires_utc_ms"], snapshot["expires_at_ms"])

    def test_invalid_unknown_or_unbounded_quota_does_not_become_zero_usage(self):
        for key, value in (("cursor_used_percent", True), ("other_used_percent", float("nan")),
                           ("cursor_used_percent", -1), ("other_used_percent", 101),
                           ("expires_at_ms", 1789232600000), ("reset_at_ms", 0)):
            actual = bridge.cursor_quota_payload({**self.snapshot(), key: value})
            self.assertEqual(actual["observed_utc_ms"], 0)
            self.assertIsNone(actual["cursor_used_percent"])
        snapshot = self.snapshot(); snapshot["cursor_used_percent"] = None
        self.assertIsNone(bridge.cursor_quota_payload(snapshot)["cursor_used_percent"])

    def test_old_firmware_is_skipped_and_supported_firmware_must_ack(self):
        device = mock.Mock()
        usage = {"cursor_quota": self.snapshot()}
        bridge.sync_cursor_dashboard(device, {}, usage)
        device.request.assert_not_called()
        device.wait_fields.side_effect = lambda fields: fields
        bridge.sync_cursor_dashboard(device, {"cursor_quota_supported": True}, usage)
        device.wait_fields.assert_called_once_with({"cursor_quota_observed_utc_ms": self.snapshot()["observed_at_ms"]})
        self.assertEqual(usage["cursor_quota_sync"], "verified")

    def test_standalone_refresh_does_not_require_badge_or_read_codex(self):
        with mock.patch("sys.argv", ["bridge", "--ble-id", "unused", "cursor-usage"]), \
             mock.patch.object(bridge, "collect_cursor_usage", return_value=self.snapshot()), \
             mock.patch.object(bridge, "Device", side_effect=AssertionError("opened USB")), \
             mock.patch.object(bridge, "BLEDevice", side_effect=AssertionError("opened BLE")), \
             mock.patch.object(bridge, "read_tokens", side_effect=AssertionError("read Codex")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(bridge.main(), 0)
        self.assertEqual(json.loads(output.getvalue())["cursor_quota"]["plan"], "Pro+")


class SourceSetupBridgeTests(unittest.TestCase):
    def test_status_is_local_even_with_a_bluetooth_selection(self):
        module = SimpleNamespace(source_status=mock.Mock(return_value={"ok": True, "sources": []}))
        with mock.patch.dict("sys.modules", {"passport_sources": module}), \
             mock.patch("sys.argv", ["bridge", "--ble-id", "unused", "sources-status"]), \
             mock.patch.object(bridge, "Device", side_effect=AssertionError("opened USB")), \
             mock.patch.object(bridge, "BLEDevice", side_effect=AssertionError("opened BLE")), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(bridge.main(), 0)
        self.assertTrue(json.loads(output.getvalue())["ok"])
        module.source_status.assert_called_once()

    def test_configure_targets_only_explicitly_selected_provider(self):
        module = SimpleNamespace(configure_sources=mock.Mock(return_value={"ok": True, "sources": []}))
        with mock.patch.dict("sys.modules", {"passport_sources": module}), \
             mock.patch("sys.argv", ["bridge", "sources-configure", "--provider", "cursor"]), \
             mock.patch.object(bridge, "Device", side_effect=AssertionError("opened USB")), \
             mock.patch("sys.stdout", new_callable=io.StringIO):
            self.assertEqual(bridge.main(), 0)
        self.assertEqual(module.configure_sources.call_args.args[2], ["cursor"])

    def test_setup_error_is_explained_without_raw_configuration(self):
        module = SimpleNamespace(configure_sources=mock.Mock(return_value={"ok": False, "error_code": "concurrent_change"}))
        with mock.patch.dict("sys.modules", {"passport_sources": module}), \
             mock.patch("sys.argv", ["bridge", "sources-configure", "--provider", "gemini"]), \
             mock.patch("sys.stdout", new_callable=io.StringIO) as output:
            self.assertEqual(bridge.main(), 1)
        result = json.loads(output.getvalue())
        self.assertFalse(result["ok"])
        self.assertIn("配置在操作过程中发生变化", result["error"])


if __name__ == "__main__":
    unittest.main()
