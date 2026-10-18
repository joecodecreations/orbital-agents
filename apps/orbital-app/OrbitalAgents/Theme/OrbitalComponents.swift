import SwiftUI

// Shared pieces of the Orbital Agents look: card surface, icon tile, ambient backdrop, chips.

/// Card surface: soft vertical gradient, hairline that catches light at the top edge.
struct OrbitalCard: ViewModifier {
    var padding: CGFloat = 16
    var radius: CGFloat = OrbitalTheme.cardRadius
    var tint: Color?

    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: radius, style: .continuous)
        content
            .padding(padding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background {
                shape
                    .fill(
                        LinearGradient(
                            colors: [OrbitalTheme.surfaceRaised.opacity(0.92), OrbitalTheme.surface],
                            startPoint: .top,
                            endPoint: .bottom
                        )
                    )
                    .overlay {
                        if let tint {
                            shape.fill(
                                LinearGradient(
                                    colors: [tint.opacity(0.16), tint.opacity(0.02)],
                                    startPoint: .topLeading,
                                    endPoint: .bottomTrailing
                                )
                            )
                        }
                    }
            }
            .overlay(
                shape.strokeBorder(
                    LinearGradient(
                        colors: [Color.white.opacity(0.11), Color.white.opacity(0.03)],
                        startPoint: .top,
                        endPoint: .bottom
                    ),
                    lineWidth: 1
                )
            )
            .clipShape(shape)
            .shadow(color: .black.opacity(0.28), radius: 10, x: 0, y: 6)
    }
}

extension View {
    func orbitalCard(padding: CGFloat = 16, radius: CGFloat = OrbitalTheme.cardRadius, tint: Color? = nil) -> some View {
        modifier(OrbitalCard(padding: padding, radius: radius, tint: tint))
    }

    /// Screens without a navigation bar: content fades out under the status bar instead of running behind it.
    func orbitalScrollFade() -> some View {
        mask {
            VStack(spacing: 0) {
                LinearGradient(colors: [.clear, .black], startPoint: .top, endPoint: .bottom)
                    .frame(height: 18)
                Rectangle()
            }
            .ignoresSafeArea(edges: .bottom)
        }
    }

    /// Window backdrop with a glow of the current accent in the top corner.
    func orbitalBackdrop(_ accent: Color) -> some View {
        background(OrbitalBackdrop(accent: accent))
    }
}

struct OrbitalBackdrop: View {
    var accent: Color

    var body: some View {
        ZStack {
            LinearGradient(
                colors: [OrbitalTheme.background, OrbitalTheme.backgroundDeep],
                startPoint: .top,
                endPoint: .bottom
            )
            RadialGradient(
                colors: [accent.opacity(0.20), accent.opacity(0.0)],
                center: .topTrailing,
                startRadius: 0,
                endRadius: 380
            )
            RadialGradient(
                colors: [accent.opacity(0.07), accent.opacity(0.0)],
                center: UnitPoint(x: 0.0, y: 0.35),
                startRadius: 0,
                endRadius: 320
            )
        }
        .ignoresSafeArea()
        .animation(.easeInOut(duration: 0.35), value: accent)
    }
}

/// Rounded square with an SF Symbol. Filled when selected.
struct IconTile: View {
    let symbol: String
    var tint: Color = OrbitalTheme.accent
    var size: CGFloat = 28
    var filled = false

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: size * 0.3, style: .continuous)
        Image(systemName: orbitalSymbol(symbol))
            .font(.system(size: size * 0.46, weight: .semibold))
            .foregroundStyle(filled ? OrbitalTheme.backgroundDeep : tint)
            .frame(width: size, height: size)
            .background {
                if filled {
                    shape.fill(LinearGradient(colors: [tint, tint.opacity(0.78)], startPoint: .top, endPoint: .bottom))
                } else {
                    shape.fill(tint.opacity(0.15))
                }
            }
            .overlay(shape.strokeBorder(tint.opacity(filled ? 0 : 0.22), lineWidth: 1))
            .accessibilityHidden(true)
    }
}

