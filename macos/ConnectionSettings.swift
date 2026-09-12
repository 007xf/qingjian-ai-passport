import Foundation

enum ConnectionTransport: String, Codable, CaseIterable, Identifiable {
    case usb, bluetooth
    var id: String { rawValue }
    var title: String { self == .usb ? "USB" : "蓝牙" }
}

struct ConnectionSettings: Codable, Equatable {
    var transport: ConnectionTransport = .usb
    var bluetoothIdentifier: String? = nil
    var bluetoothName: String? = nil

    var validBluetoothIdentifier: String? {
        guard let value = bluetoothIdentifier.flatMap(UUID.init(uuidString:))?.uuidString,
              value != "00000000-0000-0000-0000-000000000000" else { return nil }
        return value
    }

    var canConnect: Bool { transport == .usb || validBluetoothIdentifier != nil }

    // Discovery and local bookkeeping never acquire a device. Pairing always
    // uses USB; a Bluetooth choice is never silently replaced with USB.
    func bridgeArguments(for command: [String]) throws -> [String] {
        guard let action = command.first else { throw ConnectionError("缺少设备操作。") }
        if ["ble-scan", "reset-cycle", "tokens", "preview-avatar", "sources-status", "sources-configure", "cursor-usage"].contains(action) { return command }
        if action == "pair" {
            guard transport == .usb else { throw ConnectionError("开启配对需要先通过 USB 连接工牌。") }
            return command
        }
        guard transport == .bluetooth else { return command }
        guard let identifier = validBluetoothIdentifier else { throw ConnectionError("请先搜索并选择一块蓝牙工牌。") }
        return ["--ble-id", identifier] + command
    }

    func save(to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        try encoder.encode(self).write(to: url, options: .atomic)
    }
}

struct ConnectionError: LocalizedError {
    let message: String
    init(_ message: String) { self.message = message }
    var errorDescription: String? { message }
}

struct BluetoothDevice: Identifiable, Equatable {
    let identifier: String
    let name: String
    let rssi: Int?
    var id: String { identifier }

    static func discovered(from reply: [String: Any]) -> [BluetoothDevice] {
        var seen = Set<String>()
        return (reply["devices"] as? [[String: Any]] ?? []).compactMap { entry in
            guard let raw = entry["identifier"] as? String, let uuid = UUID(uuidString: raw)?.uuidString,
                  uuid != "00000000-0000-0000-0000-000000000000",
                  seen.insert(uuid).inserted else { return nil }
            let name = (entry["name"] as? String)?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            return BluetoothDevice(identifier: uuid, name: name.isEmpty ? "青笺工牌" : name, rssi: entry["rssi"] as? Int)
        }.sorted { a, b in a.name == b.name ? a.identifier < b.identifier : a.name.localizedStandardCompare(b.name) == .orderedAscending }
    }
}
