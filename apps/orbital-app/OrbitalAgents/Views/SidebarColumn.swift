import SwiftUI
import UIKit

/// The wide layout's left bar: the main screens, then spaces, then every section, then tools.
struct SidebarColumn: View {
    var showsSummaries: Bool = false
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @EnvironmentObject private var desk: DeskState

    var body: some View {
        VStack(spacing: 0) {
            brand
            ScrollView {
                VStack(alignment: .leading, spacing: 4) {
                    pinned("Home", system: "house.fill", selected: router.destination == .home && router.widePath.isEmpty) {
                        openPinned(.home)
                    }
                    pinned("Agents", system: "cpu", selected: router.destination == .agents, count: store.activeAgentJobs.count, countTint: OrbitalTheme.success) {
                        openPinned(.agents)
                    }
                    pinned("Desk", system: "sparkles", selected: router.destination == .desk) {
                        openPinned(.desk)
                    }
                    pinned("Tracker", system: "dot.radiowaves.left.and.right", selected: router.destination == .tracker, count: desk.trackerActive, countTint: OrbitalTheme.success) {
                        openPinned(.tracker)
                    }
                    pinned("Alerts", system: "bell.fill", selected: router.destination == .alerts, count: store.unackedCount) {
                        openPinned(.alerts)
                    }
                    if !store.directory.spaces.isEmpty {
                        OrbitalEyebrow(text: "Spaces")
                            .padding(.horizontal, 10)
                            .padding(.top, 10)
                            .padding(.bottom, 4)
                        ForEach(store.directory.spaces) { space in
                            spaceRow(space)
                        }
                    }
                    OrbitalEyebrow(text: "Sections")
                        .padding(.horizontal, 10)
                        .padding(.top, 10)
                        .padding(.bottom, 4)
                    ForEach(store.sortedCatalog) { entry in
                        sectionRow(entry)
                    }
                    if store.sortedCatalog.isEmpty {
                        Text("No sections yet. Feeds push them with `orbital push`.")
                            .font(.caption)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .padding(.horizontal, 10)
                            .padding(.top, 4)
                    }
                }
                .padding(.horizontal, 6)
                .padding(.bottom, 10)
            }
            .scrollIndicators(.hidden)
            .refreshable { await store.refresh() }
            OrbitalRule()
            VStack(spacing: 2) {
                if store.jobs != nil {
                    pinned("Automations", system: "clock.arrow.2.circlepath", selected: router.destination == .automations, count: (store.jobs?.failing.count ?? 0) + (store.jobs?.warning.count ?? 0)) {
                        openPinned(.automations)
                    }
                }
                pinned("Manage", system: "slider.horizontal.3", selected: router.destination == .manage) {
                    openPinned(.manage)
                }
                pinned("Settings", system: "gearshape.fill", selected: router.destination == .settings) {
                    openPinned(.settings)
                }
            }
            .padding(.horizontal, 6)
            .padding(.vertical, 8)
        }
        .background(
            LinearGradient(
                colors: [OrbitalTheme.backgroundDeep, Color.black],
                startPoint: .top,
                endPoint: .bottom
            )
            .ignoresSafeArea()
        )
    }

