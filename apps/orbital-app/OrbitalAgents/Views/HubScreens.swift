import SwiftUI

// Whole-screen views a hub nav item can open by name ("social", "analytics", "videos"). Each one is
// built from the cards Home and the space pages already draw, across every section.

/// Social numbers from every series block of kind "social", then one card per series.
struct SocialView: View {
    var body: some View {
        SeriesKindScreen(kind: "social", title: "Social", eyebrow: "By platform", tint: Color(hex: "#FF4FB4") ?? OrbitalTheme.violet, defaultStat: "activity")
    }
}

/// Website traffic from every series block of kind "site".
struct AnalyticsView: View {
    var body: some View {
        SeriesKindScreen(kind: "site", title: "Analytics", eyebrow: "Site traffic", tint: Color(hex: "#5AC8FA") ?? OrbitalTheme.accent, defaultStat: "visitors")
    }
}

/// The rollup chart for one series kind, then each series on its own.
struct SeriesKindScreen: View {
    let kind: String
    let title: String
    let eyebrow: String
    let tint: Color
    var defaultStat: String?
    @EnvironmentObject private var store: OrbitalStore
    @Environment(\.orbitalPushed) private var pushed

    var body: some View {
        let refs = store.series.filter { $0.block.kind == kind }
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                OrbitalScreenHeader(eyebrow: eyebrow, title: title, accent: tint)
                if refs.isEmpty {
                    Text("No \(title.lowercased()) numbers yet. They appear when a feed pushes a series block of kind \"\(kind)\".")
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .orbitalCard(padding: 16)
                } else {
                    RollupChartCard(eyebrow: eyebrow, title: title, tint: tint, kind: kind, refs: refs, directory: store.directory, defaultStat: defaultStat)
                    ForEach(refs) { ref in
                        SeriesCard(block: ref.block, accent: OrbitalTheme.accentColor(ref.accent), eyebrow: store.directory.product(ref.productID)?.name)
                    }
                }
            }
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, 36)
        }
        .scrollIndicators(.hidden)
        .refreshable { await store.refresh() }
        .orbitalBackdrop(tint)
        .navigationTitle(title)
        .orbitalNavigationBar(pushed: pushed)
    }
}

/// Every videos block across sections.
struct VideosHubView: View {
    @EnvironmentObject private var store: OrbitalStore
    @Environment(\.orbitalPushed) private var pushed

    private var blocks: [(id: String, block: VideosBlock, accent: Color)] {
        var out: [(String, VideosBlock, Color)] = []
        for section in store.sectionsByID.values.sorted(by: { $0.order < $1.order }) {
            for page in section.pages {
                for block in page.blocks {
                    if case .videos(let videos) = block {
                        out.append(("\(section.id)/\(page.node?.id ?? "-")/\(videos.id)", videos, OrbitalTheme.accentColor(section.accent)))
                    }
                }
            }
        }
        return out.map { (id: $0.0, block: $0.1, accent: $0.2) }
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                OrbitalScreenHeader(eyebrow: "Videos", title: "Videos", accent: OrbitalTheme.violet)
                if blocks.isEmpty {
                    Text("No videos yet. They appear when a feed pushes a videos block.")
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .orbitalCard(padding: 16)
                }
                ForEach(blocks, id: \.id) { row in
                    VideosBlockCard(block: row.block, accent: row.accent)
                }
            }
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, 36)
        }
        .scrollIndicators(.hidden)
        .refreshable { await store.refresh() }
        .orbitalBackdrop(OrbitalTheme.violet)
        .navigationTitle("Videos")
        .orbitalNavigationBar(pushed: pushed)
    }
}
