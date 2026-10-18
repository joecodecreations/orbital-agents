import SwiftUI
import UIKit

// Cards on Home: the header menu, what needs attention, agent activity, pinned sections, space tiles,
// the scheduler's one-line summary, and the money card the space and product pages share.

// MARK: - Header menu

/// The menu every iPhone root screen carries for screens that are not tabs.
struct ShellHeaderButtons: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter

    var body: some View {
        if router.isCompact {
            Menu {
                Button { router.openBrowse() } label: { Label("All sections", systemImage: "square.grid.2x2") }
                Button { router.runAgent() } label: { Label("Run an agent", systemImage: "play.circle") }
                if store.jobs != nil {
                    Button { router.openAutomations() } label: { Label("Automations", systemImage: "clock.arrow.2.circlepath") }
                }
                Button { router.openManage() } label: { Label("Manage sections", systemImage: "slider.horizontal.3") }
                Button { router.openSettings() } label: { Label("Settings", systemImage: "gearshape") }
            } label: {
                Image(systemName: "ellipsis")
                    .font(.subheadline.weight(.bold))
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .frame(width: 38, height: 38)
                    .background(Color.white.opacity(0.06), in: Circle())
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .accessibilityLabel("More")
        }
    }
}

// MARK: - Needs attention

/// Everything urgent across the hub, most pressing first. "All clear" when there is nothing.
struct AttentionCard: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter

    struct Item: Identifiable {
        var id: String
        var symbol: String
        var tone: String
        var title: String
        var detail: String
        var image: String?
        var fallback: String
        var accent: Color
        var action: () -> Void
    }

    var body: some View {
        let items = self.items
        VStack(alignment: .leading, spacing: 10) {
            if let jobs = store.jobs, jobs.needsALook {
                AutomationsHomeCard(snapshot: jobs)
            }
            if items.isEmpty {
                if !(store.jobs?.needsALook ?? false) {
                    HStack(spacing: 10) {
                        Image(systemName: "checkmark.seal.fill")
                            .foregroundStyle(OrbitalTheme.success)
                        Text("All clear. Nothing needs you right now.")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(OrbitalTheme.text)
                        Spacer(minLength: 0)
                    }
                    .orbitalCard(padding: 14, tint: OrbitalTheme.success)
                }
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        OrbitalEyebrow(text: "Needs a look · \(items.count)", color: OrbitalTheme.warning)
                        Spacer(minLength: 0)
                    }
                    .padding(.bottom, 4)
                    ForEach(Array(items.prefix(8).enumerated()), id: \.element.id) { index, item in
                        if index > 0 {
                            Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                        }
                        row(item)
                    }
                    if items.count > 8 {
                        Text("\(items.count - 8) more in Alerts, Agents, and the pages they come from")
                            .font(.caption)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .padding(.top, 4)
                    }
                }
                .orbitalCard(padding: 14, tint: OrbitalTheme.warning)
            }
        }
    }

    private func row(_ item: Item) -> some View {
        Button(action: item.action) {
            HStack(spacing: 12) {
                ZStack(alignment: .bottomTrailing) {
                    AppIcon(image: item.image, symbol: item.fallback, tint: item.accent, size: 34)
                    Image(systemName: item.symbol)
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(OrbitalTheme.backgroundDeep)
                        .frame(width: 18, height: 18)
                        .background(OrbitalTheme.toneColor(item.tone), in: Circle())
                        .overlay(Circle().strokeBorder(OrbitalTheme.surface, lineWidth: 2))
                        .offset(x: 5, y: 5)
                }
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                    Text(item.detail)
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            .padding(.vertical, 8)
            .contentShape(Rectangle())
        }
        .buttonStyle(OrbitalPressStyle())
    }

    private var items: [Item] {
        var out: [Item] = []
        let now = Date()

        // Unread alerts, urgent ones first.
        let waiting = store.alerts.filter { $0.isWaiting(at: now) }
        for alert in waiting.prefix(3) {
            let entry = store.entry(alert.sectionId)
            out.append(Item(
                id: "alert-\(alert.id)",
                symbol: "bell.badge.fill",
                tone: alert.interruptionLevel == "critical" || alert.interruptionLevel == "timeSensitive" ? "danger" : "info",
                title: alert.title,
                detail: [entry?.title, OrbitalDates.relative(alert.createdAt)].compactMap { $0 }.joined(separator: " · "),
                image: entry?.iconImage,
                fallback: entry?.icon ?? "bell.fill",
                accent: OrbitalTheme.accentColor(entry?.accent),
                action: { open(alert) }
            ))
        }
        if waiting.count > 3 {
            out.append(Item(
                id: "alerts-more",
                symbol: "tray.full.fill",
                tone: "info",
                title: "\(waiting.count - 3) more unread \(waiting.count - 3 == 1 ? "alert" : "alerts")",
                detail: "Open the inbox",
                image: nil,
                fallback: "bell.fill",
                accent: OrbitalTheme.accent,
                action: { router.openAlerts() }
            ))
        }

        // Machines that stopped answering.
        for machine in store.satellites where !machine.isOnline {
            out.append(Item(
                id: "satellite-\(machine.id)",
                symbol: "wifi.slash",
                tone: "warning",
                title: "\(machine.name) is offline",
                detail: "Last seen \(OrbitalDates.relative(machine.lastSeen) ?? "a while ago"). Agents can't start there until it is back.",
                image: nil,
                fallback: machine.symbol,
                accent: OrbitalTheme.textSecondary,
                action: { router.openAgents(route: .satellite(machine.id)) }
            ))
        }

        // Agent jobs that failed in the last day.
        for job in store.agentJobs where job.isFailed && (job.finishedDate.map { now.timeIntervalSince($0) < 86_400 } ?? false) {
            out.append(Item(
                id: "job-\(job.id)",
                symbol: "xmark",
                tone: "danger",
                title: "\(AgentStyle.displayName(job.agent)) failed: \(job.shownTitle)",
                detail: [job.machine, OrbitalDates.relative(job.finishedAt)].compactMap { $0 }.joined(separator: " · "),
                image: nil,
                fallback: AgentStyle.symbol(job.agent),
                accent: AgentStyle.color(job.agent),
                action: { router.openAgents(route: .agentJob(job.id)) }
            ))
        }

        // Services down, from status blocks on any page.
        for problem in statusProblems {
            out.append(problem)
        }

        // To-dos due now or overdue.
        for row in TaskPriority.ordered(store.tasks, now: now).filter({ $0.tier.isUrgent }).prefix(4) {
            let entry = store.entry(row.ref.location.sectionID)
            out.append(Item(
                id: "task-\(row.ref.id)",
                symbol: "checklist",
                tone: row.tier <= .today ? "danger" : "warning",
                title: row.ref.item.title,
                detail: [DayMath.due(row.ref.item)?.text, entry?.title].compactMap { $0 }.joined(separator: " · "),
                image: entry?.iconImage,
                fallback: entry?.icon ?? "checklist",
                accent: OrbitalTheme.accentColor(entry?.accent),
                action: { router.show(row.ref.location, tab: row.ref.location.tabID) }
            ))
        }

        // Releases stuck or rejected, and products that stopped checking in.
        for product in store.products {
            for (index, release) in product.block.releases.enumerated() where release.status == "rejected" || release.status == "pending" {
                let storeName = release.store ?? ReleaseStyle.platformName(release.platform)
                var detail = [release.version, release.state?.lowercased(), "on \(storeName)"].compactMap { $0 }.joined(separator: " ")
                if let since = OrbitalDates.shortDay(release.since) { detail += " · since \(since)" }
                out.append(Item(
                    id: "release-\(product.id)-\(index)",
                    symbol: ReleaseStyle.symbol(release.status),
                    tone: release.status == "rejected" ? "danger" : "warning",
                    title: "\(product.title) on \(ReleaseStyle.platformName(release.platform))",
                    detail: detail,
                    image: product.iconImage,
                    fallback: product.symbol,
                    accent: OrbitalTheme.accentColor(product.accent),
                    action: { router.show(product.location, tab: product.location.tabID) }
                ))
            }
            if product.isStale {
                out.append(Item(
                    id: "stale-\(product.id)",
                    symbol: "exclamationmark.arrow.circlepath",
                    tone: "warning",
                    title: "\(product.title) has not checked in",
                    detail: "Last check \(OrbitalDates.relative(product.block.checkedAt) ?? "unknown").",
                    image: product.iconImage,
                    fallback: product.symbol,
                    accent: OrbitalTheme.accentColor(product.accent),
                    action: { router.show(product.location, tab: product.location.tabID) }
                ))
            }
        }

        let offline = store.ledgers.filter { !$0.block.connected }
        if !offline.isEmpty {
            out.append(Item(
                id: "money-offline",
                symbol: "dollarsign.circle",
                tone: "info",
                title: "\(offline.count) money \(offline.count == 1 ? "source is" : "sources are") not connected",
                detail: offline.map(\.title).prefix(3).joined(separator: ", ") + (offline.count > 3 ? "…" : ""),
                image: nil,
                fallback: "dollarsign.circle",
                accent: OrbitalTheme.success,
                action: { router.show(offline[0].location, tab: offline[0].location.tabID) }
            ))
        }
        return out
    }

    /// One row per status block that has something down or degraded.
    private var statusProblems: [Item] {
        var out: [Item] = []
        for entry in store.sortedCatalog {
            guard let section = store.sectionsByID[entry.id] else { continue }
            for page in section.pages {
                for block in page.blocks {
                    guard case .status(let status) = block, status.downCount + status.degradedCount > 0 else { continue }
                    let bad = status.items.filter { $0.health == "down" || $0.health == "degraded" }
                    let location = PageLocation(sectionID: section.id, nodeID: page.node?.id, tabID: page.tabID)
                    out.append(Item(
                        id: "status-\(section.id)-\(page.node?.id ?? "-")-\(page.tabID ?? "-")-\(status.id)",
                        symbol: status.downCount > 0 ? "xmark.octagon.fill" : "exclamationmark.triangle.fill",
                        tone: status.downCount > 0 ? "danger" : "warning",
                        title: bad.count == 1 ? "\(bad[0].label) is \(bad[0].health)" : "\(bad.count) services need a look",
                        detail: [page.node?.title ?? section.title, bad.prefix(3).map(\.label).joined(separator: ", ")].joined(separator: " · "),
                        image: page.node?.iconImage ?? section.iconImage,
                        fallback: page.node?.icon ?? section.icon,
                        accent: OrbitalTheme.accentColor(section.accent),
                        action: { router.show(location, tab: location.tabID) }
                    ))
                }
            }
        }
        return out
    }

    private func open(_ alert: OrbitalAlert) {
        if let url = alert.resolvedLaunchURL {
            router.handle(url)
        } else {
            router.openAlerts()
        }
        Task { await store.ack([alert.id]) }
    }
}

