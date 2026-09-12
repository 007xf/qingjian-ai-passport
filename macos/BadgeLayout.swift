import Foundation

struct DeviceBadgeLayout: Decodable {
    let version: Int
    let width: Double
    let height: Double
    let colors: [String: String]
    let labels: [String: String]
    let elements: [String: Element]

    struct Element: Decodable {
        let x: Double
        let y: Double
        let width: Double
        let height: Double
        let font_size: Double?
        let font_postscript: String?
        let line_height: Double?
        let baseline_from_bottom: Double?
        let align: String?
        let color: String?
        let max_lines: Int?
        let overflow: String?
        let letter_spacing: Double?
        let kerning: Bool?
    }

    static func load(from url: URL) throws -> DeviceBadgeLayout {
        let value = try JSONDecoder().decode(DeviceBadgeLayout.self, from: Data(contentsOf: url))
        let required = ["time", "battery", "avatar", "name", "title", "intro", "token_caption", "token_value", "separator"]
        let textKeys = ["time", "battery", "name", "title", "intro", "token_caption", "token_value"]
        guard value.version == 1, value.width > 0, value.height > 0,
              required.allSatisfy({ value.elements[$0] != nil }),
              ["background", "primary", "secondary", "accent", "separator", "warning"].allSatisfy({ value.colors[$0] != nil }),
              ["token_caption", "token_caption_stale", "time_unknown", "battery_unknown", "tokens_unknown"].allSatisfy({ value.labels[$0] != nil }),
              textKeys.allSatisfy({ key in
                  guard let e = value.elements[key], let size = e.font_size, let line = e.line_height, let baseline = e.baseline_from_bottom else { return false }
                  return size > 0 && line > 0 && baseline >= 0 && baseline < line && !(e.font_postscript ?? "").isEmpty
              }),
              value.elements.values.allSatisfy({ $0.x >= 0 && $0.y >= 0 && $0.width > 0 && $0.height > 0 && $0.x + $0.width <= value.width && $0.y + $0.height <= value.height }) else {
            throw CocoaError(.coderInvalidValue)
        }
        return value
    }

    static var bundled: DeviceBadgeLayout? {
        guard let url = Bundle.main.url(forResource: "badge-layout", withExtension: "json") else { return nil }
        return try? load(from: url)
    }
}
