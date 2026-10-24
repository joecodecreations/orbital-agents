import SwiftUI

/// The first screen, built only from what the hub sends: what needs you, agent activity, social and
/// website charts, money across ledgers, pinned sections, and a tile per space.
struct HomeView: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @EnvironmentObject private var desk: DeskState

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 18) {
                header
                InsightHomeStrip()
                AttentionCard()
                AgentActivityCard()
                socialCard
                siteCard
                #if ORBITAL_PRIVATE
                PrivateHomeProductCard()
                #endif
                if !store.ledgers.isEmpty {
                    MoneySummaryCard()
                }
                #if ORBITAL_PRIVATE
                PrivateHomeTasksCard()
                #endif
                pinnedSection
                spacesSection
                if !store.pulses.isEmpty {
                    LiveNowCard()
                }
                footer
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
        .navigationTitle("Home")
        .toolbar(.hidden, for: .navigationBar)
    }

    // MARK: - Header

    private var header: some View {
        OrbitalScreenHeader(
            eyebrow: Date().formatted(.dateTime.weekday(.wide).day().month(.wide)),
            title: store.systemName,
            subtitle: summaryLine
        ) {
            VStack(alignment: .trailing, spacing: 4) {
                ShellHeaderButtons()
                ConnectionPill(label: store.connectionLabel)
            }
        }
    }

    private var summaryLine: String {
        var parts: [String] = []
        let spaces = store.directory.spaces.count
        if spaces > 0 { parts.append("\(spaces) \(spaces == 1 ? "space" : "spaces")") }
        parts.append("\(store.catalog.count) \(store.catalog.count == 1 ? "section" : "sections")")
        if !store.satellites.isEmpty {
            parts.append("\(store.onlineSatellites.count) of \(store.satellites.count) machines online")
        }
        return parts.joined(separator: " · ")
    }

    // MARK: - Pinned

    @ViewBuilder
    private var pinnedSection: some View {
        let pinned = store.homeSections
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                OrbitalEyebrow(text: "Pinned")
                Spacer(minLength: 0)
                if !pinned.isEmpty {
                    Button("Manage") { router.openManage() }
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.accent)
                        .frame(minHeight: 30)
                }
            }
            if pinned.isEmpty {
                HStack(alignment: .top, spacing: 12) {
                    IconTile(symbol: "pin.fill", tint: OrbitalTheme.textSecondary, size: 34)
                    VStack(alignment: .leading, spacing: 3) {
                        Text("Pin the sections you check most")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(OrbitalTheme.text)
                        Text("Tap the pin on any page, or send `\"pinned\": true` from its feed. Pins follow you to every device.")
                            .font(.caption)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    Spacer(minLength: 0)
                }
                .orbitalCard(padding: 14)
            } else {
                ForEach(pinned) { entry in
                    PinnedSectionCard(entry: entry)
                }
            }
        }
    }

    // MARK: - Metric rollups

    private var socialCard: some View {
        let refs = store.series.filter { $0.block.kind == "social" }
        return Group {
            if !refs.isEmpty {
                RollupChartCard(
                    eyebrow: "Social",
                    title: "By platform",
                    tint: Color(hex: "#FF4FB4") ?? OrbitalTheme.violet,
                    kind: "social",
                    refs: refs,
                    directory: store.directory,
                    defaultStat: "activity",
                    onOpen: { router.openView("social") }
                )
            }
        }
    }

    private var siteCard: some View {
        let refs = store.series.filter { $0.block.kind == "site" }
        return Group {
            if !refs.isEmpty {
                RollupChartCard(
                    eyebrow: "Websites",
                    title: "Site traffic",
                    tint: Color(hex: "#5AC8FA") ?? OrbitalTheme.accent,
                    kind: "site",
                    refs: refs,
                    directory: store.directory,
                    defaultStat: "visitors",
                    onOpen: { router.openView("analytics") }
                )
            }
        }
    }

    // MARK: - Spaces

    @ViewBuilder
    private var spacesSection: some View {
        if !store.directory.spaces.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                OrbitalEyebrow(text: "Spaces")
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 260), spacing: 10)], spacing: 10) {
                    ForEach(store.directory.spaces) { space in
                        Button {
                            router.openSpace(space.id)
                        } label: {
                            SpaceTile(space: space)
                        }
                        .buttonStyle(OrbitalPressStyle())
                    }
                }
            }
        }
    }

    // MARK: - Footer

    private var footer: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let jobs = store.jobs, !jobs.jobs.isEmpty, !jobs.needsALook {
                AutomationsHomeCard(snapshot: jobs)
            }
            Button {
                router.openBrowse()
            } label: {
                HStack(spacing: 12) {
                    IconTile(symbol: "square.grid.2x2.fill", tint: OrbitalTheme.textSecondary, size: 34)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("All sections")
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(OrbitalTheme.text)
                        Text(store.catalog.isEmpty ? "Nothing yet. Feeds push sections with `orbital push`." : "\(store.catalog.count) sections, every page your feeds send")
                            .font(.caption)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
                .orbitalCard(padding: 12)
            }
            .buttonStyle(OrbitalPressStyle())
        }
    }
}
