import SwiftUI
import UIKit

// The marketing block's Automation pane: everything that runs on its own for marketing (scheduled agent
// tasks and scheduler jobs), when each ran and runs next, what is next on each list (posts, production,
// outreach, releases, experiments), and what agents are working on now. Drawn from the block's
// `automation` object, with scheduler jobs redrawn from the live scheduler picture.

private let automationTint = Color(hex: "#5AC8FA") ?? OrbitalTheme.accent

// MARK: - Ledger | Automation switch

struct MarketingPanePicker: View {
    @Binding var pane: String
    let issues: Int

    var body: some View {
        HStack(spacing: 4) {
            segment("ledger", title: "Ledger", symbol: "list.bullet.rectangle.portrait")
            segment("automation", title: "Automation", symbol: "gearshape.2.fill", badge: issues)
        }
        .padding(4)
        .background(Color.white.opacity(0.05), in: Capsule())
        .overlay(Capsule().strokeBorder(OrbitalTheme.hairline.opacity(0.8), lineWidth: 1))
    }

    private func segment(_ id: String, title: String, symbol: String, badge: Int = 0) -> some View {
        let selected = pane == id
        return Button {
            UISelectionFeedbackGenerator().selectionChanged()
            withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) { pane = id }
        } label: {
            HStack(spacing: 6) {
                Image(systemName: symbol)
                    .font(.system(size: 12, weight: .bold))
                Text(title)
                    .font(.subheadline.weight(.semibold))
                if badge > 0 {
                    Text("\(badge)")
                        .font(.caption2.weight(.heavy).monospacedDigit())
                        .foregroundStyle(OrbitalTheme.backgroundDeep)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 1)
                        .background(OrbitalTheme.warning, in: Capsule())
                        .accessibilityLabel("\(badge) need a look")
                }
            }
            .foregroundStyle(selected ? OrbitalTheme.text : OrbitalTheme.textSecondary)
            .frame(maxWidth: .infinity, minHeight: 40)
            .background(selected ? automationTint.opacity(0.24) : Color.clear, in: Capsule())
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
    }
}

// MARK: - The pane

struct MarketingAutomationPane: View {
    let automation: MarketingAutomation?
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @State private var issuesOnly = false
    @State private var opened: MarketingAutomationItem?
    @State private var openedNext: OpenedUpNext?
    @State private var openedWork: WorkingItem?
    @Environment(\.marketingBrands) private var brands

