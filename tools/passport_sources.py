"""Read-only source diagnostics and transactional opt-in hook installation.

No device connection, model request, authentication probe, or application launch
is performed here. Provider targets are fixed beneath the supplied home; no
manifest or hook payload can choose a write target. Existing unrelated hooks and
settings are retained. Backups contain exact original bytes and remain private.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import time

import passport_activity as activity


MAX_FILE = activity.MAX_CONFIG
PROVIDERS = ("cursor", "gemini")
SUPPORT_NAMES = ("passport_activity.py", "passport-activity", "runtime.path")
BACKUP_DIR = "09 setup backups"
LOCK_TIMEOUT = 0.5


class SourceError(Exception):
    """Only fixed, user-safe codes may cross this boundary."""


@dataclass(frozen=True)
class Snapshot:
    data: bytes
    mode: int
    device: int
    inode: int
    modified_ns: int


@dataclass
class Change:
    path: Path
    original: Snapshot | None
    data: bytes
    mode: int
    label: str
    provider: str | None = None
    installed: Snapshot | None = None


def _path(value):
    try:
        text = os.fspath(value)
    except TypeError:
        raise SourceError("invalid_path") from None
    if not isinstance(text, str) or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise SourceError("invalid_path")
    return Path(os.path.abspath(text))


def _paths(state_dir, runtime_path, home):
    base = _path(Path.home() if home is None else home)
    state = _path(state_dir)
    runtime = _path(runtime_path)
    if state == base or base not in state.parents:
        raise SourceError("state_outside_home")
    if any(state == base / p or base / p in state.parents for p in (".cursor", ".gemini")):
        raise SourceError("unsafe_state_directory")
    targets = {"cursor": base / ".cursor/hooks.json", "gemini": base / ".gemini/settings.json"}
    return base, state, runtime, targets


def _directory_ok(fd):
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise SourceError("unsafe_directory")


@contextmanager
def _directory(path, home, create=False):
    """Walk beneath a trusted home using directory descriptors, never symlinks."""
    try:
        relative = path.relative_to(home)
    except ValueError:
        raise SourceError("path_outside_home") from None
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = None
    try:
        fd = os.open(home, flags)
        _directory_ok(fd)
        for part in relative.parts:
            try:
                next_fd = os.open(part, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
                next_fd = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
            _directory_ok(fd)
        yield fd
    except OSError as error:
        if isinstance(error, FileNotFoundError):
            raise
        raise SourceError("unsafe_or_unavailable_directory") from None
    finally:
        if fd is not None:
            os.close(fd)


def _snapshot_at(fd, name, limit=MAX_FILE):
    try:
        file_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return None
    except OSError:
        raise SourceError("unsafe_or_unavailable_file") from None
    try:
        info = os.fstat(file_fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_nlink != 1 or info.st_mode & 0o022):
            raise SourceError("unsafe_file")
        if info.st_size > limit:
            raise SourceError("oversized_file")
        with os.fdopen(file_fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        final = os.fstat(file_fd)
        if len(data) > limit:
            raise SourceError("oversized_file")
        if info.st_size != final.st_size or info.st_mtime_ns != final.st_mtime_ns:
            raise SourceError("concurrent_change")
        return Snapshot(data, stat.S_IMODE(info.st_mode), info.st_dev, info.st_ino, info.st_mtime_ns)
    finally:
        os.close(file_fd)


def _snapshot(path, home, limit=MAX_FILE):
    try:
        with _directory(path.parent, home) as fd:
            return _snapshot_at(fd, path.name, limit)
    except FileNotFoundError:
        return None


def _json(data):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise SourceError("duplicate_config_key")
            result[key] = value
        return result

    def invalid_constant(_):
        raise SourceError("invalid_config_json")

    try:
        return json.loads(data, object_pairs_hook=unique, parse_constant=invalid_constant)
    except (ValueError, UnicodeError, RecursionError):
        raise SourceError("invalid_config_json") from None


def _merged(provider, original, launcher):
    obj = _json(original.data) if original else {}
    if not isinstance(obj, dict):
        raise SourceError("invalid_config")
    if provider == "cursor" and "version" in obj and (type(obj["version"]) is not int or obj["version"] != 1):
        raise SourceError("unsupported_hooks_version")
    # Explicit opt-outs must not be silently reversed by connecting Qingjian.
    hooks_config = obj.get("hooksConfig", {})
    if provider == "gemini" and isinstance(hooks_config, dict) and hooks_config.get("enabled") is False:
        raise SourceError("hooks_disabled")
    try:
        merged = activity.merge_hook_config(provider, obj, launcher)
    except activity.ActivityError as error:
        raise SourceError(str(error)) from None
    except RecursionError:
        raise SourceError("invalid_config_json") from None
    # Do not silently accept a modified observer that could block the provider
    # or filter out required events. Preserve it and request an explicit repair.
    expected = activity.merge_hook_config(provider, {}, launcher)["hooks"]
    for event, template in expected.items():
        entries = merged["hooks"][event]
        if provider == "cursor":
            wanted = template[0]
            matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("command") == wanted["command"]]
            if matches != [wanted]:
                raise SourceError("owned_hook_modified")
        else:
            wanted = template[0]["hooks"][0]
            matches = [(group, child) for group in entries if isinstance(group, dict) and isinstance(group.get("hooks"), list)
                       for child in group["hooks"] if isinstance(child, dict) and child.get("command") == wanted["command"]]
            if (len(matches) != 1 or matches[0][1] != wanted
                    or matches[0][0].get("matcher") != template[0].get("matcher")):
                raise SourceError("owned_hook_modified")
    return obj, merged


def _runtime_valid(runtime):
    # Python framework/venv executables may legitimately be symlinks. They are
    # read-only inputs, unlike every configuration/support target we write.
    try:
        info = runtime.stat()
        return stat.S_ISREG(info.st_mode) and not info.st_mode & 0o022 and os.access(runtime, os.X_OK)
    except OSError:
        return False


def _support(state, runtime):
    root = state / activity.HOOK_DIR
    return {
        "passport_activity.py": (Path(activity.__file__).read_bytes(), 0o600),
        "passport-activity": (activity.launcher_text(root, state).encode(), 0o700),
        "runtime.path": ((str(runtime) + "\n").encode(), 0o600),
    }


def _event_status(state, provider, home, now):
    result = {"events_observed": False, "event_freshness": "none", "activity_state": "unknown",
              "last_event_at_ms": 0}
    records, invalid = [], False
    try:
        with _directory(state / activity.ACTIVITY_DIR, home) as fd:
            names = [name for name in os.listdir(fd) if name.startswith(provider + "-") and name.endswith(".json")]
            invalid = len(names) > activity.MAX_RECORDS
            for name in names[:activity.MAX_RECORDS]:
                try:
                    if not activity.RECORD_RE.fullmatch(name):
                        raise SourceError("invalid_activity")
                    saved = _snapshot_at(fd, name, activity.MAX_FILE)
                    obj = _json(saved.data) if saved else None
                    if (not activity._valid_record(obj) or obj["provider"] != provider
                            or obj["state_at_ms"] > now
                            or name != f"{provider}-{obj['session_id_hash']}.json"):
                        raise SourceError("invalid_activity")
                    records.append(obj)
                except SourceError:
                    invalid = True
    except FileNotFoundError:
        return result
    except SourceError:
        invalid = True
    if records:
        result["events_observed"] = True
        result["last_event_at_ms"] = max(r["state_at_ms"] for r in records)
        fresh = [r for r in records if now < r["state_until_ms"]]
        result["event_freshness"] = "fresh" if fresh else "expired"
        positive = [r for r in fresh if r["state"] != "idle"]
        if positive:
            priority = {"working": 1, "error": 2, "waiting": 3}
            result["activity_state"] = max(positive, key=lambda r: priority[r["state"]])["state"]
        elif fresh and not invalid and not any(r["state"] in ("working", "waiting") and now >= r["state_until_ms"] for r in records):
            result["activity_state"] = "idle"
    if invalid:
        result["event_freshness"] = "invalid"
        result["activity_state"] = "unknown"
    return result


def _app_installed(names, home):
    roots = [home / "Applications"]
    if home == _path(Path.home()):
        roots.append(Path("/Applications"))
    return any((root / name).is_dir() for root in roots for name in names)


def _cli_available(provider, home):
    names = ("cursor-agent", "agent") if provider == "cursor" else ("gemini",)
    if home == _path(Path.home()):
        if any(shutil.which(name) for name in names):
            return True
    roots = [home / ".local/bin", home / ".npm-global/bin"]
    if home == _path(Path.home()):
        roots.extend((Path("/opt/homebrew/bin"), Path("/usr/local/bin")))
    if provider == "cursor":
        roots.append(home / ".cursor/bin")
    return any(_runtime_valid(root / name) for root in roots for name in names)


def source_status(state_dir, runtime_path, home=None):
    """Return setup, integrity, observation, and freshness as separate facts."""
    now = time.time_ns() // 1_000_000
    try:
        base, state, runtime, targets = _paths(state_dir, runtime_path, home)
        desired = _support(state, runtime)
    except (SourceError, OSError) as error:
        return {"ok": False, "error_code": str(error) if isinstance(error, SourceError) else "source_status_failed"}
    integrity, support_error = {}, None
    for name, (data, mode) in desired.items():
        try:
            saved = _snapshot(state / activity.HOOK_DIR / name, base)
            integrity[name] = saved is not None and saved.data == data and saved.mode == mode
        except SourceError as error:
            integrity[name] = False
            support_error = str(error)
    integrity["runtime.path"] = integrity["runtime.path"] and _runtime_valid(runtime)
    sources = []
    for provider in PROVIDERS:
        row = {
            "provider": provider, "label": "Cursor" if provider == "cursor" else "Gemini CLI",
            "connection_kind": "desktop_and_cli_hooks" if provider == "cursor" else "cli_hooks",
            "supported": True, "can_configure": True, "configured": False, "config_enabled": True,
            "receiver_intact": integrity["passport_activity.py"], "launcher_intact": integrity["passport-activity"],
            "runtime_intact": integrity["runtime.path"],
            "app_installed": _app_installed(("Cursor.app",) if provider == "cursor" else ("Gemini.app",), base),
            "cli_available": _cli_available(provider, base), "desktop_supported": provider == "cursor",
            "desktop_linked": None if provider == "cursor" else False,
            "quota_supported": False, "auth_status": "unknown", "error_code": support_error,
            **_event_status(state, provider, base, now),
        }
        try:
            saved = _snapshot(targets[provider], base)
            old, merged = _merged(provider, saved, state / activity.HOOK_DIR / "passport-activity")
            row["configured"] = saved is not None and old == merged
        except SourceError as error:
            row["error_code"] = str(error)
            row["config_enabled"] = str(error) != "hooks_disabled"
        if row["error_code"]:
            row["status"] = "error"
        elif not row["configured"]:
            row["status"] = "not_configured"
        elif not all(integrity.values()):
            row["status"] = "needs_repair"
        elif row["event_freshness"] == "invalid":
            row["status"], row["error_code"] = "error", "invalid_activity"
        else:
            row["status"] = {"none": "waiting_for_events", "fresh": "live", "expired": "expired"}[row["event_freshness"]]
        sources.append(row)
    return {"ok": True, "checked_at_ms": now, "sources": sources}


def _plan(base, state, runtime, targets, providers):
    if not _runtime_valid(runtime):
        raise SourceError("invalid_runtime")
    changes = []
    launcher = state / activity.HOOK_DIR / "passport-activity"
    # Validate every selected provider before preparing even the shared files.
    for provider in providers:
        target = targets[provider]
        old = _snapshot(target, base)
        obj, merged = _merged(provider, old, launcher)
        if obj != merged:
            data = (json.dumps(merged, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode()
            if len(data) > MAX_FILE:
                raise SourceError("oversized_config")
            changes.append(Change(target, old, data, old.mode if old else 0o600, provider, provider))
    # Shared files precede settings at commit time, so hooks never point to a
    # partly staged receiver. Any changed original, including runtime, is backed up.
    support_changes = []
    for name, (data, mode) in _support(state, runtime).items():
        target = state / activity.HOOK_DIR / name
        old = _snapshot(target, base)
        if old is None or old.data != data or old.mode != mode:
            support_changes.append(Change(target, old, data, mode, name))
    return support_changes + changes


def _sync_directory(fd):
    try:
        os.fsync(fd)
    except OSError:
        pass  # Some filesystems do not implement directory fsync.


def _replace(change, base, expected=None):
    """Compare exact original bytes/identity immediately before replacement."""
    expected = change.original if expected is None else expected
    with _directory(change.path.parent, base, create=True) as fd:
        name = ".qingjian-" + secrets.token_hex(12)
        temporary = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd)
        try:
            os.fchmod(temporary, change.mode)
            with os.fdopen(temporary, "wb", closefd=False) as stream:
                stream.write(change.data)
                stream.flush()
                os.fsync(temporary)
            current = _snapshot_at(fd, change.path.name)
            if current != expected:
                raise SourceError("concurrent_change")
            info = os.fstat(temporary)
            os.replace(name, change.path.name, src_dir_fd=fd, dst_dir_fd=fd)
            change.installed = Snapshot(change.data, stat.S_IMODE(info.st_mode), info.st_dev,
                                        info.st_ino, info.st_mtime_ns)
            _sync_directory(fd)
            readback = _snapshot_at(fd, change.path.name)
            if readback is None or readback.data != change.data or readback.mode != change.mode:
                raise SourceError("write_verification_failed")
            return readback
        finally:
            os.close(temporary)
            try:
                os.unlink(name, dir_fd=fd)
            except FileNotFoundError:
                pass


@contextmanager
def _setup_lock(state, base):
    with _directory(state, base, create=True) as directory:
        fd = os.open(".sources-setup.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1 or info.st_mode & 0o077:
                raise SourceError("unsafe_lock")
            deadline = time.monotonic() + LOCK_TIMEOUT
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise SourceError("setup_busy") from None
                    time.sleep(0.01)
            yield
        finally:
            os.close(fd)


def _backup(changes, state, base):
    existing = [change for change in changes if change.original is not None]
    if not existing:
        return 0
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + "-" + secrets.token_hex(6)
    root = state / activity.HOOK_DIR / BACKUP_DIR / stamp
    for index, change in enumerate(existing, 1):
        target = root / f"{index:02d} {change.label}.original"
        _replace(Change(target, None, change.original.data, 0o600, "backup"), base)
    return len(existing)


def _rollback(applied, base):
    """Restore only files still equal to this transaction's verified write."""
    complete = True
    for change in reversed(applied):
        if change.installed is None:
            continue  # Preparation failed before replacing this target.
        try:
            if change.original is not None:
                restore = Change(change.path, change.installed, change.original.data, change.original.mode, change.label)
                _replace(restore, base)
            else:
                with _directory(change.path.parent, base) as fd:
                    if _snapshot_at(fd, change.path.name) != change.installed:
                        raise SourceError("concurrent_change")
                    os.unlink(change.path.name, dir_fd=fd)
                    _sync_directory(fd)
        except (SourceError, OSError):
            complete = False  # Never overwrite a user's edit made during rollback.
    return complete


