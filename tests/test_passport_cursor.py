import base64
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import ssl
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import passport_cursor as cursor


NOW = 1789232000000
START, RESET = NOW - 10_000_000, NOW + 10_000_000


def token(subject="account-one"):
    payload = base64.urlsafe_b64encode(json.dumps({"sub": subject}).encode()).decode().rstrip("=")
    return "eyJhbGciOiJSUzI1NiJ9." + payload + ".testsignature"


def usage(**changes):
    return {"billingCycleStart": str(START), "billingCycleEnd": str(RESET),
            "planUsage": {"autoPercentUsed": 96.6225, "apiPercentUsed": 72.98181818181818}, **changes}


PLAN = {"planInfo": {"planName": "Pro+", "billingCycleEnd": str(RESET)}}


class CursorQuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.state = self.home / "Bridge"
        self.db = self.home / "Library/Application Support/Cursor/User/globalStorage/state.vscdb"
        self.db.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("CREATE TABLE ItemTable(key TEXT PRIMARY KEY,value TEXT)")
            db.commit()
        self.sign_in()

    def sign_in(self, subject="account-one", raw=None):
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("INSERT OR REPLACE INTO ItemTable VALUES (?,?)",
                       ("cursorAuth/accessToken", token(subject) if raw is None else raw))
            db.commit()

    def collect(self, **kwargs):
        return cursor.collect_cursor_quota(self.state, home=self.home, now_ms=kwargs.pop("now_ms", NOW), **kwargs)

    def fresh(self):
        with mock.patch.object(cursor, "_request", side_effect=[usage(), PLAN]):
            return self.collect()

    def test_real_response_shape_keeps_used_percent_not_remaining(self):
        result = self.fresh()
        self.assertEqual(result["cursor_used_percent"], 96.6225)
        self.assertEqual(result["other_used_percent"], 72.98181818181818)
        self.assertEqual(result["plan"], "Pro+")
        self.assertEqual((result["cycle_start_ms"], result["reset_at_ms"]), (START, RESET))
        self.assertEqual(result["expires_at_ms"], NOW + cursor.TTL_MS)
        self.assertEqual(result["source"], "cursor_app_api")
        self.assertFalse(result["source_supported"])

    def test_fresh_cache_makes_no_network_call(self):
        self.fresh()
        with mock.patch.object(cursor, "_request") as request:
            result = self.collect(now_ms=NOW + 1000)
        request.assert_not_called()
        self.assertEqual(result["status"], "cached")
        self.assertEqual(result["observed_at_ms"], NOW)

    def test_cache_expiry_requests_both_read_methods(self):
        self.fresh()
        with mock.patch.object(cursor, "_request", side_effect=[usage(), PLAN]) as request:
            result = self.collect(now_ms=NOW + cursor.TTL_MS)
        self.assertEqual([call.args[0] for call in request.call_args_list],
                         ["GetCurrentPeriodUsage", "GetPlanInfo"])
        self.assertEqual(result["observed_at_ms"], NOW + cursor.TTL_MS)

    def test_failed_refresh_does_not_extend_cached_time(self):
        before = self.fresh()
        with mock.patch.object(cursor, "_request", side_effect=cursor.QuotaError("network_unavailable")):
            result = self.collect(now_ms=NOW + 1000, refresh=True)
        self.assertTrue(result["available"])
        self.assertEqual(result["error_code"], "network_unavailable")
        self.assertEqual(result["observed_at_ms"], before["observed_at_ms"])
        self.assertEqual(result["expires_at_ms"], before["expires_at_ms"])

    def test_stale_cache_does_not_become_fresh_on_failure(self):
        self.fresh()
        with mock.patch.object(cursor, "_request", side_effect=cursor.QuotaError("network_unavailable")):
            result = self.collect(now_ms=NOW + cursor.TTL_MS + 1)
        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["observed_at_ms"], NOW)

    def test_new_account_never_gets_previous_account_cache(self):
        self.fresh()
        self.sign_in("account-two")
        with mock.patch.object(cursor, "_request", side_effect=cursor.QuotaError("network_unavailable")):
            result = self.collect()
        self.assertIsNone(result["cursor_used_percent"])
        self.assertEqual(result["observed_at_ms"], 0)

    def test_sign_out_never_returns_previous_quota(self):
        self.fresh()
        self.sign_in(raw="")
        with mock.patch.object(cursor, "_request") as request:
            result = self.collect()
        request.assert_not_called()
        self.assertEqual(result["error_code"], "not_signed_in")
        self.assertIsNone(result["cursor_used_percent"])

    def test_account_switch_while_loading_discards_response(self):
        def request(method, credential):
            if method == "GetPlanInfo":
                self.sign_in("account-two")
                return PLAN
            return usage()
        with mock.patch.object(cursor, "_request", side_effect=request):
            result = self.collect()
        self.assertEqual(result["error_code"], "account_changed")
        self.assertFalse((self.state / cursor.CACHE_NAME).exists())

    def test_rejected_auth_does_not_resurrect_cache(self):
        self.fresh()
        with mock.patch.object(cursor, "_request", side_effect=cursor.QuotaError("sign_in_required")):
            result = self.collect(refresh=True)
        self.assertIsNone(result["cursor_used_percent"])

    def test_account_switch_during_failed_refresh_does_not_return_old_cache(self):
        self.fresh()
        def request(*args):
            self.sign_in("account-two")
            raise cursor.QuotaError("network_unavailable")
        with mock.patch.object(cursor, "_request", side_effect=request):
            result = self.collect(refresh=True)
        self.assertEqual(result["error_code"], "account_changed")
        self.assertIsNone(result["cursor_used_percent"])

    def test_cache_and_return_never_include_credential_or_subject(self):
        result = self.fresh()
        path = self.state / cursor.CACHE_NAME
        content = path.read_text() + json.dumps(result)
        for value in (token(), "account-one", "Authorization", "accessToken"):
            self.assertNotIn(value, content)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_missing_bucket_is_unknown_and_zero_is_real_zero(self):
        with mock.patch.object(cursor, "_request", side_effect=[usage(planUsage={"autoPercentUsed": 0}), PLAN]):
            result = self.collect()
        self.assertEqual(result["cursor_used_percent"], 0)
        self.assertIsNone(result["other_used_percent"])

    def test_invalid_percentages_cannot_enter_snapshot(self):
        for value in (True, -1, float("nan"), float("inf"), "73"):
            with self.subTest(value=value):
                with mock.patch.object(cursor, "_request", side_effect=[usage(planUsage={"autoPercentUsed": value}), PLAN]):
                    result = self.collect()
                self.assertFalse(result["available"])
                self.assertEqual(result["error_code"], "invalid_response")

    def test_overspend_clamps_like_cursor_app(self):
        result = cursor._normalize(usage(planUsage={"autoPercentUsed": 106.5}), PLAN, NOW)
        self.assertEqual(result["cursor_used_percent"], 100)

    def test_invalid_or_different_billing_cycle_is_rejected(self):
        for data, plan in ((usage(billingCycleEnd=NOW), PLAN),
                           (usage(billingCycleStart=NOW + 1), PLAN),
                           (usage(), {"planInfo": {"billingCycleEnd": str(RESET + 1)}})):
            with mock.patch.object(cursor, "_request", side_effect=[data, plan]):
                self.assertFalse(self.collect()["available"])

    def test_partial_plan_failure_does_not_discard_real_usage(self):
        with mock.patch.object(cursor, "_request", side_effect=[usage(), cursor.QuotaError("service_unavailable")]):
            result = self.collect()
        self.assertEqual(result["cursor_used_percent"], 96.6225)
        self.assertEqual(result["plan"], "")

    def test_future_cache_timestamp_not_trusted(self):
        self.fresh()
        with mock.patch.object(cursor, "_request", side_effect=cursor.QuotaError("network_unavailable")):
            result = self.collect(now_ms=NOW - 1)
        self.assertEqual(result["observed_at_ms"], 0)

    def test_symlink_cache_never_overwrites_target(self):
        self.state.mkdir()
        target = self.home / "untouched"
        target.write_text("keep")
        (self.state / cursor.CACHE_NAME).symlink_to(target)
        result = self.fresh()
        self.assertEqual(target.read_text(), "keep")
        self.assertEqual(result["error_code"], "cache_unwritable")

    def test_symlink_auth_source_is_not_opened(self):
        target = self.db.with_suffix(".original")
        self.db.rename(target)
        self.db.symlink_to(target)
        self.assertEqual(self.collect()["error_code"], "unsafe_auth_source")

    def test_invalid_auth_never_reaches_network(self):
        self.sign_in(raw="sensitive-invalid-token")
        with mock.patch.object(cursor, "_request") as request:
            result = self.collect()
        request.assert_not_called()
        self.assertEqual(result["error_code"], "invalid_auth")
        self.assertNotIn("sensitive", json.dumps(result))