    var body: some View {
        if let automation {
            content(automation)
                .sheet(item: $opened) { item in
                    AutomationSheet(item: item, groupTitle: automation.groups.first { $0.id == item.group }?.title)
                        .environmentObject(store)
                        .environmentObject(router)
                }
                .sheet(item: $openedNext) { opened in
                    UpNextSheet(item: opened.item, section: opened.section)
                        .environment(\.marketingBrands, brands)
                }
                .sheet(item: $openedWork) { item in
                    WorkingSheet(item: item)
                }
                .task {
                    // Live countdowns for scheduler jobs: the socket pushes changes; this covers a dropped socket.
                    while !Task.isCancelled {
                        await store.refreshJobs()
                        try? await Task.sleep(nanoseconds: 30_000_000_000)
                    }
                }
        } else {
            VStack(alignment: .leading, spacing: 8) {
                Image(systemName: "gearshape.2")
                    .font(.title2)
                    .foregroundStyle(automationTint)
                Text("No automation picture yet")
                    .font(.headline)
                    .foregroundStyle(OrbitalTheme.text)
                Text("Every schedule, last run, next run, and queue lands here when the marketing feed sends an automation picture.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textSecondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .orbitalCard(padding: 16)
        }
    }

    private func content(_ automation: MarketingAutomation) -> some View {
        let rows = automation.live(store.jobs)
        let schedulerUp = store.jobs?.scheduler.up ?? automation.schedulerUp
        return VStack(alignment: .leading, spacing: 16) {
            AutomationStatusStrip(rows: rows, schedulerUp: schedulerUp, asOf: automation.asOf) { opened = $0 }
            if !automation.working.isEmpty {
                WorkingNowCard(items: automation.working, source: automation.workingSource) { openedWork = $0 }
            }
            sectionHeader("Up next", detail: "What each list does next, and what is holding it")
            ForEach(automation.upNext) { section in
                UpNextCard(section: section) { openedNext = OpenedUpNext(item: $0, section: section) }
            }
            if automation.upNext.isEmpty {
                Text("Nothing queued on any list.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .orbitalCard(padding: 14)
            }
            automationsHeader(rows)
            let shown = issuesOnly ? rows.filter(\.needsALook) : rows
            ForEach(automation.orderedGroups(shown)) { group in
                AutomationGroupCard(group: group, rows: shown.filter { $0.group == group.id }) { opened = $0 }
            }
            if shown.isEmpty {
                Text(issuesOnly ? "Nothing needs a look." : "No automations reported.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .orbitalCard(padding: 14)
            }
            footer(automation)
        }
    }

    private func sectionHeader(_ title: String, detail: String) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
                .font(.title3.weight(.bold))
                .foregroundStyle(OrbitalTheme.text)
            Text(detail)
                .font(.caption)
                .foregroundStyle(OrbitalTheme.textTertiary)
        }
        .padding(.top, 4)
    }

    private func automationsHeader(_ rows: [MarketingAutomationItem]) -> some View {
        let bad = rows.filter(\.needsALook).count
        return HStack(alignment: .firstTextBaseline) {
            sectionHeader("Automations", detail: "\(rows.count) that run on their own · tap one for its runs")
            Spacer(minLength: 0)
            if bad > 0 {
                Button {
                    UISelectionFeedbackGenerator().selectionChanged()
                    withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) { issuesOnly.toggle() }
                } label: {
                    OrbitalChip(text: issuesOnly ? "Show all" : "\(bad) need a look",
                                  symbol: issuesOnly ? "line.3.horizontal.decrease.circle.fill" : "exclamationmark.triangle.fill",
                                  tint: issuesOnly ? automationTint : OrbitalTheme.warning)
                        .frame(minHeight: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityHint(issuesOnly ? "Shows every automation" : "Shows only the ones that need a look")
            }
        }
    }

    private func footer(_ automation: MarketingAutomation) -> some View {
        let failed = automation.failedSources
        return VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "arrow.triangle.2.circlepath")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(OrbitalTheme.textTertiary)
                Text("Checked \(OrbitalDates.relative(automation.asOf) ?? "recently") by the marketing-ops feed (every 5 min, and when a queue or release changes). Scheduler jobs update live.")
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !failed.isEmpty {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "exclamationmark.circle")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(OrbitalTheme.warning)
                    Text("Read partly: " + failed.map { "\($0.name.replacingOccurrences(of: "_", with: " ")) (\($0.status))" }.joined(separator: "; "))
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }
}

struct OpenedUpNext: Identifiable {
    var id: String { "\(section.id)/\(item.id)" }
    let item: UpNextItem
    let section: UpNextSection
}

// MARK: - Status strip

struct AutomationStatusStrip: View {
    let rows: [MarketingAutomationItem]
    let schedulerUp: Bool?
    let asOf: String?
    let onOpen: (MarketingAutomationItem) -> Void

    var body: some View {
        let bad = rows.filter(\.needsALook)
        let running = rows.filter { $0.status == "running" }.count
        let paused = rows.filter(\.paused).count
        let down = schedulerUp == false
        let allGreen = bad.isEmpty && !down
        let tint = down ? OrbitalTheme.danger : (allGreen ? OrbitalTheme.success : OrbitalTheme.warning)
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .center, spacing: 12) {
                IconTile(symbol: allGreen ? "checkmark.seal.fill" : "exclamationmark.triangle.fill", tint: tint, size: 44, filled: true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(down ? "Scheduler is not running" : (allGreen ? "All green" : "\(bad.count) \(bad.count == 1 ? "issue" : "issues")"))
                        .font(.title3.weight(.bold))
                        .foregroundStyle(OrbitalTheme.text)
                    Text(countsLine(running: running, paused: paused))
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 0)
            }
            TimelineView(.periodic(from: .now, by: 30)) { context in
                if let soonest = soonest(context.date) {
                    HStack(spacing: 6) {
                        Image(systemName: "clock")
                            .font(.caption2.weight(.bold))
                        Text("Next up: \(soonest.name), \(AutomationTime.until(soonest.nextDate, now: context.date) ?? "soon")")
                            .lineLimit(2)
                    }
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.textTertiary)
                }
            }
            if !bad.isEmpty {
                VStack(spacing: 0) {
                    ForEach(Array(bad.prefix(6).enumerated()), id: \.element.id) { index, row in
                        if index > 0 {
                            Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                        }
                        Button { onOpen(row) } label: {
                            HStack(alignment: .top, spacing: 8) {
                                Image(systemName: row.health.symbol)
                                    .font(.system(size: 13, weight: .bold))
                                    .foregroundStyle(row.health.tint)
                                    .frame(width: 18)
                                    .padding(.top, 2)
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(row.name)
                                        .font(.footnote.weight(.semibold))
                                        .foregroundStyle(OrbitalTheme.text)
                                    if let issue = row.issues.first ?? row.lastSummary {
                                        Text(issue)
                                            .font(.caption)
                                            .foregroundStyle(row.health.tint)
                                            .lineLimit(2)
                                            .multilineTextAlignment(.leading)
                                    }
                                }
                                Spacer(minLength: 0)
                                Image(systemName: "chevron.right")
                                    .font(.caption2.weight(.bold))
                                    .foregroundStyle(OrbitalTheme.textTertiary)
                                    .padding(.top, 4)
                            }
                            .padding(.vertical, 8)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(OrbitalPressStyle())
                    }
                    if bad.count > 6 {
                        Text("and \(bad.count - 6) more below")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .padding(.top, 4)
                    }
                }
            }
        }
        .orbitalCard(padding: 16, tint: tint)
        .accessibilityElement(children: .contain)
    }

    private func countsLine(running: Int, paused: Int) -> String {
        var parts = ["\(rows.count) automations"]
        if running > 0 { parts.append("\(running) running now") }
        if paused > 0 { parts.append("\(paused) paused") }
        if let checked = OrbitalDates.relative(asOf) { parts.append("checked \(checked)") }
        return parts.joined(separator: " · ")
    }

    private func soonest(_ now: Date) -> MarketingAutomationItem? {
        rows.filter { !$0.paused && ($0.nextDate ?? .distantPast) > now }
            .filter { $0.jobID != "gameanalytics" && $0.jobID != "marketing-ops" }
            .min { ($0.nextDate ?? .distantFuture) < ($1.nextDate ?? .distantFuture) }
    }
}