// MARK: - Agent activity

/// Machines online, what agents are running now, and the way into Run agent.
struct AgentActivityCard: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @EnvironmentObject private var desk: DeskState

    var body: some View {
        let running = store.activeAgentJobs
        VStack(alignment: .leading, spacing: 12) {
            Button {
                router.openAgents()
            } label: {
                HStack {
                    OrbitalEyebrow(text: "Agents", color: OrbitalTheme.accent)
                    Spacer(minLength: 0)
                    HStack(spacing: 3) {
                        Text(running.isEmpty ? "All machines" : "\(running.count) running")
                        Image(systemName: "chevron.right")
                            .font(.caption2.weight(.bold))
                    }
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.accent)
                }
                .frame(minHeight: 28)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)

            if store.satellites.isEmpty {
                Text(store.agentsLoaded
                    ? (store.agentsError ?? "No machines have joined this hub yet. Run ./setup on another computer and pick “satellite”.")
                    : "Looking for machines…")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 8) {
                        ForEach(store.satellites) { machine in
                            Button {
                                router.openAgents(route: .satellite(machine.id))
                            } label: {
                                SatelliteChip(machine: machine, running: running.filter { store.satellite(for: $0)?.id == machine.id }.count)
                            }
                            .buttonStyle(OrbitalPressStyle())
                        }
                    }
                    .padding(.vertical, 1)
                }
            }

            if running.isEmpty {
                if let last = store.agentJobs.first {
                    Button {
                        router.openAgents(route: .agentJob(last.id))
                    } label: {
                        HStack(spacing: 8) {
                            Image(systemName: last.statusSymbol)
                                .foregroundStyle(last.tint)
                            Text("Last: \(last.shownTitle)")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(OrbitalTheme.textSecondary)
                                .lineLimit(1)
                            Spacer(minLength: 0)
                            Text(OrbitalDates.relative(last.finishedAt ?? last.startedAt) ?? "")
                                .font(.caption2)
                                .foregroundStyle(OrbitalTheme.textTertiary)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(running.prefix(3).enumerated()), id: \.element.id) { index, job in
                        if index > 0 {
                            Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                        }
                        Button {
                            router.openAgents(route: .agentJob(job.id))
                        } label: {
                            AgentJobRow(job: job, compact: true)
                        }
                        .buttonStyle(OrbitalPressStyle())
                    }
                }
            }

            if desk.trackerActive > 0 {
                Button {
                    router.openTracker(item: nil)
                } label: {
                    HStack(spacing: 6) {
                        TrackerLiveDot(size: 6)
                        Text("\(desk.trackerActive) \(desk.trackerActive == 1 ? "item" : "items") live in the tracker")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(OrbitalTheme.success)
                        Spacer(minLength: 0)
                    }
                    .frame(minHeight: 28)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }

            HStack(spacing: 10) {
                OrbitalActionButton(title: "Run an agent", symbol: "play.fill", kind: .primary) {
                    router.runAgent()
                }
                .disabled(store.onlineSatellites.isEmpty)
                OrbitalActionButton(title: "Agents", symbol: "cpu", kind: .secondary) {
                    router.openAgents()
                }
            }
        }
        .orbitalCard(padding: 16, tint: running.isEmpty ? nil : OrbitalTheme.accent)
    }
}

