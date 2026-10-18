import Charts
import SwiftUI

// Money pieces shared by a ledger block on its own page and the money card on space and product pages.

struct MoneyBar: Identifiable {
    var id: String { "\(day.timeIntervalSince1970)-\(group)" }
    var day: Date
    var group: String
    var amount: Double
    var inPeriod: Bool
}

/// Daily bars for the last 30 days (back to the 1st when the month is longer), stacked by product.
/// Days inside `highlight` are full strength. The biggest earner is green, the rest follow the palette.
struct MoneyChart: View {
    let rows: [MoneyRow]
    var currency: String = "USD"
    var highlight: ClosedRange<Date>?
    var height: CGFloat = 180

    var body: some View {
        let window = Self.window()
        let days = (Calendar.current.dateComponents([.day], from: window.lowerBound, to: window.upperBound).day ?? 29) + 1
        let bars = bars(in: window)
        let groups = orderedGroups(bars)
        if bars.allSatisfy({ abs($0.amount) < 0.005 }) {
            VStack(spacing: 6) {
                Image(systemName: "chart.bar.xaxis")
                    .font(.title3)
                Text("No money recorded in the last \(days) days.")
                    .font(.footnote)
            }
            .foregroundStyle(OrbitalTheme.textTertiary)
            .frame(maxWidth: .infinity, minHeight: height * 0.6)
        } else {
            Chart(bars) { bar in
                BarMark(
                    x: .value("Day", bar.day, unit: .day),
                    y: .value("Amount", bar.amount)
                )
                .foregroundStyle(by: .value("Group", bar.group))
                .opacity(bar.inPeriod ? 1 : 0.3)
                .cornerRadius(3)
            }
            .chartForegroundStyleScale(domain: groups, range: groups.indices.map { OrbitalTheme.palette[($0 == 0 ? 1 : ($0 == 1 ? 0 : $0)) % OrbitalTheme.palette.count] })
            .chartLegend(.hidden)
            .chartXScale(domain: window.lowerBound ... (Calendar.current.date(byAdding: .day, value: 1, to: window.upperBound) ?? window.upperBound))
            .chartXAxis {
                AxisMarks(values: .stride(by: .day, count: max(1, days / 4))) { _ in
                    AxisValueLabel(format: .dateTime.day().month(.abbreviated), centered: true)
                        .font(.caption2)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
            }
            .chartYAxis {
                AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) { value in
                    AxisGridLine(stroke: StrokeStyle(lineWidth: 1, dash: [3, 4]))
                        .foregroundStyle(OrbitalTheme.hairline.opacity(0.6))
                    AxisValueLabel {
                        if let amount = value.as(Double.self) {
                            Text(OrbitalMoney.format(amount, currency: currency, compact: true))
                                .font(.caption2)
                                .foregroundStyle(OrbitalTheme.textTertiary)
                        }
                    }
                }
            }
            .frame(height: height)
            .accessibilityLabel("Daily money for the last \(days) days")
        }
    }

    /// The last 30 days, stretched back to the 1st when the month is longer, so This month is always whole.
    static func window(now: Date = Date(), calendar: Calendar = .current) -> ClosedRange<Date> {
        let today = calendar.startOfDay(for: now)
        let thirty = calendar.date(byAdding: .day, value: -29, to: today) ?? today
        let monthStart = MoneyPeriod.month.range(now: now, calendar: calendar).lowerBound
        return min(thirty, monthStart) ... today
    }

    private func bars(in window: ClosedRange<Date>) -> [MoneyBar] {
        var sums: [String: MoneyBar] = [:]
        for row in rows where window.contains(row.day) {
            let key = "\(row.day.timeIntervalSince1970)|\(row.product)"
            var bar = sums[key] ?? MoneyBar(day: row.day, group: row.product, amount: 0, inPeriod: highlight?.contains(row.day) ?? true)
            bar.amount += row.amount
            sums[key] = bar
        }
        return sums.values.sorted { $0.day < $1.day }
    }

    private func orderedGroups(_ bars: [MoneyBar]) -> [String] {
        var totals: [String: Double] = [:]
        for bar in bars { totals[bar.group, default: 0] += bar.amount }
        return totals.sorted { $0.value > $1.value }.map(\.key)
    }
}

struct MoneyEntryRow: View {
    let entry: MoneyEntry