/// Small capsule label. Icon is optional; the text always says what the color means.
struct OrbitalChip: View {
    let text: String
    var symbol: String?
    var tint: Color = OrbitalTheme.textSecondary

    var body: some View {
        HStack(spacing: 4) {
            if let symbol {
                Image(systemName: symbol)
                    .font(.system(size: 10, weight: .bold))
            }
            Text(text)
                .font(.caption2.weight(.semibold))
                .lineLimit(1)
        }
        .foregroundStyle(tint)
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(tint.opacity(0.13), in: Capsule())
        .overlay(Capsule().strokeBorder(tint.opacity(0.22), lineWidth: 1))
    }
}

/// Live / Connecting / Offline with a dot.
struct ConnectionPill: View {
    let label: String
    var compact = false

    var body: some View {
        let color = OrbitalTheme.connectionColor(label)
        HStack(spacing: 5) {
            Circle()
                .fill(color)
                .frame(width: 6, height: 6)
                .shadow(color: color.opacity(0.8), radius: 3)
            Text(label)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(compact ? OrbitalTheme.textSecondary : color)
        }
        .padding(.horizontal, compact ? 0 : 8)
        .padding(.vertical, compact ? 0 : 4)
        .background {
            if !compact {
                Capsule().fill(color.opacity(0.12))
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Connection \(label)")
    }
}

/// Uppercase label above a group.
struct OrbitalEyebrow: View {
    let text: String
    var color: Color = OrbitalTheme.textTertiary

    var body: some View {
        Text(text.uppercased())
            .font(.caption2.weight(.bold))
            .tracking(0.9)
            .foregroundStyle(color)
            .lineLimit(1)
    }
}

/// Large title block used at the top of the pinned screens.
struct OrbitalScreenHeader<Trailing: View>: View {
    let eyebrow: String?
    let title: String
    var subtitle: String?
    var accent: Color = OrbitalTheme.accent
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        HStack(alignment: .bottom, spacing: 12) {
            VStack(alignment: .leading, spacing: 4) {
                if let eyebrow {
                    OrbitalEyebrow(text: eyebrow, color: accent.opacity(0.9))
                }
                Text(title)
                    .font(.title.weight(.bold))
                    .foregroundStyle(OrbitalTheme.text)
                    .lineLimit(2)
                    .minimumScaleFactor(0.8)
                if let subtitle, !subtitle.isEmpty {
                    Text(subtitle)
                        .font(.subheadline)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                }
            }
            Spacer(minLength: 0)
            trailing()
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

extension OrbitalScreenHeader where Trailing == EmptyView {
    init(eyebrow: String?, title: String, subtitle: String? = nil, accent: Color = OrbitalTheme.accent) {
        self.init(eyebrow: eyebrow, title: title, subtitle: subtitle, accent: accent) { EmptyView() }
    }
}

/// Pressed state for custom buttons: a small squeeze, no flash.
struct OrbitalPressStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed ? 0.97 : 1)
            .opacity(configuration.isPressed ? 0.86 : 1)
            .animation(.spring(response: 0.25, dampingFraction: 0.7), value: configuration.isPressed)
    }
}

/// Full-width action button in the house style.
struct OrbitalActionButton: View {
    enum Kind { case primary, secondary, destructive }

