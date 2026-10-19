import SwiftUI
import UIKit

// The docket block: hearings and deadlines with a live day count and a Ready toggle
// (docket.set_ready). The feed that owns the page writes the toggle back and pushes it again.

// MARK: - Dates

enum DocketDates {
    /// Whole days from today to a `YYYY-MM-DD` day, in the phone's calendar. Negative when it is past.
    static func daysUntil(_ raw: String?, now: Date = Date(), calendar: Calendar = .current) -> Int? {
        guard let raw, let day = Insights.day(raw, calendar: calendar) else { return nil }
        return calendar.dateComponents([.day], from: calendar.startOfDay(for: now), to: calendar.startOfDay(for: day)).day
    }

    private static let dayFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "EEE MMM d"
        return formatter
    }()

    private static let dayYearFormatter: DateFormatter = {
        let formatter = DateFormatter()
        formatter.dateFormat = "EEE MMM d, yyyy"
        return formatter
    }()

    /// "Wed Oct 21", with the year when it is not this year.
    static func dayLabel(_ raw: String) -> String? {
        guard let date = Insights.day(raw) else { return nil }
        let sameYear = Calendar.current.isDate(date, equalTo: Date(), toGranularity: .year)
        return (sameYear ? dayFormatter : dayYearFormatter).string(from: date)
    }

    /// Minutes after midnight for "9:00 AM", "2:30 pm", "14:30", or "9 AM". Nil when it is not a time.
    static func minutes(_ raw: String?) -> Int? {
        guard let text = raw?.trimmingCharacters(in: .whitespaces).uppercased(), !text.isEmpty else { return nil }
        let clock = text.prefix { "0123456789:".contains($0) }
        let parts = clock.split(separator: ":").compactMap { Int($0) }
        guard var hour = parts.first, hour <= 24 else { return nil }
        let minute = parts.count > 1 ? parts[1] : 0
        let rest = text.dropFirst(clock.count).replacingOccurrences(of: ".", with: "")
        if rest.contains("PM"), hour < 12 { hour += 12 }
        if rest.contains("AM"), hour == 12 { hour = 0 }
        return hour * 60 + minute
    }

    /// `YYYY-MM-DD` for a date in the phone's calendar.
    static func isoDay(_ date: Date, calendar: Calendar = .current) -> String {
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }

    /// Date, then time. A row with no time sorts first on its day.
    static func ordered(_ lhs: DocketItem, _ rhs: DocketItem) -> Bool {
        if lhs.date != rhs.date { return lhs.date < rhs.date }
        let left = minutes(lhs.time) ?? -1
        let right = minutes(rhs.time) ?? -1
        if left != right { return left < right }
        return lhs.title < rhs.title
    }

    /// "Wed Oct 21 · 9:00 AM · Room 2B"
    static func whenLine(_ item: DocketItem, includePlace: Bool = true) -> String {
        var parts: [String] = [dayLabel(item.date) ?? item.date]
        if let time = Insights.nonEmpty(item.time) { parts.append(time) }
        if includePlace, let place = Insights.nonEmpty(item.place) { parts.append(place) }
        return parts.joined(separator: " · ")
    }
}

// MARK: - Styling

enum DocketStyle {
    /// Docs: a hearing not marked ready is danger at 3 days or less, warning at 14 or less; a ready hearing is
    /// success; a deadline is danger at 3, warning at 7. Calm hearings use the default accent rather than the
    /// section accent, because a docket page's accent is often the warning yellow.
    static func urgency(_ item: DocketItem, days: Int?) -> Color {
        guard let days else { return OrbitalTheme.textSecondary }
        if days < 0 { return OrbitalTheme.textTertiary }
        if item.isDeadline {
            if days <= 3 { return OrbitalTheme.danger }
            if days <= 7 { return OrbitalTheme.warning }
            return OrbitalTheme.textSecondary
        }
        if item.ready { return OrbitalTheme.success }
        if !item.appearance { return OrbitalTheme.textSecondary }
        if days <= 3 { return OrbitalTheme.danger }
        if days <= 14 { return OrbitalTheme.warning }
        return OrbitalTheme.accent
    }

}

