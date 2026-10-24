// Rebuild the pilot Telegram banner from the approved transparent emblem.
// Usage: swift scripts/render_welcome_banner.swift <emblem.png> <output.png>
import AppKit

guard CommandLine.arguments.count == 3,
      let emblem = NSImage(contentsOfFile: CommandLine.arguments[1]) else { exit(2) }
let width = 1200, height = 628
guard let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: width,
    pixelsHigh: height, bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true,
    isPlanar: false, colorSpaceName: .deviceRGB, bytesPerRow: 0,
    bitsPerPixel: 0), let context = NSGraphicsContext(bitmapImageRep: bitmap) else { exit(3) }
NSGraphicsContext.saveGraphicsState()
NSGraphicsContext.current = context
let rect = NSRect(x: 0, y: 0, width: width, height: height)
NSGradient(starting: NSColor(calibratedRed: 0.015, green: 0.035, blue: 0.09, alpha: 1),
           ending: NSColor(calibratedRed: 0.025, green: 0.105, blue: 0.23, alpha: 1))!
    .draw(in: rect, angle: 0)
let edge = NSBezierPath(roundedRect: rect.insetBy(dx: 22, dy: 22), xRadius: 28, yRadius: 28)
NSColor(calibratedRed: 0.19, green: 0.58, blue: 1, alpha: 0.25).setStroke()
edge.lineWidth = 2
edge.stroke()
let orbit = NSBezierPath(ovalIn: NSRect(x: 604, y: 80, width: 500, height: 470))
NSColor(calibratedRed: 0.18, green: 0.61, blue: 1, alpha: 0.23).setStroke()
orbit.lineWidth = 2
orbit.stroke()
emblem.draw(in: NSRect(x: 630, y: 78, width: 448, height: 448),
            from: .zero, operation: .sourceOver, fraction: 1)
func label(_ text: String, x: CGFloat, y: CGFloat, size: CGFloat, color: NSColor,
           weight: NSFont.Weight = .regular) {
    let attributes: [NSAttributedString.Key: Any] = [
        .font: NSFont.systemFont(ofSize: size, weight: weight), .foregroundColor: color]
    (text as NSString).draw(at: NSPoint(x: x, y: y), withAttributes: attributes)
}
let white = NSColor(calibratedRed: 0.94, green: 0.98, blue: 1, alpha: 1)
let blue = NSColor(calibratedRed: 0.31, green: 0.79, blue: 1, alpha: 1)
label("KudaVPN", x: 84, y: 360, size: 72, color: white, weight: .bold)
label("PREMIUM", x: 88, y: 314, size: 27, color: blue, weight: .semibold)
label("Свобода быть на связи.", x: 88, y: 222, size: 32, color: white, weight: .medium)
label("Один доступ. Твои устройства.", x: 88, y: 165, size: 21, color: blue)
NSGraphicsContext.restoreGraphicsState()
guard let data = bitmap.representation(using: .png, properties: [:]) else { exit(4) }
try data.write(to: URL(fileURLWithPath: CommandLine.arguments[2]))
