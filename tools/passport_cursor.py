"""Read-only Cursor account quota, using the installed client's private API.

Verified against Cursor 3.19.13: DashboardService.GetCurrentPeriodUsage supplies
autoPercentUsed (Cursor models) and apiPercentUsed (other models), both USED
percentages. GetPlanInfo supplies the plan name. This is not a public supported
integration API. No model request, authentication refresh, or application edit
is made. Only a normalized quota cache and a one-way account fingerprint persist;
the existing access credential remains in memory and goes only to api2.cursor.sh.
"""
from __future__ import annotations

import base64
from contextlib import closing
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import ssl
import stat
import tempfile
import time

TTL_MS = 300_000
CACHE_NAME = "10 cursor quota.json"
SOURCE = "cursor_app_api"
HOST = "api2.cursor.sh"
METHODS = frozenset(("GetCurrentPeriodUsage", "GetPlanInfo"))
MAX_RESPONSE = 65_536
MIN_MS, MAX_MS = 1704067200000, 4102444800000


class QuotaError(Exception):
    """Contains a fixed non-sensitive error code, never upstream error text."""


def _empty(code=None):
    return {"available": False, "status": "unavailable", "source": SOURCE,
            "source_supported": False, "observed_at_ms": 0, "expires_at_ms": 0,
            "plan": "", "cycle_start_ms": 0, "reset_at_ms": 0,
            "cursor_used_percent": None, "other_used_percent": None,
            "error_code": code}


def _json(raw):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result
    try:
        return json.loads(raw, object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
    except (ValueError, UnicodeError, RecursionError):
        raise QuotaError("invalid_response") from None


def _credential(home):
    path = home / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise QuotaError("unsafe_auth_source")
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            row = db.execute("SELECT value FROM ItemTable WHERE key=?",
                             ("cursorAuth/accessToken",)).fetchone()
        token = row[0] if row else None
    except FileNotFoundError:
        raise QuotaError("cursor_not_found") from None
    except (OSError, sqlite3.Error):
        raise QuotaError("auth_unreadable") from None
    if not token:
        raise QuotaError("not_signed_in")
    if not isinstance(token, str) or not 16 <= len(token) <= 8192 or \
            re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token) is None:
        raise QuotaError("invalid_auth")
    try:
        payload = token.split(".")[1]
        claims = _json(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        subject = claims.get("sub") if isinstance(claims, dict) else None
        if not isinstance(subject, str) or not 1 <= len(subject) <= 512:
            raise ValueError("subject")
    except (ValueError, QuotaError):
        raise QuotaError("invalid_auth") from None
    # Used solely to prevent another signed-in account inheriting the old cache.
    account = hashlib.sha256(("qingjian-cursor-quota-v1\0" + subject).encode()).hexdigest()
    return token, account


def _tls_context():
    context = ssl.create_default_context()
    # Self-contained Python distributions may have no default CA bundle. Keep
    # verification enabled and add the macOS trust bundle when available.
    if Path("/etc/ssl/cert.pem").is_file():
        context.load_verify_locations(cafile="/etc/ssl/cert.pem")
    return context


def _request(method, token):
    if method not in METHODS:
        raise QuotaError("unsupported_method")
    connection = None
    try:
        connection = http.client.HTTPSConnection(HOST, context=_tls_context(), timeout=12)
        connection.request("POST", "/aiserver.v1.DashboardService/" + method, body=b"{}",
                           headers={"Authorization": "Bearer " + token,
                                    "Content-Type": "application/json",
                                    "Connect-Protocol-Version": "1"})
        response = connection.getresponse()
        if response.status in (401, 403):
            raise QuotaError("sign_in_required")
        if response.status == 429:
            raise QuotaError("rate_limited")
        if 300 <= response.status < 400:
            # http.client never follows redirects; credentials cannot follow one.
            raise QuotaError("redirect_rejected")
        if response.status != 200:
            raise QuotaError("service_unavailable")
        raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise QuotaError("response_too_large")
        result = _json(raw)
        if not isinstance(result, dict):
            raise QuotaError("invalid_response")
        return result
    except ssl.SSLError:
        raise QuotaError("tls_failed") from None
    except (OSError, http.client.HTTPException):
        raise QuotaError("network_unavailable") from None
    finally:
        if connection is not None:
            connection.close()


def _timestamp(value):
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 13:
        value = int(value)
    if type(value) is not int or not MIN_MS <= value < MAX_MS:
        raise QuotaError("invalid_response")
    return value


def _percentage(value):
    if value is None:
        return None
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise QuotaError("invalid_response")
    # The Cursor UI clamps overspend to 100; these are USED, not remaining.
    return min(float(value), 100.0)


def _plan_name(value):
    return value if isinstance(value, str) and len(value) <= 23 and \
        all(32 <= ord(c) <= 126 for c in value) else ""


def _normalize(usage, plan, now):
    if not isinstance(usage, dict) or not isinstance(usage.get("planUsage"), dict):
        raise QuotaError("quota_unavailable")
    start = _timestamp(usage.get("billingCycleStart"))
    end = _timestamp(usage.get("billingCycleEnd"))
    if not start <= now < end:
        raise QuotaError("invalid_billing_cycle")
    buckets = usage["planUsage"]
    info = plan.get("planInfo", {}) if isinstance(plan, dict) else {}
    if not isinstance(info, dict):
        info = {}
    # Do not attach a plan returned for a different billing period.
    if info.get("billingCycleEnd") is not None and _timestamp(info["billingCycleEnd"]) != end:
        raise QuotaError("billing_cycle_changed")
    return {**_empty(), "available": True, "status": "fresh", "observed_at_ms": now,
            "expires_at_ms": min(now + TTL_MS, end), "plan": _plan_name(info.get("planName")),
            "cycle_start_ms": start, "reset_at_ms": end,
            "cursor_used_percent": _percentage(buckets.get("autoPercentUsed")),
            "other_used_percent": _percentage(buckets.get("apiPercentUsed"))}


def _cache_read(state, account, now):
    path = state / CACHE_NAME
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or \
                info.st_mode & 0o077 or info.st_size > MAX_RESPONSE:
            return None
        flags = os.O_RDONLY | os.O_NOFOLLOW
        with os.fdopen(os.open(path, flags), "rb") as stream:
            data = _json(stream.read(MAX_RESPONSE + 1))
        if not isinstance(data, dict) or data.get("account") != account:
            return None
        value = data["snapshot"]
        observed = _timestamp(value["observed_at_ms"])
        expires = _timestamp(value["expires_at_ms"])
        start, end = _timestamp(value["cycle_start_ms"]), _timestamp(value["reset_at_ms"])
        if not start <= observed <= now or not observed < expires <= observed + TTL_MS or expires > end:
            return None
        return {**_empty(), "available": expires > now, "status": "cached" if expires > now else "stale",
                "observed_at_ms": observed, "expires_at_ms": expires,
                "cycle_start_ms": start, "reset_at_ms": end, "plan": _plan_name(value.get("plan")),
                "cursor_used_percent": _percentage(value.get("cursor_used_percent")),
                "other_used_percent": _percentage(value.get("other_used_percent"))}
    except (OSError, KeyError, TypeError, QuotaError):
        return None


def _cache_write(state, account, snapshot):
    temp = None
    try:
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = state.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise OSError("unsafe state")
        path = state / CACHE_NAME
        if path.is_symlink():
            raise OSError("unsafe cache")
        fd, temp = tempfile.mkstemp(prefix=".cursor-quota-", dir=state)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "account": account, "snapshot": snapshot}, stream,
                      ensure_ascii=True, allow_nan=False, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
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


