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

    func displayable(at date: Date) -> Bool {
        date.timeIntervalSince1970 >= observedAt && (cursorUsed != nil || otherUsed != nil)
    }
}

struct CodexQuotaSnapshot {
    struct Window: Identifiable {
        let id: Int
        let used: Double?
        let minutes: Int
        let reset: Double
        var title: String {
            if minutes > 0 && minutes % 1440 == 0 { return "\(minutes / 1440) 天窗口" }
            if minutes > 0 && minutes % 60 == 0 { return "\(minutes / 60) 小时窗口" }
            return minutes > 0 ? "\(minutes) 分钟窗口" : "额度窗口"
        }
    }
    let observedAt: Double
    let expiresAt: Double
    let windows: [Window]

    init?(_ raw: [String: Any]) {
        func number(_ key: String) -> Double? {
            guard let n = raw[key] as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID(), n.doubleValue.isFinite else { return nil }
            return n.doubleValue
        }
        guard let at = number("observed_utc_ms"), let until = number("expires_utc_ms"),
              at >= 1_704_067_200_000, at < 4_102_444_800_000, until >= at, until <= at + 180_000,
              ["official_app_server", "local_log"].contains(raw["source"] as? String ?? "") else { return nil }
        var rows: [Window] = []
        for i in 1...2 {
            let used = number("w\(i)_used_percent")
            if let used, !(0...100).contains(used) { return nil }
            if raw["w\(i)_used_percent"] != nil && !(raw["w\(i)_used_percent"] is NSNull) && used == nil { return nil }
            let minutes = number("w\(i)_duration_min") ?? 0
            let reset = number("w\(i)_reset_s") ?? 0
            guard minutes >= 0, minutes <= 5_256_000, minutes.rounded(.towardZero) == minutes,
                  reset >= 0, reset < 4_102_444_800 else { return nil }
            if used != nil || minutes > 0 || reset > 0 { rows.append(Window(id: i, used: used, minutes: Int(minutes), reset: reset)) }
        }
        observedAt = at / 1000; expiresAt = until / 1000; windows = rows
    }

    func displayable(at date: Date) -> Bool { date.timeIntervalSince1970 >= observedAt }
    func fresh(at date: Date) -> Bool {
        displayable(at: date) && date.timeIntervalSince1970 < expiresAt &&
            windows.allSatisfy { $0.reset == 0 || date.timeIntervalSince1970 < $0.reset }
    }
}