/// The big day count on the left of a docket row: "16 / days", "1 / day", "TODAY", "3d / ago".
struct DayCountBadge: View {
    let days: Int?
    let tint: Color
    var compact = false

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: compact ? 11 : 14, style: .continuous)
        VStack(spacing: compact ? 0 : 1) {
            switch days {
            case .none:
                number("–")
            case .some(0):
                Text("TODAY")
                    .font(.system(size: compact ? 10 : 12, weight: .heavy, design: .rounded))
                    .tracking(0.3)
                    .lineLimit(1)
                    .minimumScaleFactor(0.7)
            case .some(let value) where value < 0:
                number("\(-value)d")
                unit("ago")
            case .some(let value):
                number("\(value)")
                unit(value == 1 ? "day" : "days")
            }
        }
        .foregroundStyle(tint)
        .padding(.horizontal, 4)
        .frame(width: compact ? 46 : 58, height: compact ? 44 : 58)
        .background(tint.opacity(days == 0 ? 0.22 : 0.12), in: shape)
        .overlay(shape.strokeBorder(tint.opacity(days == 0 ? 0.5 : 0.28), lineWidth: 1))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label)
    }

    private func number(_ text: String) -> some View {
        Text(text)
            .font(.system(compact ? .headline : .title2, design: .rounded).weight(.bold))
            .monospacedDigit()
            .lineLimit(1)
            .minimumScaleFactor(0.55)
    }

    private func unit(_ text: String) -> some View {
        Text(text.uppercased())
            .font(.system(size: compact ? 8 : 9, weight: .bold))
            .tracking(0.6)
            .opacity(0.85)
    }

    private var label: String {
        guard let days else { return "No date" }
        switch days {
        case 0: return "Today"
        case 1: return "Tomorrow"
        case ..<0: return "\(-days) \(days == -1 ? "day" : "days") ago"
        default: return "In \(days) days"
        }
    }
}

// MARK: - Docket

struct DocketCard: View {
    let block: DocketBlock
    let accent: Color
    var location: PageLocation?
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter

    private var items: [DocketItem] { block.items.sorted(by: DocketDates.ordered) }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            if items.isEmpty {
                Text(Insights.nonEmpty(block.emptyText) ?? "Nothing on the calendar.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .padding(.vertical, 6)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(items.enumerated()), id: \.element.id) { index, item in
                        if index > 0 {
                            Rectangle().fill(OrbitalTheme.hairline.opacity(0.7)).frame(height: 1)
                        }
                        DocketRow(
                            item: item,
                            accent: accent,
                            openTarget: target(for: item),
                            canPost: location != nil,
                            onOpen: { open(item) },
                            onToggleReady: { toggleReady(item) }
                        )
                        .padding(.vertical, 12)
                    }
                }
            }
        }
        .orbitalCard(padding: 16)
    }

    @ViewBuilder
    private var header: some View {
        let title = Insights.nonEmpty(block.title)
        let updated = OrbitalDates.relative(block.asOf)
        if title != nil || updated != nil {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if let title {
                    Text(title)
                        .font(.headline)
                        .foregroundStyle(OrbitalTheme.text)
                }
                Spacer(minLength: 0)
                if let updated {
                    Text(updated)
                        .font(.caption2)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .lineLimit(1)
                }
            }
        }
    }

    /// The page a row opens, unless it is the page already on screen.
    private func target(for item: DocketItem) -> PageLocation? {
        guard let open = item.open?.location else { return nil }
        if let location, open.sectionID == location.sectionID, open.nodeID == location.nodeID,
           open.tabID == nil || open.tabID == location.tabID {
            return nil
        }
        return open
    }

    private func open(_ item: DocketItem) {
        guard let target = target(for: item) else { return }
        router.show(target, tab: target.tabID)
    }

    private func toggleReady(_ item: DocketItem) {
        guard let location else { return }
        let next = !item.ready
        if next {
            UINotificationFeedbackGenerator().notificationOccurred(.success)
        } else {
            UIImpactFeedbackGenerator(style: .light).impactOccurred()
        }
        Task {
            await store.perform(
                type: "docket.set_ready",
                sectionID: location.sectionID,
                itemID: item.id,
                blockID: block.id,
                nodeID: location.nodeID,
                tabID: location.tabID,
                value: ["ready": next]
            )
        }
    }
}

struct DocketRow: View {
    let item: DocketItem
    let accent: Color
    let openTarget: PageLocation?
    let canPost: Bool
    let onOpen: () -> Void
    let onToggleReady: () -> Void

    var body: some View {
        let days = DocketDates.daysUntil(item.date)
        let past = (days ?? 0) < 0
        let tint = DocketStyle.urgency(item, days: days)
        VStack(alignment: .leading, spacing: 12) {
            if openTarget != nil {
                Button(action: onOpen) {
                    content(days: days, tint: tint)
                }
                .buttonStyle(OrbitalPressStyle())
                .accessibilityHint("Opens the case page")
            } else {
                content(days: days, tint: tint)
            }
            if item.showsReady, canPost, !past {
                ReadyToggleButton(ready: item.ready, tint: tint, action: onToggleReady)
            }
        }
        .opacity(past ? 0.55 : 1)
    }

