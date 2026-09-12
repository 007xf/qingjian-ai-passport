import Foundation
import CoreFoundation

struct CursorQuotaSnapshot {
    let plan: String
    let observedAt: Double
    let expiresAt: Double
    let resetAt: Double
    let cursorUsed: Double?
    let otherUsed: Double?

    init?(_ raw: [String: Any]) {
        func number(_ key: String) -> Double? {
            guard let n = raw[key] as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(), n.doubleValue.isFinite else { return nil }
            return n.doubleValue
        }
        guard let at = number("observed_at_ms"), let until = number("expires_at_ms"), let reset = number("reset_at_ms"),
              at >= 1_704_067_200_000, at < 4_102_444_800_000, until > at, until <= at + 300_000,
              reset > at, reset < 4_102_444_800_000, raw["source"] as? String == "cursor_app_api" else { return nil }
        for key in ["cursor_used_percent", "other_used_percent"] {
            if raw[key] != nil && !(raw[key] is NSNull) {
                guard let value = number(key), (0...100).contains(value) else { return nil }
            }
        }
        plan = String((raw["plan"] as? String ?? "").prefix(23))
        observedAt = at / 1000; expiresAt = until / 1000; resetAt = reset / 1000
        cursorUsed = number("cursor_used_percent"); otherUsed = number("other_used_percent")
    }

    func fresh(at date: Date) -> Bool {
        let now = date.timeIntervalSince1970
        return now >= observedAt && now < min(expiresAt, resetAt) && (cursorUsed != nil || otherUsed != nil)
    }
}
