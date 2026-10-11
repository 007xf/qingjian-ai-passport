"""Claude subscription quota from the documented Claude Code statusLine JSON.

Sources: https://code.claude.com/docs/en/statusline and /en/cli-reference.
No private OAuth endpoint, credential file, Keychain read, or model request.
Only selected numeric windows and anonymous profile/session/account scopes are
stored. `claude auth status` confirms the current CLI identity without exposing
its JSON on a terminal. It cannot prove that an already-running session used
that identity, so account_unverified remains true even when the current CLI
identity matches the cached scope. Unsupported identity metadata degrades to
a last reading; sign-out and a known identity change discard the cached view.
Repeated identical statusLine windows retain their first capture time: a UI
rerender must not make an old quota observation fresh again.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import time

TTL_MS = 180_000
MIN_MS, MAX_MS = 1704067200000, 4102444800000
MAX_JSON = 65_536
CACHE_NAME = "15 Claude Quota.json"
SOURCE = "claude_code_statusline"
WINDOWS = {"five_hour": 300, "seven_day": 10080}


class QuotaError(Exception):
    """A fixed code; never include CLI output, paths, or identity metadata."""


def _empty(code=None):
    return {"available": False, "status": "unavailable", "source": SOURCE,
            "observed_at_ms": 0, "expires_at_ms": 0, "profile_scope": None,
            "session_scope": None, "account_scope": None, "account_verified": False,
            "account_unverified": True, "current_auth_confirmed": False,
            "five_hour_used_percent": None, "five_hour_reset_at_ms": 0,
            "seven_day_used_percent": None, "seven_day_reset_at_ms": 0,
            "windows": {}, "error_code": code}


def _json(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError("duplicate")
            value[key] = item
        return value
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
    except (ValueError, UnicodeError, RecursionError):
        raise QuotaError("invalid_source") from None


def _milliseconds(value):
    if type(value) is not int or not MIN_MS <= value < MAX_MS:
        raise QuotaError("invalid_clock")
    return value


def _profile(home=None):
    value = home if home is not None else os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude"
    try:
        path = Path(value).expanduser().resolve()
    except (OSError, ValueError, RuntimeError, TypeError):
        raise QuotaError("invalid_profile") from None
    scope = hashlib.sha256(("qingjian-claude-profile-v1\0" + str(path)).encode()).hexdigest()
    return path, scope


def _claude_executable():
    executable = shutil.which("claude")
    if executable is not None:
        return executable
    # GUI/LaunchAgent PATH does not normally include the native installer path.
    # Resolve fixed executable locations only; never invoke a login shell.
    home = Path.home()
    for path in (home / ".local/bin/claude", home / ".claude/local/claude",
                 Path("/opt/homebrew/bin/claude"), Path("/usr/local/bin/claude")):
        if path.is_file() and os.access(path, os.X_OK):
            return str(path)
    return None


def _identity(profile):
    """Runtime-only documented metadata command; no shell and no inference."""
    executable = _claude_executable()
    if executable is None:
        raise QuotaError("identity_unavailable")
    try:
        result = subprocess.run([executable, "auth", "status"], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3,
                                env={**os.environ, "CLAUDE_CONFIG_DIR": str(profile)}, check=False)
        if len(result.stdout) > MAX_JSON:
            raise QuotaError("identity_unavailable")
        value = _json(result.stdout)
    except (OSError, subprocess.SubprocessError, QuotaError):
        raise QuotaError("identity_unavailable") from None
    if not isinstance(value, dict):
        raise QuotaError("identity_unavailable")
    if value.get("loggedIn") is False or value.get("authMethod") == "none":
        raise QuotaError("not_signed_in")
    if result.returncode != 0 or value.get("loggedIn") is not True:
        raise QuotaError("identity_unavailable")
    if value.get("authMethod") != "claude.ai":
        raise QuotaError("subscription_identity_unavailable")
    directory = value.get("configDirectory")
    email, organization = value.get("email"), value.get("orgId")
    # Official @anthropic-ai/claude-code-linux-x64 2.1.296 authStatus emits
    # loggedIn/authMethod/configDirectory and nullable email/orgId for claude.ai.
    # Email identifies the account; optional organization distinguishes contexts
    # when reported. A legitimate personal account need not have an orgId.
    if organization is None:
        organization = ""
    if not isinstance(directory, str) or _profile(directory)[0] != profile or \
            not isinstance(email, str) or not 3 <= len(email) <= 512 or "@" not in email or \
            not isinstance(organization, str) or len(organization) > 256:
        raise QuotaError("identity_unavailable")
    _, scope = _profile(profile)
    # Identity strings remain only in this call; never persist or emit them.
    return hashlib.sha256(("qingjian-claude-account-v1\0" + scope + "\0" +
                           email.strip().casefold() + "\0" + organization).encode()).hexdigest()


def _window(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise QuotaError("invalid_source")
    percent, reset = value.get("used_percentage"), value.get("resets_at")
    if type(percent) not in (int, float) or not math.isfinite(percent) or not 0 <= percent <= 100 or \
            type(reset) is not int:
        raise QuotaError("invalid_source")
    return {"used_percent": float(percent), "reset_at_ms": _milliseconds(reset * 1000)}


def _snapshot(windows, now, profile_scope, session_scope, account_scope):
    expiry = min(now + TTL_MS, *(value["reset_at_ms"] for value in windows.values()))
    value = {**_empty(), "observed_at_ms": now, "expires_at_ms": expiry,
             "profile_scope": profile_scope, "session_scope": session_scope,
             "account_scope": account_scope, "windows": windows,
             "current_auth_confirmed": account_scope is not None}
    for name, window in windows.items():
        value[name + "_used_percent"] = window["used_percent"]
        value[name + "_reset_at_ms"] = window["reset_at_ms"]
    value.update(available=bool(account_scope and now < expiry),
                 status="fresh" if account_scope and now < expiry else "stale" if now >= expiry else "last_reading",
                 error_code=None if account_scope else "account_unverified")
    return value


def _cache_read(state, profile_scope, now):
    path = state / CACHE_NAME
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > MAX_JSON:
            return None
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            data = _json(stream.read(MAX_JSON + 1))
        if not isinstance(data, dict) or data.get("version") != 1 or data.get("source") != SOURCE or \
                data.get("profile_scope") != profile_scope:
            return None
        observed = _milliseconds(data.get("observed_at_ms"))
        if observed > now:
            return None
        account = data.get("account_scope")
        session = data.get("session_scope")
        if any(value is not None and (not isinstance(value, str) or len(value) != 64 or
                   any(char not in "0123456789abcdef" for char in value)) for value in (account, session)):
            return None
        raw = data.get("windows")
        if not isinstance(raw, dict) or not raw or any(name not in WINDOWS for name in raw):
            return None
        windows = {name: _window({"used_percentage": window["used_percent"],
                                 "resets_at": window["reset_at_ms"] // 1000}) for name, window in raw.items()}
        value = _snapshot(windows, observed, profile_scope, session, account)
        if value["expires_at_ms"] != data.get("expires_at_ms"):
            return None
        value.update(available=bool(account and now < value["expires_at_ms"]),
                     status="cached" if account and now < value["expires_at_ms"] else
                            "stale" if now >= value["expires_at_ms"] else "last_reading")
        return value
    except (OSError, KeyError, TypeError, QuotaError):
        return None


def _cache_write(state, snapshot):
    temp = None
    try:
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = state.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022 or \
                (state / CACHE_NAME).is_symlink():
            return False
        # Explicit allowlist prevents future caller fields entering the cache.
        saved = {key: snapshot[key] for key in ("source", "profile_scope", "session_scope", "account_scope",
                 "observed_at_ms", "expires_at_ms", "windows")}
        descriptor, temp = tempfile.mkstemp(prefix=".claude-quota-", dir=state)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, **saved}, stream, allow_nan=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, state / CACHE_NAME)
        temp = None
        return True
    except (OSError, ValueError):
        return False
    finally:
        if temp:
            try:
                os.unlink(temp)
            except OSError:
                pass


def _last_reading(cached, code):
    return {**cached, "available": False, "status": "last_reading",
            "current_auth_confirmed": False, "account_scope": None, "error_code": code} if cached else _empty(code)


def collect_claude_quota(state_dir, home=None, now_ms=None, refresh=False):
    """Read the latest capture; `home` is CLAUDE_CONFIG_DIR, not a user home.

    `refresh` never makes a model/usage request or renews a source timestamp.
    Every call checks current CLI identity before exposing a cached snapshot.
    """
    try:
        now = _milliseconds(time.time_ns() // 1_000_000 if now_ms is None else now_ms)
        profile, scope = _profile(home)
        state = Path(state_dir).expanduser().absolute()
    except (QuotaError, OSError, ValueError, TypeError) as error:
        return _empty(str(error) if isinstance(error, QuotaError) else "invalid_state")
    cached = _cache_read(state, scope, now)
    try:
        account = _identity(profile)
    except QuotaError as error:
        return _empty(str(error)) if str(error) == "not_signed_in" else _last_reading(cached, str(error))
    if cached is None:
        return _empty("capture_unavailable")
    if cached["account_scope"] is None:
        return _last_reading(cached, "account_unverified")
    if cached["account_scope"] != account:
        return _empty("account_changed")
    return cached


def capture_statusline(payload, state_dir, claude_home=None, now_ms=None):
    """Capture only documented quota windows; never infer quota from context."""
    try:
        now = _milliseconds(time.time_ns() // 1_000_000 if now_ms is None else now_ms)
        profile, scope = _profile(claude_home)
        state = Path(state_dir).expanduser().absolute()
        if not isinstance(payload, dict) or not isinstance(payload.get("rate_limits"), dict):
            raise QuotaError("quota_absent")
        windows = {name: _window(payload["rate_limits"].get(name)) for name in WINDOWS}
        windows = {name: value for name, value in windows.items() if value is not None}
        if not windows:
            raise QuotaError("quota_absent")
        session = payload.get("session_id")
        if not isinstance(session, str) or not 1 <= len(session) <= 512:
            raise QuotaError("session_unavailable")
        session_scope = hashlib.sha256(("qingjian-claude-session-v1\0" + scope + "\0" + session).encode()).hexdigest()
    except (QuotaError, OSError, ValueError, TypeError) as error:
        return _empty(str(error) if isinstance(error, QuotaError) else "invalid_state")
    try:
        account = _identity(profile)
    except QuotaError as error:
        if str(error) == "not_signed_in":
            return _empty(str(error))
        account = None
    cached = _cache_read(state, scope, now)
    snapshot = _snapshot(windows, now, scope, session_scope, account)
    if cached and cached["session_scope"] == session_scope and cached["account_scope"] != account:
        # A live session may retain its old login after the CLI identity changes.
        # Do not silently rebind its quota to the new account (or upgrade an
        # unverified prior capture). A newly started session can be captured.
        return _last_reading(cached, "session_account_unverified")
    if cached and cached["session_scope"] == session_scope and cached["account_scope"] == account and cached["windows"] == windows:
        return cached
    # A session may retain an older identity after /login. Report that limitation;
    # also recheck the active CLI scope to avoid an identity race during capture.
    if account is not None:
        try:
            if _identity(profile) != account:
                return _empty("account_changed")
        except QuotaError as error:
            return _empty(str(error))
    if not _cache_write(state, snapshot):
        snapshot["error_code"] = "cache_unwritable"
    return snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(description="Capture documented Claude Code quota without displaying identity data")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--claude-home")
    parser.add_argument("command", choices=("capture",))
    args = parser.parse_args(argv)
    try:
        from passport_providers import _MetadataJSON
        fields = {"session_id": True, "rate_limits": {name: {"used_percentage": True, "resets_at": True} for name in WINDOWS}}
        payload = _MetadataJSON(sys.stdin, MAX_JSON).document(fields)
        result = capture_statusline(payload, args.state_dir, args.claude_home)
        return 0 if result["observed_at_ms"] else 1
    except (OSError, ValueError, QuotaError):
        return 1


if __name__ == "__main__":
    sys.exit(main())