// MARK: - Working now

struct WorkingNowCard: View {
    let items: [WorkingItem]
    let source: String?
    let onOpen: (WorkingItem) -> Void
    @State private var showsQuiet = false

    var body: some View {
        let active = items.filter { !$0.quiet }
        let quiet = items.filter(\.quiet)
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                OrbitalEyebrow(text: "Working now", color: automationTint)
                Spacer(minLength: 0)
                Text(source == "ledger" ? "Ledger actions, last 2 h" : "From the tracker")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            VStack(spacing: 0) {
                if active.isEmpty {
                    Text("Nothing active right now.")
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 6)
                }
                ForEach(Array(active.prefix(8).enumerated()), id: \.element.id) { index, item in
                    if index > 0 {
                        Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                    }
                    row(item)
                }
                if !quiet.isEmpty {
                    Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                    Button {
                        withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) { showsQuiet.toggle() }
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "moon.zzz")
                            Text("\(quiet.count) quiet for 3 h or more")
                            Spacer(minLength: 0)
                            Image(systemName: "chevron.down")
                                .font(.caption2.weight(.bold))
                                .rotationEffect(.degrees(showsQuiet ? 180 : 0))
                        }
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .frame(minHeight: 44)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    if showsQuiet {
                        ForEach(quiet.prefix(10)) { item in row(item) }
                    }
                }
            }
            .orbitalCard(padding: 12, tint: automationTint)
        }
    }

    private func row(_ item: WorkingItem) -> some View {
        Button { onOpen(item) } label: {
            HStack(alignment: .top, spacing: 10) {
                ZStack {
                    IconTile(symbol: symbol(item.agent), tint: item.quiet ? OrbitalTheme.textTertiary : automationTint, size: 32)
                    if !item.quiet {
                        Circle()
                            .fill(OrbitalTheme.success)
                            .frame(width: 8, height: 8)
                            .offset(x: 13, y: -13)
                    }
                }
                VStack(alignment: .leading, spacing: 3) {
                    Text(item.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                        .multilineTextAlignment(.leading)
                        .lineLimit(2)
                    Text(timeLine(item))
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(item.quiet ? OrbitalTheme.warning : OrbitalTheme.textTertiary)
                        .lineLimit(1)
                    if let note = item.note ?? item.details {
                        Text(note)
                            .font(.caption)
                            .foregroundStyle(OrbitalTheme.textSecondary)
                            .lineLimit(2)
                            .multilineTextAlignment(.leading)
                    }
                }
                Spacer(minLength: 0)
            }
            .padding(.vertical, 8)
            .contentShape(Rectangle())
        }
        .buttonStyle(OrbitalPressStyle())
    }

    private func symbol(_ agent: String?) -> String {
        switch agent?.lowercased() {
        case "grok": return "bolt.fill"
        case "chatgpt": return "bubble.left.fill"
        case "gemini": return "diamond.fill"
        default: return "sparkles"
        }
    }

    private func timeLine(_ item: WorkingItem) -> String {
        var parts: [String] = []
        if let agent = item.agent { parts.append(agent.capitalized) }
        if let started = OrbitalDates.relative(item.startedAt) { parts.append("started \(started)") }
        if item.quiet, let updated = OrbitalDates.relative(item.updatedAt) {
            parts.append("quiet since \(updated)")
        } else if let updated = OrbitalDates.relative(item.updatedAt), item.updatedAt != item.startedAt {
            parts.append("updated \(updated)")
        }
        if item.subagents > 0 { parts.append("\(item.subagents) helper\(item.subagents == 1 ? "" : "s") working") }
        return parts.joined(separator: " · ")
    }
}

