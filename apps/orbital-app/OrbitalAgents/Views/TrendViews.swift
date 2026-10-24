import Charts
import SwiftUI

// The chart pieces Home and the space and product pages share: a 7-day line chart, round space and
// product toggles, metric pill toggles, a series block on its own page, and a card that adds one kind
// of series up across products.

// MARK: - Chart

struct TrendLine: Identifiable, Equatable {
    var id: String
    var name: String
    var color: Color
    var values: [Double?]
    var format: String?
    var level: Bool
}

/// Lines over calendar days. One line gets an area fill. Drag to read a day.
struct TrendChart: View {
    let days: [Date]
    let lines: [TrendLine]
    var height: CGFloat = 190
    @State private var selected: Date?

    private struct Sample: Identifiable {
        var id: String { "\(line.id)|\(day.timeIntervalSince1970)" }
        var line: TrendLine
        var day: Date
        var value: Double
    }

    private var samples: [Sample] {
        lines.flatMap { line in
            zip(days, line.values).compactMap { day, value in value.map { Sample(line: line, day: day, value: $0) } }
        }
    }

    var body: some View {
        if samples.isEmpty {
            VStack(spacing: 6) {
                Image(systemName: "chart.xyaxis.line")
                    .font(.title3)
                Text("Nothing measured in these days yet.")
                    .font(.footnote)
            }
            .foregroundStyle(OrbitalTheme.textTertiary)
            .frame(maxWidth: .infinity, minHeight: height * 0.7)
        } else {
            chart
                .frame(height: height)
        }
    }

    private var single: TrendLine? { lines.count == 1 ? lines.first : nil }

    private var chart: some View {
        Chart {
            ForEach(samples) { sample in
                if let single {
                    AreaMark(
                        x: .value("Day", sample.day),
                        y: .value("Value", sample.value)
                    )
                    .foregroundStyle(
                        LinearGradient(colors: [single.color.opacity(0.32), single.color.opacity(0.01)], startPoint: .top, endPoint: .bottom)
                    )
                    .interpolationMethod(.monotone)
                }
                LineMark(
                    x: .value("Day", sample.day),
                    y: .value("Value", sample.value),
                    series: .value("Line", sample.line.id)
                )
                .foregroundStyle(sample.line.color)
                .lineStyle(StrokeStyle(lineWidth: lines.count == 1 ? 2.8 : 2.2, lineCap: .round, lineJoin: .round))
                .interpolationMethod(.monotone)
                PointMark(
                    x: .value("Day", sample.day),
                    y: .value("Value", sample.value)
                )
                .foregroundStyle(sample.line.color)
                .symbolSize(days.count > 14 ? 10 : (lines.count == 1 ? 34 : 22))
            }
            if let day = selectedDay {
                RuleMark(x: .value("Day", day))
                    .foregroundStyle(Color.white.opacity(0.25))
                    .lineStyle(StrokeStyle(lineWidth: 1, dash: [3, 3]))
                    .annotation(position: .top, spacing: 4, overflowResolution: .init(x: .fit(to: .chart), y: .disabled)) {
                        tooltip(day)
                    }
            }
        }
        .chartLegend(.hidden)
        .chartXScale(domain: (days.first ?? Date()) ... (days.last ?? Date()), range: .plotDimension(padding: 16))
        .chartXAxis {
            // Every day for a week, every 7th day back from today for a month, so today is always labelled.
            AxisMarks(values: axisDays) { _ in
                AxisValueLabel(format: days.count > 10 ? .dateTime.day().month(.abbreviated) : .dateTime.weekday(.abbreviated), anchor: .top)
                    .font(.caption2)
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
        }
        .chartYAxis {
            AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) { value in
                AxisGridLine(stroke: StrokeStyle(lineWidth: 1, dash: [3, 4]))
                    .foregroundStyle(OrbitalTheme.hairline.opacity(0.6))
                AxisValueLabel {
                    if let number = value.as(Double.self) {
                        Text(Trends.format(number, format: lines.first?.format, compact: true))
                            .font(.caption2)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                    }
                }
            }
        }
        .chartYScale(domain: .automatic(includesZero: true))
        .chartXSelection(value: $selected)
        .accessibilityLabel(lines.map(\.name).joined(separator: ", "))
    }

