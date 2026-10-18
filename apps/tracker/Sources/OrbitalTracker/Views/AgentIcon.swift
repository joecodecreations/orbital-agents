import AppKit
import SwiftUI
import OrbitalTrackerCore

/// Provider icons. Order: <tracker data>/icons/<agent>.png (drop official logos here) →
/// the installed app's icon (Claude.app, ChatGPT.app, …) → a drawn brand mark.
@MainActor
enum AgentIcons {
    static var iconsDir: URL?
    private static var cache: [String: NSImage?] = [:]

    private static let bundleIDs: [String: [String]] = [
        "claude": ["com.anthropic.claudefordesktop"],
        "chatgpt": ["com.openai.chat", "com.openai.codex"],
        "grok": ["ai.x.grok", "com.xai.grok", "ai.x.GrokApp"],
        "gemini": ["com.google.gemini", "com.google.GeminiMacOS"],
    ]

    static func image(for agent: String) -> NSImage? {
        let key = Agents.normalize(agent)
        if let cached = cache[key] { return cached }
        var found: NSImage?
        if let dir = iconsDir, IDGen.isValid(key) {   // plain names only: they become file names
            for ext in ["png", "jpg", "icns", "pdf"] {
                let url = dir.appendingPathComponent("\(key).\(ext)")
                if let img = NSImage(contentsOf: url) { found = img; break }
            }
        }
        if found == nil {
            for id in bundleIDs[key] ?? [] {
                if let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: id) {
                    found = NSWorkspace.shared.icon(forFile: url.path)
                    break
                }
            }
        }
        if cache.count > 64 { cache.removeAll() }   // agent names come from commands: keep it small
        cache[key] = found
        return found
    }
}

struct AgentIconView: View {
    let agent: String
    var size: CGFloat = 16

    var body: some View {
        Group {
            if let image = AgentIcons.image(for: agent) {
                Image(nsImage: image).resizable().interpolation(.high).aspectRatio(contentMode: .fit)
                    .scaleEffect(1.18)  // macOS app icons carry transparent padding
            } else {
                AgentGlyph(agent: Agents.normalize(agent))
            }
        }
        .frame(width: size, height: size)
        .help(Agents.displayName(agent))
    }
}

/// Icon for an item's origin: the provider for AI items, a waveform/pencil for your own.
struct SourceIcon: View {
    let item: TrackedItem
    var size: CGFloat = 16

    var body: some View {
        if item.isAI {
            AgentIconView(agent: item.agent ?? "other", size: size)
        } else {
            Image(systemName: item.source == .voice ? "waveform" : "pencil")
                .font(.system(size: size * 0.55, weight: .bold))
                .foregroundStyle(.white.opacity(0.92))
                .frame(width: size, height: size)
                .background(Circle().fill(Color.white.opacity(0.18)))
                .help(item.source == .voice ? "You said this" : "You typed this")
        }
    }
}

/// Hand-drawn fallbacks in each provider's colours.
struct AgentGlyph: View {
    let agent: String

    var body: some View {
        GeometryReader { geo in
            let s = min(geo.size.width, geo.size.height)
            ZStack {
                RoundedRectangle(cornerRadius: s * 0.24, style: .continuous).fill(background)
                Canvas { ctx, size in draw(&ctx, size) }
            }
            .frame(width: s, height: s)
        }
    }

    private var background: Color {
        switch agent {
        case "claude": return Color(red: 0.85, green: 0.47, blue: 0.34)
        case "chatgpt": return Color(red: 0.06, green: 0.64, blue: 0.50)
        case "grok": return .black
        case "gemini": return Color(red: 0.12, green: 0.12, blue: 0.14)
        default: return Color.white.opacity(0.25)
        }
    }

    private func draw(_ ctx: inout GraphicsContext, _ size: CGSize) {
        let s = min(size.width, size.height)
        let c = CGPoint(x: size.width / 2, y: size.height / 2)
        switch agent {
        case "claude":
            // Radiating spark.
            let lengths: [CGFloat] = [0.34, 0.27, 0.33, 0.25, 0.35, 0.28, 0.32, 0.26, 0.34, 0.27, 0.31, 0.24]
            var path = Path()
            for (i, len) in lengths.enumerated() {
                let a = CGFloat(i) / CGFloat(lengths.count) * .pi * 2 + 0.2
                path.move(to: CGPoint(x: c.x + cos(a) * s * 0.05, y: c.y + sin(a) * s * 0.05))
                path.addLine(to: CGPoint(x: c.x + cos(a) * s * len, y: c.y + sin(a) * s * len))
            }
            ctx.stroke(path, with: .color(.white), style: StrokeStyle(lineWidth: s * 0.085, lineCap: .round))
        case "chatgpt":
            // Six interlocking petals, approximating the OpenAI blossom.
            for i in 0..<6 {
                let a = CGFloat(i) * .pi / 3
                var petal = Path(roundedRect: CGRect(x: -s * 0.09, y: -s * 0.30, width: s * 0.18, height: s * 0.34),
                                 cornerRadius: s * 0.09)
                petal = petal.applying(CGAffineTransform(rotationAngle: a)).applying(CGAffineTransform(translationX: c.x, y: c.y))
                ctx.stroke(petal, with: .color(.white), lineWidth: s * 0.06)
            }
        case "grok":
            // Broken ring with a diagonal slash.
            var ring = Path()
            ring.addArc(center: c, radius: s * 0.24, startAngle: .degrees(-20), endAngle: .degrees(240), clockwise: false)
            ctx.stroke(ring, with: .color(.white), style: StrokeStyle(lineWidth: s * 0.08, lineCap: .round))
            var slash = Path()
            slash.move(to: CGPoint(x: c.x - s * 0.30, y: c.y + s * 0.30))
            slash.addLine(to: CGPoint(x: c.x + s * 0.30, y: c.y - s * 0.30))
            ctx.stroke(slash, with: .color(.white), style: StrokeStyle(lineWidth: s * 0.08, lineCap: .round))
        case "gemini":
            // Four-point sparkle with Gemini's blue→purple→rose gradient.
            let r = s * 0.36
            var star = Path()
            star.move(to: CGPoint(x: c.x, y: c.y - r))
            star.addQuadCurve(to: CGPoint(x: c.x + r, y: c.y), control: c)
            star.addQuadCurve(to: CGPoint(x: c.x, y: c.y + r), control: c)
            star.addQuadCurve(to: CGPoint(x: c.x - r, y: c.y), control: c)
            star.addQuadCurve(to: CGPoint(x: c.x, y: c.y - r), control: c)
            ctx.fill(star, with: .linearGradient(
                Gradient(colors: [Color(red: 0.26, green: 0.52, blue: 0.96), Color(red: 0.61, green: 0.45, blue: 0.80),
                                  Color(red: 0.85, green: 0.40, blue: 0.44)]),
                startPoint: CGPoint(x: c.x - r, y: c.y + r), endPoint: CGPoint(x: c.x + r, y: c.y - r)))
        default:
            let symbol = ctx.resolve(Image(systemName: "sparkles"))
            ctx.draw(symbol, in: CGRect(x: c.x - s * 0.3, y: c.y - s * 0.3, width: s * 0.6, height: s * 0.6))
        }
    }
}
