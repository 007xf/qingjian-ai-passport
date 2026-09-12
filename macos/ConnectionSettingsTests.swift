import Foundation

@main struct ConnectionSettingsTests {
    static func verify(_ value: Bool) { precondition(value) }
    static func main() throws {
        let uuid = "D3F038C0-E398-4F98-92EF-458A67A84EE2"
        let usb = ConnectionSettings()
        precondition(usb.canConnect)
        try verify(usb.bridgeArguments(for: ["sync"]) == ["sync"])
        var ble = ConnectionSettings(transport: .bluetooth)
        precondition(!ble.canConnect)
        do { _ = try ble.bridgeArguments(for: ["upload"]); preconditionFailure("Unselected BLE must fail closed") }
        catch is ConnectionError { }
        ble.bluetoothIdentifier = uuid.lowercased(); ble.bluetoothName = "我的工牌"
        precondition(ble.canConnect)
        for action in ["status", "sync", "upload", "screen"] {
            try verify(ble.bridgeArguments(for: [action]) == ["--ble-id", uuid, action])
        }
        try verify(ble.bridgeArguments(for: ["ble-scan"]) == ["ble-scan"])
        let unpaired = ConnectionSettings(transport: .bluetooth)
        try verify(unpaired.bridgeArguments(for: ["sources-status"]) == ["sources-status"])
        try verify(unpaired.bridgeArguments(for: ["sources-configure", "--provider", "cursor"]) == ["sources-configure", "--provider", "cursor"])
        try verify(ble.bridgeArguments(for: ["reset-cycle", "--reason", "manual_reset_card"]) == ["reset-cycle", "--reason", "manual_reset_card"])
        try verify(usb.bridgeArguments(for: ["pair"]) == ["pair"])
        do { _ = try ble.bridgeArguments(for: ["pair"]); preconditionFailure("Pair must remain USB-only") }
        catch is ConnectionError { }
        let encoded = try JSONEncoder().encode(ble)
        let decoded = try JSONDecoder().decode(ConnectionSettings.self, from: encoded)
        precondition(decoded == ble)
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("qingjian-connection-test-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let badge = directory.appendingPathComponent("badge.json")
        let draft = Data(#"{"name":"保留的草稿","threshold1":11,"threshold2":22}"#.utf8)
        try draft.write(to: badge)
        try ble.save(to: directory.appendingPathComponent("connection.json"))
        try verify(Data(contentsOf: badge) == draft)
        let restored = try JSONDecoder().decode(ConnectionSettings.self, from: Data(contentsOf: directory.appendingPathComponent("connection.json")))
        precondition(restored == ble)
        let devices = BluetoothDevice.discovered(from: ["devices": [
            ["identifier": uuid.lowercased(), "name": "我的工牌", "rssi": -50],
            ["identifier": uuid, "name": "重复"],
            ["identifier": "bad", "name": "无效"],
            ["identifier": "D3F038C0-E398-4F98-92EF-458A67A84EE3", "name": ""]
        ]])
        precondition(devices.count == 2)
        precondition(devices.contains { $0.identifier == uuid && $0.rssi == -50 })
        precondition(devices.contains { $0.name == "青笺工牌" })
        precondition(ble == decoded, "Scanning must never change the explicit selection")
        ble.bluetoothIdentifier = "not-a-uuid"; precondition(!ble.canConnect)
        do { _ = try ble.bridgeArguments(for: ["sync"]); preconditionFailure("Invalid BLE must not fall back to USB") }
        catch is ConnectionError { }
        ble.bluetoothIdentifier = "00000000-0000-0000-0000-000000000000"; precondition(!ble.canConnect)
        print("Connection settings: PASS (transport routing, explicit selection, invalid IDs, USB-only pair, persistence, discovery normalization)")
    }
}