// MARK: - Pinned section

/// A pinned section: its header, then a highlight or two from its first page (figures, service
/// status, progress bars, or a daily chart). Tap opens the page.
struct PinnedSectionCard: View {
    let entry: CatalogEntry
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter

    private var tint: Color { OrbitalTheme.accentColor(entry.accent) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Button {
                router.show(PageLocation(sectionID: entry.id))
            } label: {
                HStack(spacing: 12) {
                    AppIcon(image: entry.iconImage, symbol: entry.icon, tint: tint, size: 38)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(entry.title)
                            .font(.headline)
                            .foregroundStyle(OrbitalTheme.text)
                            .lineLimit(1)
                        Text(subtitle)
                            .font(.caption)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .lineLimit(1)
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            ForEach(Array(highlights.enumerated()), id: \.offset) { _, block in
                highlight(block)
            }
        }
        .orbitalCard(padding: 14, tint: tint)
        .task(id: entry.id) {
            if store.sectionsByID[entry.id] == nil { await store.load(entry.id) }
        }
    }

    private var subtitle: String {
        var parts: [String] = []
        if let summary = Insights.nonEmpty(entry.summary) ?? Insights.nonEmpty(entry.subtitle) { parts.append(summary) }
        if let updated = OrbitalDates.relative(entry.updatedAt) { parts.append("updated \(updated)") }
        return parts.joined(separator: " · ")
    }

    /// The first figures, status, progress, and series blocks on the section's first page, two at most.
    private var highlights: [ContentBlock] {
        guard let section = store.sectionsByID[entry.id], let page = section.pages.first else { return [] }
        var out: [ContentBlock] = []
        var kinds = Set<String>()
        for block in page.blocks {
            let kind: String
            switch block {
            case .metrics: kind = "metrics"
            case .status: kind = "status"
            case .progress: kind = "progress"
            case .series(let series) where !series.metrics.isEmpty: kind = "series"
            case .callout(let callout) where callout.tone == "danger" || callout.tone == "warning": kind = "callout"
            default: continue
            }
            if kinds.insert(kind).inserted { out.append(block) }
            if out.count == 2 { break }
        }
        return out
    }

    @ViewBuilder
    private func highlight(_ block: ContentBlock) -> some View {
        switch block {
        case .metrics(let metrics):
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 104), spacing: 8)], alignment: .leading, spacing: 8) {
                ForEach(metrics.items.prefix(4)) { MiniMetric(tile: $0) }
            }
        case .status(let status):
            StatusSummaryRow(block: status)
        case .progress(let progress):
            VStack(alignment: .leading, spacing: 8) {
                ForEach(progress.items.prefix(3)) { ProgressRow(item: $0, accent: tint) }
            }
        case .series(let series):
            SeriesSparkRow(block: series, accent: tint)
        case .callout(let callout):
            Label(callout.title ?? callout.text, systemImage: OrbitalTheme.toneSymbol(callout.tone))
                .font(.footnote.weight(.semibold))
                .foregroundStyle(OrbitalTheme.toneColor(callout.tone))
                .lineLimit(2)
        default:
            EmptyView()
        }
    }
}