// MARK: - Up next

struct UpNextCard: View {
    let section: UpNextSection
    @Environment(\.marketingBrands) private var brands
    let onOpen: (UpNextItem) -> Void
    @State private var expanded = false

    var body: some View {
        let limit = 5
        let shown = expanded ? section.items : Array(section.items.prefix(limit))
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .center, spacing: 10) {
                IconTile(symbol: section.symbol ?? "list.bullet", tint: MarketingBrand(section.brand, in: brands).tint, size: 30)
                VStack(alignment: .leading, spacing: 1) {
                    Text(section.title)
                        .font(.headline)
                        .foregroundStyle(OrbitalTheme.text)
                    if let note = section.note {
                        Text(note)
                            .font(.caption)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .lineLimit(2)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                Spacer(minLength: 0)
                if let brand = section.brand, !brand.isEmpty, brand != "both" {
                    MarketingBrandChip(brand: section.brand)
                }
            }
            VStack(spacing: 0) {
                ForEach(Array(shown.enumerated()), id: \.element.id) { index, item in
                    if index > 0 {
                        Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                    }
                    UpNextRow(item: item) { onOpen(item) }
                }
                if section.items.count > limit {
                    Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                    Button {
                        withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) { expanded.toggle() }
                    } label: {
                        HStack(spacing: 6) {
                            Text(expanded ? "Show fewer" : "\(section.items.count - limit) more")
                            Spacer(minLength: 0)
                            Image(systemName: "chevron.down")
                                .font(.caption2.weight(.bold))
                                .rotationEffect(.degrees(expanded ? 180 : 0))
                        }
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .frame(minHeight: 44)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            }
        }
        .orbitalCard(padding: 12)
    }
}

struct UpNextRow: View {
    let item: UpNextItem
    let onOpen: () -> Void

    var body: some View {
        let style = item.style
        Button(action: onOpen) {
            HStack(alignment: .top, spacing: 10) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(whenText)
                        .font(.caption.weight(.semibold).monospacedDigit())
                        .foregroundStyle(item.date == nil ? style.tint : OrbitalTheme.textSecondary)
                        .lineLimit(2)
                        .minimumScaleFactor(0.8)
                    if let until = AutomationTime.until(item.date), item.date.map({ $0 > Date() }) ?? false {
                        Text(until)
                            .font(.caption2)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                    }
                }
                .frame(width: 74, alignment: .leading)
                .padding(.top, 2)
                Image(systemName: style.symbol)
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(style.tint)
                    .frame(width: 18)
                    .padding(.top, 2)
                VStack(alignment: .leading, spacing: 3) {
                    Text(item.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                        .multilineTextAlignment(.leading)
                        .lineLimit(2)
                    if let line = item.reason ?? item.detail {
                        Text(line)
                            .font(.caption)
                            .foregroundStyle(item.status == "blocked" ? OrbitalTheme.danger.opacity(0.9) : OrbitalTheme.textSecondary)
                            .multilineTextAlignment(.leading)
                            .lineLimit(2)
                    }
                    if !item.checklist.isEmpty {
                        let done = item.checklist.filter(\.ok).count
                        Text("\(done) of \(item.checklist.count) ready")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(done == item.checklist.count ? OrbitalTheme.success : OrbitalTheme.warning)
                    }
                }
                Spacer(minLength: 0)
                if item.hasMore {
                    Image(systemName: "chevron.right")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .padding(.top, 4)
                }
            }
            .padding(.vertical, 8)
            .contentShape(Rectangle())
        }
        .buttonStyle(OrbitalPressStyle())
        .accessibilityElement(children: .combine)
    }

