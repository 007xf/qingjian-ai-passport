#!/usr/bin/env python3
"""Guarded statusLine install and exact prior-command forwarding fixtures."""
import base64
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import passport_claude_setup as installer
import passport_claude_statusline as wrapper
import passport_sources as setup


class ClaudeSetupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.profile = self.home / ".claude"
        self.state = self.home / "app/Bridge"
        self.target = self.profile / "settings.json"
        self.runtime = self.home / "runtime"
        self.runtime.write_text("#!/bin/sh\nexit 0\n")
        self.runtime.chmod(0o700)

    def configure(self):
        return installer.configure(self.state, self.runtime, self.profile, self.home)

    def write(self, value):
        self.target.parent.mkdir(parents=True, exist_ok=True)
        self.target.write_text(json.dumps(value))
        self.target.chmod(0o600)

    def test_install_preserves_command_padding_hooks_and_private_backup(self):
        value = {"statusLine": {"type": "command", "command": "printf '%s' \"$PWD\"", "padding": 2},
                 "hooks": {"Stop": [{"existing": True}]}, "env": {"PRIVATE_TEST": "preserve"}}
        self.write(value)
        before = self.target.read_bytes()
        result = self.configure()
        self.assertTrue(result["ok"])
        current = json.loads(self.target.read_text())
        self.assertEqual(current["hooks"], value["hooks"])
        self.assertEqual(current["env"], value["env"])
        self.assertEqual(current["statusLine"]["padding"], 2)
        encoded = current["statusLine"]["command"].split()[-1]
        self.assertEqual(json.loads(base64.b64decode(encoded)), value["statusLine"]["command"])
        backups = list((self.state / "08 hooks/09 setup backups").rglob("*.original"))
        self.assertTrue(any(path.read_bytes() == before for path in backups))
        self.assertTrue(all(path.stat().st_mode & 0o077 == 0 for path in backups))
        second = self.configure()
        self.assertEqual(second["changed"], {"providers": [], "support_files": [], "backup_count": 0})
        self.assertTrue(second["sources"][0]["configured"])

    def test_repair_retains_prior_command_without_recursive_wrapper(self):
        self.write({"statusLine": {"type": "command", "command": "echo original"}})
        self.assertTrue(self.configure()["ok"])
        prior = self.target.read_bytes()
        (self.state / "08 hooks/passport_claude.py").write_text("broken")
        value = self.configure()
        self.assertTrue(value["ok"])
        self.assertEqual(prior, self.target.read_bytes())
        self.assertNotIn("claude", value["changed"]["providers"])

    def test_invalid_and_symlink_settings_are_preserved(self):
        self.write({"statusLine": "unexpected"})
        before = self.target.read_bytes()
        self.assertFalse(self.configure()["ok"])
        self.assertEqual(self.target.read_bytes(), before)
        self.target.unlink()
        outside = self.home / "outside.json"
        outside.write_bytes(before)
        self.target.symlink_to(outside)
        self.assertFalse(self.configure()["ok"])
        self.assertEqual(outside.read_bytes(), before)

    def test_failed_settings_write_rolls_back_support_and_preserves_user_file(self):
        self.write({"statusLine": {"type": "command", "command": "echo original"}})
        before = self.target.read_bytes()
        real = setup._replace
        def fail(change, *args, **kwargs):
            if change.provider == "claude":
                raise setup.SourceError("write_verification_failed")
            return real(change, *args, **kwargs)
        with mock.patch.object(setup, "_replace", side_effect=fail):
            self.assertFalse(self.configure()["ok"])
        self.assertEqual(self.target.read_bytes(), before)
        self.assertFalse((self.state / "08 hooks/passport-claude-statusline").exists())

    def test_wrapper_passes_exact_original_input_command_and_exit_status(self):
        raw = b'{"session_id":"anonymous","rate_limits":{}}\n'
        text = io.TextIOWrapper(io.BytesIO(raw), encoding="utf-8")
        encoded = base64.b64encode(json.dumps("printf 'unchanged' && exit 7").encode()).decode()
        with mock.patch.object(wrapper.sys, "stdin", text), \
                mock.patch.object(wrapper, "capture_statusline", return_value={}) as capture, \
                mock.patch.object(wrapper.subprocess, "run", return_value=mock.Mock(returncode=7)) as run:
            result = wrapper.main(["--state-dir", str(self.state), "--claude-home", str(self.profile), "--original-command-b64", encoded])
        self.assertEqual(result, 7)
        self.assertEqual(run.call_args.args[0], ["/bin/sh", "-c", "printf 'unchanged' && exit 7"])
        self.assertEqual(run.call_args.kwargs["input"], raw)
        capture.assert_called_once()


if __name__ == "__main__":
    unittest.main()