    var body: some View {
        HStack(spacing: 12) {
            AppIcon(
                image: entry.ledger.iconImage,
                symbol: entry.ledger.symbol,
                tint: OrbitalTheme.accentColor(entry.ledger.accent),
                size: 32
            )
            VStack(alignment: .leading, spacing: 2) {
                Text(entry.label ?? entry.product)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
                    .lineLimit(1)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 2) {
                Text(OrbitalMoney.format(entry.amount, currency: entry.ledger.currency))
                    .font(.subheadline.weight(.bold).monospacedDigit())
                    .foregroundStyle(entry.amount < 0 ? OrbitalTheme.danger : OrbitalTheme.success)
                if let status = entry.status, !["succeeded", "paid", "ok"].contains(status.lowercased()) {
                    Text(status.capitalized)
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.warning)
                } else {
                    Text(when)
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
            }
        }
        .padding(.vertical, 6)
        .accessibilityElement(children: .combine)
    }

    private var detail: String {
        entry.label == nil ? "\(entry.category) · \(when)" : "\(entry.product) · \(entry.category)"
    }

    private var when: String {
        entry.at.formatted(Calendar.current.isDateInToday(entry.at) ? .dateTime.hour().minute() : .dateTime.day().month(.abbreviated))
    }
}

/// Today, this week, this month, and 30 days side by side.
struct PeriodTotals: View {
    let rows: [MoneyRow]
    var currency: String = "USD"
    var compact = false

    var body: some View {
        HStack(spacing: 8) {
            ForEach(MoneyPeriod.allCases) { period in
                let total = Insights.total(rows, in: period.range())
                VStack(alignment: .leading, spacing: 2) {
                    Text((compact ? period.short : period.rawValue).uppercased())
                        .font(.system(size: 9.5, weight: .bold))
                        .tracking(0.5)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .lineLimit(1)
                    Text(OrbitalMoney.format(total, currency: currency))
                        .font(.system(compact ? .headline : .title3, design: .rounded).weight(.bold))
                        .monospacedDigit()
                        .foregroundStyle(total > 0 ? OrbitalTheme.text : OrbitalTheme.textSecondary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.6)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(10)
                .background(Color.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            }
        }
    }
}

/// A ledger block drawn on its own page.
struct LedgerCard: View {
    let block: LedgerBlock
    let accent: Color
    var location: PageLocation?
    @EnvironmentObject private var store: OrbitalStore

    private var ref: LedgerRef? {
        guard let location else { return nil }
        return store.ledgers.first { $0.location == location && $0.block.id == block.id }
    }

    var body: some View {
        let ledger = ref
        let rows = ledger.map { Insights.rows([$0], currency: block.currency) } ?? []
        let entries = ledger.map { Insights.entries([$0], currency: block.currency) } ?? []
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 8) {
                OrbitalEyebrow(text: [block.source, block.space].compactMap { $0 }.joined(separator: " · "), color: accent)
                Spacer(minLength: 0)
                if block.connected {
                    StatusPill(text: "Connected", status: "live")
                } else {
                    StatusPill(text: "Not connected", status: "pending")
                }
            }
            if let title = block.title, !title.isEmpty {
                Text(title)
                    .font(.headline)
                    .foregroundStyle(OrbitalTheme.text)
            }
            if !block.connected {
                Text(block.note ?? "This source is not readable yet. No amount is shown.")
                    .font(.subheadline)
                    .foregroundStyle(OrbitalTheme.text.opacity(0.88))
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                PeriodTotals(rows: rows, currency: block.currency, compact: true)
                if rows.contains(where: { abs($0.amount) > 0.004 }) {
                    MoneyChart(rows: rows, currency: block.currency, highlight: MoneyPeriod.month.range(), height: 140)
                }
                if let note = block.note, !note.isEmpty {
                    Text(note)
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if !block.metrics.isEmpty {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 104), spacing: 8)], alignment: .leading, spacing: 8) {
                    ForEach(block.metrics) { MiniMetric(tile: $0) }
                }
            }
            if !entries.isEmpty {
                VStack(spacing: 0) {
                    ForEach(entries.prefix(5)) { entry in
                        MoneyEntryRow(entry: entry)
                    }
                }
            }
            CheckedLine(raw: block.asOf)
        }
        .orbitalCard(padding: 16, tint: block.connected ? accent : OrbitalTheme.warning)
    }
}