/// One series metric as a figure and a small 7-day line, for Home.
struct SeriesSparkRow: View {
    let block: SeriesBlock
    var accent: Color = OrbitalTheme.accent

    var body: some View {
        let days = Trends.days(7)
        if let metric = block.metrics.first {
            let values = Trends.line(metric, days: days)
            let color = metric.color.flatMap { Color(hex: $0) } ?? metric.platform.map { PlatformStyle.color($0) } ?? accent
            HStack(alignment: .center, spacing: 12) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(Trends.format(Trends.summary(values, level: metric.level), format: metric.format, compact: true))
                        .font(.system(.title3, design: .rounded).weight(.bold))
                        .monospacedDigit()
                        .foregroundStyle(OrbitalTheme.text)
                    Text(metric.level ? "\(metric.label) now" : "\(metric.label), 7 days")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .lineLimit(1)
                }
                Spacer(minLength: 8)
                TrendChart(days: days, lines: [TrendLine(id: metric.id, name: metric.label, color: color, values: values, format: metric.format, level: metric.level)], height: 44)
                    .chartXAxis(.hidden)
                    .chartYAxis(.hidden)
                    .frame(width: 140)
                    .allowsHitTesting(false)
            }
        }
    }
}

// MARK: - Space tile

/// One space on Home: its icon, its products, and what is going on in it.
struct SpaceTile: View {
    let space: SpaceSummary
    @EnvironmentObject private var store: OrbitalStore

