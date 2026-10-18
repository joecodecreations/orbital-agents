import Charts
import SwiftUI

// Realtime values from /v1/pulse: a beating dot, a tile per value, the Home card, and the
// badge on a product tile. Every view redraws on a 10 s clock so "12 s ago" and the
// live/stale colour stay true between pushes.

struct LiveDot: View {
    var live: Bool
    var size: CGFloat = 8
    @State private var beat = false

    var body: some View {
        let color = live ? OrbitalTheme.success : OrbitalTheme.textTertiary
        ZStack {
            if live {
                Circle()
                    .fill(color.opacity(0.35))
                    .frame(width: size * 2.2, height: size * 2.2)
                    .scaleEffect(beat ? 1 : 0.4)
                    .opacity(beat ? 0 : 1)
            }
            Circle()
                .fill(color)
                .frame(width: size, height: size)
        }
        .frame(width: size * 2.2, height: size * 2.2)
        .onAppear {
            guard live else { return }
            withAnimation(.easeOut(duration: 1.4).repeatForever(autoreverses: false)) { beat = true }
        }
        .accessibilityLabel(live ? "Live" : "Not updated recently")
    }
}

struct PulseSparkline: View {
    let points: [PulsePoint]
    var color: Color = OrbitalTheme.success

    var body: some View {
        Chart(Array(points.enumerated()), id: \.offset) { index, point in
            AreaMark(x: .value("i", index), y: .value("v", point.y))
                .foregroundStyle(LinearGradient(colors: [color.opacity(0.35), color.opacity(0.02)], startPoint: .top, endPoint: .bottom))
                .interpolationMethod(.monotone)
            LineMark(x: .value("i", index), y: .value("v", point.y))
                .foregroundStyle(color)
                .lineStyle(StrokeStyle(lineWidth: 1.6, lineCap: .round, lineJoin: .round))
                .interpolationMethod(.monotone)
        }
        .chartXAxis(.hidden)
        .chartYAxis(.hidden)
        .chartLegend(.hidden)
        .chartYScale(domain: 0 ... max(1, points.map(\.y).max() ?? 1))
        .accessibilityHidden(true)
    }
}

/// One realtime value: number, label, detail, age, and a sparkline when the feed sends a series.
struct PulseTile: View {
    let item: PulseItem
    let now: Date

    var body: some View {
        let live = item.isLive(now: now)
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 4) {
                LiveDot(live: live, size: 6)
                    .id(live)
                Text(item.label.uppercased())
                    .font(.system(size: 9.5, weight: .bold))
                    .tracking(0.5)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .lineLimit(1)
            }
            Text(item.shownValue)
                .font(.system(.title3, design: .rounded).weight(.bold))
                .monospacedDigit()
                .foregroundStyle(live ? OrbitalTheme.text : OrbitalTheme.textSecondary)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
                .contentTransition(.numericText())
                .animation(.snappy, value: item.value)
            if let series = item.series, series.count >= 2 {
                PulseSparkline(points: series, color: live ? OrbitalTheme.success : OrbitalTheme.textTertiary)
                    .frame(height: 26)
            }
            Text(caption)
                .font(.caption2)
                .foregroundStyle(OrbitalTheme.textTertiary)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(Color.white.opacity(live ? 0.05 : 0.03), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .accessibilityElement(children: .combine)
    }

    private var caption: String {
        var parts: [String] = []
        if let detail = item.detail, !detail.isEmpty { parts.append(detail) }
        if let observed = item.observedAt {
            let seconds = Int(now.timeIntervalSince(observed))
            parts.append(seconds < 60 ? "\(max(0, seconds))s ago" : OrbitalDates.relative(item.at) ?? "")
        }
        return parts.joined(separator: " · ")
    }
}

/// Realtime values for one product, as a grid. Used on the product page.
struct PulseGrid: View {
    let items: [PulseItem]

    var body: some View {
        TimelineView(.periodic(from: .now, by: 10)) { context in
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 120), spacing: 8)], alignment: .leading, spacing: 8) {
                ForEach(items) { PulseTile(item: $0, now: context.date) }
            }
        }
    }
}