    private var whenText: String {
        if let date = item.date { return AutomationTime.clock(date) ?? "" }
        switch item.status {
        case "blocked": return "Blocked"
        case "waiting": return "On you"
        case "paused": return "Paused"
        case "ready": return "Next run"
        case "done": return "Done"
        default: return ""
        }
    }
}

// MARK: - Automations

struct AutomationGroupCard: View {
    let group: AutomationGroup
    let rows: [MarketingAutomationItem]
    let onOpen: (MarketingAutomationItem) -> Void

    var body: some View {
        let bad = rows.filter(\.needsALook).count
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Image(systemName: group.symbol ?? "square.stack.3d.up")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(automationTint)
                OrbitalEyebrow(text: group.title)
                Spacer(minLength: 0)
                Text(bad == 0 ? "\(rows.count) · all OK" : "\(rows.count) · \(bad) need a look")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(bad == 0 ? OrbitalTheme.textTertiary : OrbitalTheme.warning)
            }
            VStack(spacing: 0) {
                ForEach(Array(rows.enumerated()), id: \.element.id) { index, row in
                    if index > 0 {
                        Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                    }
                    AutomationRow(row: row) { onOpen(row) }
                }
            }
            .orbitalCard(padding: 12)
        }
    }
}

struct AutomationRow: View {
    let row: MarketingAutomationItem
    let onOpen: () -> Void

    var body: some View {
        let health = row.health
        Button(action: onOpen) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: health.symbol)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(health.tint)
                    .symbolEffect(.pulse, isActive: row.status == "running")
                    .frame(width: 22, height: 22)
                    .padding(.top, 1)
                VStack(alignment: .leading, spacing: 3) {
                    Text(row.name)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                        .multilineTextAlignment(.leading)
                        .lineLimit(2)
                    if let purpose = row.purpose {
                        Text(purpose)
                            .font(.caption)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .multilineTextAlignment(.leading)
                            .lineLimit(1)
                    }
                    TimelineView(.periodic(from: .now, by: 30)) { context in
                        Text(AutomationRow.lastNext(row, now: context.date))
                            .font(.caption2.weight(.semibold).monospacedDigit())
                            .foregroundStyle(OrbitalTheme.textSecondary)
                            .lineLimit(2)
                    }
                    if let issue = row.issues.first, row.needsALook {
                        Text(issue)
                            .font(.caption)
                            .foregroundStyle(health.tint)
                            .multilineTextAlignment(.leading)
                            .lineLimit(2)
                    }
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right")
                    .font(.caption2.weight(.bold))
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .padding(.top, 6)
            }
            .padding(.vertical, 8)
            .contentShape(Rectangle())
        }
        .buttonStyle(OrbitalPressStyle())
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens its schedule, last run, and recent runs")
    }

    /// "Last 2:25 PM ✓ · Next 3:25 PM, in 12 min".
    static func lastNext(_ row: MarketingAutomationItem, now: Date) -> String {
        var parts: [String] = []
        if row.status == "running" {
            parts.append("Running now")
        } else if row.queued {
            parts.append("Queued to run")
        } else if let last = AutomationTime.clock(row.lastDate, now: now) {
            parts.append("Last \(last) \(mark(row.lastStatus))")
        } else {
            parts.append("Not run yet")
        }
        if row.paused {
            parts.append("Paused")
        } else if let date = row.nextDate, date <= now, row.status != "running" {
            parts.append("Next: due now")
        } else if let next = AutomationTime.clock(row.nextDate, now: now) {
            let until = AutomationTime.until(row.nextDate, now: now).map { ", \($0)" } ?? ""
            parts.append("Next \(next)\(until)")
        } else if row.isClaudeTask {
            parts.append("Next: set in the Claude app")
        }
        if row.errorStreak > 1 { parts.append("\(row.errorStreak) bad runs in a row") }
        return parts.joined(separator: " · ")
    }

    static func mark(_ status: String?) -> String {
        switch status {
        case "ok": return "✓"
        case "warn": return "⚠︎"
        case "error": return "✗"
        case "running": return "…"
        default: return ""
        }
    }
}

