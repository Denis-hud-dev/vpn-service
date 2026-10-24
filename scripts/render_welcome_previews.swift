// Offline Telegram-like mockups. No live subscription data or personal chat appears.
import AppKit
let output = CommandLine.arguments[1]
let banner = NSImage(contentsOfFile: CommandLine.arguments[2])!
let blue = NSColor(calibratedRed: 0.08, green: 0.24, blue: 0.45, alpha: 1)
let navy = NSColor(calibratedRed: 0.025, green: 0.065, blue: 0.14, alpha: 1)
let cyan = NSColor(calibratedRed: 0.27, green: 0.76, blue: 1, alpha: 1)
let white = NSColor(calibratedRed: 0.94, green: 0.97, blue: 1, alpha: 1)
func rounded(_ rect: NSRect, color: NSColor, radius: CGFloat = 18) {
    color.setFill(); NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius).fill()
}
func line(_ text: String, _ x: CGFloat, _ y: CGFloat, _ size: CGFloat = 21,
          _ color: NSColor = white, _ weight: NSFont.Weight = .regular) {
    (text as NSString).draw(at: NSPoint(x: x, y: y), withAttributes: [
        .font: NSFont.systemFont(ofSize: size, weight: weight), .foregroundColor: color])
}
for mode in ["welcome", "keyboard"] {
    let w = 700, h = 1200
    let bitmap = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: w, pixelsHigh: h,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState(); NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: bitmap)
    navy.setFill(); NSRect(x: 0, y: 0, width: w, height: h).fill()
    rounded(NSRect(x: 0, y: 1110, width: w, height: 90), color: blue, radius: 0)
    line("KudaVPN Test", 36, 1138, 28, white, .semibold)
    line("Предпросмотр интерфейса · без личных данных", 28, 1074, 16, cyan)
    if mode == "welcome" {
        rounded(NSRect(x: 24, y: 294, width: 652, height: 738), color: blue)
        banner.draw(in: NSRect(x: 40, y: 675, width: 620, height: 325), from: .zero, operation: .sourceOver, fraction: 1)
        line("Добро пожаловать в KudaVPN.", 47, 635, 25, white, .bold)
        line("Свобода быть на связи.", 47, 593, 22, cyan, .semibold)
        line("Твоё пространство в интернете — без лишних", 47, 552, 19)
        line("границ и сложных настроек.", 47, 520, 19)
        line("Один доступ. Твои устройства.", 47, 470, 19)
        line("Подключение за несколько шагов.", 47, 438, 19)
        line("KudaVPN Premium — технологии для тебя", 47, 389, 17, cyan)
        rounded(NSRect(x: 43, y: 314, width: 614, height: 58), color: NSColor(calibratedRed: 0.07, green: 0.39, blue: 0.80, alpha: 1), radius: 13)
        line("🚀 Начать пользоваться", 194, 331, 22, white, .semibold)
    } else {
        rounded(NSRect(x: 25, y: 570, width: 650, height: 445), color: blue)
        line("KudaVPN Premium", 55, 953, 27, white, .bold)
        line("Свобода подключения. Простота управления.", 55, 898, 21, cyan)
        line("◆ Подписка: активна", 55, 827, 21)
        line("┃ Личный доступ и управление — здесь.", 55, 787, 20)
        rounded(NSRect(x: 48, y: 690, width: 604, height: 58), color: NSColor(calibratedRed: 0.07, green: 0.39, blue: 0.80, alpha: 1), radius: 12)
        line("🚀 Подключить VPN", 229, 707, 21)
        rounded(NSRect(x: 48, y: 618, width: 293, height: 54), color: navy, radius: 12)
        rounded(NSRect(x: 358, y: 618, width: 293, height: 54), color: navy, radius: 12)
        line("👤 Личный кабинет", 93, 635, 19); line("💎 Тарифы", 445, 635, 19)
        rounded(NSRect(x: 0, y: 0, width: 700, height: 250), color: NSColor(calibratedRed: 0.045, green: 0.10, blue: 0.19, alpha: 1), radius: 0)
        rounded(NSRect(x: 18, y: 171, width: 664, height: 64), color: blue, radius: 13)
        line("🔐 Подключиться", 266, 190, 22, white, .medium)
        rounded(NSRect(x: 18, y: 91, width: 322, height: 64), color: blue, radius: 13)
        rounded(NSRect(x: 359, y: 91, width: 323, height: 64), color: blue, radius: 13)
        line("👤 Личный кабинет", 62, 110, 21, white, .medium)
        line("ℹ️ Информация", 422, 110, 21, white, .medium)
        line("Постоянная клавиатура Telegram", 199, 41, 16, cyan)
    }
    NSGraphicsContext.restoreGraphicsState()
    let data = bitmap.representation(using: .png, properties: [:])!
    try data.write(to: URL(fileURLWithPath: output + "/pilot-" + mode + "-preview.png"))
}