def collect_cursor_quota(state_dir, home=None, now_ms=None, refresh=False):
    """Return a credential-free quota snapshot; refresh failure never renews TTL.

    A fresh private cache avoids requests for five minutes. Every call checks the
    current local account before using that cache, including sign-out/account
    change. On expiry/explicit refresh only the two fixed read-only methods run.
    """
    now = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    try:
        now = _timestamp(now)
        base = Path.home() if home is None else Path(home).expanduser().absolute()
        state = Path(state_dir).expanduser().absolute()
        token, account = _credential(base)
    except QuotaError as error:
        return _empty(str(error))
    cached = _cache_read(state, account, now)
    if cached and cached["available"] and not refresh:
        return cached
    try:
        usage = _request("GetCurrentPeriodUsage", token)
        plan_error = None
        try:
            plan = _request("GetPlanInfo", token)
        except QuotaError as error:
            if str(error) == "sign_in_required":
                raise
            plan, plan_error = {}, str(error)
        snapshot = _normalize(usage, plan, now)
        # A sign-out/account switch during HTTP requests must not expose the
        # previous account's response to the newly active account.
        _, current_account = _credential(base)
        if current_account != account:
            return _empty("account_changed")
        snapshot["error_code"] = plan_error
        if not _cache_write(state, account, snapshot):
            snapshot["error_code"] = "cache_unwritable"
        return snapshot
    except QuotaError as error:
        code = str(error)
        if code in ("sign_in_required", "not_signed_in", "cursor_not_found", "invalid_auth", "auth_unreadable"):
            return _empty(code)
        # A failed request can overlap an account switch just like a successful
        # one. Recheck before falling back to the previous cached response.
        try:
            _, current_account = _credential(base)
        except QuotaError as auth_error:
            return _empty(str(auth_error))
        if current_account != account:
            return _empty("account_changed")
        return {**cached, "error_code": code} if cached else _empty(code)
