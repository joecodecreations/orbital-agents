import SwiftUI

private struct OrbitalPushedKey: EnvironmentKey {
    static let defaultValue = false
}

private struct OrbitalStackKey: EnvironmentKey {
    static let defaultValue: RootTab? = nil
}

extension EnvironmentValues {
    /// True on a screen pushed onto an iPhone tab stack. It shows the system bar so Back and swipe-back work.
    var orbitalPushed: Bool {
        get { self[OrbitalPushedKey.self] }
        set { self[OrbitalPushedKey.self] = newValue }
    }

    /// The iPhone tab whose stack this screen sits in.
    var orbitalStack: RootTab? {
        get { self[OrbitalStackKey.self] }
        set { self[OrbitalStackKey.self] = newValue }
    }
}

extension View {
    /// Hide the bar on a root screen that draws its own header; show it, transparent, when pushed.
    func orbitalNavigationBar(pushed: Bool) -> some View {
        self
            .toolbar(pushed ? .visible : .hidden, for: .navigationBar)
            .toolbarBackground(.hidden, for: .navigationBar)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                // The page header already shows the title. The bar keeps it only as the next screen's Back label.
                ToolbarItem(placement: .principal) { Color.clear.frame(width: 1, height: 1) }
            }
    }
}

/// What a pushed route draws on iPhone.
struct RouteView: View {
    let route: BrowseRoute
    @EnvironmentObject private var store: OrbitalStore

    var body: some View {
        Group {
            switch route {
            case .section(let id):
                if layout(of: id) == .sidebar {
                    NodeColumn(sectionID: id)
                } else {
                    PageCanvas(sectionID: id, nodeID: nil)
                }
            case .node(let sectionID, let nodeID):
                PageCanvas(sectionID: sectionID, nodeID: nodeID)
            case .space(let id):
                SpaceView(spaceID: id)
            case .product(let id):
                ProductView(productID: id)
            case .browse:
                BrowseView()
            case .alerts:
                AlertsInboxView()
            case .manage:
                SectionEditorView()
            case .settings:
                SettingsView()
            case .automations:
                AutomationsView()
            case .agents:
                AgentsView()
            case .satellite(let id):
                SatelliteView(satelliteID: id)
            case .agentJob(let id):
                AgentJobView(jobID: id)
            case .tracker:
                TrackerListView()
            case .trackerItem(let id):
                TrackerItemView(itemID: id)
            }
        }
        .environment(\.orbitalPushed, true)
    }

    private func layout(of id: String) -> SectionLayout? {
        store.sectionsByID[id]?.layout ?? store.catalog.first { $0.id == id }?.layout
    }
}

/// Every section the hub has, then the tools. Opened from Home's menu.
struct BrowseView: View {
    @EnvironmentObject private var store: OrbitalStore
    @Environment(\.orbitalPushed) private var pushed

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                OrbitalScreenHeader(
                    eyebrow: store.systemName,
                    title: "All sections",
                    subtitle: "\(store.sortedCatalog.count) sections, every page your feeds send"
                ) {
                    ConnectionPill(label: store.connectionLabel)
                }
                VStack(spacing: 0) {
                    ForEach(Array(store.sortedCatalog.enumerated()), id: \.element.id) { index, entry in
                        if index > 0 {
                            Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                        }
                        NavigationLink(value: BrowseRoute.section(entry.id)) {
                            sectionRow(entry)
                        }
                        .buttonStyle(OrbitalPressStyle())
                    }
                    if store.sortedCatalog.isEmpty {
                        Text("No sections yet. Feeds push them with `orbital push`. Pull to refresh.")
                            .font(.footnote)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .padding(.vertical, 20)
                    }
                }
                .orbitalCard(padding: 14)
                VStack(spacing: 0) {
                    if store.jobs != nil {
                        NavigationLink(value: BrowseRoute.automations) {
                            toolRow("Automations", detail: automationsDetail, symbol: "clock.arrow.2.circlepath")
                        }
                        .buttonStyle(OrbitalPressStyle())
                        Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                    }
                    NavigationLink(value: BrowseRoute.manage) {
                        toolRow("Manage sections", detail: "Add, rename, reorder, pin, or delete sections", symbol: "slider.horizontal.3")
                    }
                    .buttonStyle(OrbitalPressStyle())
                    Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                    NavigationLink(value: BrowseRoute.settings) {
                        toolRow("Settings", detail: "Hub, notifications, account", symbol: "gearshape.fill")
                    }
                    .buttonStyle(OrbitalPressStyle())
                }
                .orbitalCard(padding: 14)
            }
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, 36)
        }
        .scrollIndicators(.hidden)
        .orbitalScrollFade()
        .refreshable { await store.refresh() }
        .orbitalBackdrop(OrbitalTheme.accent)
        .navigationTitle("All sections")
        .orbitalNavigationBar(pushed: pushed)
    }

    private var automationsDetail: String {
        guard let jobs = store.jobs else { return "Schedules, countdowns, run any feed now" }
        let failing = jobs.failing.count + jobs.warning.count
        if failing > 0 { return "\(failing) need a look · schedules and run buttons" }
        return "\(jobs.jobs.count) feeds working · schedules and run buttons"
    }

    private func sectionRow(_ entry: CatalogEntry) -> some View {
        let tint = OrbitalTheme.accentColor(entry.accent)
        return HStack(spacing: 12) {
            AppIcon(image: entry.iconImage, symbol: entry.icon, tint: tint, size: 42)
            VStack(alignment: .leading, spacing: 2) {
                Text(entry.title)
                    .font(.headline)
                    .foregroundStyle(OrbitalTheme.text)
                    .lineLimit(1)
                if let line = entry.summary ?? entry.subtitle, !line.isEmpty {
                    Text(line)
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .lineLimit(1)
                }
                HStack(spacing: 6) {
                    if entry.layout == .sidebar, entry.nodeCount > 0 {
                        Text("\(entry.nodeCount) \(entry.nodeCount == 1 ? "page" : "pages")")
                    }
                    if let updated = OrbitalDates.relative(entry.updatedAt) {
                        Text("Updated \(updated)")
                    }
                }
                .font(.caption2)
                .foregroundStyle(OrbitalTheme.textTertiary)
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right")
                .font(.caption.weight(.bold))
                .foregroundStyle(OrbitalTheme.textTertiary)
        }
        .padding(.vertical, 10)
        .contentShape(Rectangle())
    }

    private func toolRow(_ title: String, detail: String, symbol: String) -> some View {
        HStack(spacing: 12) {
            IconTile(symbol: symbol, tint: OrbitalTheme.textSecondary, size: 34)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right")
                .font(.caption.weight(.bold))
                .foregroundStyle(OrbitalTheme.textTertiary)
        }
        .padding(.vertical, 8)
        .contentShape(Rectangle())
    }
}