// MARK: - Sheets

struct AutomationSheet: View {
    let item: MarketingAutomationItem
    let groupTitle: String?
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @Environment(\.dismiss) private var dismiss
    @State private var showsFeed = false

    var body: some View {
        let row = item.live(store.jobs)
        let health = row.health
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(alignment: .top, spacing: 12) {
                        IconTile(symbol: health.symbol, tint: health.tint, size: 40, filled: true)
                        VStack(alignment: .leading, spacing: 6) {
                            Text(row.name)
                                .font(.headline)
                                .foregroundStyle(OrbitalTheme.text)
                                .fixedSize(horizontal: false, vertical: true)
                            FlowLayout(spacing: 6) {
                                OrbitalChip(text: health.word, symbol: health.symbol, tint: health.tint)
                                OrbitalChip(text: row.isClaudeTask ? "Claude task" : "Scheduler job",
                                              symbol: row.isClaudeTask ? "sparkles" : "clock.arrow.circlepath", tint: automationTint)
                                if let groupTitle { OrbitalChip(text: groupTitle) }
                            }
                        }
                    }
                    MarketingTextCard(title: "What it does", text: row.purpose)
                    TimelineView(.periodic(from: .now, by: 30)) { context in
                        MarketingFacts(rows: facts(row, now: context.date))
                    }
                    if !row.issues.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            OrbitalEyebrow(text: "Issues", color: health.tint)
                            VStack(alignment: .leading, spacing: 8) {
                                ForEach(row.issues, id: \.self) { issue in
                                    HStack(alignment: .top, spacing: 8) {
                                        Image(systemName: "exclamationmark.triangle.fill")
                                            .font(.caption.weight(.bold))
                                            .foregroundStyle(health.tint)
                                            .padding(.top, 2)
                                        Text(issue)
                                            .font(.subheadline)
                                            .foregroundStyle(OrbitalTheme.text)
                                            .textSelection(.enabled)
                                            .fixedSize(horizontal: false, vertical: true)
                                    }
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .orbitalCard(padding: 12, tint: health.tint)
                        }
                    }
                    MarketingTextCard(title: "Last run", text: row.lastSummary)
                    MarketingTextCard(title: "What the last run found", text: row.lastDetails)
                    if !row.runs.isEmpty {
                        runsCard(row)
                    }
                    if row.kind == "scheduler", let jobID = row.jobID {
                        VStack(spacing: 10) {
                            OrbitalActionButton(title: row.status == "running" || row.queued ? "Running…" : "Run now",
                                                  symbol: "arrow.clockwise", busy: row.status == "running" || row.queued) {
                                UIImpactFeedbackGenerator(style: .medium).impactOccurred()
                                Task { await store.runJob(jobID) }
                            }
                            OrbitalActionButton(title: "Feed controls and full log", symbol: "slider.horizontal.3", kind: .secondary) {
                                showsFeed = true
                            }
                        }
                    } else if row.isClaudeTask {
                        Text("Runs in the Claude app on this Mac. Its schedule here is copied from the app, and its runs are read from its own log.")
                            .font(.caption)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(16)
            }
            .background(OrbitalTheme.background.ignoresSafeArea())
            .navigationTitle(row.isClaudeTask ? "Claude task" : "Automation")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .preferredColorScheme(.dark)
        .sheet(isPresented: $showsFeed) {
            if let jobID = row.jobID {
                JobDetailView(jobID: jobID)
                    .environmentObject(store)
                    .environmentObject(router)
            }
        }
    }