    private var axisDays: [Date] {
        guard days.count > 10 else { return days }
        return stride(from: days.count - 1, through: 0, by: -7).map { days[$0] }.reversed()
    }

    /// The drag position snapped to the nearest day on the chart.
    private var selectedDay: Date? {
        guard let selected else { return nil }
        return days.min { abs($0.timeIntervalSince(selected)) < abs($1.timeIntervalSince(selected)) }
    }

    private func tooltip(_ day: Date) -> some View {
        let index = days.firstIndex(of: day) ?? 0
        return VStack(alignment: .leading, spacing: 3) {
            Text(day.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated)))
                .font(.caption2.weight(.bold))
                .foregroundStyle(OrbitalTheme.textSecondary)
            ForEach(lines.prefix(6)) { line in
                HStack(spacing: 5) {
                    Circle().fill(line.color).frame(width: 6, height: 6)
                    if lines.count > 1 {
                        Text(line.name)
                            .lineLimit(1)
                    }
                    Text(Trends.format(index < line.values.count ? line.values[index] : nil, format: line.format))
                        .fontWeight(.bold)
                        .monospacedDigit()
                }
                .font(.caption2)
                .foregroundStyle(OrbitalTheme.text)
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 6)
        .background(OrbitalTheme.surfaceHighlight.opacity(0.96), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(Color.white.opacity(0.1), lineWidth: 1))
    }
}

// MARK: - Toggles

struct CircleChoice: Identifiable, Equatable {
    /// "" is All.
    var id: String
    var label: String
    var image: String?
    var symbol: String
    var tint: Color

    static func all(_ label: String = "All") -> CircleChoice {
        CircleChoice(id: "", label: label, image: nil, symbol: "square.grid.2x2.fill", tint: OrbitalTheme.accent)
    }

    static func space(_ space: SpaceSummary) -> CircleChoice {
        CircleChoice(id: space.id, label: space.name, image: space.iconImage, symbol: space.symbol, tint: space.tint)
    }

    static func product(_ product: ProductInfo) -> CircleChoice {
        CircleChoice(id: product.id, label: product.name, image: product.iconImage, symbol: product.symbol, tint: product.tint)
    }
}

/// Round buttons with an icon and a name under it: All, then each space or product.
struct CircleToggleRow: View {
    let choices: [CircleChoice]
    @Binding var selection: String
    var size: CGFloat = 46

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(alignment: .top, spacing: 14) {
                ForEach(choices) { choice in
                    let selected = choice.id == selection
                    Button {
                        UISelectionFeedbackGenerator().selectionChanged()
                        withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) { selection = choice.id }
                    } label: {
                        VStack(spacing: 5) {
                            ZStack {
                                Circle()
                                    .fill(choice.tint.opacity(selected ? 0.24 : 0.08))
                                if let image = choice.image {
                                    AppIcon(image: image, symbol: choice.symbol, tint: choice.tint, size: size - 10)
                                        .clipShape(Circle())
                                } else {
                                    Image(systemName: choice.symbol)
                                        .font(.system(size: size * 0.36, weight: .semibold))
                                        .foregroundStyle(selected ? choice.tint : OrbitalTheme.textSecondary)
                                }
                            }
                            .frame(width: size, height: size)
                            .overlay(
                                Circle().strokeBorder(selected ? choice.tint : Color.white.opacity(0.1), lineWidth: selected ? 2.5 : 1)
                            )
                            .opacity(selected || selection.isEmpty || choice.id.isEmpty ? 1 : 0.6)
                            Text(choice.label)
                                .font(.caption2.weight(selected ? .bold : .medium))
                                .foregroundStyle(selected ? OrbitalTheme.text : OrbitalTheme.textSecondary)
                                .lineLimit(2)
                                .multilineTextAlignment(.center)
                                .fixedSize(horizontal: false, vertical: true)
                                .frame(width: size + 30)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(OrbitalPressStyle())
                    .accessibilityLabel(choice.label)
                    .accessibilityAddTraits(selected ? .isSelected : [])
                }
            }
            .padding(.vertical, 2)
            .padding(.horizontal, 1)
        }
    }
}

struct PillChoice: Identifiable, Equatable {
    var id: String
    var label: String
    var value: String?
    var tint: Color
}

/// Rounded toggles that switch what the chart shows. They wrap onto as many lines as they need.
struct PillToggleRow: View {
    let choices: [PillChoice]
    @Binding var selection: String