def configure_sources(state_dir, runtime_path, providers=None, home=None):
    """Install official observer hooks with private backups and guarded rollback."""
    changed = {"providers": [], "support_files": [], "backup_count": 0}
    applied = []
    try:
        if providers is None:
            providers = list(PROVIDERS)
        if not isinstance(providers, (list, tuple)) or not providers or any(p not in PROVIDERS for p in providers):
            raise SourceError("unsupported_provider")
        providers = list(dict.fromkeys(providers))
        base, state, runtime, targets = _paths(state_dir, runtime_path, home)
        _plan(base, state, runtime, targets, providers)  # Reject invalid inputs with no writes.
        with _setup_lock(state, base):
            plan = _plan(base, state, runtime, targets, providers)
            # All backups finish before any live target is changed.
            changed["backup_count"] = _backup(plan, state, base)
            # Catch edits made while making backups, before the first live write.
            if any(_snapshot(change.path, base) != change.original for change in plan):
                raise SourceError("concurrent_change")
            try:
                for change in plan:
                    applied.append(change)
                    change.installed = _replace(change, base)
            except (SourceError, OSError):
                if not _rollback(applied, base):
                    raise SourceError("rollback_incomplete") from None
                raise
            changed["providers"] = [change.provider for change in plan if change.provider]
            changed["support_files"] = [change.label for change in plan if not change.provider]
        result = source_status(state, runtime, base)
        result["changed"] = changed
        return result
    except (SourceError, OSError) as error:
        return {"ok": False, "error_code": str(error) if isinstance(error, SourceError) else "source_setup_failed",
                "changed": {"providers": [], "support_files": [], "backup_count": changed["backup_count"]}}
