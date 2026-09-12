#!/usr/bin/env python3
"""Private, fail-open Cursor/Gemini hook observer and installation preview.

Official input contracts (checked 2026-09-12):
https://cursor.com/docs/hooks
https://geminicli.com/docs/hooks/reference/

Only hashed identifiers, allowlisted state/model and timestamps reach disk.
This module never opens transcripts, runs payload commands, grants permissions,
starts a model turn, or modifies either provider's settings. ``prepare`` writes
review artifacts; the caller must inspect and explicitly install them later.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import select
import shlex
import stat
import sys
import tempfile
import time


DEFAULT_STATE = Path.home() / "Library/Application Support/AI Passport/Bridge"
ACTIVITY_DIR = "07 activity"
HOOK_DIR = "08 hooks"
TTL_MS = 180_000
MAX_INPUT = 262_144
MAX_RECORDS = 128  # Per provider, across conversations/generations.
MAX_FILE = 4096
MAX_CONFIG = 1_048_576
READ_TIMEOUT = 0.25
LOCK_TIMEOUT = 0.2
RETENTION_MS = 7 * 24 * 60 * 60 * 1000
PROVIDERS = ("cursor", "gemini")
CURSOR_EVENTS = (
    "beforeSubmitPrompt", "preToolUse", "postToolUse", "postToolUseFailure",
    "stop", "sessionEnd",
)
GEMINI_EVENTS = (
    "BeforeAgent", "BeforeTool", "AfterTool", "AfterAgent", "Notification",
    "SessionEnd",
)
TERMINAL_EVENTS = {"stop", "sessionEnd", "AfterAgent", "SessionEnd"}
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:+-]{0,31}\Z", re.ASCII)
HASH_RE = re.compile(r"[0-9a-f]{64}\Z", re.ASCII)
RECORD_RE = re.compile(r"(cursor|gemini)-[0-9a-f]{64}\.json\Z", re.ASCII)
RECORD_FIELDS = {"provider", "session_id_hash", "session_group_hash", "generation_id_hash",
                 "state", "state_at_ms", "state_until_ms", "model", "event_id"}


class ActivityError(Exception):
    """A fixed diagnostic code; never include input or filesystem details."""


def utc_ms():
    return time.time_ns() // 1_000_000


def _hash(*parts):
    # Length prefixes avoid ambiguity without retaining original identifiers.
    digest = hashlib.sha256(b"qingjian-activity-v1\0")
    for value in parts:
        raw = value.encode("utf-8")
        digest.update(len(raw).to_bytes(4, "big"))
        digest.update(raw)
    return digest.hexdigest()


def _identifier(value):
    if not isinstance(value, str) or not 1 <= len(value.encode("utf-8")) <= 512:
        raise ActivityError("invalid_identifier")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ActivityError("invalid_identifier")
    return value


def _model(value):
    return value if isinstance(value, str) and MODEL_RE.fullmatch(value) else ""


def _timestamp(payload, now_ms):
    # Gemini supplies a source timestamp. Preserve it so delayed/replayed hooks
    # cannot renew a working lease. Cursor's documented schema has no timestamp.
    raw = payload.get("timestamp")
    if raw is None:
        return now_ms
    if not isinstance(raw, str) or len(raw) > 40:
        raise ActivityError("invalid_timestamp")
    try:
        parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError
        result = int(parsed.timestamp() * 1000)
    except (ValueError, OverflowError):
        raise ActivityError("invalid_timestamp") from None
    if result > now_ms + 5000 or result < now_ms - TTL_MS:
        raise ActivityError("stale_timestamp")
    return min(result, now_ms)


def normalize_event(provider, payload, now_ms):
    """Return an allowlisted state record, or None for irrelevant notification."""
    if provider not in PROVIDERS or not isinstance(payload, dict):
        raise ActivityError("invalid_input")
    event = payload.get("hook_event_name")
    events = CURSOR_EVENTS if provider == "cursor" else GEMINI_EVENTS
    if not isinstance(event, str) or event not in events:
        raise ActivityError("unsupported_event")
    if provider == "gemini" and event == "Notification":
        if payload.get("notification_type") != "ToolPermission":
            return None

    if provider == "cursor":
        # Common conversation_id is authoritative across turns. session_id is
        # only a fallback for sessionEnd payloads that omit the common field;
        # we never guess that unrelated identifiers denote the same session.
        session = payload.get("conversation_id")
        if session is None and event == "sessionEnd":
            session = payload.get("session_id")
        session = _identifier(session)
        generation = payload.get("generation_id")
        if generation is None and event == "sessionEnd":
            generation = ""
        elif generation is not None:
            generation = _identifier(generation)
        else:
            raise ActivityError("missing_generation")
    else:
        session = _identifier(payload.get("session_id"))
        generation = ""  # Gemini's common hook contract has no turn ID.

    state = "working"
    if event in ("sessionEnd", "SessionEnd", "AfterAgent"):
        state = "idle"
    elif event == "stop":
        status_value = payload.get("status")
        if status_value not in ("completed", "aborted", "error"):
            raise ActivityError("invalid_stop_status")
        state = "error" if status_value == "error" else "idle"
    elif event == "Notification":
        state = "waiting"
    if event == "sessionEnd" and payload.get("reason") == "error":
        state = "error"

    # A failed individual tool is not proof that the whole agent has failed.
    at = _timestamp(payload, now_ms)
    return {
        "provider": provider,
        "session_id_hash": _hash(provider, session, generation),
        "session_group_hash": _hash(provider, session),
        "generation_id_hash": _hash(generation) if generation else "",
        "state": state,
        "state_at_ms": at,
        "state_until_ms": at + TTL_MS,
        "model": _model(payload.get("model_id")) or _model(payload.get("model")),
        "event_id": event,  # Canonical enum, never arbitrary input text.
    }


def _private_dir(path):
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ActivityError("unsafe_directory")
    os.chmod(path, 0o700)
    return path


def _open_private(path, flags):
    fd = os.open(path, flags | os.O_NOFOLLOW, 0o600)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        os.close(fd)
        raise ActivityError("unsafe_file")
    os.fchmod(fd, 0o600)
    return fd


@contextmanager
def _lock(directory, name=".lock", timeout=LOCK_TIMEOUT):
    fd = _open_private(directory / name, os.O_CREAT | os.O_RDWR)
    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ActivityError("lock_timeout") from None
                time.sleep(0.005)
        yield
    finally:
        os.close(fd)


def _read_json(path, limit=MAX_FILE):
    try:
        fd = _open_private(path, os.O_RDONLY)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as handle:
        data = handle.read(limit + 1)
    if len(data) > limit:
        raise ActivityError("oversized_file")
    try:
        return json.loads(data)
    except (UnicodeError, ValueError, RecursionError):
        raise ActivityError("invalid_saved_json") from None


def _atomic_bytes(path, data, mode=0o600):
    if path.is_symlink():
        raise ActivityError("unsafe_file")
    fd, temporary = tempfile.mkstemp(prefix=".qingjian-", dir=path.parent)
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def _atomic_json(path, data):
    _atomic_bytes(path, (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode())


def _valid_record(record):
    return (isinstance(record, dict) and set(record) == RECORD_FIELDS
            and record.get("provider") in PROVIDERS
            and all(isinstance(record.get(key), str) and HASH_RE.fullmatch(record[key])
                    for key in ("session_id_hash", "session_group_hash"))
            and record.get("state") in ("working", "idle", "waiting", "error")
            and type(record.get("state_at_ms")) is int
            and type(record.get("state_until_ms")) is int
            and 0 < record["state_at_ms"] < record["state_until_ms"]
            <= record["state_at_ms"] + TTL_MS
            and (record.get("generation_id_hash") == ""
                 or isinstance(record.get("generation_id_hash"), str)
                 and HASH_RE.fullmatch(record["generation_id_hash"]))
            and isinstance(record.get("model"), str)
            and (record["model"] == "" or MODEL_RE.fullmatch(record["model"]))
            and record.get("event_id") in (*CURSOR_EVENTS, *GEMINI_EVENTS))


def _records(directory, provider):
    result = []
    for path in directory.iterdir():
        if not path.name.startswith(provider + "-") or not RECORD_RE.fullmatch(path.name):
            continue
        if len(result) >= MAX_RECORDS:
            raise ActivityError("record_capacity")
        record = _read_json(path)
        if not _valid_record(record) or record["provider"] != provider:
            raise ActivityError("invalid_saved_record")
        result.append((path, record))
    return result


def persist_event(state_dir, record, now_ms):
    """Atomic per-run updates; caller supplies a normalized allowlisted record."""
    if not _valid_record(record):
        raise ActivityError("invalid_record")
    root = _private_dir(state_dir)
    directory = _private_dir(root / ACTIVITY_DIR)
    with _lock(directory):
        records = _records(directory, record["provider"])
        target = directory / f"{record['provider']}-{record['session_id_hash']}.json"
        prior = next((old for path, old in records if path == target), None)

        if record["event_id"] in ("sessionEnd", "SessionEnd"):
            # End every observed generation in this exact anonymous group. Do
            # not use process presence or close an unrelated provider/session.
            matched = False
            for path, old in records:
                if old["session_group_hash"] != record["session_group_hash"]:
                    continue
                if old["state_at_ms"] > record["state_at_ms"]:
                    continue
                ended = dict(old, state=record["state"], event_id=record["event_id"],
                             state_at_ms=record["state_at_ms"],
                             state_until_ms=record["state_until_ms"])
                _atomic_json(path, ended)
                matched = True
            if matched:
                return True

        if prior is not None:
            if record["state_at_ms"] < prior["state_at_ms"]:
                return False
            if prior["event_id"] in ("sessionEnd", "SessionEnd"):
                return False
            if prior["event_id"] == "stop":
                return False  # A completed Cursor generation cannot restart.
            if (prior["event_id"] == "AfterAgent"
                    and record["event_id"] not in ("BeforeAgent", "AfterAgent")):
                return False
            if (record["event_id"] == "BeforeAgent" and prior["event_id"] == "AfterAgent"
                    and record["state_at_ms"] <= prior["state_at_ms"]):
                return False
            if record["state_at_ms"] == prior["state_at_ms"] and record == prior:
                return False
        # Keep bounded metadata, retaining expired active records so collectors
        # can label missing stop events unknown. Reclaim proved-ended records
        # first; abandoned records are retained for at most seven days.
        for path, old in records:
            if path == target:
                continue
            expired = old["state_until_ms"] <= now_ms
            if expired and (old["event_id"] in TERMINAL_EVENTS
                            or old["state_at_ms"] < now_ms - RETENTION_MS):
                path.unlink()
                records = [(p, r) for p, r in records if p != path]
        if prior is None and len(records) >= MAX_RECORDS:
            raise ActivityError("record_capacity")
        _atomic_json(target, record)
        return True


def record_error(state_dir, provider, code, now_ms):
    """Best effort own diagnostics; no raw exceptions, input, paths or output."""
    try:
        directory = _private_dir(_private_dir(_private_dir(state_dir) / ACTIVITY_DIR) / ".metadata")
        with _lock(directory, timeout=0.02):
            path = directory / f"{provider if provider in PROVIDERS else 'receiver'}.json"
            previous = _read_json(path)
            count = previous.get("error_count", 0) if isinstance(previous, dict) else 0
            count = count if type(count) is int and count >= 0 else 0
            _atomic_json(path, {"error_code": code, "error_count": min(count + 1, 2**31 - 1),
                                "observed_at_ms": now_ms})
    except Exception:
        pass  # Reporting a full/unwritable filesystem must never block AI.


def receive_payload(provider, data, state_dir=DEFAULT_STATE, now_ms=None):
    now_ms = utc_ms() if now_ms is None else now_ms
    try:
        if not isinstance(data, bytes) or len(data) > MAX_INPUT:
            raise ActivityError("input_too_large")
        try:
            payload = json.loads(data)
        except (UnicodeError, ValueError, RecursionError):
            raise ActivityError("invalid_json") from None
        record = normalize_event(provider, payload, now_ms)
        return record is not None and persist_event(state_dir, record, now_ms)
    except Exception as error:
        code = str(error) if isinstance(error, ActivityError) else "receiver_error"
        record_error(state_dir, provider, code, now_ms)
        return False


def read_bounded_stdin(fd, timeout=READ_TIMEOUT, limit=MAX_INPUT):
    deadline = time.monotonic() + timeout
    data = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], max(0, remaining))[0]:
            raise ActivityError("stdin_timeout")
        chunk = os.read(fd, min(8192, limit + 1 - len(data)))
        if not chunk:
            return bytes(data)
        data.extend(chunk)
        if len(data) > limit:
            raise ActivityError("input_too_large")


def launcher_text(support_dir, state_dir):
    """Stable shell entry; runtime mapping is data, never sourced/evaluated."""
    support = shlex.quote(str(Path(support_dir).absolute()))
    state = shlex.quote(str(Path(state_dir).absolute()))
    return f'''#!/bin/sh
# QingJian activity observer. Missing/moved runtime fails open with valid JSON.
case "$1" in cursor|gemini) provider="$1" ;; *) printf '{{}}\\n'; exit 0 ;; esac
support={support}
runtime=''
IFS= read -r runtime 2>/dev/null < "$support/runtime.path" || :
case "$runtime" in /*) ;; *) printf '{{}}\\n'; exit 0 ;; esac
if [ ! -x "$runtime" ] || [ ! -f "$support/passport_activity.py" ]; then
    printf '{{}}\\n'
    exit 0
fi
"$runtime" -I "$support/passport_activity.py" receive --provider "$provider" --state-dir {state} 2>/dev/null || printf '{{}}\\n'
exit 0
'''


def merge_hook_config(provider, existing, launcher):
    """Pure merge; preserve all user settings and all unrelated hook entries."""
    if provider not in PROVIDERS or not isinstance(existing, dict):
        raise ActivityError("invalid_config")
    merged = copy.deepcopy(existing)
    hooks = merged.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ActivityError("invalid_hooks_config")
    command = shlex.quote(str(Path(launcher).absolute())) + " " + provider
    if provider == "cursor":
        if "version" in merged and merged["version"] != 1:
            raise ActivityError("unsupported_hooks_version")
        merged.setdefault("version", 1)
        for event in CURSOR_EVENTS:
            entries = hooks.setdefault(event, [])
            if not isinstance(entries, list):
                raise ActivityError("invalid_hooks_config")
            if not any(isinstance(item, dict) and item.get("command") == command for item in entries):
                entries.append({"command": command, "type": "command", "timeout": 1,
                                "failClosed": False})
    else:
        for event in GEMINI_EVENTS:
            entries = hooks.setdefault(event, [])
            if not isinstance(entries, list):
                raise ActivityError("invalid_hooks_config")
            if any(isinstance(item, dict) and isinstance(item.get("hooks"), list)
                   and any(isinstance(child, dict) and child.get("command") == command
                           for child in item["hooks"]) for item in entries):
                continue
            group = {"hooks": [{"name": "qingjian-activity", "type": "command",
                                 "command": command, "timeout": 1000}]}
            if event == "Notification":
                group["matcher"] = "ToolPermission"
            entries.append(group)
    return merged


def prepare_install(review_dir, runtime_path, state_dir=DEFAULT_STATE,
                    cursor_config=None, gemini_config=None):
    """Stage private files and merged JSON only; never install user hooks.

    Installer must compare original_sha256, copy the original bytes to the
    timestamped backup_path, then atomically replace the target. Runtime and
    receiver go in support_dir, with launcher mode 0700 and other files 0600.
    App launch refreshes runtime.path atomically after a move/update. It must
    not change provider settings automatically after this initial opt-in.
    """
    runtime = Path(runtime_path).absolute()
    if not runtime.is_file() or not os.access(runtime, os.X_OK) or "\n" in str(runtime):
        raise ActivityError("invalid_runtime")
    state_dir = Path(state_dir).absolute()
    review = Path(review_dir).absolute()
    # A caller cannot accidentally stage into a live settings or support path.
    forbidden = [Path.home() / ".cursor", Path.home() / ".gemini", state_dir]
    if any(review == path or path in review.parents for path in forbidden):
        raise ActivityError("review_directory_not_isolated")
    review = _private_dir(review)
    support = state_dir / HOOK_DIR
    launcher = support / "passport-activity"
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    manifest = {"version": 1, "prepared_only": True, "support_dir": str(support),
                "state_dir": str(state_dir), "files": [], "configs": []}
    artifacts = [("passport_activity.py", Path(__file__).read_bytes(), 0o600),
                 ("passport-activity", launcher_text(support, state_dir).encode(), 0o700),
                 ("runtime.path", (str(runtime) + "\n").encode(), 0o600)]
    for name, data, mode in artifacts:
        staged = review / name
        _atomic_bytes(staged, data, mode)
        manifest["files"].append({"staged_path": str(staged), "target_path": str(support / name),
                                  "sha256": hashlib.sha256(data).hexdigest(), "mode": mode})
    paths = {"cursor": Path(cursor_config) if cursor_config else Path.home() / ".cursor/hooks.json",
             "gemini": Path(gemini_config) if gemini_config else Path.home() / ".gemini/settings.json"}
    for provider, target in paths.items():
        if target.is_symlink():
            raise ActivityError("unsafe_config")
        try:
            with target.open("rb") as handle:
                raw = handle.read(MAX_CONFIG + 1)
        except FileNotFoundError:
            raw = None
        if raw is not None and len(raw) > MAX_CONFIG:
            raise ActivityError("oversized_config")
        try:
            existing = json.loads(raw) if raw is not None else {}
        except (UnicodeError, ValueError, RecursionError):
            raise ActivityError("invalid_config_json") from None
        merged = merge_hook_config(provider, existing, launcher)
        staged = review / ("01 Cursor hooks.json" if provider == "cursor" else "02 Gemini settings.json")
        _atomic_json(staged, merged)
        manifest["configs"].append({
            "provider": provider, "target_path": str(target.absolute()), "staged_path": str(staged),
            "original_sha256": hashlib.sha256(raw).hexdigest() if raw is not None else None,
            "backup_path": str(target.with_name(target.name + ".qingjian-backup-" + stamp)),
            "merged_sha256": hashlib.sha256(staged.read_bytes()).hexdigest(),
        })
    _atomic_json(review / "03 Install manifest.json", manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    receiver = sub.add_parser("receive")
    receiver.add_argument("--provider", required=True, choices=PROVIDERS)
    receiver.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--review-dir", type=Path, required=True)
    prepare.add_argument("--runtime", type=Path, required=True)
    prepare.add_argument("--state-dir", type=Path, default=DEFAULT_STATE)
    args = parser.parse_args(argv)
    if args.action == "receive":
        try:
            receive_payload(args.provider, read_bounded_stdin(sys.stdin.fileno()), args.state_dir)
        except Exception as error:
            code = str(error) if isinstance(error, ActivityError) else "receiver_error"
            record_error(args.state_dir, args.provider, code, utc_ms())
        # Empty JSON has no blocking, permission or follow-up fields in either
        # product. In particular, never approve an outstanding tool permission.
        print("{}")
        return 0
    try:
        result = prepare_install(args.review_dir, args.runtime, args.state_dir)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except Exception as error:
        code = str(error) if isinstance(error, ActivityError) else "prepare_failed"
        print(json.dumps({"error_code": code}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
