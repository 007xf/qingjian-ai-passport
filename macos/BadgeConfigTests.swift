import Foundation

@main
struct BadgeConfigTests {
    static func main() throws {
        let status: [String: Any] = ["name": "设备名", "title": "Device", "intro": "Saved", "feature_all": false, "feature_mask": 9, "threshold1": 123456789, "threshold2": 678901234]
        var fresh = BadgeConfig()
        precondition(fresh.name == "苍崎青子" && fresh.title == "MISS BLUE" && fresh.intro == "如果你惹怒了我，我将会开启3技能")
        precondition(!fresh.feature_all && fresh.feature_mask == 147 && fresh.threshold1 == 77_777_777)
        precondition(fresh.adoptInitialDeviceConfig(status, hasSavedConfig: false, didLoadDevice: false, hasUnsavedChanges: false))
        precondition(fresh.name == "设备名" && fresh.threshold1 == 123456789 && fresh.threshold2 == 678901234)
        precondition(!fresh.feature_all && fresh.feature_mask == 1)

        for guardFlags in [(true, false, false), (false, true, false), (false, false, true)] {
            var protected = BadgeConfig()
            protected.name = "本机草稿"
            protected.threshold1 = 11; protected.threshold2 = 22
            precondition(!protected.adoptInitialDeviceConfig(status, hasSavedConfig: guardFlags.0, didLoadDevice: guardFlags.1, hasUnsavedChanges: guardFlags.2))
            precondition(protected.name == "本机草稿" && protected.threshold1 == 11 && protected.threshold2 == 22)
        }

        for pair in [(0,100), (200,100), (10,10), (10,9_007_199_254_740_992)] {
            var invalid = BadgeConfig()
            _ = invalid.adoptInitialDeviceConfig(["threshold1":pair.0,"threshold2":pair.1], hasSavedConfig: false, didLoadDevice: false, hasUnsavedChanges: false)
            precondition(invalid.threshold1 == 77_777_777 && invalid.threshold2 == 555_555_555)
        }

        let legacy = try JSONDecoder().decode(BadgeConfig.self, from: Data(#"{"name":"旧草稿","avatar_path":"/tmp/legacy.png"}"#.utf8))
        precondition(legacy.name == "旧草稿" && legacy.avatar_paths["0"] == "/tmp/legacy.png")
        precondition(legacy.threshold2 == 555_555_555)
        let roundTrip = try JSONDecoder().decode(BadgeConfig.self, from: JSONEncoder().encode(fresh))
        precondition(roundTrip.threshold1 == fresh.threshold1 && roundTrip.threshold2 == fresh.threshold2)
        precondition(roundTrip == fresh)
        var pending = roundTrip
        pending.avatar_paths["1"] = "/tmp/new-avatar.png"
        precondition(pending != roundTrip)
        pending = roundTrip; pending.threshold2 += 1
        precondition(pending != roundTrip)

        precondition(BadgeFeatureSelection.wireFeatureCount == 8)
        precondition(BadgeFeatureSelection.selectableIDs == [0, 1, 2, 4, 7])
        for mask in 0...255 {
            let normalized = BadgeFeatureSelection.normalized(mask)
            precondition(normalized & 0x68 == 0)
            precondition(normalized & 0x97 == mask & 0x97)
        }
        let previous = try JSONDecoder().decode(BadgeConfig.self, from: Data(#"{"name":"保留姓名","title":"保留头衔","intro":"保留介绍","feature_all":false,"feature_mask":255,"avatar_paths":{"2":"/tmp/avatar.png"},"threshold1":11,"threshold2":22}"#.utf8))
        precondition(previous.feature_mask == 151 && !previous.feature_all)
        precondition(previous.name == "保留姓名" && previous.title == "保留头衔" && previous.intro == "保留介绍")
        precondition(previous.avatar_paths["2"] == "/tmp/avatar.png" && previous.threshold1 == 11 && previous.threshold2 == 22)
        var removedOnly = BadgeConfig(); removedOnly.feature_all = false; removedOnly.feature_mask = 0x68
        let encoded = try JSONDecoder().decode(BadgeConfig.self, from: JSONEncoder().encode(removedOnly))
        precondition(encoded.feature_mask == 0 && !encoded.feature_all)
        print("Badge configuration regression: PASS (10 existing cases + all 256 feature masks + preference preservation)")
    }
}