    let title: String
    var symbol: String?
    var kind: Kind = .primary
    var accent: Color = OrbitalTheme.accent
    var busy = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                if busy {
                    ProgressView()
                        .tint(foreground)
                        .controlSize(.small)
                } else if let symbol {
                    Image(systemName: symbol)
                        .font(.subheadline.weight(.bold))
                }
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.85)
            }
            .foregroundStyle(foreground)
            .frame(maxWidth: .infinity, minHeight: 46)
            .padding(.horizontal, 12)
            .background(background)
            .overlay(
                RoundedRectangle(cornerRadius: OrbitalTheme.controlRadius, style: .continuous)
                    .strokeBorder(stroke, lineWidth: 1)
            )
            .clipShape(RoundedRectangle(cornerRadius: OrbitalTheme.controlRadius, style: .continuous))
            .contentShape(Rectangle())
        }
        .buttonStyle(OrbitalPressStyle())
    }

    private var foreground: Color {
        switch kind {
        case .primary: return OrbitalTheme.backgroundDeep
        case .secondary: return OrbitalTheme.text
        case .destructive: return OrbitalTheme.danger
        }
    }

    @ViewBuilder
    private var background: some View {
        switch kind {
        case .primary:
            LinearGradient(colors: [accent, accent.opacity(0.80)], startPoint: .top, endPoint: .bottom)
        case .secondary:
            OrbitalTheme.surfaceRaised
        case .destructive:
            OrbitalTheme.danger.opacity(0.12)
        }
    }

    private var stroke: Color {
        switch kind {
        case .primary: return Color.white.opacity(0.18)
        case .secondary: return Color.white.opacity(0.08)
        case .destructive: return OrbitalTheme.danger.opacity(0.35)
        }
    }
}

/// Text field with a leading symbol, on a raised surface.
struct OrbitalField: View {
    let placeholder: String
    @Binding var text: String
    var symbol: String
    var secure = false
    var monospaced = false

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: symbol)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(OrbitalTheme.textTertiary)
                .frame(width: 20)
            Group {
                if secure {
                    SecureField(placeholder, text: $text)
                } else {
                    TextField(placeholder, text: $text)
                }
            }
            .font(monospaced ? .subheadline.monospaced() : .body)
            .foregroundStyle(OrbitalTheme.text)
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
        }
        .padding(.horizontal, 14)
        .frame(minHeight: 48)
        .background(OrbitalTheme.backgroundDeep.opacity(0.7))
        .overlay(
            RoundedRectangle(cornerRadius: OrbitalTheme.controlRadius, style: .continuous)
                .strokeBorder(OrbitalTheme.hairline, lineWidth: 1)
        )
        .clipShape(RoundedRectangle(cornerRadius: OrbitalTheme.controlRadius, style: .continuous))
    }
}

/// Hairline that fades out at both ends.
struct OrbitalRule: View {
    var body: some View {
        LinearGradient(
            colors: [OrbitalTheme.hairline.opacity(0), OrbitalTheme.hairline, OrbitalTheme.hairline.opacity(0)],
            startPoint: .leading,
            endPoint: .trailing
        )
        .frame(height: 1)
    }
}

/// Hub timestamps: ISO 8601, with or without fractional seconds.
enum OrbitalDates {
    private static let fractional: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()