class CursorTransportTests(unittest.TestCase):
    def request(self, status=200, payload=b"{}"):
        response = mock.Mock(status=status)
        response.read.return_value = payload
        connection = mock.Mock()
        connection.getresponse.return_value = response
        return connection

    def test_only_known_host_and_exact_read_method_with_tls(self):
        connection = self.request()
        with mock.patch.object(cursor.http.client, "HTTPSConnection", return_value=connection) as factory:
            self.assertEqual(cursor._request("GetPlanInfo", "in-memory-secret"), {})
        self.assertEqual(factory.call_args.args, ("api2.cursor.sh",))
        context = factory.call_args.kwargs["context"]
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)
        args = connection.request.call_args
        self.assertEqual(args.args, ("POST", "/aiserver.v1.DashboardService/GetPlanInfo"))
        self.assertEqual(args.kwargs["body"], b"{}")
        connection.close.assert_called_once()

    def test_mutation_and_arbitrary_url_never_make_request(self):
        with mock.patch.object(cursor.http.client, "HTTPSConnection") as factory:
            for method in ("SetHardLimit", "https://other.invalid", "UseSandBankedReset"):
                with self.assertRaisesRegex(cursor.QuotaError, "unsupported_method"):
                    cursor._request(method, "secret")
        factory.assert_not_called()

    def test_redirect_and_upstream_error_body_are_never_followed_or_exposed(self):
        for status, code in ((302, "redirect_rejected"), (401, "sign_in_required"),
                             (429, "rate_limited"), (500, "service_unavailable")):
            connection = self.request(status, b"secret sensitive upstream body")
            with mock.patch.object(cursor.http.client, "HTTPSConnection", return_value=connection):
                with self.assertRaisesRegex(cursor.QuotaError, "^" + code + "$"):
                    cursor._request("GetPlanInfo", "secret")
            connection.getresponse.return_value.read.assert_not_called()
            self.assertEqual(connection.request.call_count, 1)

    def test_nonfinite_duplicate_and_oversized_response_are_rejected(self):
        for payload in (b'{"usage":NaN}', b'{"usage":1,"usage":2}', b'[]', b'x' * (cursor.MAX_RESPONSE + 1)):
            with mock.patch.object(cursor.http.client, "HTTPSConnection", return_value=self.request(payload=payload)):
                with self.assertRaises(cursor.QuotaError):
                    cursor._request("GetPlanInfo", "secret")

    def test_network_exception_text_is_sanitized(self):
        with mock.patch.object(cursor.http.client, "HTTPSConnection", side_effect=OSError("credential-secret")):
            with self.assertRaisesRegex(cursor.QuotaError, "^network_unavailable$"):
                cursor._request("GetPlanInfo", "secret")


if __name__ == "__main__":
    unittest.main()