    private func facts(_ row: MarketingAutomationItem, now: Date) -> [(String, String?)] {
        let next = row.paused ? "Paused" : AutomationTime.clock(row.nextDate, now: now).map { time in
            [time, AutomationTime.until(row.nextDate, now: now)].compactMap { $0 }.joined(separator: " · ")
        }
        let last = AutomationTime.clock(row.lastDate, now: now).map { time in
            [time, AutomationHealth.run(row.lastStatus).word, JobFormat.duration(row.lastDurationS)].compactMap { $0 }.joined(separator: " · ")
        }
        return [
            ("Schedule", row.schedule),
            ("Next run", next),
            ("Last run", last),
            ("Failing since", OrbitalDates.shortDateTime(row.failingSince)),
            ("In a row", row.errorStreak > 0 ? "\(row.errorStreak) \(row.errorStreak == 1 ? "run" : "runs") with problems" : nil),
            ("Log", row.log),
        ]
    }

    private func runsCard(_ row: MarketingAutomationItem) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            OrbitalEyebrow(text: "Recent runs")
            HStack(spacing: 3) {
                ForEach(Array(row.runs.prefix(12).reversed().enumerated()), id: \.offset) { _, run in
                    RoundedRectangle(cornerRadius: 3, style: .continuous)
                        .fill(AutomationHealth.run(run.status).tint.opacity(run.status == "ok" ? 0.8 : 1))
                        .frame(height: 14)
                }
            }
            .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 0) {
                ForEach(Array(row.runs.prefix(10).enumerated()), id: \.offset) { index, run in
                    if index > 0 {
                        Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                    }
                    let style = AutomationHealth.run(run.status)
                    HStack(alignment: .top, spacing: 8) {
                        Circle()
                            .fill(style.tint)
                            .frame(width: 8, height: 8)
                            .padding(.top, 5)
                        VStack(alignment: .leading, spacing: 2) {
                            Text([AutomationTime.clock(OrbitalDates.parse(run.at)), style.word, JobFormat.duration(run.durationS)]
                                .compactMap { $0 }.joined(separator: " · "))
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(OrbitalTheme.textSecondary)
                            if let summary = run.summary {
                                Text(summary)
                                    .font(.caption)
                                    .foregroundStyle(OrbitalTheme.textTertiary)
                                    .lineLimit(3)
                                    .textSelection(.enabled)
                            }
                        }
                        Spacer(minLength: 0)
                    }
                    .padding(.vertical, 7)
                }
            }
            .orbitalCard(padding: 12)
        }
    }
}

struct UpNextSheet: View {
    let item: UpNextItem
    let section: UpNextSection
    @Environment(\.dismiss) private var dismiss
    @State private var copied = false

