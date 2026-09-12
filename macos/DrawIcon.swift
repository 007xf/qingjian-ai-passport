import AppKit

let root = URL(fileURLWithPath: CommandLine.arguments[1], isDirectory: true)
let iconset = root.appendingPathComponent("QingJian.iconset", isDirectory: true)
try FileManager.default.createDirectory(at: iconset, withIntermediateDirectories: true)

func render(_ size: Int) -> Data {
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: size, pixelsHigh: size, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    let context = NSGraphicsContext(bitmapImageRep: bitmap)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = context
    context.cgContext.scaleBy(x: CGFloat(size) / 1024, y: CGFloat(size) / 1024)
    let outer = NSBezierPath(roundedRect: NSRect(x: 66, y: 66, width: 892, height: 892), xRadius: 195, yRadius: 195)
    NSGradient(colors: [NSColor(red: 0.11, green: 0.29, blue: 0.57, alpha: 1), NSColor(red: 0.34, green: 0.64, blue: 0.91, alpha: 1)])!.draw(in: outer, angle: 90)
    NSGraphicsContext.saveGraphicsState()
    let shadow = NSShadow(); shadow.shadowColor = NSColor.black.withAlphaComponent(0.20); shadow.shadowBlurRadius = 35; shadow.shadowOffset = NSSize(width: 0, height: -15); shadow.set()
    let badge = NSBezierPath(roundedRect: NSRect(x: 252, y: 175, width: 520, height: 654), xRadius: 65, yRadius: 65)
    NSColor.white.setFill(); badge.fill()
    NSGraphicsContext.restoreGraphicsState()
    NSColor(red: 0.81, green: 0.89, blue: 0.97, alpha: 1).setFill()
    NSBezierPath(roundedRect: NSRect(x: 395, y: 745, width: 234, height: 30), xRadius: 15, yRadius: 15).fill()
    NSColor(red: 0.14, green: 0.39, blue: 0.72, alpha: 1).setFill()
    let star = NSBezierPath()
    for i in 0..<10 {
        let angle = Double.pi / 2 + Double(i) * Double.pi / 5
        let radius = i.isMultiple(of: 2) ? 140.0 : 63.0
        let point = NSPoint(x: 512 + cos(angle) * radius, y: 522 + sin(angle) * radius)
        if i == 0 { star.move(to: point) } else { star.line(to: point) }
    }
    star.close(); star.fill()
    NSColor(red: 0.68, green: 0.79, blue: 0.91, alpha: 1).setFill()
    NSBezierPath(roundedRect: NSRect(x: 355, y: 296, width: 314, height: 24), xRadius: 12, yRadius: 12).fill()
    NSColor(red: 0.84, green: 0.90, blue: 0.96, alpha: 1).setFill()
    NSBezierPath(roundedRect: NSRect(x: 407, y: 244, width: 210, height: 17), xRadius: 8.5, yRadius: 8.5).fill()
    NSGraphicsContext.restoreGraphicsState()
    return bitmap.representation(using: .png, properties: [:])!
}

for points in [16, 32, 128, 256, 512] {
    for scale in [1, 2] {
        let suffix = scale == 2 ? "@2x" : ""
        try render(points * scale).write(to: iconset.appendingPathComponent("icon_\(points)x\(points)\(suffix).png"))
    }
}
try render(1024).write(to: root.appendingPathComponent("02 青笺图标.png"))

func big32(_ value: UInt32) -> Data {
    var encoded = value.bigEndian
    return withUnsafeBytes(of: &encoded) { Data($0) }
}
var chunks = Data()
for (type, size) in [("icp4",16),("icp5",32),("icp6",64),("ic07",128),("ic08",256),("ic09",512),("ic10",1024)] {
    let png = render(size)
    chunks.append(type.data(using: .ascii)!)
    chunks.append(big32(UInt32(png.count + 8)))
    chunks.append(png)
}
var container = "icns".data(using: .ascii)!
container.append(big32(UInt32(chunks.count + 8)))
container.append(chunks)
try container.write(to: root.appendingPathComponent("QingJian.icns"))