    var body: some View {
        let products = store.directory.products(of: space.id)
        let sections = Set(space.sectionIDs)
        let unread = store.alerts.filter { $0.isWaiting() && sections.contains($0.sectionId ?? "") }.count
        let latest = space.sectionIDs.compactMap { store.entry($0)?.updatedAt }.compactMap(OrbitalDates.parse).max()
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                AppIcon(image: space.iconImage, symbol: space.symbol, tint: space.tint, size: 38)
                    .clipShape(Circle())
                    .overlay(Circle().strokeBorder(space.tint.opacity(0.6), lineWidth: 1.5))
                VStack(alignment: .leading, spacing: 1) {
                    Text(space.name)
                        .font(.headline)
                        .foregroundStyle(OrbitalTheme.text)
                        .lineLimit(1)
                    Text(space.subtitle ?? countLine(sections: space.sectionIDs.count, products: products.count))
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            if !products.isEmpty {
                HStack(spacing: -6) {
                    ForEach(products.prefix(6)) { product in
                        AppIcon(image: product.iconImage, symbol: product.symbol, tint: product.tint, size: 26)
                            .overlay(RoundedRectangle(cornerRadius: 26 * 0.225, style: .continuous).strokeBorder(OrbitalTheme.surface, lineWidth: 2))
                    }
                }
            }
            HStack(spacing: 6) {
                stat("\(space.sectionIDs.count)", space.sectionIDs.count == 1 ? "section" : "sections")
                stat("\(unread)", unread == 1 ? "unread alert" : "unread alerts", tint: unread > 0 ? OrbitalTheme.danger : nil)
                stat(latest.map { OrbitalDates.relative(OrbitalDates.iso($0)) ?? "—" } ?? "—", "last update")
            }
        }
        .orbitalCard(padding: 14, tint: space.tint)
    }

