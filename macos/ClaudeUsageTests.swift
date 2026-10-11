import Foundation

@main
struct ClaudeUsageTests {
    static func main() throws {
        let at = 1_800_000_000_000.0
        let token: [String: Any] = ["source": "claude_code_local", "available": true, "complete": true,
                                  "today_tokens": 0, "lifetime_tokens": 123, "observed_at_ms": at, "expires_at_ms": at + 180_000]
        let quota: [String: Any] = ["source": "claude_code_statusline", "available": true,
                                  "observed_at_ms": at, "expires_at_ms": at + 180_000,
                                  "five_hour_used_percent": 0, "five_hour_reset_at_ms": at + 90_000,
                                  "seven_day_used_percent": 40.5, "seven_day_reset_at_ms": at + 86400_000]
        let usage = ClaudeUsageSnapshot(["token_usage": token, "quota": quota])
        precondition(usage.todayTokens == 0 && usage.lifetimeTokens == 123 && usage.windows.count == 2)
        let now = Date(timeIntervalSince1970: at / 1000)
        precondition(usage.tokensFresh(at: now) && usage.quotaFresh(usage.windows[0], at: now))
        precondition(!usage.quotaFresh(usage.windows[0], at: now.addingTimeInterval(91)))
        precondition(usage.quotaFresh(usage.windows[1], at: now.addingTimeInterval(91)))
        precondition(!usage.tokensFresh(at: now.addingTimeInterval(180)))
        precondition(!usage.tokensFresh(at: now.addingTimeInterval(-1)))
        var stale = token; stale["available"] = false
        let retained = ClaudeUsageSnapshot(["token_usage": stale])
        precondition(retained.lifetimeTokens == 123 && retained.tokensObservedAt == at / 1000 && !retained.tokensFresh(at: now))
        var bad = token; bad["today_tokens"] = true
        precondition(ClaudeUsageSnapshot(["token_usage": bad]).todayTokens == nil)
        bad["today_tokens"] = 124
        precondition(ClaudeUsageSnapshot(["token_usage": bad]).todayTokens == nil)
        bad = token; bad["complete"] = NSNumber(value: 1)
        precondition(ClaudeUsageSnapshot(["token_usage": bad]).todayTokens == nil)
        bad = token; bad["available"] = NSNumber(value: 1)
        precondition(!ClaudeUsageSnapshot(["token_usage": bad]).tokensFresh(at: now))
        var wrong = quota; wrong["source"] = "context_window"
        precondition(ClaudeUsageSnapshot(["quota": wrong]).windows.isEmpty)
        let old = try JSONDecoder().decode(BadgeConfig.self, from: Data("{}".utf8))
        precondition(!old.claude_enabled && old.feature_mask == 147)
        var enabled = old; enabled.claude_enabled = true
        let saved = try JSONDecoder().decode(BadgeConfig.self, from: JSONEncoder().encode(enabled))
        precondition(saved.claude_enabled && saved.feature_mask == 147)
        var adopted = old
        _ = adopted.adoptInitialDeviceConfig(["claude_enabled": NSNumber(value: 1)], hasSavedConfig: false, didLoadDevice: false, hasUnsavedChanges: false)
        precondition(!adopted.claude_enabled)
        print("Claude usage display/config: PASS (real zero, unknown, independent expiry, last reading, rollback, invalid counts, old drafts)")
    }
}