/// Home: every realtime value, grouped by the product it belongs to.
struct LiveNowCard: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter

    private struct Group: Identifiable {
        var id: String
        var location: PageLocation
        var title: String
        var image: String?
        var symbol: String
        var accent: String?
        var items: [PulseItem]
    }

    private var groups: [Group] {
        var byKey: [String: Group] = [:]
        var order: [String] = []
        for item in store.pulses.values {
            let location = PageLocation(sectionID: item.sectionId, nodeID: item.nodeId)
            let key = "\(item.sectionId)/\(item.nodeId ?? "-")"
            if byKey[key] == nil {
                let product = store.products.first { $0.location.sectionID == item.sectionId && $0.location.nodeID == item.nodeId }
                let section = store.sectionsByID[item.sectionId]
                let node = item.nodeId.flatMap { id in section?.nodes.first { $0.id == id } }
                byKey[key] = Group(
                    id: key,
                    location: location,
                    title: product?.title ?? node?.title ?? section?.title ?? item.sectionId,
                    image: product?.iconImage ?? node?.iconImage ?? section?.iconImage,
                    symbol: product?.symbol ?? node?.icon ?? section?.icon ?? "dot.radiowaves.left.and.right",
                    accent: section?.accent,
                    items: []
                )
                order.append(key)
            }
            byKey[key]?.items.append(item)
        }
        let productOrder = store.products.map { "\($0.location.sectionID)/\($0.location.nodeID ?? "-")" }
        return order
            .compactMap { byKey[$0] }
            .map { group in
                var sorted = group
                sorted.items.sort { ($0.order ?? 100, $0.label) < ($1.order ?? 100, $1.label) }
                return sorted
            }
            .sorted { (productOrder.firstIndex(of: $0.id) ?? 999, $0.title) < (productOrder.firstIndex(of: $1.id) ?? 999, $1.title) }
    }

    var body: some View {
        TimelineView(.periodic(from: .now, by: 10)) { context in
            let rows = groups
            let anyLive = store.pulses.values.contains { $0.isLive(now: context.date) }
            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 6) {
                    LiveDot(live: anyLive, size: 7)
                        .id(anyLive)
                    OrbitalEyebrow(text: "Live now", color: anyLive ? OrbitalTheme.success : OrbitalTheme.textTertiary)
                    Spacer(minLength: 0)
                    Text(anyLive ? "Updating" : "No fresh values")
                        .font(.caption2)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
                ForEach(Array(rows.enumerated()), id: \.element.id) { index, group in
                    if index > 0 {
                        Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                    }
                    Button {
                        router.show(group.location)
                    } label: {
                        compactRow(group, now: context.date)
                    }
                    .buttonStyle(OrbitalPressStyle())
                }
            }
            .orbitalCard(padding: 14, tint: anyLive ? OrbitalTheme.success : nil)
        }
    }
}

extension LiveNowCard {
    /// One product: icon, name, the first value's sparkline, then up to four numbers in a row.
    private func compactRow(_ group: Group, now: Date) -> some View {
        let live = group.items.contains { $0.isLive(now: now) }
        let newest = group.items.compactMap(\.observedAt).max()
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                AppIcon(image: group.image, symbol: group.symbol, tint: OrbitalTheme.accentColor(group.accent), size: 26)
                VStack(alignment: .leading, spacing: 1) {
                    Text(group.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                        .lineLimit(1)
                    if let newest {
                        let seconds = Int(now.timeIntervalSince(newest))
                        Text(seconds < 60 ? "\(max(0, seconds))s ago" : (OrbitalDates.relative(OrbitalDates.iso(newest)) ?? ""))
                            .font(.caption2)
                            .foregroundStyle(live ? OrbitalTheme.success : OrbitalTheme.textTertiary)
                    }
                }
                Spacer(minLength: 8)
                if let series = group.items.first(where: { ($0.series?.count ?? 0) >= 2 })?.series {
                    PulseSparkline(points: series, color: live ? OrbitalTheme.success : OrbitalTheme.textTertiary)
                        .frame(width: 72, height: 24)
                }
                Image(systemName: "chevron.right")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            let shown = Array(group.items.prefix(4))
            HStack(alignment: .top, spacing: 8) {
                ForEach(shown) { item in
                    VStack(alignment: .leading, spacing: 2) {
                        Text(item.shownValue)
                            .font(.system(.headline, design: .rounded).weight(.bold))
                            .monospacedDigit()
                            .foregroundStyle(item.isLive(now: now) ? OrbitalTheme.text : OrbitalTheme.textSecondary)
                            .lineLimit(1)
                            .minimumScaleFactor(0.6)
                            .contentTransition(.numericText())
                            .animation(.snappy, value: item.value)
                        Text(item.label)
                            .font(.system(size: 10.5, weight: .medium))
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }
}

/// The first realtime value of a product, for its Home tile.
struct PulseBadge: View {
    let item: PulseItem

    var body: some View {
        TimelineView(.periodic(from: .now, by: 10)) { context in
            let live = item.isLive(now: context.date)
            HStack(spacing: 4) {
                LiveDot(live: live, size: 5)
                    .id(live)
                Text(item.shownValue)
                    .font(.caption.weight(.bold).monospacedDigit())
                    .foregroundStyle(live ? OrbitalTheme.success : OrbitalTheme.textSecondary)
                    .contentTransition(.numericText())
                Text(item.label.lowercased())
                    .font(.caption2)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .lineLimit(1)
            }
            .accessibilityElement(children: .combine)
        }
    }
}
