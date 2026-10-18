import AppKit
import OrbitalTrackerCore
import SwiftUI

enum Palette {
    static let bubbles: [Color] = [
        Color(red: 1.00, green: 0.42, blue: 0.42),  // coral
        Color(red: 0.20, green: 0.74, blue: 0.80),  // teal
        Color(red: 1.00, green: 0.65, blue: 0.20),  // amber
        Color(red: 0.55, green: 0.42, blue: 1.00),  // violet
        Color(red: 0.36, green: 0.80, blue: 0.45),  // green
        Color(red: 0.95, green: 0.40, blue: 0.78),  // pink
        Color(red: 0.30, green: 0.58, blue: 1.00),  // blue
        Color(red: 0.88, green: 0.76, blue: 0.24),  // mustard
    ]

    static func color(_ index: Int) -> Color { bubbles[((index % bubbles.count) + bubbles.count) % bubbles.count] }

    static let live = Color(red: 1.0, green: 0.30, blue: 0.32)
    static let ok = Color(red: 0.36, green: 0.85, blue: 0.50)
    static let warn = Color(red: 1.0, green: 0.72, blue: 0.30)
    /// The bar's camera when agents must attach screenshots (white icon on it).
    static let evidence = Color(red: 0.13, green: 0.55, blue: 0.30)
}

/// Real behind-window blur (SwiftUI materials only blur in-window content,
/// which is nothing in a transparent panel).
struct VisualEffectBlur: NSViewRepresentable {
    var material: NSVisualEffectView.Material = .hudWindow
    var cornerRadius: CGFloat = 0
    /// Below 1 lets more of what's behind show through the frost.
    var opacity: CGFloat = 1

    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.blendingMode = .behindWindow
        view.state = .active
        view.material = material
        view.alphaValue = opacity
        view.maskImage = cornerRadius > 0 ? Self.mask(cornerRadius) : nil
        return view
    }

    func updateNSView(_ view: NSVisualEffectView, context: Context) {
        view.material = material
        view.alphaValue = opacity
        view.maskImage = cornerRadius > 0 ? Self.mask(cornerRadius) : nil
    }

    private static func mask(_ radius: CGFloat) -> NSImage {
        let edge = radius * 2 + 1
        let image = NSImage(size: NSSize(width: edge, height: edge), flipped: false) { rect in
            NSColor.black.setFill()
            NSBezierPath(roundedRect: rect, xRadius: radius, yRadius: radius).fill()
            return true
        }
        image.capInsets = NSEdgeInsets(top: radius, left: radius, bottom: radius, right: radius)
        image.resizingMode = .stretch
        return image
    }
}

/// Dark frosted glass with a hairline edge and soft shadow.
struct Glass: View {
    var cornerRadius: CGFloat
    var tint: Color = .clear
    var tintOpacity: Double = 0
    /// 0 = the panel's smoky glass. Higher = clearer black glass (the detail windows use 0.55).
    var clarity: Double = 0

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)
        let smoke = 1 - clarity
        ZStack {
            shape.fill(Color.black.opacity(0.35 * smoke))
                .shadow(color: .black.opacity(0.35), radius: 10, y: 5)
            VisualEffectBlur(material: .hudWindow, cornerRadius: cornerRadius, opacity: 1 - clarity * 0.4)
                .clipShape(shape)
            shape.fill(Color.black.opacity(0.22 * smoke))
            if tintOpacity > 0 { shape.fill(tint.opacity(tintOpacity)) }
            shape.strokeBorder(Color.white.opacity(0.13), lineWidth: 1)
        }
    }
}

/// Lets the bar drag the whole panel (buttons on top still get their clicks).
struct WindowDragHandle: NSViewRepresentable {
    final class DragView: NSView {
        override func mouseDown(with event: NSEvent) { window?.performDrag(with: event) }
        override var mouseDownCanMoveWindow: Bool { true }
        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
    }

    func makeNSView(context: Context) -> NSView { DragView() }
    func updateNSView(_ nsView: NSView, context: Context) {}
}

/// Bottom-right handle that resizes a borderless window; the top-left corner stays put.
struct ResizeGrip: NSViewRepresentable {
    final class GripView: NSView {
        private var start: (mouse: NSPoint, frame: NSRect)?
        override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
        override var mouseDownCanMoveWindow: Bool { false }
        override func resetCursorRects() {
            if #available(macOS 15, *) {
                addCursorRect(bounds, cursor: .frameResize(position: .bottomRight, directions: .all))
            } else {
                addCursorRect(bounds, cursor: .crosshair)
            }
        }
        override func mouseDown(with event: NSEvent) {
            if let window { start = (NSEvent.mouseLocation, window.frame) }
        }
        override func mouseDragged(with event: NSEvent) {
            guard let window, let start else { return }
            let p = NSEvent.mouseLocation
            let width = max(window.minSize.width, start.frame.width + p.x - start.mouse.x)
            let height = max(window.minSize.height, start.frame.height - (p.y - start.mouse.y))
            window.setFrame(NSRect(x: start.frame.minX, y: start.frame.maxY - height, width: width, height: height), display: true)
        }
        override func mouseUp(with event: NSEvent) { start = nil }
    }

    func makeNSView(context: Context) -> GripView { GripView() }
    func updateNSView(_ nsView: GripView, context: Context) {}
}

/// Small round icon button used in the bar.
struct BarButton: View {
    var systemName: String
    var help: String
    var active = false
    var tint: Color = .white
    var action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            Image(systemName: systemName)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(active ? Color.black : tint.opacity(0.92))
                .frame(width: 26, height: 26)
                .background(Circle().fill(active ? Color.white.opacity(0.92) : Color.white.opacity(hover ? 0.16 : 0.08)))
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .help(help)
    }
}

enum Fmt {
    static func clock(_ date: Date) -> String {
        let cal = Calendar.current
        if cal.isDateInToday(date) { return date.formatted(date: .omitted, time: .shortened) }
        if let days = cal.dateComponents([.day], from: date, to: Date()).day, days < 6 {
            return date.formatted(.dateTime.weekday(.abbreviated).hour().minute())
        }
        return date.formatted(.dateTime.month(.abbreviated).day().hour().minute())
    }

    static func duration(_ seconds: TimeInterval) -> String {
        let s = max(0, Int(seconds))
        if s < 60 { return "\(s)s" }
        let m = s / 60
        if m < 60 { return "\(m)m" }
        let h = m / 60
        if h < 24 { return m % 60 == 0 ? "\(h)h" : "\(h)h \(m % 60)m" }
        return "\(h / 24)d \(h % 24)h"
    }

    static func ago(_ date: Date, now: Date) -> String {
        let s = now.timeIntervalSince(date)
        return s < 45 ? "just now" : "\(duration(s)) ago"
    }

    static func bytes(_ n: Int64?) -> String {
        guard let n else { return "" }
        return ByteCountFormatter.string(fromByteCount: n, countStyle: .file)
    }
}
