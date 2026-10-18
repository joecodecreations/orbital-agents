import SwiftUI

enum OrbitalTheme {
    static let backgroundDeep = Color(red: 0x0A / 255, green: 0x0C / 255, blue: 0x10 / 255)
    static let background = Color(red: 0x0E / 255, green: 0x10 / 255, blue: 0x14 / 255)
    static let surface = Color(red: 0x17 / 255, green: 0x1A / 255, blue: 0x21 / 255)
    static let surfaceRaised = Color(red: 0x1F / 255, green: 0x24 / 255, blue: 0x30 / 255)
    static let surfaceHighlight = Color(red: 0x27 / 255, green: 0x2D / 255, blue: 0x3B / 255)
    static let hairline = Color(red: 0x2A / 255, green: 0x31 / 255, blue: 0x42 / 255)
    static let text = Color(red: 0xF4 / 255, green: 0xF6 / 255, blue: 0xFB / 255)
    static let textSecondary = Color(red: 0xA8 / 255, green: 0xB0 / 255, blue: 0xC2 / 255)
    static let textTertiary = Color(red: 0x6E / 255, green: 0x77 / 255, blue: 0x8C / 255)
    static let accent = Color(red: 0x8B / 255, green: 0xA4 / 255, blue: 1.0)
    static let success = Color(red: 0x3D / 255, green: 0xDC / 255, blue: 0x97 / 255)
    static let warning = Color(red: 0xF5 / 255, green: 0xC1 / 255, blue: 0x6C / 255)
    static let danger = Color(red: 1.0, green: 0x6B / 255, blue: 0x6B / 255)
    static let violet = Color(red: 0xC0 / 255, green: 0x84 / 255, blue: 0xFC / 255)

    static let chartPalette: [Color] = [accent, success, warning, danger, violet]

    /// Swatches offered by the section form. Same hues as the seeds and the chart palette.
    static let accentSwatches: [String] = ["#8BA4FF", "#3DDC97", "#F5C16C", "#FF6B6B", "#C084FC", "#5AC8FA", "#FF9F6B"]

    static let columnMax: CGFloat = 720
    static let blockSpacing: CGFloat = 16
    static let cardRadius: CGFloat = 18
    static let controlRadius: CGFloat = 12

    static func accentColor(_ hex: String?) -> Color {
        guard let hex, let color = Color(hex: hex) else { return accent }
        return color
    }

    /// Hues for things that carry no colour of their own: spaces, products, chart lines, agents.
    static let palette: [Color] = [
        accent,
        success,
        violet,
        warning,
        Color(red: 0x5A / 255, green: 0xC8 / 255, blue: 0xFA / 255),
        Color(red: 1.0, green: 0x9F / 255, blue: 0x6B / 255),
        Color(red: 0xA3 / 255, green: 0xE6 / 255, blue: 0x35 / 255),
        danger,
        Color(red: 1.0, green: 0x4F / 255, blue: 0xB4 / 255),
    ]

    /// The same colour for the same name on every screen and every launch: the payload's hex when it
    /// sends one, otherwise a stable hash of the name into `palette`.
    static func color(for key: String, hex: String? = nil) -> Color {
        if let hex, let color = Color(hex: hex) { return color }
        guard !key.isEmpty else { return accent }
        // FNV-1a, so the pick never changes between runs (Swift's hashValue is seeded per launch).
        var hash: UInt32 = 2_166_136_261
        for byte in key.lowercased().utf8 {
            hash ^= UInt32(byte)
            hash = hash &* 16_777_619
        }
        return palette[Int(hash % UInt32(palette.count))]
    }

    /// Callouts, timeline dots, and chips. Info follows the section accent.
    static func toneColor(_ tone: String?, accent: Color = OrbitalTheme.accent) -> Color {
        switch tone {
        case "success": return success
        case "warning": return warning
        case "danger": return danger
        default: return accent
        }
    }

    static func toneSymbol(_ tone: String?) -> String {
        switch tone {
        case "success": return "checkmark.circle.fill"
        case "warning": return "exclamationmark.triangle.fill"
        case "danger": return "xmark.octagon.fill"
        default: return "info.circle.fill"
        }
    }

    static func toneLabel(_ tone: String?) -> String {
        switch tone {
        case "success": return "Success"
        case "warning": return "Warning"
        case "danger": return "Danger"
        default: return "Info"
        }
    }

    static func connectionColor(_ label: String) -> Color {
        switch label {
        case "Live": return success
        case "Connecting": return warning
        case "Preview": return violet
        default: return danger
        }
    }
}

extension Color {
    init?(hex: String) {
        var s = hex.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.hasPrefix("#") { s.removeFirst() }
        guard s.count == 6, let value = UInt32(s, radix: 16) else { return nil }
        let r = Double((value >> 16) & 0xFF) / 255
        let g = Double((value >> 8) & 0xFF) / 255
        let b = Double(value & 0xFF) / 255
        self.init(red: r, green: g, blue: b)
    }
}
