import Foundation

@main struct CursorQuotaTests {
    static func main() {
        let at = 1_789_232_000_000.0
        let raw: [String: Any] = ["observed_at_ms": at, "expires_at_ms": at + 300_000,
            "reset_at_ms": at + 600_000, "source": "cursor_app_api", "plan": "Pro+",
            "cursor_used_percent": 96.6225, "other_used_percent": 72.98181818181818]
        let value = CursorQuotaSnapshot(raw)!
        precondition(value.cursorUsed == 96.6225 && value.otherUsed == 72.98181818181818)
        precondition(value.fresh(at: Date(timeIntervalSince1970: at / 1000)))
        precondition(!value.fresh(at: Date(timeIntervalSince1970: at / 1000 - 1)))
        precondition(!value.fresh(at: Date(timeIntervalSince1970: at / 1000 + 300)))
        for (key, invalid) in [("cursor_used_percent", true as Any), ("cursor_used_percent", Double.nan),
                               ("other_used_percent", 101.0), ("other_used_percent", -1.0),
                               ("source", "invented"), ("expires_at_ms", at + 300_001), ("observed_at_ms", false)] {
            var bad = raw; bad[key] = invalid
            precondition(CursorQuotaSnapshot(bad) == nil)
        }
        var unknown = raw
        unknown["cursor_used_percent"] = NSNull(); unknown["other_used_percent"] = NSNull()
        precondition(!CursorQuotaSnapshot(unknown)!.fresh(at: Date(timeIntervalSince1970: at / 1000)))
        var reset = raw; reset["reset_at_ms"] = at + 1000
        precondition(!CursorQuotaSnapshot(reset)!.fresh(at: Date(timeIntervalSince1970: at / 1000 + 1)))
        print("Cursor quota display: PASS (source identity, exact used percentages, TTL/reset expiry, unknown/invalid numbers)")
    }
}