    private func countLine(sections: Int, products: Int) -> String {
        var parts = ["\(sections) \(sections == 1 ? "section" : "sections")"]
        if products > 0 { parts.append("\(products) \(products == 1 ? "product" : "products")") }
        return parts.joined(separator: " · ")
    }

    private func stat(_ value: String, _ label: String, tint: Color? = nil) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(value)
                .font(.subheadline.weight(.bold).monospacedDigit())
                .foregroundStyle(tint ?? OrbitalTheme.text)
                .lineLimit(1)
                .minimumScaleFactor(0.7)
            Text(label)
                .font(.system(size: 9.5, weight: .semibold))
                .foregroundStyle(OrbitalTheme.textTertiary)
                .lineLimit(1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(8)
        .background(Color.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
    }
}

// MARK: - Scheduler

/// One line: are the feeds working, when the next one runs, and Refresh all.
struct AutomationsHomeCard: View {
    let snapshot: JobsSnapshot
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter

    var body: some View {
        let bad = snapshot.failing.count + snapshot.warning.count + (snapshot.disk?.low == true ? 1 : 0)
        let down = snapshot.scheduler.up == false
        let tint = down || !snapshot.failing.isEmpty || snapshot.disk?.low == true ? OrbitalTheme.danger : (bad > 0 ? OrbitalTheme.warning : OrbitalTheme.success)
        HStack(spacing: 12) {
            Button {
                router.openAutomations(job: down ? nil : (snapshot.failing.first ?? snapshot.warning.first)?.id)
            } label: {
                HStack(spacing: 10) {
                    IconTile(symbol: down ? "exclamationmark.octagon.fill" : (bad > 0 ? "exclamationmark.triangle.fill" : "clock.arrow.2.circlepath"), tint: tint, size: 34)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(title(bad: bad, down: down))
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(OrbitalTheme.text)
                            .lineLimit(1)
                        TimelineView(.periodic(from: .now, by: 1)) { context in
                            Text(detail(context.date))
                                .font(.caption)
                                .monospacedDigit()
                                .foregroundStyle(OrbitalTheme.textTertiary)
                                .lineLimit(1)
                        }
                    }
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(OrbitalPressStyle())
            RunButton(busy: snapshot.activeBatch != nil) {
                Task { await store.runAll() }
            }
            .accessibilityLabel("Refresh every feed now")
        }
        .orbitalCard(padding: 12, tint: bad > 0 || down ? tint : nil)
    }

    private func title(bad: Int, down: Bool) -> String {
        if down { return "Feeds are not updating" }
        if let disk = snapshot.disk, disk.low == true { return "Hub disk almost full: \(String(format: "%.1f", disk.freeGb ?? 0)) GB free" }
        if let worst = snapshot.failing.first { return "\(worst.name) is failing" + (bad > 1 ? " · \(bad - 1) more" : "") }
        if let partial = snapshot.warning.first { return "\(partial.name) partly working" + (bad > 1 ? " · \(bad - 1) more" : "") }
        return "All \(snapshot.jobs.count) feeds working"
    }

    private func detail(_ now: Date) -> String {
        if let batch = snapshot.activeBatch {
            return "Refreshing everything · \(snapshot.doneCount(batch)) of \(batch.jobs.count) done"
        }
        // A failing feed: say what went wrong, not what else happens to be running.
        if let worst = snapshot.failing.first ?? snapshot.warning.first {
            let why = Insights.nonEmpty(worst.last?.summary) ?? worst.last?.issues?.first ?? "Tap to see the log"
            if let since = OrbitalDates.relative(worst.failingSince), snapshot.failing.first?.id == worst.id {
                return "\(why) · \(since)"
            }
            return why
        }
        let running = snapshot.jobs.filter(\.isRunning)
        if let first = running.first {
            return "Updating \(first.name)" + (running.count > 1 ? " +\(running.count - 1)" : "")
        }
        guard let next = snapshot.nextScheduled else { return "Tap for schedules" }
        return "Next: \(next.job.name) in \(JobFormat.countdown(to: next.at, now: now))"
    }
}

// MARK: - Money

/// Money across the ledgers of one space or one product: this month, 30 days, the period totals,
/// a daily chart, and the last payment.
struct MoneySummaryCard: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    /// Only this space's ledgers (SpaceDirectory id).
    var space: String?
    /// Only rows for this product (SpaceDirectory id).
    var product: String?