    var body: some View {
        FlowLayout(spacing: 7, lineSpacing: 7) {
            ForEach(choices) { choice in
                let selected = choice.id == selection
                Button {
                    UISelectionFeedbackGenerator().selectionChanged()
                    withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) { selection = choice.id }
                } label: {
                    HStack(spacing: 6) {
                        Circle()
                            .fill(choice.tint)
                            .frame(width: 7, height: 7)
                            .opacity(selected ? 1 : 0.55)
                        Text(choice.label)
                            .foregroundStyle(selected ? OrbitalTheme.text : OrbitalTheme.textSecondary)
                            .lineLimit(1)
                        if let value = choice.value {
                            Text(value)
                                .monospacedDigit()
                                .foregroundStyle(selected ? choice.tint : OrbitalTheme.textTertiary)
                                .lineLimit(1)
                        }
                    }
                    .font(.footnote.weight(.semibold))
                    .padding(.horizontal, 12)
                    .frame(minHeight: 34)
                    .background(selected ? choice.tint.opacity(0.2) : Color.white.opacity(0.05), in: Capsule())
                    .overlay(Capsule().strokeBorder(selected ? choice.tint.opacity(0.6) : Color.white.opacity(0.08), lineWidth: 1))
                    .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityLabel([choice.label, choice.value].compactMap { $0 }.joined(separator: ", "))
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
        }
    }
}

/// 7 days or 30 days.
struct WindowToggle: View {
    @Binding var days: Int
    var tint: Color = OrbitalTheme.accent

    var body: some View {
        HStack(spacing: 2) {
            ForEach([7, 30], id: \.self) { option in
                let selected = option == days
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) { days = option }
                } label: {
                    Text("\(option)D")
                        .font(.caption2.weight(.bold))
                        .monospacedDigit()
                        .foregroundStyle(selected ? OrbitalTheme.backgroundDeep : OrbitalTheme.textSecondary)
                        .padding(.horizontal, 9)
                        .frame(minHeight: 26)
                        .background(selected ? tint : Color.clear, in: Capsule())
                        .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("\(option) days")
                .accessibilityAddTraits(selected ? .isSelected : [])
            }
        }
        .padding(2)
        .background(Color.white.opacity(0.06), in: Capsule())
        .frame(minHeight: 44)
    }
}

/// A coloured dot (or platform glyph), the name, and the period's figure, one per line.
struct TrendLegend: View {
    let lines: [TrendLine]
    var showsGlyph: (TrendLine) -> String? = { _ in nil }

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 140), spacing: 8)], alignment: .leading, spacing: 8) {
            ForEach(lines) { line in
                HStack(spacing: 8) {
                    if let glyph = showsGlyph(line) {
                        PlatformBadge(glyph: glyph, color: line.color, size: 20)
                    } else {
                        Circle().fill(line.color).frame(width: 9, height: 9)
                    }
                    Text(line.name)
                        .font(.caption.weight(.medium))
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .lineLimit(2)
                        .minimumScaleFactor(0.85)
                    Spacer(minLength: 4)
                    Text(Trends.format(Trends.summary(line.values, level: line.level), format: line.format, compact: true))
                        .font(.caption.weight(.bold).monospacedDigit())
                        .foregroundStyle(OrbitalTheme.text)
                }
                .padding(.horizontal, 10)
                .padding(.vertical, 7)
                .background(Color.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            }
        }
    }
}

/// A platform's glyph in its colour.
struct PlatformBadge: View {
    let glyph: String
    let color: Color
    var size: CGFloat = 24
    var glyphColor: Color = .white

    init(glyph: String, color: Color, size: CGFloat = 24, glyphColor: Color = .white) {
        self.glyph = glyph
        self.color = color
        self.size = size
        self.glyphColor = glyphColor
    }

    init(platform: String?, size: CGFloat = 24) {
        self.init(glyph: PlatformStyle.glyph(platform), color: PlatformStyle.color(platform), size: size)
    }

    var body: some View {
        Text(glyph)
            .font(.system(size: size * 0.52, weight: .black, design: .rounded))
            .foregroundStyle(glyphColor)
            .frame(width: size, height: size)
            .background(color, in: Circle())
            .accessibilityHidden(true)
    }
}

