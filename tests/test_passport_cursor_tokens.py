"""Actual monthly token accounting, whole-range completeness and stale safety."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import passport_cursor_tokens as tokens

NOW = 1789232000000
START, RESET = NOW - 10_000_000, NOW + 10_000_000


def aggregate():
    return {"totalInputTokens": "120", "totalOutputTokens": "17", "totalCacheWriteTokens": "5",
            "totalCacheReadTokens": "1212", "aggregations": [
                {"modelIntent": "PRIVATE_MODEL", "inputTokens": "100", "outputTokens": "10", "cacheWriteTokens": "5", "cacheReadTokens": "1000"},
                {"modelIntent": "PRIVATE_MODEL", "tier": 1, "inputTokens": "20", "outputTokens": "7", "cacheReadTokens": "212"}]}


class CursorTokenTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.quota = {"cycle_start_ms": START, "reset_at_ms": RESET, "expires_at_ms": NOW + 300000}
        self.auth = mock.patch.object(tokens.quota_api, "_credential", return_value=("PRIVATE_CREDENTIAL", "a" * 64)).start()
        self.addCleanup(mock.patch.stopall)

    def collect(self, **kwargs):
        return tokens.collect_cursor_tokens(self.root, quota=kwargs.pop("quota", self.quota), home=self.root,
                                            now_ms=kwargs.pop("now_ms", NOW), **kwargs)

    def prime(self):
        with mock.patch.object(tokens, "_request", return_value=aggregate()):
            return self.collect()

    def test_four_disjoint_token_categories_sum_once(self):
        result = self.prime()
        self.assertEqual(result["cycle_tokens"], 1354)
        self.assertEqual(result["input_tokens"], 120)
        self.assertEqual(result["cache_read_tokens"], 1212)
        self.assertTrue(result["complete"])
        self.assertEqual(result["aggregation_groups"], 2)

    def test_request_covers_exact_month_start_to_observation_not_recent_page(self):
        with mock.patch.object(tokens, "_request", return_value=aggregate()) as request:
            result = self.collect()
        request.assert_called_once_with("PRIVATE_CREDENTIAL", START, NOW)
        self.assertEqual(result["observed_at_ms"], NOW)
        self.assertEqual(result["reset_at_ms"], RESET)

    def test_cache_avoids_request_and_never_advances_source_time(self):
        self.prime()
        with mock.patch.object(tokens, "_request") as request:
            result = self.collect(now_ms=NOW + 1000)
        request.assert_not_called()
        self.assertEqual(result["status"], "cached")
        self.assertEqual(result["observed_at_ms"], NOW)

    def test_failed_refresh_retains_counters_and_original_time_as_stale(self):
        old = self.prime()
        with mock.patch.object(tokens, "_request", side_effect=tokens.quota_api.QuotaError("network_unavailable")):
            result = self.collect(now_ms=NOW + 1000, refresh=True)
        self.assertEqual(result["cycle_tokens"], old["cycle_tokens"])
        self.assertFalse(result["available"])
        self.assertEqual(result["status"], "stale")
        self.assertEqual(result["observed_at_ms"], old["observed_at_ms"])
        self.assertEqual(result["expires_at_ms"], old["expires_at_ms"])

    def test_changed_month_cannot_reuse_previous_month_even_when_cache_recent(self):
        self.prime()
        changed = {**self.quota, "cycle_start_ms": NOW - 1}
        with mock.patch.object(tokens, "_request", return_value={}) as request:
            result = self.collect(quota=changed)
        self.assertEqual(result["cycle_tokens"], 0)
        request.assert_called_once_with("PRIVATE_CREDENTIAL", NOW - 1, NOW)

    def test_new_month_failed_fetch_keeps_previous_period_label_and_stale_status(self):
        self.prime()
        changed = {**self.quota, "cycle_start_ms": NOW - 1}
        with mock.patch.object(tokens, "_request", side_effect=tokens.quota_api.QuotaError("network_unavailable")):
            result = self.collect(quota=changed)
        self.assertEqual(result["cycle_start_ms"], START)
        self.assertFalse(result["available"])

    def test_account_switch_never_reuses_old_month(self):
        self.prime()
        self.auth.return_value = ("PRIVATE_CREDENTIAL_2", "b" * 64)
        with mock.patch.object(tokens, "_request", side_effect=tokens.quota_api.QuotaError("network_unavailable")):
            result = self.collect()
        self.assertIsNone(result["cycle_tokens"])

    def test_account_switch_during_http_discards_response(self):
        def request(*args):
            self.auth.return_value = ("PRIVATE_CREDENTIAL_2", "b" * 64)
            return aggregate()
        with mock.patch.object(tokens, "_request", side_effect=request):
            result = self.collect()
        self.assertEqual(result["error_code"], "account_changed")

    def test_partial_group_totals_and_invalid_counters_never_publish(self):
        bad = aggregate(); bad["totalInputTokens"] = "121"
        cases = [bad]
        for value in (None, True, -1, "12.3", float("nan"), 2**53):
            data = aggregate(); data["totalInputTokens"] = value; cases.append(data)
        for data in cases:
            with self.subTest(data=data), mock.patch.object(tokens, "_request", return_value=data):
                result = self.collect(refresh=True)
                self.assertIsNone(result["cycle_tokens"])

    def test_unexpected_pagination_response_is_not_treated_as_complete_total(self):
        data = {**aggregate(), "nextPageToken": "continuation"}
        with mock.patch.object(tokens, "_request", return_value=data):
            result = self.collect()
        self.assertEqual(result["error_code"], "unsupported_token_schema")

    def test_confirmed_proto3_zero_response_is_complete_zero(self):
        with mock.patch.object(tokens, "_request", return_value={}):
            result = self.collect()
        self.assertEqual(result["cycle_tokens"], 0)
        self.assertTrue(result["complete"])

    def test_cache_excludes_models_credentials_and_raw_aggregates(self):
        result = self.prime()
        path = self.root / tokens.CACHE_NAME
        serialized = path.read_text() + json.dumps(result)
        for value in ("PRIVATE_CREDENTIAL", "PRIVATE_MODEL", "modelIntent", "aggregations"):
            self.assertNotIn(value, serialized)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_unavailable_cycle_or_network_is_unknown_not_zero(self):
        result = self.collect(quota={})
        self.assertIsNone(result["cycle_tokens"])
        with mock.patch.object(tokens, "_request", side_effect=tokens.quota_api.QuotaError("network_unavailable")):
            self.assertIsNone(self.collect()["cycle_tokens"])

    def test_auth_error_never_exposes_cached_last_count(self):
        self.prime()
        result = self.collect(quota={"error_code": "sign_in_required"})
        self.assertIsNone(result["cycle_tokens"])

    def test_transport_uses_fixed_verified_host_and_single_whole_range_request(self):
        response = mock.Mock(status=200)
        response.read.return_value = b"{}"
        connection = mock.Mock(); connection.getresponse.return_value = response
        with mock.patch.object(tokens.http.client, "HTTPSConnection", return_value=connection) as factory:
            tokens._request("PRIVATE_CREDENTIAL", START, NOW)
        self.assertEqual(factory.call_args.args[0], "api2.cursor.sh")
        call = connection.request.call_args
        self.assertEqual(call.args[:2], ("POST", "/aiserver.v1.DashboardService/GetAggregatedUsageEvents"))
        self.assertEqual(json.loads(call.kwargs["body"]), {"teamId": 0, "startDate": str(START), "endDate": str(NOW)})
        self.assertTrue(factory.call_args.kwargs["context"].check_hostname)

    def test_redirect_never_receives_followup_or_exposes_upstream_body(self):
        response = mock.Mock(status=302)
        connection = mock.Mock(); connection.getresponse.return_value = response
        with mock.patch.object(tokens.http.client, "HTTPSConnection", return_value=connection):
            with self.assertRaisesRegex(tokens.quota_api.QuotaError, "^redirect_rejected$"):
                tokens._request("PRIVATE_CREDENTIAL", START, NOW)
        response.read.assert_not_called()
        self.assertEqual(connection.request.call_count, 1)


if __name__ == "__main__":
    unittest.main()
