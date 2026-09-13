"""Exact account-month token counters from Cursor's read-only usage aggregate.

The installed Cursor 3.19 client defines GetAggregatedUsageEvents with a date
range and four total token counters; unlike GetFilteredUsageEvents it has no
page parameter. Server totals cover the whole requested interval. Model-group
component sums are checked against each total, never added to the totals again.
Input excludes cache-write/read here, so all four disjoint categories are added.
Only counters, source times and an account fingerprint persist; no event bodies,
conversation IDs, models, account IDs or credentials are stored or emitted.
"""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import ssl
import stat
import tempfile
import time

import passport_cursor as quota_api

CACHE_NAME = "12 Cursor Token.json"
MAX_RESPONSE = 1_048_576
MAX_TOKENS = 2**53 - 1
SOURCE = "cursor_usage_aggregate"
COMPONENTS = {"input_tokens": "InputTokens", "output_tokens": "OutputTokens",
              "cache_write_tokens": "CacheWriteTokens", "cache_read_tokens": "CacheReadTokens"}
AUTH_ERRORS = {"not_signed_in", "sign_in_required", "cursor_not_found", "invalid_auth", "auth_unreadable", "unsafe_auth_source", "account_changed"}


def empty(code=None):
    return {"available": False, "complete": False, "status": "unavailable", "source": SOURCE,
            "cycle_tokens": None, **{key: None for key in COMPONENTS}, "cycle_start_ms": 0,
            "reset_at_ms": 0, "observed_at_ms": 0, "expires_at_ms": 0,
            "error_code": code, "aggregation_groups": 0,
            "counting": "input + output + cache write + cache read"}


def _integer(value):
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 16:
        value = int(value)
    if type(value) is not int or not 0 <= value <= MAX_TOKENS:
        raise quota_api.QuotaError("invalid_token_response")
    return value


def _request(credential, start, cutoff):
    connection = None
    try:
        connection = http.client.HTTPSConnection(quota_api.HOST, context=quota_api._tls_context(), timeout=12)
        body = json.dumps({"teamId": 0, "startDate": str(start), "endDate": str(cutoff)}, separators=(",", ":")).encode()
        connection.request("POST", "/aiserver.v1.DashboardService/GetAggregatedUsageEvents", body=body,
                           headers={"Authorization": "Bearer " + credential, "Content-Type": "application/json",
                                    "Connect-Protocol-Version": "1"})
        response = connection.getresponse()
        if response.status in (401, 403):
            raise quota_api.QuotaError("sign_in_required")
        if response.status == 429:
            raise quota_api.QuotaError("rate_limited")
        if 300 <= response.status < 400:
            raise quota_api.QuotaError("redirect_rejected")
        if response.status != 200:
            raise quota_api.QuotaError("service_unavailable")
        raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise quota_api.QuotaError("token_response_too_large")
        result = quota_api._json(raw)
        if not isinstance(result, dict):
            raise quota_api.QuotaError("invalid_token_response")
        return result
    except ssl.SSLError:
        raise quota_api.QuotaError("tls_failed") from None
    except (OSError, http.client.HTTPException):
        raise quota_api.QuotaError("network_unavailable") from None
    finally:
        if connection is not None:
            connection.close()


def _normalize(response, start, reset, observed):
    if not isinstance(response, dict):
        raise quota_api.QuotaError("invalid_token_response")
    expected = {"aggregations", "totalCostCents", "percentOfBurstUsed", "totalRequestCost"} | {"total" + v for v in COMPONENTS.values()}
    # No continuation is defined for this aggregate RPC. Do not silently trust
    # an incompatible/paginated response after a future upstream API change.
    if any(key not in expected for key in response):
        raise quota_api.QuotaError("unsupported_token_schema")
    groups = response.get("aggregations", [])
    if not isinstance(groups, list) or len(groups) > 10000 or any(not isinstance(row, dict) for row in groups):
        raise quota_api.QuotaError("invalid_token_response")
    totals = {}
    for name, wire in COMPONENTS.items():
        # Proto3 non-optional scalar counters omit real zero values. This was
        # confirmed with an empty interval: HTTP 200 returns {}. Null is invalid.
        total = _integer(response.get("total" + wire, 0))
        field = wire[0].lower() + wire[1:]
        grouped = sum(_integer(row.get(field, 0)) for row in groups)
        if total != grouped:
            raise quota_api.QuotaError("token_totals_mismatch")
        totals[name] = total
    total = _integer(sum(totals.values()))
    return {**empty(), **totals, "available": True, "complete": True, "status": "fresh",
            "cycle_tokens": total, "cycle_start_ms": start, "reset_at_ms": reset,
            "observed_at_ms": observed, "expires_at_ms": min(observed + quota_api.TTL_MS, reset),
            "aggregation_groups": len(groups)}