/// Eyebrow, optional icon, title, and an optional chevron that opens more.
struct TrendCardHeader: View {
    let eyebrow: String
    var title: String?
    var tint: Color
    var image: String?
    var symbol: String?
    @Binding var window: Int
    var showsWindow = true
    var onOpen: (() -> Void)?

    var body: some View {
        HStack(alignment: .center, spacing: 10) {
            if let symbol {
                AppIcon(image: image, symbol: symbol, tint: tint, size: 34)
            }
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    OrbitalEyebrow(text: eyebrow, color: tint)
                    Spacer(minLength: 4)
                    if let onOpen {
                        Button(action: onOpen) {
                            HStack(spacing: 3) {
                                Text("Open")
                                Image(systemName: "chevron.right")
                                    .font(.caption2.weight(.bold))
                            }
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(tint)
                            .frame(minWidth: 44, minHeight: 28, alignment: .trailing)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Open \(title ?? eyebrow)")
                    }
                }
                HStack(alignment: .center, spacing: 8) {
                    if let title {
                        Text(title)
                            .font(.headline)
                            .foregroundStyle(OrbitalTheme.text)
                            .lineLimit(1)
                            .minimumScaleFactor(0.85)
                    }
                    Spacer(minLength: 4)
                    if showsWindow {
                        WindowToggle(days: $window, tint: tint)
                    }
                }
            }
        }
    }
}

// MARK: - One series block

/// A series block: one chart, then a rounded toggle per metric. Metrics split by `platform` (a social
/// network, a region, a host) show one toggle per stat with a line per platform.
struct SeriesCard: View {
    let block: SeriesBlock
    var accent: Color = OrbitalTheme.accent
    var eyebrow: String?
    var image: String?
    var symbol: String?
    var onOpen: (() -> Void)?
    @State private var selection = ""
    @State private var window = 0

    private var byPlatform: Bool { block.metrics.contains { $0.platform != nil } }
    /// The block's own window until a toggle is tapped: 7, or 30 when it asks for more than 10 days.
    private var spanDays: Int { window == 0 ? (block.window > 10 ? 30 : 7) : window }
    private var days: [Date] { Trends.days(spanDays) }
    private var span: Binding<Int> {
        Binding(get: { spanDays }, set: { window = $0 })
    }

    /// By platform: one toggle per stat. Otherwise: one toggle per metric.
    private var choices: [PillChoice] {
        let days = days
        if byPlatform {
            let stats = StatStyle.present([SeriesRef(id: block.id, location: PageLocation(sectionID: ""), block: block)], kind: block.kind)
            return stats.map { stat in
                let metrics = block.metrics.filter { $0.statKey == stat }
                let level = metrics.first?.level ?? false
                let value = Trends.summary(Trends.combine(metrics, days: days), level: level)
                return PillChoice(id: stat, label: StatStyle.label(stat), value: Trends.format(value, format: metrics.first?.format, compact: true), tint: accent)
            }
        }
        return orderedMetrics.map { metric in
            let value = Trends.summary(Trends.line(metric, days: days), level: metric.level)
            return PillChoice(id: metric.id, label: metric.label, value: Trends.format(value, format: metric.format, compact: true), tint: color(for: metric))
        }
    }

    /// Metrics grouped by `group` in the order the groups first appear.
    private var orderedMetrics: [SeriesMetric] {
        var groups: [String] = []
        for metric in block.metrics where !groups.contains(metric.group ?? "") {
            groups.append(metric.group ?? "")
        }
        return groups.flatMap { group in block.metrics.filter { ($0.group ?? "") == group } }
    }

    private var current: String {
        if choices.contains(where: { $0.id == selection }) { return selection }
        return choices.first?.id ?? ""
    }

    private var lines: [TrendLine] {
        let days = days
        if byPlatform {
            let metrics = block.metrics.filter { $0.statKey == current }
            let platforms = Array(Set(metrics.map { $0.platform ?? "" })).sorted { PlatformStyle.sortIndex($0) < PlatformStyle.sortIndex($1) }
            return platforms.map { platform in
                let mine = metrics.filter { ($0.platform ?? "") == platform }
                return TrendLine(id: platform, name: PlatformStyle.name(platform), color: PlatformStyle.color(platform, hex: mine.first?.color),
                                 values: Trends.combine(mine, days: days), format: mine.first?.format, level: mine.first?.level ?? false)
            }
        }
        guard let metric = block.metrics.first(where: { $0.id == current }) else { return [] }
        return [TrendLine(id: metric.id, name: metric.label, color: color(for: metric), values: Trends.line(metric, days: days),
                          format: metric.format, level: metric.level)]
    }

