import Foundation
import CoreFoundation

struct ClaudeUsageSnapshot {
    struct Window: Identifiable {
        let id: String
        let used: Double
        let resetAt: TimeInterval
        var title: String { id == "five_hour" ? "5 小时额度" : "7 天额度" }
    }
    let todayTokens: Int64?
    let lifetimeTokens: Int64?
    let tokensObservedAt: TimeInterval?
    let tokensExpiresAt: TimeInterval?
    let tokensAvailable: Bool
    let tokensIndexPending: Bool
    let quotaObservedAt: TimeInterval?
    let quotaExpiresAt: TimeInterval?
    let quotaAvailable: Bool
    let windows: [Window]
    let errorCode: String?

    init(_ raw: [String: Any]) {
        func flag(_ value: Any?) -> Bool {
            guard let value = value as? NSNumber, CFGetTypeID(value) == CFBooleanGetTypeID() else { return false }
            return value.boolValue
        }
        func number(_ value: Any?) -> Double? {
            guard let value = value as? NSNumber, CFGetTypeID(value) != CFBooleanGetTypeID(), value.doubleValue.isFinite else { return nil }
            return value.doubleValue
        }
        func count(_ value: Any?) -> Int64? {
            guard let value = number(value), value >= 0, value <= 9_007_199_254_740_991, value.rounded(.towardZero) == value else { return nil }
            return Int64(value)
        }
        func timeframe(_ value: [String: Any], ttl: Double) -> (Double, Double)? {
            guard let at = number(value["observed_at_ms"]), let until = number(value["expires_at_ms"]),
                  at.rounded(.towardZero) == at, until.rounded(.towardZero) == until,
                  at >= 1_704_067_200_000, at < 4_102_444_800_000, until > at, until <= at + ttl else { return nil }
            return (at / 1000, until / 1000)
        }
        let tokens = raw["token_usage"] as? [String: Any] ?? [:]
        let tokenTime = timeframe(tokens, ttl: 180_000)
        let today = count(tokens["today_tokens"]), lifetime = count(tokens["lifetime_tokens"])
        let complete = tokens["source"] as? String == "claude_code_local" && flag(tokens["complete"]) && tokenTime != nil && today != nil && lifetime != nil && today! <= lifetime!
        todayTokens = complete ? today : nil; lifetimeTokens = complete ? lifetime : nil
        tokensObservedAt = complete ? tokenTime?.0 : nil; tokensExpiresAt = complete ? tokenTime?.1 : nil
        tokensAvailable = complete && flag(tokens["available"])
        let coverage = tokens["coverage"] as? [String: Any] ?? [:]
        tokensIndexPending = (count(coverage["pending_files"]) ?? 0) > 0
        let quota = raw["quota"] as? [String: Any] ?? [:]
        let quotaTime = quota["source"] as? String == "claude_code_statusline" ? timeframe(quota, ttl: 180_000) : nil
        var rows: [Window] = []
        if let quotaTime {
            for prefix in ["five_hour", "seven_day"] {
                if let used = number(quota[prefix + "_used_percent"]), (0...100).contains(used),
                   let reset = number(quota[prefix + "_reset_at_ms"]), reset.rounded(.towardZero) == reset, reset / 1000 > quotaTime.0, reset < 4_102_444_800_000 {
                    rows.append(Window(id: prefix, used: used, resetAt: reset / 1000))
                }
            }
        }
        windows = rows
        quotaObservedAt = rows.isEmpty ? nil : quotaTime?.0; quotaExpiresAt = rows.isEmpty ? nil : quotaTime?.1
        quotaAvailable = !rows.isEmpty && flag(quota["available"])
        errorCode = quota["error_code"] as? String
    }

    func tokensFresh(at date: Date) -> Bool {
        guard tokensAvailable, let at = tokensObservedAt, let until = tokensExpiresAt else { return false }
        return date.timeIntervalSince1970 >= at && date.timeIntervalSince1970 < until
    }

    func quotaFresh(_ window: Window, at date: Date) -> Bool {
        guard quotaAvailable, let at = quotaObservedAt, let until = quotaExpiresAt else { return false }
        return date.timeIntervalSince1970 >= at && date.timeIntervalSince1970 < min(until, window.resetAt)
    }
}
