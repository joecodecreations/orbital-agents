import SwiftUI

// The progress, status, and links blocks: labelled bars, up/down rows, and a list of links.

// MARK: - Progress

struct ProgressRow: View {
    let item: ProgressItem
    var accent: Color = OrbitalTheme.accent

    private var tint: Color {
        guard let tone = item.tone else { return accent }
        return OrbitalTheme.toneColor(tone, accent: accent)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text(item.label)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
                    .lineLimit(1)
                Spacer(minLength: 8)
                Text(item.shownValue)
                    .font(.subheadline.weight(.bold).monospacedDigit())
                    .foregroundStyle(tint)
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.white.opacity(0.07))
                    Capsule()
                        .fill(LinearGradient(colors: [tint.opacity(0.75), tint], startPoint: .leading, endPoint: .trailing))
                        .frame(width: max(item.fraction > 0 ? 6 : 0, geo.size.width * item.fraction))
                }
            }
            .frame(height: 8)
            if let caption = Insights.nonEmpty(item.caption) {
                Text(caption)
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .lineLimit(2)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(item.label), \(item.shownValue)")
        .accessibilityValue("\(Int((item.fraction * 100).rounded())) percent")
    }
}

struct ProgressCard: View {
    let block: ProgressBlock
    let accent: Color

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let title = block.title {
                Text(title)
                    .font(.headline)
                    .foregroundStyle(OrbitalTheme.text)
            }
            ForEach(block.items) { ProgressRow(item: $0, accent: accent) }
            if block.items.isEmpty {
                Text("Nothing to show yet.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
        }
        .orbitalCard(padding: 16)
    }
}

// MARK: - Status

enum StatusStyle {
    static func color(_ health: String) -> Color {
        switch health {
        case "up": return OrbitalTheme.success
        case "down": return OrbitalTheme.danger
        case "degraded": return OrbitalTheme.warning
        default: return OrbitalTheme.textTertiary
        }
    }

    static func symbol(_ health: String) -> String {
        switch health {
        case "up": return "checkmark.circle.fill"
        case "down": return "xmark.octagon.fill"
        case "degraded": return "exclamationmark.triangle.fill"
        default: return "questionmark.circle"
        }
    }

    static func word(_ health: String) -> String {
        switch health {
        case "up": return "Up"
        case "down": return "Down"
        case "degraded": return "Degraded"
        default: return "Unknown"
        }
    }
}

/// "5 up · 1 down", and chips for the ones that are not up.
struct StatusSummaryRow: View {
    let block: StatusBlock

    var body: some View {
        let up = block.items.filter { $0.health == "up" }.count
        let bad = block.items.filter { $0.health == "down" || $0.health == "degraded" }
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Image(systemName: bad.isEmpty ? "checkmark.seal.fill" : "exclamationmark.triangle.fill")
                    .foregroundStyle(bad.isEmpty ? OrbitalTheme.success : (block.downCount > 0 ? OrbitalTheme.danger : OrbitalTheme.warning))
                Text(summary(up: up))
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
                Spacer(minLength: 0)
            }
            if !bad.isEmpty {
                FlowLayout(spacing: 6, lineSpacing: 6) {
                    ForEach(bad) { item in
                        OrbitalChip(text: item.label, symbol: StatusStyle.symbol(item.health), tint: StatusStyle.color(item.health))
                    }
                }
            }
        }
    }

    private func summary(up: Int) -> String {
        var parts = ["\(up) up"]
        if block.downCount > 0 { parts.append("\(block.downCount) down") }
        if block.degradedCount > 0 { parts.append("\(block.degradedCount) degraded") }
        let unknown = block.items.count - up - block.downCount - block.degradedCount
        if unknown > 0 { parts.append("\(unknown) unknown") }
        return parts.joined(separator: " · ")
    }
}

struct StatusCard: View {
    let block: StatusBlock
    let accent: Color
    @Environment(\.openURL) private var openURL

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text(block.title ?? "Status")
                    .font(.headline)
                    .foregroundStyle(OrbitalTheme.text)
                Spacer(minLength: 8)
                let up = block.items.filter { $0.health == "up" }.count
                Text("\(up) of \(block.items.count) up")
                    .font(.caption.weight(.semibold).monospacedDigit())
                    .foregroundStyle(up == block.items.count ? OrbitalTheme.success : OrbitalTheme.warning)
            }
            VStack(spacing: 0) {
                ForEach(Array(block.items.enumerated()), id: \.element.id) { index, item in
                    if index > 0 {
                        Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                    }
                    row(item)
                }
            }
        }
        .orbitalCard(padding: 16, tint: block.downCount > 0 ? OrbitalTheme.danger : (block.degradedCount > 0 ? OrbitalTheme.warning : nil))
    }

    private func row(_ item: StatusItem) -> some View {
        let color = StatusStyle.color(item.health)
        return Button {
            if let url = item.url.flatMap(URL.init(string:)) { openURL(url) }
        } label: {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: StatusStyle.symbol(item.health))
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(color)
                    .frame(width: 22)
                    .padding(.top, 1)
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.label)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                    if let detail = Insights.nonEmpty(item.detail) {
                        Text(detail)
                            .font(.caption)
                            .foregroundStyle(OrbitalTheme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 8)
                VStack(alignment: .trailing, spacing: 2) {
                    Text(StatusStyle.word(item.health))
                        .font(.caption.weight(.bold))
                        .foregroundStyle(color)
                    if let since = OrbitalDates.relative(item.since) {
                        Text("since \(since)")
                            .font(.caption2)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                    }
                }
            }
            .padding(.vertical, 9)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .disabled(item.url == nil)
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Links

struct LinksCard: View {
    let block: LinksBlock
    let accent: Color
    @Environment(\.openURL) private var openURL

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let title = block.title {
                Text(title)
                    .font(.headline)
                    .foregroundStyle(OrbitalTheme.text)
            }
            VStack(spacing: 0) {
                ForEach(Array(block.items.enumerated()), id: \.element.id) { index, item in
                    if index > 0 {
                        Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                    }
                    Button {
                        if let url = URL(string: item.url) { openURL(url) }
                    } label: {
                        HStack(spacing: 12) {
                            IconTile(symbol: item.icon ?? "link", tint: accent, size: 30)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(item.label)
                                    .font(.subheadline.weight(.semibold))
                                    .foregroundStyle(OrbitalTheme.text)
                                    .lineLimit(1)
                                Text(item.detail ?? URL(string: item.url)?.host ?? item.url)
                                    .font(.caption)
                                    .foregroundStyle(OrbitalTheme.textTertiary)
                                    .lineLimit(1)
                            }
                            Spacer(minLength: 0)
                            Image(systemName: "arrow.up.right")
                                .font(.caption.weight(.bold))
                                .foregroundStyle(accent)
                        }
                        .padding(.vertical, 8)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(OrbitalPressStyle())
                }
            }
        }
        .orbitalCard(padding: 16)
    }
}
