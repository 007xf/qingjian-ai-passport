import Foundation

func integer(_ value: Any?) -> Int64? {
    if let number = value as? NSNumber { return number.int64Value }
    if let text = value as? String { return Int64(text) }
    return nil
}


enum BadgeFeatureSelection {
    static let wireFeatureCount = 8
    // IDs 3 (Grok Bot), 5 (Coding Pet) and 6 (AI Talk) remain reserved on the wire.
    static let selectableIDs: Set<Int> = [0, 1, 2, 4, 7]
    static let selectableMask = selectableIDs.reduce(0) { $0 | (1 << $1) }
    static func normalized(_ mask: Int) -> Int {
        guard (0...255).contains(mask) else { return 0 }
        return mask & selectableMask
    }
}

struct BadgeConfig: Codable, Equatable {
    var name = "苍崎青子"
    var title = "MISS BLUE"
    var intro = "如果你惹怒了我，我将会开启3技能"
    var feature_all = false
    var feature_mask = 147
    var avatar_paths: [String: String] = [:]
    var threshold1: Int64 = 77_777_777
    var threshold2: Int64 = 555_555_555
    init() {}
    enum CodingKeys: String, CodingKey { case name, title, intro, feature_all, feature_mask, avatar_paths, avatar_path, threshold1, threshold2 }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decodeIfPresent(String.self, forKey: .name) ?? "苍崎青子"
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? "MISS BLUE"
        intro = try c.decodeIfPresent(String.self, forKey: .intro) ?? "如果你惹怒了我，我将会开启3技能"
        feature_all = try c.decodeIfPresent(Bool.self, forKey: .feature_all) ?? false
        feature_mask = BadgeFeatureSelection.normalized(try c.decodeIfPresent(Int.self, forKey: .feature_mask) ?? 147)
        avatar_paths = try c.decodeIfPresent([String: String].self, forKey: .avatar_paths) ?? [:]
        if avatar_paths.isEmpty, let legacy = try c.decodeIfPresent(String.self, forKey: .avatar_path) { avatar_paths["0"] = legacy }
        threshold1 = try c.decodeIfPresent(Int64.self, forKey: .threshold1) ?? 77_777_777
        threshold2 = try c.decodeIfPresent(Int64.self, forKey: .threshold2) ?? 555_555_555
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(name, forKey: .name); try c.encode(title, forKey: .title); try c.encode(intro, forKey: .intro)
        try c.encode(feature_all, forKey: .feature_all); try c.encode(BadgeFeatureSelection.normalized(feature_mask), forKey: .feature_mask)
        try c.encode(avatar_paths, forKey: .avatar_paths)
        try c.encode(threshold1, forKey: .threshold1); try c.encode(threshold2, forKey: .threshold2)
    }

    mutating func adoptInitialDeviceConfig(_ device: [String: Any], hasSavedConfig: Bool, didLoadDevice: Bool, hasUnsavedChanges: Bool) -> Bool {
        guard !hasSavedConfig, !didLoadDevice, !hasUnsavedChanges else { return false }
        if let value = device["name"] as? String, !value.isEmpty { name = value }
        if let value = device["title"] as? String { title = value }
        if let value = device["intro"] as? String { intro = value }
        if let value = device["feature_all"] as? Bool { feature_all = value }
        if let value = integer(device["feature_mask"]), (0...255).contains(value) { feature_mask = BadgeFeatureSelection.normalized(Int(value)) }
        if let first = integer(device["threshold1"]), let second = integer(device["threshold2"]),
           first > 0, second > first, second <= 9_007_199_254_740_991 {
            threshold1 = first
            threshold2 = second
        }
        return true
    }
}
