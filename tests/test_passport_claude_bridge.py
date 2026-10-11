#!/usr/bin/env python3
"""Actual Python payload -> C parser/state fixtures, without USB or accounts."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import passport_bridge as bridge
import passport_claude as quota
import passport_claude_tokens as tokens


class ClaudeBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.build.cleanup)
        folder = Path(cls.build.name)
        source = folder / "roundtrip.c"
        source.write_text('''#include "passport_claude_wire.h"
#include <stdio.h>
#include <stdlib.h>
int main(int argc, char **argv) {
    if(argc!=2) return 2;
    int64_t now=strtoll(argv[1],NULL,10);
    passport_claude_state_t state;
    passport_claude_state_init(&state);
    char line[1024];
    while(fgets(line,sizeof(line),stdin)) {
        size_t n=0; while(line[n] && line[n]!='\\n') ++n;
        passport_claude_snapshot_t snapshot; bool present,enabled;
        if(!passport_claude_wire_parse((unsigned char*)line,n,&snapshot,&present,&enabled)) return 3;
        if(!passport_claude_state_apply(&state,&snapshot)) return 4;
        printf("%d %d %lld\\n",passport_claude_quota_display_known(&state.snapshot,0),
               passport_claude_quota_fresh(&state.snapshot,0,now),
               (long long)state.snapshot.quota_observed_utc_ms);
    }
    return 0;
}''')
        cls.exe = folder / "roundtrip"
        subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-Wall", "-Wextra", "-Werror", "-I" + str(ROOT / "main"),
                        str(source), str(ROOT / "main/passport_claude_model.c"), str(ROOT / "main/passport_claude_wire.c"),
                        str(ROOT / "main/passport_wire.c"), "-lm", "-o", str(cls.exe)], check=True, capture_output=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name) / "state"
        self.home = Path(self.temp.name) / "claude"
        self.now = time.time_ns() // 1_000_000
        self.input = {"session_id": "fixture-session-a", "rate_limits": {
            "five_hour": {"used_percentage": 25, "resets_at": self.now // 1000 + 3600},
            "seven_day": {"used_percentage": 42, "resets_at": self.now // 1000 + 86400}}}

    def capture(self):
        with mock.patch.object(quota, "_identity", return_value="a" * 64):
            return quota.capture_statusline(self.input, self.state, self.home, self.now)

    def roundtrip(self, values):
        encoded = "".join(json.dumps(bridge.claude_dashboard_payload({"quota": value}), separators=(",", ":")) + "\n" for value in values)
        result = subprocess.run([str(self.exe), str(self.now)], input=encoded, text=True, check=True, capture_output=True)
        return [tuple(map(int, row.split())) for row in result.stdout.splitlines()]

    def test_signout_and_account_switch_immediately_clear_device_quota(self):
        fresh = self.capture()
        for identity in ("b" * 64, quota.QuotaError("not_signed_in")):
            with self.subTest(identity=type(identity).__name__):
                with mock.patch.object(quota, "_identity", **({"side_effect": identity} if isinstance(identity, Exception) else {"return_value": identity})):
                    invalidated = quota.collect_claude_quota(self.state, self.home, self.now)
                states = self.roundtrip([fresh, invalidated, fresh])
                self.assertEqual(states[0][:2], (1, 1))
                self.assertEqual(states[1], (0, 0, 0))
                self.assertEqual(states[2], (0, 0, 0))

    def test_unavailable_identity_keeps_original_time_as_last_reading(self):
        fresh = self.capture()
        with mock.patch.object(quota, "_identity", side_effect=quota.QuotaError("identity_unavailable")):
            last = quota.collect_claude_quota(self.state, self.home, self.now)
        states = self.roundtrip([fresh, last, fresh])
        self.assertEqual(states[1], (1, 0, self.now))
        self.assertEqual(states[2], (1, 0, self.now))

    def test_legacy_device_does_not_receive_new_protocol_and_upload_controls_enable(self):
        device = mock.Mock()
        usage = {"claude_usage": {}}
        bridge.sync_claude_dashboard(device, {}, usage, True)
        device.request.assert_not_called()
        device.wait_fields.side_effect = lambda fields: fields
        bridge.sync_claude_dashboard(device, {"claude_dashboard_supported": True}, usage)
        self.assertNotIn("claude_enabled", device.wait_fields.call_args.args[0])
        bridge.sync_claude_dashboard(device, {"claude_dashboard_supported": True}, usage, True)
        self.assertTrue(device.wait_fields.call_args.args[0]["claude_enabled"])

    def test_no_quota_reads_tokens_without_probing_identity(self):
        with mock.patch.object(quota, "collect_claude_quota") as read, \
                mock.patch.object(tokens, "collect_claude_tokens", return_value={"source": "claude_code_local"}) as local:
            result = bridge.collect_claude_usage(self.state, self.home, no_quota=True)
        read.assert_not_called()
        local.assert_called_once()
        self.assertFalse(result["quota"]["available"])

    def test_standalone_read_is_device_free_and_preserves_growth_contract(self):
        with mock.patch.object(bridge.sys, "argv", ["bridge", "--state-dir", str(self.state), "claude-usage"]), \
             mock.patch("passport_service.should_forward", return_value=False), \
             mock.patch.object(bridge, "collect_claude_usage", return_value={"quota": {}}), \
             mock.patch.object(bridge, "Device", side_effect=AssertionError("USB")), \
             mock.patch.object(bridge, "read_tokens", side_effect=AssertionError("Codex")), \
             mock.patch.object(bridge.sys, "stdout", io.StringIO()) as output:
            self.assertEqual(bridge.main(), 0)
        self.assertIn("claude_usage", json.loads(output.getvalue()))
        usage = {"claude_usage": {"token_usage": {"today_tokens": 999}}, "threshold1": 10, "threshold2": 20}
        bridge.combine_growth(usage)
        self.assertIsNone(usage["growth_tokens"])
        self.assertFalse(usage["growth_ready"])


if __name__ == "__main__":
    unittest.main()