def _read_cache(state, account, now):
    path = state / CACHE_NAME
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 16384:
            return None
        with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
            saved = quota_api._json(stream.read(16385))
        if saved.get("account") != account:
            return None
        value = saved["snapshot"]
        start, end, at, until = [quota_api._timestamp(value[k]) for k in
                                ("cycle_start_ms", "reset_at_ms", "observed_at_ms", "expires_at_ms")]
        if not start <= at <= now or not at < until <= min(at + quota_api.TTL_MS, end):
            return None
        parts = {key: _integer(value[key]) for key in COMPONENTS}
        count = _integer(value["cycle_tokens"])
        if count != sum(parts.values()) or value.get("complete") is not True:
            return None
        return {**empty(), **parts, "complete": True, "available": now < until,
                "status": "cached" if now < until else "stale", "cycle_tokens": count,
                "cycle_start_ms": start, "reset_at_ms": end, "observed_at_ms": at,
                "expires_at_ms": until, "aggregation_groups": _integer(value.get("aggregation_groups", 0)),
                "account_scope": account}
    except (OSError, KeyError, TypeError, AttributeError, quota_api.QuotaError):
        return None


def _write_cache(state, account, snapshot):
    temporary = None
    try:
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = state.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022:
            return False
        target = state / CACHE_NAME
        if target.is_symlink():
            return False
        fd, temporary = tempfile.mkstemp(prefix=".cursor-tokens-", dir=state)
        with os.fdopen(fd, "w") as stream:
            json.dump({"version": 1, "account": account, "snapshot": snapshot}, stream,
                      separators=(",", ":"), allow_nan=False)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, target)
        temporary = None
        return True
    except OSError:
        return False
    finally:
        if temporary:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def collect_cursor_tokens(state_dir, quota=None, home=None, now_ms=None, refresh=False):
    now = time.time_ns() // 1_000_000 if now_ms is None else now_ms
    base = Path.home() if home is None else Path(home).expanduser().absolute()
    state = Path(state_dir).expanduser().absolute()
    try:
        now = quota_api._timestamp(now)
        credential, account = quota_api._credential(base)
    except quota_api.QuotaError as error:
        return empty(str(error))
    cached = _read_cache(state, account, now)
    if quota is None:
        quota = quota_api.collect_cursor_quota(state, home=base, now_ms=now, refresh=refresh)
    if not isinstance(quota, dict):
        quota = {}
    if quota.get("error_code") in AUTH_ERRORS:
        return empty(quota["error_code"])
    try:
        start, reset, expires = [quota_api._timestamp(quota.get(k)) for k in
                                  ("cycle_start_ms", "reset_at_ms", "expires_at_ms")]
        if not start <= now < min(reset, expires):
            raise quota_api.QuotaError("billing_cycle_unavailable")
        same_cycle = cached and (cached["cycle_start_ms"], cached["reset_at_ms"]) == (start, reset)
        if same_cycle and cached["available"] and not refresh:
            return cached
        response = _request(credential, start, now)
        result = _normalize(response, start, reset, now)
        if quota_api._credential(base)[1] != account:
            return empty("account_changed")
        result["account_scope"] = account
        if not _write_cache(state, account, result):
            result["error_code"] = "cache_unwritable"
        return result
    except quota_api.QuotaError as error:
        code = str(error)
        try:
            if quota_api._credential(base)[1] != account:
                return empty("account_changed")
        except quota_api.QuotaError as auth_error:
            return empty(str(auth_error))
        if code in AUTH_ERRORS:
            return empty(code)
        # Preserve old source numbers and their original observation/expiry.
        # A failed refresh is never presented as a new complete observation.
        return {**cached, "available": False, "status": "stale", "error_code": code} if cached else empty(code)
