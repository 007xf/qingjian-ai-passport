#!/usr/bin/env python3
"""Bounded service ownership, recovery and privacy contract tests (no hardware)."""
import copy
import json
import os
from pathlib import Path
import plistlib
import queue
import socket
import stat
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import passport_service as service
import passport_bridge as bridge


class FakeDevice:
    def __init__(self, transport, identifier=None):
        self.transport = transport
        self.port = "BLE:" + identifier if transport == "ble" else "/dev/cu.test"
        self.closed = False

    def close(self):
        self.closed = True


class FakeBridge:
    BridgeError = bridge.BridgeError

    def __init__(self):
        self.devices = []
        self.calls = []
        self.failure = None
        self.battery = 81
        self.read_count = 0
        self.command_arguments = []

    def Device(self):
        value = FakeDevice("usb")
        self.devices.append(value)
        return value

    def BLEDevice(self, identifier):
        value = FakeDevice("ble", identifier)
        self.devices.append(value)
        return value

    def execute_device_command(self, args, device, background=False, token_result=None):
        self.calls.append((args.command, device, background))
        self.command_arguments.append((copy.copy(args), copy.deepcopy(token_result)))
        if self.failure:
            raise self.failure
        return {"ok": True, "port": device.port, "transport": device.transport,
                "status": {"battery_soc": self.battery, "device_id": "AABBCCDDEEFF"},
                "usage": {"cycle_tokens": 123, "quota_observed_at": 999}}

    def read_tokens(self, args, manual=False):
        self.read_count += 1
        return {"ok": True, "cycle_tokens": 15, "quota_observed_at": 1000}

    def collect_provider_metadata(self, result, args):
        return result

    def collect_cursor_usage(self, state_dir, refresh=False):
        return {"observed_at_ms": 1000000, "cursor_used_percent": 21}


class ServiceContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="qj-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / "Bridge"
        self.paths = service.service_paths(self.state)
        self.args = SimpleNamespace(state_dir=str(self.state), command="service-run",
                                    codex_home=str(self.root / ".codex"), codex_cli=None,
                                    no_quota=False, refresh_quota=False, keep_awake=False,
                                    port=None, ble_id=None, via_service=False)
        self.fake = FakeBridge()
        self.now = 100.0
        self.owner = service.SyncOwner(self.args, bridge=self.fake,
                                       clock=lambda: self.now, monotonic=lambda: self.now)
        self.addCleanup(self.owner.close)

    def test_reuses_one_device_for_periodic_and_manual_commands(self):
        self.owner.tick()
        self.owner.execute({"command": "status"})
        self.owner.execute({"command": "sync"})
        self.assertEqual(len(self.fake.devices), 1)
        self.assertFalse(self.fake.devices[0].closed)
        periodic = [x for x in self.fake.calls if x[2]]
        self.assertEqual([x[0] for x in periodic], ["sync"])
        self.now += 61
        self.owner.tick()
        self.assertEqual(len(self.fake.devices), 1)
        self.assertEqual(len([x for x in self.fake.calls if x[2]]), 2)

    def test_disconnect_retains_previous_observation_and_exponential_backoff(self):
        self.owner.tick()
        prior = self.owner.view()
        self.fake.failure = bridge.BridgeError("temporary")
        self.now += 61
        self.owner.tick()
        stale = self.owner.view()
        self.assertFalse(stale["connected"])
        self.assertEqual(stale["last_success_at"], prior["last_success_at"])
        self.assertEqual(stale["status_observed_at"], prior["status_observed_at"])
        self.assertEqual(stale["status"]["battery_soc"], 81)
        self.assertEqual(stale["usage"]["quota_observed_at"], 1000)  # new local read, original source date
        self.assertEqual(stale["next_retry_at"], self.now + 2)
        self.now += 1
        self.owner.tick()
        self.assertEqual(len(self.fake.devices), 1)
        self.now += 1
        self.owner.tick()
        self.assertEqual(len(self.fake.devices), 2)
        self.assertEqual(self.owner.view()["next_retry_at"], self.now + 4)
        self.fake.failure = None
        self.now += 4
        self.owner.tick()
        self.assertTrue(self.owner.view()["connected"])
        self.assertTrue(self.fake.devices[0].closed)
        self.assertTrue(self.fake.devices[1].closed)

    def test_successful_manual_connection_clears_pending_backoff(self):
        self.owner.tick()
        self.owner.fail(bridge.BridgeError("lost"))
        self.assertGreater(self.owner.next_retry, self.now)
        self.owner.execute({"command": "status"})
        self.assertEqual(self.owner.next_retry, 0)
        self.assertIsNone(self.owner.view()["next_retry_at"])
        self.assertTrue(self.owner.view()["connected"])

    def test_changed_connection_during_backoff_is_tried_immediately(self):
        self.owner.tick()
        self.owner.fail(bridge.BridgeError("lost"))
        self.owner.next_retry = self.now + 60
        selected = "1BEE324C-19E0-2203-F5D3-FCC0845EB99D"
        service.private_json(self.paths["connection"], {"transport": "bluetooth", "bluetoothIdentifier": selected})
        self.owner.tick()
        self.assertEqual(self.owner.device.port, "BLE:" + selected)
        self.assertTrue(self.owner.view()["connected"])

    def test_status_heartbeat_checks_connection_before_next_usage_scan(self):
        self.owner.tick()
        initial_reads = self.fake.read_count
        self.now += service.STATUS_INTERVAL + 1
        self.owner.tick()
        self.assertEqual(self.fake.read_count, initial_reads)
        self.assertEqual(self.fake.calls[-1][0], "status")
        self.fake.failure = bridge.BridgeError("radio lost")
        self.now += service.STATUS_INTERVAL + 1
        self.owner.tick()
        self.assertFalse(self.owner.view()["connected"])
        self.fake.failure = None
        self.now += 3
        self.owner.tick()
        self.assertTrue(self.owner.view()["connected"])
        self.assertEqual(self.fake.calls[-1][0], "sync")
        self.assertEqual(self.fake.read_count, initial_reads + 1)

    def test_wake_refreshes_even_when_monotonic_excludes_sleep(self):
        monotonic = [100.0]
        owner = service.SyncOwner(self.args, bridge=self.fake, clock=lambda: self.now, monotonic=lambda: monotonic[0])
        self.addCleanup(owner.close)
        owner.tick()
        count = self.fake.read_count
        owner.fail(bridge.BridgeError("sleep interrupted radio"))
        self.now += 500
        owner.tick()
        self.assertTrue(owner.view()["connected"])
        self.assertGreater(self.fake.read_count, count)
        self.assertEqual(owner.next_retry, 0)

    def test_repeated_sync_failures_keep_increasing_backoff_despite_status_success(self):
        self.owner.tick()
        original = self.fake.execute_device_command
        def failure_only_sync(args, device, background=False, token_result=None):
            if args.command == "sync":
                raise bridge.BridgeError("sync transport failure")
            return original(args, device, background=background, token_result=token_result)
        self.fake.execute_device_command = failure_only_sync
        self.now += 61
        self.owner.tick()
        self.assertEqual(self.owner.view()["next_retry_at"], self.now + 2)
        self.now += 2
        self.owner.tick()
        self.assertEqual(self.owner.view()["next_retry_at"], self.now + 4)
        self.now += 4
        self.owner.tick()
        self.assertEqual(self.owner.view()["next_retry_at"], self.now + 8)

    def test_invalid_new_battery_keeps_last_value_and_date_marked_stale(self):
        self.owner.tick()
        observed = self.owner.view()["battery_observed_at"]
        self.now += 61
        self.fake.battery = -1
        self.owner.tick()
        current = self.owner.view()
        self.assertEqual(current["status"]["battery_soc"], 81)
        self.assertEqual(current["battery_observed_at"], observed)
        self.assertTrue(current["battery_stale"])
        self.assertGreater(current["status_observed_at"], observed)

    def test_known_bluetooth_selection_is_reused_without_scan_or_switch(self):
        selected = "1BEE324C-19E0-2203-F5D3-FCC0845EB99D"
        service.private_json(self.paths["connection"], {"transport": "bluetooth", "bluetoothIdentifier": selected})
        self.owner.tick()
        self.owner.fail(bridge.BridgeError("lost"))
        self.now += 3
        self.owner.tick()
        self.assertEqual([x.port for x in self.fake.devices], ["BLE:" + selected] * 2)
        with self.assertRaises(service.ServiceError):
            self.owner.execute({"command": "pair"})
        with self.assertRaises(service.ServiceError):
            self.owner.execute({"command": "status", "ble_id": "6BF34EA0-A781-4A32-9A15-9715395D1C3B"})

    def test_malformed_selection_never_falls_back_to_usb(self):
        service.private_json(self.paths["connection"], {"transport": "bluetooth", "bluetoothIdentifier": "invalid"})
        self.owner.tick()
        self.assertEqual(self.fake.devices, [])
        self.assertFalse(self.owner.view()["connected"])
        self.assertTrue(self.owner.view()["retrying"])

    def test_local_sources_continue_refreshing_while_badge_is_unreachable(self):
        self.fake.failure = bridge.BridgeError("out of range")
        self.owner.tick()
        self.assertEqual(self.fake.read_count, 1)
        self.now += 61
        self.owner.tick()
        self.assertEqual(self.fake.read_count, 2)
        self.assertEqual(self.owner.view()["usage"]["quota_observed_at"], 1000)
        self.assertFalse(self.owner.view()["connected"])

    def test_remote_rejection_keeps_established_transport(self):
        import passport_ble
        self.owner.tick()
        failure = bridge.BridgeError("设备拒绝了蓝牙操作")
        failure.__cause__ = passport_ble.BLERemoteCommandError("rejected")
        self.owner.fail(failure)
        self.assertFalse(self.fake.devices[0].closed)
        self.assertTrue(self.owner.view()["connected"])
        self.assertFalse(self.owner.view()["retrying"])

    def test_permission_failure_is_visible_and_backs_off(self):
        denied = type("BleakBluetoothNotAvailableError", (Exception,), {})()
        denied.reason = SimpleNamespace(name="DENIED_BY_USER")
        failure = bridge.BridgeError("请在 macOS 设置中允许蓝牙权限")
        failure.__cause__ = denied
        self.owner.fail(failure)
        snapshot = self.owner.view()
        self.assertIn("权限", snapshot["error"])
        self.assertEqual(snapshot["error_kind"], "permission_denied")
        self.assertEqual(snapshot["next_retry_at"], self.now + 60)

    def test_generic_timeout_mentioning_permissions_uses_normal_backoff(self):
        failure = bridge.BridgeError("蓝牙连接失败或超时，请检查设备和 macOS 蓝牙权限")
        failure.__cause__ = TimeoutError("private low level details must not be exported")
        self.owner.fail(failure)
        snapshot = self.owner.view()
        self.assertEqual(snapshot["error_kind"], "timeout")
        self.assertEqual(snapshot["next_retry_at"], self.now + 2)
        self.assertNotIn("private low level", json.dumps(snapshot))
        self.owner.fail(failure)
        self.assertEqual(self.owner.view()["next_retry_at"], self.now + 4)

    def test_advice_to_check_permissions_is_not_evidence_of_denial(self):
        self.owner.fail(bridge.BridgeError("请检查设备和 macOS 蓝牙权限"))
        self.assertEqual(self.owner.view()["error_kind"], "transport_error")
        self.assertEqual(self.owner.view()["next_retry_at"], self.now + 2)

    def test_disconnect_kind_uses_normal_backoff(self):
        import passport_ble
        failure = bridge.BridgeError("蓝牙会话已断开")
        failure.__cause__ = passport_ble.BLEConnectionClosed("disconnected")
        self.owner.fail(failure)
        self.assertEqual(self.owner.view()["error_kind"], "disconnected")
        self.assertEqual(self.owner.view()["next_retry_at"], self.now + 2)

    def test_only_explicit_pending_authorization_gets_pending_kind(self):
        pending = type("BLEAuthorizationPending", (Exception,), {})("private system detail")
        self.owner.fail(pending)
        snapshot = self.owner.view()
        self.assertEqual(snapshot["error_kind"], "permission_pending")
        self.assertEqual(snapshot["next_retry_at"], self.now + 60)
        self.assertNotIn("private system detail", json.dumps(snapshot))

    def test_powered_off_is_separate_from_permission_denial(self):
        powered_off = type("BleakBluetoothNotAvailableError", (Exception,), {})()
        powered_off.reason = SimpleNamespace(name="POWERED_OFF")
        self.owner.fail(powered_off)
        self.assertEqual(self.owner.view()["error_kind"], "bluetooth_off")
        self.assertEqual(self.owner.view()["next_retry_at"], self.now + 2)

    def test_success_clears_previous_error_kind(self):
        self.owner.fail(TimeoutError())
        self.owner.execute({"command": "status"})
        self.assertIsNone(self.owner.view()["error_kind"])

    def test_corrupt_connection_settings_never_choose_usb(self):
        self.paths["connection"].write_text("not json")
        self.owner.tick()
        self.assertEqual(self.fake.devices, [])
        self.assertIn("连接设置", self.owner.view()["error"])

    def test_local_refresh_never_opens_transport(self):
        self.owner.execute({"command": "tokens"})
        self.owner.execute({"command": "cursor-usage"})
        self.assertEqual(self.fake.devices, [])
        self.assertEqual(self.owner.view()["usage"]["cycle_tokens"], 15)

    def test_worker_serializes_upload_and_does_not_retry_failed_upload(self):
        self.fake.failure = bridge.BridgeError("confirmation lost")
        server = service.ServiceServer(self.owner)
        response = queue.Queue()
        server.jobs.put(({"id": "first", "command": "upload", "config": str(self.root / "draft.json")}, response, time.monotonic() + 10))
        worker = threading.Thread(target=server.work)
        worker.start()
        result = response.get(timeout=2)
        self.assertFalse(result["ok"])
        self.fake.failure = None
        self.now += 3
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not self.owner.view().get("connected"):
            time.sleep(0.05)
        server.stop.set()
        worker.join(timeout=3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len([x for x in self.fake.calls if x[0] == "upload"]), 1)
        self.assertEqual(len([x for x in self.fake.calls if x[0] == "sync"]), 1)

    def test_repeated_request_id_is_executed_once(self):
        self.owner.next_refresh = self.now + 60
        self.owner.next_status = self.now + service.STATUS_INTERVAL
        server = service.ServiceServer(self.owner)
        responses = [queue.Queue(), queue.Queue()]
        for response in responses:
            server.jobs.put(({"id": "same-operation", "command": "tokens"}, response, time.monotonic() + 10))
        worker = threading.Thread(target=server.work)
        worker.start()
        first, second = [response.get(timeout=2) for response in responses]
        server.stop.set()
        worker.join(timeout=3)
        self.assertEqual(first, second)
        self.assertEqual(self.fake.read_count, 1)

    def test_private_snapshot_and_socket_permissions(self):
        service.private_directory(self.paths["folder"])
        self.assertEqual(stat.S_IMODE(self.paths["folder"].stat().st_mode), 0o700)
        self.owner.update(error=None)
        self.assertEqual(stat.S_IMODE(self.paths["snapshot"].stat().st_mode), 0o600)
        server = service.ServiceServer(self.owner)
        thread = threading.Thread(target=server.run)
        thread.start()
        try:
            until = time.monotonic() + 3
            while not self.paths["socket"].exists() and time.monotonic() < until:
                time.sleep(0.01)
            self.assertEqual(stat.S_IMODE(self.paths["socket"].stat().st_mode), 0o600)
            reply = service.request(self.state, {"command": "service-status"}, timeout=2)
            self.assertTrue(reply["running"])
            refreshed = service.request(self.state, {"command": "tokens"}, timeout=2)
            self.assertEqual(refreshed["cycle_tokens"], 15)
        finally:
            server.stop.set()
            thread.join(timeout=6)
        self.assertFalse(thread.is_alive())
        self.assertFalse(self.paths["socket"].exists())

    def test_snapshot_after_long_sleep_is_marked_old_without_advancing_date(self):
        self.owner.tick()
        observed = self.owner.view()["status_observed_at"]
        self.now += 400
        snapshot = self.owner.view()
        self.assertTrue(snapshot["snapshot_stale"])
        self.assertTrue(snapshot["battery_stale"])
        self.assertEqual(snapshot["status_observed_at"], observed)
        self.assertEqual(snapshot["status_age_seconds"], 400)

    def test_snapshot_read_when_stopped_is_explicitly_old(self):
        self.owner.tick()
        self.owner.close()
        snapshot = service.status(self.state)
        self.assertFalse(snapshot["connected"])
        self.assertFalse(snapshot["running"])
        self.assertTrue(snapshot["snapshot_stale"])
        self.assertEqual(snapshot["status_observed_at"], 100)

    def test_launch_agent_has_no_tcp_no_credentials_and_optional_idle_assertion(self):
        self.args.keep_awake = True
        plist = service.launch_agent_plist(self.args)
        self.assertIn("--keep-awake", plist["ProgramArguments"])
        self.assertEqual(plist["Label"], service.LABEL)
        self.assertEqual(plist["Umask"], 0o077)
        self.assertEqual(plist["StandardOutPath"], "/dev/null")
        self.assertNotIn("Sockets", plist)
        self.assertNotIn("CODEX_HOME", plist["EnvironmentVariables"])
        self.assertNotIn("token", json.dumps(plist).lower())
        self.assertEqual(plistlib.loads(plistlib.dumps(plist)), plist)

    def test_replacing_helper_source_changes_launch_revision(self):
        script = self.root / "passport_bridge.py"
        script.write_text("version = 1")
        first = service.launch_agent_plist(self.args, script=script)
        script.write_text("version = 2")
        second = service.launch_agent_plist(self.args, script=script)
        self.assertEqual(first["ProgramArguments"], second["ProgramArguments"])
        self.assertNotEqual(first["EnvironmentVariables"]["QINGJIAN_SERVICE_REVISION"],
                            second["EnvironmentVariables"]["QINGJIAN_SERVICE_REVISION"])

    def test_hourly_cadence_reconnect_reuses_cache_and_stale_ttl_stays_short(self):
        self.args.sync_interval = 3600
        owner = service.SyncOwner(self.args, bridge=self.fake, clock=lambda: self.now, monotonic=lambda: self.now)
        self.addCleanup(owner.close)
        owner.tick()
        self.assertEqual(self.fake.read_count, 1)
        self.assertEqual(owner.view()["sync_interval_seconds"], 3600)
        self.now += 61
        owner.tick()
        self.assertEqual(self.fake.read_count, 1)
        owner.fail(bridge.BridgeError("lost radio"))
        self.now += 3
        owner.tick()
        self.assertTrue(owner.view()["connected"])
        self.assertEqual(self.fake.read_count, 1)
        self.assertIsNotNone(self.fake.command_arguments[-1][1])
        self.now += 121
        self.assertTrue(owner.view()["snapshot_stale"])
        self.now = 3700
        owner.tick()
        self.assertEqual(self.fake.read_count, 2)

    def test_manual_sync_forces_fresh_source_reads_even_during_hourly_interval(self):
        self.owner.tick()
        self.owner.execute({"command": "sync"})
        args, cached = self.fake.command_arguments[-1]
        self.assertTrue(args.refresh_quota)
        self.assertIsNone(cached)
        self.assertFalse(self.fake.calls[-1][2])

    def test_sync_interval_persists_and_is_in_launch_arguments(self):
        self.args.sync_interval = 3600
        runner = mock.Mock(return_value=SimpleNamespace(returncode=0))
        with mock.patch.object(service, "status", return_value={"ok": True, "running": False}):
            enabled = service.enable(self.args, home=self.root, runner=runner)
        self.assertEqual(enabled["sync_interval_seconds"], 3600)
        self.args.sync_interval = None
        self.assertEqual(service.configured_sync_interval(self.args), 3600)
        plist = service.launch_agent_plist(self.args)
        index = plist["ProgramArguments"].index("--sync-interval")
        self.assertEqual(plist["ProgramArguments"][index + 1], "3600")
        self.assertEqual(service.status(self.state)["sync_interval_seconds"], 3600)
        self.args.sync_interval = 1
        with self.assertRaises(service.ServiceError):
            service.configured_sync_interval(self.args)

    def test_power_assertion_releases_after_offline_grace_then_restores(self):
        processes = []
        def launch(*args, **kwargs):
            process = mock.Mock()
            process.poll.return_value = None
            processes.append(process)
            return process
        power = service.AwakeAssertionController(True, clock=lambda: self.now, launcher=launch)
        self.assertTrue(power.tick({"connected": False})["awake_assertion_active"])
        self.now += 91
        self.assertFalse(power.tick({"connected": False})["awake_assertion_active"])
        processes[0].terminate.assert_called_once_with()
        self.assertTrue(power.tick({"connected": True, "snapshot_stale": False})["awake_assertion_active"])
        self.assertEqual(len(processes), 2)
        recent = {"connected": False, "last_success_at": self.now}
        self.now += 89
        self.assertTrue(power.tick(recent)["awake_assertion_active"])
        self.now += 2
        self.assertFalse(power.tick(recent)["awake_assertion_active"])
        processes[1].terminate.assert_called_once_with()

    def test_power_assertion_disabled_never_launches_caffeinate(self):
        launch = mock.Mock()
        power = service.AwakeAssertionController(False, clock=lambda: self.now, launcher=launch)
        self.assertFalse(power.tick({"connected": True})["awake_assertion_active"])
        launch.assert_not_called()

    def test_failed_power_assertion_reports_inactive_without_fast_retry(self):
        launch = mock.Mock(side_effect=OSError("private error details"))
        power = service.AwakeAssertionController(True, clock=lambda: self.now, launcher=launch)
        result = power.tick({"connected": True})
        self.assertFalse(result["awake_assertion_active"])
        self.assertIsNotNone(result["awake_assertion_error"])
        self.assertNotIn("private", json.dumps(result))
        self.now += 2
        power.tick({"connected": True})
        self.assertEqual(launch.call_count, 1)
        self.now += 30
        power.tick({"connected": True})
        self.assertEqual(launch.call_count, 2)

    def test_configured_distinguishes_first_launch_from_user_disabled(self):
        initial = service.status(self.state)
        self.assertFalse(initial["configured"])
        service.private_json(self.paths["settings"], {"enabled": False, "keep_awake": False})
        disabled = service.status(self.state)
        self.assertTrue(disabled["configured"])
        self.assertFalse(disabled["enabled"])

    def test_repeated_enable_does_not_disconnect_loaded_job(self):
        runner = mock.Mock(return_value=SimpleNamespace(returncode=0))
        with mock.patch.object(service, "status", return_value={"ok": True, "running": True}):
            service.enable(self.args, home=self.root, runner=runner)
            runner.reset_mock()
            result = service.enable(self.args, home=self.root, runner=runner)
        self.assertTrue(result["enabled"])
        self.assertTrue(result["configured"])
        self.assertEqual(len(runner.call_args_list), 1)
        self.assertEqual(runner.call_args.args[0][1], "print")

    def test_worker_failure_exits_server_for_launchd_recovery(self):
        server = service.ServiceServer(self.owner)
        errors = []
        def run():
            try:
                server.run()
            except service.ServiceError as exc:
                errors.append(str(exc))
        with mock.patch.object(self.owner, "tick", side_effect=RuntimeError("internal failure")):
            thread = threading.Thread(target=run)
            thread.start()
            thread.join(timeout=6)
            if thread.is_alive():
                server.stop.set()
                thread.join(timeout=6)
        self.assertFalse(thread.is_alive())
        self.assertTrue(server.worker_failed)
        self.assertEqual(len(errors), 1)
        self.assertIn("重启", errors[0])
        self.assertFalse(self.paths["socket"].exists())

    def test_enable_disable_use_current_user_launch_agent_only(self):
        runner = mock.Mock(return_value=SimpleNamespace(returncode=0))
        with mock.patch.object(service, "status", return_value={"ok": True, "running": False}):
            result = service.enable(self.args, home=self.root, runner=runner)
            self.assertTrue(result["enabled"])
            self.assertTrue(service.enabled(self.state))
            result = service.disable(self.args, home=self.root, runner=runner)
            self.assertFalse(result["enabled"])
            self.assertFalse(service.enabled(self.state))
        invocations = [call.args[0] for call in runner.call_args_list]
        self.assertTrue(all(command[0] == "/bin/launchctl" for command in invocations))
        self.assertTrue(all(any(item.startswith(f"gui/{os.getuid()}") for item in command) for command in invocations))
        self.assertFalse((self.root / "Library/LaunchAgents" / (service.LABEL + ".plist")).exists())

    def test_failed_enable_does_not_mark_enabled(self):
        runner = mock.Mock(return_value=SimpleNamespace(returncode=5))
        with self.assertRaises(service.ServiceError):
            service.enable(self.args, home=self.root, runner=runner)
        self.assertFalse(service.enabled(self.state))
        self.assertFalse((self.root / "Library/LaunchAgents" / (service.LABEL + ".plist")).exists())

    def test_symlinked_private_directory_is_rejected(self):
        destination = self.root / "other"
        destination.mkdir()
        self.state.mkdir()
        self.paths["folder"].symlink_to(destination, target_is_directory=True)
        with self.assertRaises(service.ServiceError):
            self.owner.update(error=None)

    def test_unknown_and_oversized_socket_requests_are_rejected(self):
        first, second = socket.socketpair()
        with first, second:
            sender = threading.Thread(target=lambda: first.sendall(b"x" * (service.MAX_MESSAGE + 1)))
            sender.start()
            with self.assertRaises(service.ServiceError):
                service.receive_json(second)
            sender.join(timeout=2)
            self.assertFalse(sender.is_alive())
        with self.assertRaises(service.ServiceError):
            self.owner.execute({"command": "arbitrary-shell-command"})

    def test_bridge_background_sync_uses_confirmed_settings_without_artwork(self):
        self.state.mkdir()
        committed = {**bridge.DEFAULT_BADGE, "threshold1": 77, "threshold2": 555,
                     "feature_all": False, "feature_mask": 147}
        (self.state / "04 device config.json").write_text(json.dumps(committed))
        (self.root / "badge.json").write_text(json.dumps({**committed, "name": "UNSAVED"}))
        args = copy.copy(self.args)
        args.command = "sync"
        device = mock.Mock(transport="usb", port="/dev/cu.test")
        device.status.return_value = {"device_id": "AABBCCDDEEFF"}
        quota = {"ok": True, "cycle_tokens": 1, "growth_ready": False}
        with mock.patch.object(bridge, "read_tokens", return_value=quota) as read, \
                mock.patch.object(bridge, "collect_provider_metadata"), \
                mock.patch.object(bridge, "prepare_avatars") as art, \
                mock.patch.object(bridge, "sync_device", return_value={"tokens_known": True}) as sync:
            bridge.execute_device_command(args, device, background=True)
        self.assertEqual(read.call_args.kwargs["config"]["name"], committed["name"])
        art.assert_not_called()
        self.assertIsNone(sync.call_args.args[2])
        self.assertIsNone(sync.call_args.args[3])
        device.close.assert_not_called()
        self.assertFalse((self.state / "05 device sync.json").exists())
        self.assertEqual(json.loads((self.root / "badge.json").read_text())["name"], "UNSAVED")

    def test_direct_device_command_closes_only_its_own_transport(self):
        args = copy.copy(self.args)
        args.command = "status"
        device = mock.Mock(transport="usb", port="/dev/cu.test")
        device.status.return_value = {"battery_soc": 65}
        with mock.patch.object(bridge, "Device", return_value=device):
            result = bridge.execute_device_command(args)
        self.assertEqual(result["status"]["battery_soc"], 65)
        device.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