    private func content(days: Int?, tint: Color) -> some View {
        HStack(alignment: .top, spacing: 14) {
            DayCountBadge(days: days, tint: tint)
            VStack(alignment: .leading, spacing: 5) {
                Text(Insights.nonEmpty(item.title) ?? Insights.nonEmpty(item.caseName) ?? (item.isDeadline ? "Deadline" : "Hearing"))
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                if Insights.nonEmpty(item.caseName) != nil || Insights.nonEmpty(item.caseNumber) != nil {
                    FlowLayout(spacing: 6, lineSpacing: 4) {
                        if let name = Insights.nonEmpty(item.caseName) {
                            OrbitalChip(text: name, symbol: "folder.fill", tint: accent)
                        }
                        if let number = Insights.nonEmpty(item.caseNumber) {
                            Text(number)
                                .font(.caption2.monospaced().weight(.medium))
                                .foregroundStyle(OrbitalTheme.textSecondary)
                                .lineLimit(1)
                                .padding(.vertical, 4)
                        }
                    }
                }
                Text(DocketDates.whenLine(item))
                    .font(.caption.weight(.medium))
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .multilineTextAlignment(.leading)
                    .fixedSize(horizontal: false, vertical: true)
                if let detail = Insights.nonEmpty(item.detail) {
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textSecondary.opacity(0.85))
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if hasChips {
                    FlowLayout(spacing: 6, lineSpacing: 6) {
                        chips(tint: tint)
                    }
                    .padding(.top, 2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            if openTarget != nil {
                Image(systemName: "chevron.right")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .padding(.top, 4)
            }
        }
        .contentShape(Rectangle())
    }

    private var hasChips: Bool {
        item.pending || !item.confirmed || !item.appearance || item.isDeadline
            || (item.prepTotal ?? 0) > 0 || (!item.canReady && item.ready && item.isHearing)
    }

    @ViewBuilder
    private func chips(tint: Color) -> some View {
        if item.pending {
            SyncingChip()
        }
        if item.isDeadline {
            OrbitalChip(text: "Deadline", symbol: "flag.fill", tint: tint)
        }
        if !item.confirmed {
            OrbitalChip(text: "Unconfirmed", symbol: "questionmark.circle.fill", tint: OrbitalTheme.warning)
        }
        if !item.appearance {
            OrbitalChip(text: "No appearance", symbol: "person.fill.xmark", tint: OrbitalTheme.textSecondary)
        }
        if !item.canReady, item.ready, item.isHearing {
            OrbitalChip(text: "Ready", symbol: "checkmark.circle.fill", tint: OrbitalTheme.success)
        }
        if let total = item.prepTotal, total > 0 {
            let open = max(0, min(item.prepOpen ?? 0, total))
            if open == 0 {
                OrbitalChip(text: "All \(total) prep done", symbol: "checklist.checked", tint: OrbitalTheme.success)
            } else {
                OrbitalChip(text: "\(open) of \(total) prep open", symbol: "checklist", tint: OrbitalTheme.textSecondary)
            }
        }
    }
}

/// Full-width Ready toggle. Not ready: outlined in the urgency colour. Ready: filled green; tap again to undo.
struct ReadyToggleButton: View {
    let ready: Bool
    let tint: Color
    let action: () -> Void

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: OrbitalTheme.controlRadius, style: .continuous)
        Button(action: action) {
            HStack(spacing: 8) {
                Image(systemName: ready ? "checkmark.seal.fill" : "seal")
                    .font(.subheadline.weight(.bold))
                Text(ready ? "Ready" : "Mark ready")
                    .font(.subheadline.weight(.semibold))
                if ready {
                    Image(systemName: "checkmark")
                        .font(.caption.weight(.heavy))
                }
            }
            .foregroundStyle(ready ? OrbitalTheme.backgroundDeep : tint)
            .frame(maxWidth: .infinity, minHeight: 44)
            .background {
                if ready {
                    LinearGradient(colors: [OrbitalTheme.success, OrbitalTheme.success.opacity(0.8)], startPoint: .top, endPoint: .bottom)
                } else {
                    tint.opacity(0.08)
                }
            }
            .overlay(shape.strokeBorder(ready ? Color.white.opacity(0.18) : tint.opacity(0.55), lineWidth: 1))
            .clipShape(shape)
            .contentShape(shape)
        }
        .buttonStyle(OrbitalPressStyle())
        .animation(.easeInOut(duration: 0.2), value: ready)
        .accessibilityLabel(ready ? "Ready" : "Mark ready")
        .accessibilityHint(ready ? "Marks the hearing not ready" : "Marks the hearing ready")
    }
}