    private var brand: some View {
        HStack(spacing: 9) {
            OrbitalMark(size: 30)
                .shadow(color: OrbitalTheme.accent.opacity(0.45), radius: 8)
            VStack(alignment: .leading, spacing: 2) {
                Text(store.systemName)
                    .font(.subheadline.weight(.bold))
                    .foregroundStyle(OrbitalTheme.text)
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
                ConnectionPill(label: store.connectionLabel, compact: true)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 12)
        .padding(.top, 10)
        .padding(.bottom, 10)
    }

    private func openPinned(_ destination: AppRouter.Destination) {
        router.destination = destination
        router.widePath = []
    }

    /// A space opens its page over Home.
    private func spaceRow(_ space: SpaceSummary) -> some View {
        let selected = router.destination == .home && router.widePath.first == .space(space.id)
        return Button {
            router.destination = .home
            router.widePath = [.space(space.id)]
        } label: {
            HStack(spacing: 9) {
                AppIcon(image: space.iconImage, symbol: space.symbol, tint: space.tint, size: 22)
                    .clipShape(Circle())
                Text(space.name)
                    .font(.footnote.weight(selected ? .semibold : .medium))
                    .foregroundStyle(selected ? OrbitalTheme.text : OrbitalTheme.textSecondary)
                    .lineLimit(1)
                Spacer(minLength: 0)
                Text("\(space.sectionIDs.count)")
                    .font(.caption2.weight(.semibold).monospacedDigit())
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            .padding(.horizontal, 10)
            .frame(minHeight: 36)
            .background {
                if selected {
                    RoundedRectangle(cornerRadius: 10, style: .continuous).fill(space.tint.opacity(0.14))
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func pinned(_ title: String, system: String, selected: Bool, count: Int = 0, countTint: Color = OrbitalTheme.danger, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 9) {
                Image(systemName: orbitalSymbol(system))
                    .font(.system(size: 13, weight: .semibold))
                    .frame(width: 22, height: 22)
                    .foregroundStyle(selected ? OrbitalTheme.accent : OrbitalTheme.textTertiary)
                Text(title)
                    .font(.footnote.weight(selected ? .semibold : .medium))
                    .foregroundStyle(selected ? OrbitalTheme.text : OrbitalTheme.textSecondary)
                    .lineLimit(1)
                Spacer(minLength: 0)
                if count > 0 {
                    Text(count > 99 ? "99+" : "\(count)")
                        .font(.caption2.weight(.heavy))
                        .monospacedDigit()
                        .padding(.horizontal, 6)
                        .frame(minWidth: 20, minHeight: 18)
                        .background(countTint, in: Capsule())
                        .foregroundStyle(countTint == OrbitalTheme.danger ? Color.white : OrbitalTheme.backgroundDeep)
                        .accessibilityLabel("\(count)")
                }
            }
            .padding(.horizontal, 8)
            .frame(maxWidth: .infinity, minHeight: 40, alignment: .leading)
            .background {
                if selected {
                    RoundedRectangle(cornerRadius: 10, style: .continuous)
                        .fill(OrbitalTheme.accent.opacity(0.13))
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(OrbitalPressStyle())
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func sectionRow(_ entry: CatalogEntry) -> some View {
        let selected = isSectionDestination && store.selectedSectionID == entry.id
        let tint = OrbitalTheme.accentColor(entry.accent)
        return Button {
            openSection(entry)
        } label: {
            HStack(alignment: .center, spacing: 9) {
                AppIcon(image: entry.iconImage, symbol: entry.icon, tint: tint, size: 28, filled: selected)
                VStack(alignment: .leading, spacing: 2) {
                    Text(entry.title)
                        .font(.subheadline.weight(selected ? .semibold : .medium))
                        .foregroundStyle(selected ? OrbitalTheme.text : OrbitalTheme.textSecondary)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                    if showsSummaries, let summary = entry.summary, !summary.isEmpty {
                        Text(summary)
                            .font(.caption2)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .lineLimit(1)
                    }
                }
                Spacer(minLength: 0)
                if store.isPinned(entry.id) {
                    Image(systemName: "pin.fill")
                        .font(.system(size: 9, weight: .bold))
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .accessibilityLabel("Pinned to Home")
                }
            }
            .padding(.horizontal, 6)
            .padding(.vertical, 6)
            .frame(maxWidth: .infinity, minHeight: 48, alignment: .leading)
            .background {
                if selected {
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .fill(
                            LinearGradient(
                                colors: [tint.opacity(0.20), tint.opacity(0.06)],
                                startPoint: .leading,
                                endPoint: .trailing
                            )
                        )
                        .overlay(
                            RoundedRectangle(cornerRadius: 12, style: .continuous)
                                .strokeBorder(tint.opacity(0.30), lineWidth: 1)
                        )
                }
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(OrbitalPressStyle())
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func openSection(_ entry: CatalogEntry) {
        router.destination = .section(entry.id)
        store.select(section: entry.id, node: nil, tab: nil, autofillNode: false)
    }

    private var isSectionDestination: Bool {
        if case .section = router.destination { return true }
        return false
    }
}

struct NodeColumn: View {
    var sectionID: String? = nil
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @Environment(\.orbitalDetailBesideList) private var listStaysVisible
    @Environment(\.orbitalPushed) private var pushed
    @State private var query = ""

    private var resolvedSectionID: String? {
        sectionID ?? store.selectedSectionID
    }

    private var sectionTitle: String {
        store.sectionsByID[resolvedSectionID ?? ""]?.title
            ?? store.catalog.first { $0.id == resolvedSectionID }?.title
            ?? "Pages"
    }

    private var sectionSubtitle: String? {
        let value = store.sectionsByID[resolvedSectionID ?? ""]?.subtitle
            ?? store.catalog.first { $0.id == resolvedSectionID }?.subtitle
        return (value?.isEmpty == false) ? value : nil
    }

    private var accent: Color {
        let hex = store.sectionsByID[resolvedSectionID ?? ""]?.accent
            ?? store.catalog.first { $0.id == resolvedSectionID }?.accent
        return OrbitalTheme.accentColor(hex)
    }

    private var nodes: [NavNode] {
        guard let id = resolvedSectionID, let document = store.sectionsByID[id] else { return [] }
        return document.sortedNodes
    }

    private var filtered: [NavNode] {
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nodes }
        return nodes.filter {
            $0.title.localizedCaseInsensitiveContains(trimmed)
                || ($0.subtitle ?? "").localizedCaseInsensitiveContains(trimmed)
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    OrbitalEyebrow(text: nodes.isEmpty ? "Section" : "\(nodes.count) \(nodes.count == 1 ? "page" : "pages")", color: accent)
                    Text(sectionTitle)
                        .font(listStaysVisible ? .headline.weight(.bold) : .title2.weight(.bold))
                        .foregroundStyle(OrbitalTheme.text)
                        .lineLimit(2)
                    if let sectionSubtitle {
                        Text(sectionSubtitle)
                            .font(.caption)
                            .foregroundStyle(OrbitalTheme.textSecondary)
                            .lineLimit(2)
                    }
                }
                Spacer(minLength: 0)
                if !listStaysVisible, let entry = store.entry(resolvedSectionID) {
                    AppIcon(image: entry.iconImage, symbol: entry.icon, tint: accent, size: 44)
                }
            }
            .padding(.horizontal, 14)
            .padding(.top, 14)
            .padding(.bottom, 10)
            if nodes.count >= 8 {
                HStack(spacing: 8) {
                    Image(systemName: "magnifyingglass")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.textTertiary)
                    TextField("Search", text: $query)
                        .textFieldStyle(.plain)
                        .font(.subheadline)
                        .foregroundStyle(OrbitalTheme.text)
                        .autocorrectionDisabled()
                    if !query.isEmpty {
                        Button {
                            query = ""
                        } label: {
                            Image(systemName: "xmark.circle.fill")
                                .foregroundStyle(OrbitalTheme.textTertiary)
                        }
                        .buttonStyle(.plain)
                        .accessibilityLabel("Clear search")
                    }
                }
                .padding(.horizontal, 12)
                .frame(minHeight: 40)
                .background(OrbitalTheme.backgroundDeep.opacity(0.8), in: Capsule())
                .overlay(Capsule().strokeBorder(OrbitalTheme.hairline, lineWidth: 1))
                .padding(.horizontal, 12)
                .padding(.bottom, 10)
            }
            ScrollView {
                if nodes.isEmpty {
                    VStack(spacing: 8) {
                        Image(systemName: "tray")
                            .font(.title2)
                            .foregroundStyle(accent)
                        Text("Nothing in this section yet.")
                            .font(.footnote)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.top, 40)
                } else if filtered.isEmpty {
                    Text("No page matches “\(query)”.")
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .frame(maxWidth: .infinity)
                        .padding(.top, 24)
                } else {
                    LazyVStack(spacing: 8) {
                        ForEach(filtered) { node in
                            nodeCard(node)
                        }
                    }
                    .padding(.horizontal, 10)
                    .padding(.bottom, 20)
                }
            }
            .scrollIndicators(.hidden)
            .refreshable { await store.refresh() }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background {
            if listStaysVisible {
                OrbitalTheme.background.opacity(0.6).ignoresSafeArea()
            } else {
                OrbitalBackdrop(accent: accent)
            }
        }
        .navigationTitle(sectionTitle)
        .orbitalNavigationBar(pushed: pushed)
        .task(id: resolvedSectionID) {
            guard let id = resolvedSectionID, store.sectionsByID[id] == nil else { return }
            await store.load(id)
        }
    }

    /// The node's product block, if its feed sends one.
    private func product(for node: NavNode) -> ProductRef? {
        store.products.first { $0.location.sectionID == resolvedSectionID && $0.location.nodeID == node.id }
    }

    private func nodeCard(_ node: NavNode) -> some View {
        let selected = !pushed && store.selectedNodeID == node.id
        let shape = RoundedRectangle(cornerRadius: 14, style: .continuous)
        let product = product(for: node)
        return Button {
            open(node)
        } label: {
            HStack(alignment: .center, spacing: 12) {
                AppIcon(image: node.iconImage, symbol: node.icon ?? "doc.text", tint: accent, size: pushed ? 44 : 36, filled: selected)
                VStack(alignment: .leading, spacing: 3) {
                    Text(node.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                        .multilineTextAlignment(.leading)
                        .lineLimit(2)
                    if let subtitle = node.subtitle, !subtitle.isEmpty {
                        Text(subtitle)
                            .font(.caption)
                            .foregroundStyle(OrbitalTheme.textSecondary)
                            .lineLimit(2)
                            .multilineTextAlignment(.leading)
                    }
                    if let product, let overall = ReleaseStyle.overall(product.block), overall.status != "live" {
                        StatusPill(text: overall.text, status: overall.status)
                    } else if let product, !product.block.releases.isEmpty {
                        FlowLayout(spacing: 4, lineSpacing: 4) {
                            ForEach(Array(product.block.releases.filter { $0.status != "removed" }.prefix(3).enumerated()), id: \.offset) { ReleaseChip(release: $0.element) }
                        }
                    } else if node.layout == .tabs, !node.tabs.isEmpty {
                        Text("\(node.tabs.count) tabs")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(OrbitalTheme.textTertiary)
                    }
                }
                Spacer(minLength: 0)
                if !listStaysVisible {
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
            }
            .padding(.horizontal, 10)
            .padding(.vertical, 10)
            .frame(maxWidth: .infinity, minHeight: 56, alignment: .leading)
            .background(
                shape.fill(
                    selected
                        ? AnyShapeStyle(LinearGradient(colors: [accent.opacity(0.22), accent.opacity(0.07)], startPoint: .leading, endPoint: .trailing))
                        : AnyShapeStyle(LinearGradient(colors: [OrbitalTheme.surfaceRaised.opacity(0.8), OrbitalTheme.surface], startPoint: .top, endPoint: .bottom))
                )
            )
            .overlay(
                shape.strokeBorder(selected ? accent.opacity(0.55) : Color.white.opacity(0.06), lineWidth: 1)
            )
            .contentShape(shape)
        }
        .buttonStyle(OrbitalPressStyle())
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func open(_ node: NavNode) {
        guard let id = resolvedSectionID else { return }
        if pushed {
            router.show(PageLocation(sectionID: id, nodeID: node.id))
            return
        }
        router.destination = .section(id)
        store.select(section: id, node: node.id, tab: nil, autofillNode: false)
    }
}

func orbitalSymbol(_ name: String?) -> String {
    let candidate = (name?.isEmpty == false) ? name! : "square.dashed"
    return UIImage(systemName: candidate) == nil ? "square.dashed" : candidate
}