    var body: some View {
        let style = item.style
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(alignment: .top, spacing: 12) {
                        IconTile(symbol: style.symbol, tint: style.tint, size: 40, filled: true)
                        VStack(alignment: .leading, spacing: 6) {
                            Text(item.title)
                                .font(.headline)
                                .foregroundStyle(OrbitalTheme.text)
                                .fixedSize(horizontal: false, vertical: true)
                            FlowLayout(spacing: 6) {
                                OrbitalChip(text: style.word, symbol: style.symbol, tint: style.tint)
                                OrbitalChip(text: section.title, symbol: section.symbol, tint: automationTint)
                                if let stage = item.stage {
                                    OrbitalChip(text: "Stage: \(stage.replacingOccurrences(of: "_", with: " "))")
                                }
                                if let brand = item.brand ?? section.brand, brand != "both" {
                                    MarketingBrandChip(brand: brand)
                                }
                            }
                        }
                    }
                    TimelineView(.periodic(from: .now, by: 30)) { context in
                        MarketingFacts(rows: [
                            ("When", item.date.map { date in
                                [date.formatted(.dateTime.weekday(.wide).day().month(.wide).hour().minute()),
                                 AutomationTime.until(date, now: context.date)].compactMap { $0 }.joined(separator: " · ")
                            } ?? item.when.map { $0.prefix(1).uppercased() + $0.dropFirst() }),
                        ])
                    }
                    MarketingTextCard(title: item.status == "blocked" ? "What holds it" : "Why", text: item.reason)
                    MarketingTextCard(title: "Details", text: item.detail)
                    if !item.checklist.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            OrbitalEyebrow(text: "Readiness (\(item.checklist.filter(\.ok).count) of \(item.checklist.count))")
                            VStack(alignment: .leading, spacing: 0) {
                                ForEach(Array(item.checklist.enumerated()), id: \.offset) { index, check in
                                    if index > 0 {
                                        Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                                    }
                                    HStack(alignment: .top, spacing: 8) {
                                        Image(systemName: check.ok ? "checkmark.circle.fill" : "xmark.circle")
                                            .foregroundStyle(check.ok ? OrbitalTheme.success : OrbitalTheme.danger)
                                            .padding(.top, 1)
                                        VStack(alignment: .leading, spacing: 2) {
                                            Text(check.label)
                                                .font(.footnote)
                                                .foregroundStyle(OrbitalTheme.text)
                                                .fixedSize(horizontal: false, vertical: true)
                                            if let note = check.note, !check.ok {
                                                Text(note)
                                                    .font(.caption)
                                                    .foregroundStyle(OrbitalTheme.textTertiary)
                                                    .fixedSize(horizontal: false, vertical: true)
                                            }
                                        }
                                    }
                                    .padding(.vertical, 7)
                                    .accessibilityElement(children: .combine)
                                    .accessibilityLabel("\(check.ok ? "Done" : "Not done"): \(check.label)")
                                }
                            }
                            .orbitalCard(padding: 12)
                        }
                    }
                    if let command = item.command {
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                OrbitalEyebrow(text: "Command")
                                Spacer(minLength: 0)
                                Button {
                                    UIPasteboard.general.string = command
                                    UINotificationFeedbackGenerator().notificationOccurred(.success)
                                    copied = true
                                } label: {
                                    Label(copied ? "Copied" : "Copy", systemImage: copied ? "checkmark" : "doc.on.doc")
                                        .font(.caption.weight(.semibold))
                                        .foregroundStyle(automationTint)
                                        .frame(minHeight: 44)
                                }
                                .buttonStyle(.plain)
                            }
                            Text(command)
                                .font(.caption.monospaced())
                                .foregroundStyle(OrbitalTheme.text.opacity(0.9))
                                .textSelection(.enabled)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .orbitalCard(padding: 12)
                        }
                    }
                    MarketingTextCard(title: "Then", text: item.then)
                    MarketingLinks(links: item.link.map { [$0] } ?? [])
                    if let note = section.note {
                        Text(note)
                            .font(.caption)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                }
                .padding(16)
            }
            .background(OrbitalTheme.background.ignoresSafeArea())
            .navigationTitle(section.title)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .preferredColorScheme(.dark)
    }
}

struct WorkingSheet: View {
    let item: WorkingItem
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(alignment: .top, spacing: 12) {
                        IconTile(symbol: "sparkles", tint: item.quiet ? OrbitalTheme.textTertiary : automationTint, size: 40, filled: true)
                        VStack(alignment: .leading, spacing: 6) {
                            Text(item.title)
                                .font(.headline)
                                .foregroundStyle(OrbitalTheme.text)
                                .fixedSize(horizontal: false, vertical: true)
                            FlowLayout(spacing: 6) {
                                if let agent = item.agent { OrbitalChip(text: agent.capitalized, symbol: "sparkles", tint: automationTint) }
                                OrbitalChip(text: item.source == "ledger" ? "Ledger action" : "Agent tracker")
                                if item.quiet { OrbitalChip(text: "Quiet", symbol: "moon.zzz.fill", tint: OrbitalTheme.warning) }
                            }
                        }
                    }
                    MarketingFacts(rows: [
                        ("Started", OrbitalDates.parse(item.startedAt).map { $0.formatted(.dateTime.weekday(.wide).day().month(.wide).hour().minute()) }),
                        ("Last update", OrbitalDates.relative(item.updatedAt)),
                        ("Helpers working", item.subagents > 0 ? "\(item.subagents)" : nil),
                    ])
                    MarketingTextCard(title: "Latest note", text: item.note)
                    MarketingTextCard(title: "What it is", text: item.details)
                    if !item.subagentTitles.isEmpty {
                        MarketingTextCard(title: "Helpers", text: item.subagentTitles.map { "• \($0)" }.joined(separator: "\n"))
                    }
                    Text("The Tracker tab has every item with its screenshots.")
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
                .padding(16)
            }
            .background(OrbitalTheme.background.ignoresSafeArea())
            .navigationTitle("Working now")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .preferredColorScheme(.dark)
    }
}