    private func color(for metric: SeriesMetric) -> Color {
        if let hex = metric.color, let color = Color(hex: hex) { return color }
        if let platform = metric.platform { return PlatformStyle.color(platform) }
        var groups: [String] = []
        for other in block.metrics where !groups.contains(other.group ?? "") {
            groups.append(other.group ?? "")
        }
        let index = groups.firstIndex(of: metric.group ?? "") ?? 0
        return index == 0 ? accent : OrbitalTheme.palette[index % OrbitalTheme.palette.count]
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            TrendCardHeader(
                eyebrow: eyebrow ?? (Insights.nonEmpty(block.product) ?? StatStyle.kindLabel(block.kind)),
                title: block.title,
                tint: accent,
                image: image,
                symbol: symbol,
                window: span,
                onOpen: onOpen
            )
            headline
            TrendChart(days: days, lines: lines)
            if byPlatform, lines.count > 1 {
                TrendLegend(lines: lines) { PlatformStyle.glyph($0.id) }
            }
            PillToggleRow(choices: choices, selection: Binding(get: { current }, set: { selection = $0 }))
            if let caption = block.caption {
                Text(caption)
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let checked = OrbitalDates.relative(block.asOf) {
                Text("Checked \(checked)")
                    .font(.caption2)
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
        }
        .orbitalCard(padding: 16, tint: accent)
    }

    @ViewBuilder
    private var headline: some View {
        let shown = lines
        let level = shown.first?.level ?? false
        let combined = shown.count == 1 ? shown[0].values : days.indices.map { index in
            let present = shown.compactMap { $0.values[index] }
            return present.isEmpty ? nil : present.reduce(0, +)
        }
        let total = Trends.summary(combined, level: level)
        let today = combined.last ?? nil
        let label = byPlatform ? StatStyle.label(current) : (block.metrics.first { $0.id == current }?.label ?? "")
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text(Trends.format(total, format: shown.first?.format))
                .font(.system(size: 34, weight: .bold, design: .rounded))
                .monospacedDigit()
                .foregroundStyle(total ?? 0 > 0 ? OrbitalTheme.text : OrbitalTheme.textSecondary)
                .contentTransition(.numericText())
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            VStack(alignment: .leading, spacing: 1) {
                Text(level ? "\(label) now" : "\(label), \(days.count) days")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .lineLimit(1)
                if !level {
                    Text("Today \(Trends.format(today, format: shown.first?.format))")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
            }
            Spacer(minLength: 0)
            if let change = Trends.change(combined, level: level), abs(change) > 0.0001 {
                DeltaPill(delta: (change > 0 ? "+" : "−") + Trends.format(abs(change), format: shown.first?.format, compact: true),
                          direction: change > 0 ? "up" : "down")
            }
        }
    }
}

// MARK: - One kind, added up across products

/// Every series of one kind, filtered by space, with a line per platform or per product.
struct RollupChartCard: View {
    let eyebrow: String
    var title: String?
    let tint: Color
    let kind: String
    let refs: [SeriesRef]
    let directory: SpaceDirectory
    /// Fixed to one space (a space page). Hides the space toggles.
    var lockedSpace: String?
    var defaultStat: String?
    var onOpen: (() -> Void)?
    @State private var space = ""
    @State private var stat = ""
    @State private var window = 7

    private var days: [Date] { Trends.days(window) }

    private var filtered: [SeriesRef] {
        // A space page already passes only its own series (tagged with the space, an alias such as
        // "Firm Binder LLC", or sitting on one of its sections), so there is nothing to narrow.
        if lockedSpace != nil { return refs }
        return refs.filter { space.isEmpty || directory.resolve($0.block.space) == space }
    }

    private var stats: [String] { StatStyle.present(filtered, kind: kind) }

    private var currentStat: String {
        if stats.contains(stat) { return stat }
        if let defaultStat, stats.contains(defaultStat) { return defaultStat }
        return stats.first ?? ""
    }

    private var byPlatform: Bool {
        filtered.contains { ref in ref.block.metrics.contains { $0.statKey == currentStat && $0.platform != nil } }
    }