    private var ledgers: [LedgerRef] {
        guard let space else { return store.ledgers }
        let sections = Set(store.directory.space(space)?.sectionIDs ?? [])
        return store.ledgers.filter { Spaces.key($0.space) == space || sections.contains($0.location.sectionID) }
    }

    private var currency: String { Insights.primaryCurrency(ledgers) }

    private var rows: [MoneyRow] {
        Insights.rows(ledgers, currency: currency).filter { product == nil || Spaces.key($0.product) == product }
    }

    var body: some View {
        let month = Insights.total(rows, in: MoneyPeriod.month.range())
        let thirty = Insights.total(rows, in: MoneyPeriod.last30.range())
        VStack(alignment: .leading, spacing: 12) {
            Button {
                if let first = ledgers.first { router.show(first.location, tab: first.location.tabID) }
            } label: {
                HStack {
                    OrbitalEyebrow(text: "Money", color: OrbitalTheme.success)
                    Spacer(minLength: 0)
                    if ledgers.count == 1, let first = ledgers.first {
                        HStack(spacing: 3) {
                            Text(first.title)
                            Image(systemName: "chevron.right")
                                .font(.caption2.weight(.bold))
                        }
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.success)
                    }
                }
                .frame(minHeight: 28)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(OrbitalMoney.format(month, currency: currency))
                    .font(.system(size: 34, weight: .bold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(month > 0 ? OrbitalTheme.text : OrbitalTheme.textSecondary)
                    .contentTransition(.numericText())
                VStack(alignment: .leading, spacing: 1) {
                    Text("this month")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.textSecondary)
                    Text("\(OrbitalMoney.format(thirty, currency: currency)) in 30 days")
                        .font(.caption2.weight(.semibold).monospacedDigit())
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
                Spacer(minLength: 0)
            }
            PeriodTotals(rows: rows, currency: currency, compact: true)
            if let last = lastPayment {
                HStack(spacing: 8) {
                    Image(systemName: "arrow.down.circle.fill")
                        .foregroundStyle(OrbitalTheme.success)
                    Text("Last payment \(OrbitalMoney.format(last.amount, currency: currency)) · \(last.product) · \(last.at.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated)))")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .lineLimit(2)
                }
            }
            if rows.contains(where: { abs($0.amount) > 0.004 }) {
                MoneyChart(rows: rows, currency: currency, highlight: MoneyPeriod.last30.range(), height: 110)
                    .allowsHitTesting(false)
            }
        }
        .orbitalCard(padding: 16, tint: OrbitalTheme.success)
    }

    private var lastPayment: MoneyEntry? {
        Insights.entries(ledgers, currency: currency).first { entry in
            entry.amount > 0 && (product == nil || Spaces.key(entry.product) == product)
        }
    }
}

/// Rolled-up figures across series blocks.
enum Rollups {
    /// One stat added up across matching series over the last `days` days (a level: its latest value).
    static func total(_ refs: [SeriesRef], kind: String, stat: String, space: String? = nil, product: String? = nil, days: Int = 7) -> Double? {
        let metrics = refs
            .filter { $0.block.kind == kind && (space == nil || $0.spaceID == space) && (product == nil || $0.productID == product) }
            .flatMap { $0.block.metrics.filter { $0.statKey == stat } }
        guard !metrics.isEmpty else { return nil }
        return Trends.summary(Trends.combine(metrics, days: Trends.days(days)), level: metrics.first?.level ?? false)
    }
}
