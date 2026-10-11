"""Opt-in Claude statusLine chaining using the existing guarded setup writer.

No real settings are changed by import or source diagnostics. Only configure()
installs, with exact private backups, concurrent-edit checks and rollback.
"""
import base64
import json
import os
from pathlib import Path
import shlex
import sys
import time

import passport_sources as setup

LAUNCHER = "passport-claude-statusline"
MODULES = ("passport_claude.py", "passport_providers.py", "passport_claude_statusline.py")


def _locations(state_dir, runtime_path, claude_home=None, home=None):
    base, state, runtime, _ = setup._paths(state_dir, runtime_path, home)
    profile = setup._path(claude_home or (os.environ.get("CLAUDE_CONFIG_DIR") if home is None else None) or base / ".claude")
    if base not in profile.parents or state == profile or profile in state.parents:
        raise setup.SourceError("path_outside_home")
    return base, state, runtime, profile


def _files(state, runtime, profile):
    root = state / "08 hooks"
    launcher = f'#!/bin/sh\nIFS= read -r runtime < {shlex.quote(str(root / "runtime.path"))} || exit 1\nexec "$runtime" -B {shlex.quote(str(root / "passport_claude_statusline.py"))} --state-dir {shlex.quote(str(state))} --claude-home {shlex.quote(str(profile))} "$@"\n'
    files = {name: (Path(__file__).with_name(name).read_bytes(), 0o600) for name in MODULES}
    files[LAUNCHER] = (launcher.encode(), 0o700)
    files["runtime.path"] = ((str(runtime) + "\n").encode(), 0o600)
    return files


def _merged(original, launcher):
    obj = setup._json(original.data) if original else {}
    if not isinstance(obj, dict):
        raise setup.SourceError("invalid_config")
    prior = obj.get("statusLine")
    if prior is not None and (not isinstance(prior, dict) or prior.get("type") != "command" or not isinstance(prior.get("command"), str)):
        raise setup.SourceError("unsupported_statusline")
    prefix = shlex.quote(str(launcher)) + " --original-command-b64 "
    if prior and prior["command"].startswith(prefix):
        try:
            args = shlex.split(prior["command"])
            if len(args) != 3 or args[:2] != [str(launcher), "--original-command-b64"]:
                raise ValueError()
            text = base64.b64decode(args[2], validate=True).decode("utf-8")
            if not isinstance(json.loads(text), str):
                raise ValueError()
        except (ValueError, UnicodeError):
            raise setup.SourceError("owned_hook_modified") from None
        return obj, obj
    encoded = base64.b64encode(json.dumps(prior["command"] if prior else "").encode()).decode()
    return obj, {**obj, "statusLine": {**(prior or {}), "type": "command", "command": prefix + shlex.quote(encoded)}}


def _plan(base, state, runtime, profile):
    if not setup._runtime_valid(runtime):
        raise setup.SourceError("invalid_runtime")
    changes = []
    for name, (data, mode) in _files(state, runtime, profile).items():
        target = state / "08 hooks" / name
        prior = setup._snapshot(target, base)
        if prior is None or prior.data != data or prior.mode != mode:
            changes.append(setup.Change(target, prior, data, mode, name))
    target = profile / "settings.json"
    prior = setup._snapshot(target, base)
    obj, merged = _merged(prior, state / "08 hooks" / LAUNCHER)
    if obj != merged:
        data = (json.dumps(merged, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
        if len(data) > setup.MAX_FILE:
            raise setup.SourceError("oversized_config")
        changes.append(setup.Change(target, prior, data, prior.mode if prior else 0o600, "claude", "claude"))
    return changes


def source_status(state_dir, runtime_path, claude_home=None, home=None):
    row = {"provider": "claude", "configured": False, "status": "not_configured", "error_code": None,
           "receiver_intact": False, "launcher_intact": False, "runtime_intact": False,
           "events_observed": False, "event_freshness": "none", "last_event_at_ms": 0,
           "can_configure": True, "quota_supported": True, "desktop_supported": False}
    try:
        base, state, runtime, profile = _locations(state_dir, runtime_path, claude_home, home)
        files = _files(state, runtime, profile)
        integrity = {}
        for name, (data, mode) in files.items():
            prior = setup._snapshot(state / "08 hooks" / name, base)
            integrity[name] = prior is not None and prior.data == data and prior.mode == mode
        old = setup._snapshot(profile / "settings.json", base)
        obj, merged = _merged(old, state / "08 hooks" / LAUNCHER)
        row.update(configured=old is not None and obj == merged,
                   receiver_intact=all(integrity[name] for name in MODULES),
                   launcher_intact=integrity[LAUNCHER], runtime_intact=integrity["runtime.path"] and setup._runtime_valid(runtime))
        if row["configured"]:
            row["status"] = "waiting_for_events" if all(integrity.values()) else "needs_repair"
        from passport_claude import _profile, _cache_read
        now = time.time_ns() // 1_000_000
        cached = _cache_read(state, _profile(profile)[1], now)
        if cached:
            row.update(events_observed=True, last_event_at_ms=cached["observed_at_ms"],
                       event_freshness="fresh" if now < cached["expires_at_ms"] else "expired")
            if row["configured"] and all(integrity.values()):
                row["status"] = "live" if row["event_freshness"] == "fresh" else "expired"
    except (setup.SourceError, OSError) as error:
        row.update(status="error", error_code=str(error) if isinstance(error, setup.SourceError) else "source_status_failed")
    return row


def configure(state_dir, runtime_path, claude_home=None, home=None):
    count = 0
    try:
        base, state, runtime, profile = _locations(state_dir, runtime_path, claude_home, home)
        _plan(base, state, runtime, profile)
        with setup._setup_lock(state, base):
            plan = _plan(base, state, runtime, profile)
            count = setup._backup(plan, state, base)
            if any(setup._snapshot(change.path, base) != change.original for change in plan):
                raise setup.SourceError("concurrent_change")
            applied = []
            try:
                for change in plan:
                    applied.append(change)
                    change.installed = setup._replace(change, base)
            except (setup.SourceError, OSError):
                if not setup._rollback(applied, base):
                    raise setup.SourceError("rollback_incomplete") from None
                raise
        return {"ok": True, "sources": [source_status(state, runtime, profile, base)],
                "changed": {"providers": ["claude"] if any(change.provider for change in plan) else [],
                            "support_files": [change.label for change in plan if not change.provider], "backup_count": count}}
    except (setup.SourceError, OSError) as error:
        return {"ok": False, "error_code": str(error) if isinstance(error, setup.SourceError) else "source_setup_failed",
                "changed": {"providers": [], "support_files": [], "backup_count": count}}