    private var lines: [TrendLine] {
        let days = days
        let metrics = filtered.flatMap { ref in ref.block.metrics.filter { $0.statKey == currentStat }.map { (ref, $0) } }
        if byPlatform {
            let platforms = Array(Set(metrics.map { $0.1.platform ?? "" })).sorted { PlatformStyle.sortIndex($0) < PlatformStyle.sortIndex($1) }
            return platforms.map { platform in
                let mine = metrics.filter { ($0.1.platform ?? "") == platform }.map(\.1)
                return TrendLine(id: platform, name: PlatformStyle.name(platform), color: PlatformStyle.color(platform, hex: mine.first?.color),
                                 values: Trends.combine(mine, days: days), format: mine.first?.format, level: mine.first?.level ?? false)
            }
        }
        var order: [String] = []
        var grouped: [String: [SeriesMetric]] = [:]
        var names: [String: String] = [:]
        for (ref, metric) in metrics {
            let key = ref.productID.isEmpty ? ref.id : ref.productID
            if grouped[key] == nil { order.append(key) }
            grouped[key, default: []].append(metric)
            names[key] = directory.product(key)?.name ?? (Insights.nonEmpty(ref.block.product) ?? metric.label)
        }
        var out = order.map { key in
            let mine = grouped[key] ?? []
            return TrendLine(id: key, name: names[key] ?? key, color: directory.product(key)?.tint ?? OrbitalTheme.color(for: key, hex: mine.first?.color),
                             values: Trends.combine(mine, days: days), format: mine.first?.format, level: mine.first?.level ?? false)
        }
        // Products on one page share its accent; give each line its own color so they can be told apart.
        if Set(out.map(\.color)).count < out.count {
            for index in out.indices { out[index].color = OrbitalTheme.palette[index % OrbitalTheme.palette.count] }
        }
        return out
    }

    private var statChoices: [PillChoice] {
        let days = days
        return stats.map { stat in
            let metrics = filtered.flatMap { $0.block.metrics.filter { $0.statKey == stat } }
            let level = metrics.first?.level ?? false
            let value = Trends.summary(Trends.combine(metrics, days: days), level: level)
            return PillChoice(id: stat, label: StatStyle.label(stat), value: Trends.format(value, format: metrics.first?.format, compact: true), tint: tint)
        }
    }

    private var spaceChoices: [CircleChoice] {
        let present = Set(refs.map { directory.resolve($0.block.space) })
        return [CircleChoice.all()] + directory.spaces.filter { present.contains($0.id) }.map(CircleChoice.space)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            TrendCardHeader(eyebrow: eyebrow, title: title, tint: tint, window: $window, onOpen: onOpen)
            if refs.isEmpty {
                Text("Nothing has reported this yet. The chart fills in when its feed sends daily numbers.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .padding(.vertical, 8)
            } else {
                let shown = lines
                total(shown)
                TrendChart(days: days, lines: shown)
                if !shown.isEmpty {
                    TrendLegend(lines: shown) { byPlatform ? PlatformStyle.glyph($0.id) : nil }
                }
                if lockedSpace == nil, spaceChoices.count > 2 {
                    CircleToggleRow(choices: spaceChoices, selection: $space)
                }
                if statChoices.count > 1 {
                    PillToggleRow(choices: statChoices, selection: Binding(get: { currentStat }, set: { stat = $0 }))
                }
            }
        }
        .orbitalCard(padding: 16, tint: tint)
    }

    private func total(_ shown: [TrendLine]) -> some View {
        let level = shown.first?.level ?? false
        let sum = shown.compactMap { Trends.summary($0.values, level: level) }
        let value: Double? = sum.isEmpty ? nil : sum.reduce(0, +)
        return HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(Trends.format(value, format: shown.first?.format))
                .font(.system(size: 30, weight: .bold, design: .rounded))
                .monospacedDigit()
                .foregroundStyle(OrbitalTheme.text)
                .contentTransition(.numericText())
            Text(level ? "\(StatStyle.label(currentStat).lowercased()) now" : "\(StatStyle.label(currentStat).lowercased()), \(days.count) days")
                .font(.caption.weight(.semibold))
                .foregroundStyle(OrbitalTheme.textSecondary)
            Spacer(minLength: 0)
        }
    }
}