    private static let whole: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }()

    /// ISO 8601 with or without fractional seconds (any number of digits), "Z" or an offset, or no zone
    /// at all (read as UTC). A bare number is Unix seconds, so a feed that sends `time.time()` still works.
    static func parse(_ raw: String?) -> Date? {
        guard var text = raw?.trimmingCharacters(in: .whitespaces), !text.isEmpty else { return nil }
        if let seconds = Double(text), seconds > 100_000_000 {
            return Date(timeIntervalSince1970: seconds > 100_000_000_000 ? seconds / 1000 : seconds)
        }
        if text.count == 10, text.dropFirst(4).first == "-" {
            return Insights.day(text)
        }
        if text.contains(" "), !text.contains("T") {
            text = text.replacingOccurrences(of: " ", with: "T")
        }
        // Python writes microseconds; the formatter wants at most milliseconds.
        if let dot = text.firstIndex(of: ".") {
            let after = text.index(after: dot)
            let digits = text[after...].prefix { $0.isNumber }.count
            if digits > 3 {
                let start = text.index(after, offsetBy: 3)
                let end = text.index(after, offsetBy: digits)
                text.removeSubrange(start ..< end)
            }
        }
        if !hasZone(text) { text += "Z" }
        return fractional.date(from: text) ?? whole.date(from: text)
    }

    private static func hasZone(_ text: String) -> Bool {
        if text.hasSuffix("Z") || text.hasSuffix("z") { return true }
        guard let time = text.firstIndex(of: "T") else { return true }
        let tail = text[text.index(after: time)...]
        return tail.contains("+") || tail.contains("-")
    }

    /// "5 min. ago". Falls back to the raw string so a strange format is still visible.
    static func relative(_ raw: String?) -> String? {
        guard let raw, !raw.isEmpty else { return nil }
        guard let date = parse(raw) else { return raw }
        if abs(date.timeIntervalSinceNow) < 45 { return "now" }
        let relative = RelativeDateTimeFormatter()
        relative.unitsStyle = .abbreviated
        return relative.localizedString(for: date, relativeTo: Date())
    }

    /// ISO date-time, or a bare "2026-10-02" day.
    static func parseFlexible(_ raw: String?) -> Date? {
        if let date = parse(raw) { return date }
        guard let raw, raw.count >= 10 else { return nil }
        return Insights.day(String(raw.prefix(10)))
    }

    /// "2 Oct", with the year only when it is not this year.
    static func shortDay(_ raw: String?) -> String? {
        guard let date = parseFlexible(raw) else { return nil }
        let sameYear = Calendar.current.isDate(date, equalTo: Date(), toGranularity: .year)
        return date.formatted(sameYear ? .dateTime.day().month(.abbreviated) : .dateTime.day().month(.abbreviated).year())
    }

    /// "Today", "Yesterday", "3 days ago", then a short date after a week.
    static func relativeDay(_ raw: String?) -> String? {
        guard let date = parseFlexible(raw) else { return nil }
        let calendar = Calendar.current
        if calendar.isDateInToday(date) { return "Today" }
        if calendar.isDateInYesterday(date) { return "Yesterday" }
        let days = calendar.dateComponents([.day], from: calendar.startOfDay(for: date), to: calendar.startOfDay(for: Date())).day ?? 0
        if days > 0, days < 7 { return "\(days) days ago" }
        return shortDay(raw)
    }

    static func shortDateTime(_ raw: String?) -> String? {
        guard let date = parse(raw) else { return nil }
        return date.formatted(date: .abbreviated, time: .shortened)
    }

    static func iso(_ date: Date) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.string(from: date)
    }
}

/// Left-to-right wrapping layout for chips.
struct FlowLayout: Layout {
    var spacing: CGFloat = 6
    var lineSpacing: CGFloat = 6

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? .infinity
        var x: CGFloat = 0
        var y: CGFloat = 0
        var lineHeight: CGFloat = 0
        var widest: CGFloat = 0
        for view in subviews {
            let size = fitted(view, maxWidth: width)
            if x > 0, x + size.width > width {
                y += lineHeight + lineSpacing
                x = 0
                lineHeight = 0
            }
            x += size.width + spacing
            widest = max(widest, x - spacing)
            lineHeight = max(lineHeight, size.height)
        }
        return CGSize(width: proposal.width ?? widest, height: y + lineHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX
        var y = bounds.minY
        var lineHeight: CGFloat = 0
        for view in subviews {
            let size = fitted(view, maxWidth: bounds.width)
            if x > bounds.minX, x + size.width > bounds.maxX {
                y += lineHeight + lineSpacing
                x = bounds.minX
                lineHeight = 0
            }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            lineHeight = max(lineHeight, size.height)
        }
    }

    private func fitted(_ view: LayoutSubview, maxWidth: CGFloat) -> CGSize {
        let ideal = view.sizeThatFits(.unspecified)
        guard ideal.width > maxWidth, maxWidth.isFinite else { return ideal }
        return view.sizeThatFits(ProposedViewSize(width: maxWidth, height: nil))
    }
}
