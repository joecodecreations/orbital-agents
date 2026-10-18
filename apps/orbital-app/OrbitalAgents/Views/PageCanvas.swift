import SwiftUI
import UIKit

struct PageCanvas: View {
    /// Set when the page is pushed on an iPhone stack. Nil reads the shared selection (wide layout).
    var sectionID: String? = nil
    var nodeID: String? = nil
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @Environment(\.orbitalDetailBesideList) private var listStaysVisible
    @Environment(\.orbitalPushed) private var pushed
    @Environment(\.orbitalStack) private var stack
    @Namespace private var tabPill
    /// Pushed pages keep their own tab, so two stacks can show different tabs of the same app.
    @State private var localTab: String?

    private var pinned: Bool { sectionID != nil }

    private var section: SectionDocument? {
        guard let id = sectionID ?? store.selectedSectionID else { return nil }
        return store.sectionsByID[id]
    }

    private var node: NavNode? {
        guard let section, section.layout == .sidebar else { return nil }
        guard let id = pinned ? nodeID : store.selectedNodeID else { return nil }
        return section.sortedNodes.first { $0.id == id }
    }

    private var chosenTab: String? {
        pinned ? localTab : store.selectedTabID
    }

    private func choose(tab id: String) {
        if pinned {
            localTab = id
        } else {
            store.select(section: section?.id, node: node?.id, tab: id)
        }
    }

    private var tabs: [PageTab] {
        if let node {
            return node.layout == .tabs ? node.tabs : []
        }
        if section?.layout == .tabs {
            return section?.tabs ?? []
        }
        return []
    }

    private var currentTabID: String? {
        tabs.first { $0.id == chosenTab }?.id ?? tabs.first?.id
    }

    private var blocks: [ContentBlock] {
        if let node {
            if node.layout == .tabs {
                let tab = tabs.first { $0.id == chosenTab } ?? tabs.first
                return tab?.blocks ?? []
            }
            return node.blocks
        }
        if section?.layout == .tabs {
            let tab = tabs.first { $0.id == chosenTab } ?? tabs.first
            return tab?.blocks ?? []
        }
        return section?.blocks ?? []
    }

    private var location: PageLocation? {
        guard let section else { return nil }
        return PageLocation(sectionID: section.id, nodeID: node?.id, tabID: tabs.isEmpty ? nil : currentTabID)
    }

    /// A tabs or sidebar section's own blocks, usually a summary. They sit above every tab's or node's
    /// content (and above "pick one" when no node is chosen), and act as blocks of the section itself.
    private var leadBlocks: [ContentBlock] {
        guard let section, section.layout != .page else { return [] }
        return section.blocks
    }

    private var leadLocation: PageLocation? {
        section.map { PageLocation(sectionID: $0.id, nodeID: nil, tabID: nil) }
    }

    private func blockStack(_ lead: [ContentBlock], _ page: [ContentBlock]) -> some View {
        LazyVStack(alignment: .leading, spacing: OrbitalTheme.blockSpacing) {
            ForEach(lead) { block in
                BlockView(block: block, accent: accent, location: leadLocation)
            }
            ForEach(page) { block in
                BlockView(block: block, accent: accent, location: location)
            }
        }
        .frame(maxWidth: OrbitalTheme.columnMax)
        .frame(maxWidth: .infinity)
        .padding(.horizontal, 16)
        .padding(.top, 16)
    }

    private var pageImage: String? {
        node?.iconImage ?? (node == nil ? section?.iconImage : nil)
    }

    private var pageTitle: String {
        node?.title ?? section?.title ?? OrbitalBrand.appName
    }

    private var pageSubtitle: String? {
        let value = node?.subtitle ?? section?.subtitle
        return (value?.isEmpty == false) ? value : nil
    }

    private var pageSymbol: String {
        node?.icon ?? section?.icon ?? "square.grid.2x2"
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            ScrollView {
                if section?.layout == .sidebar && node == nil {
                    VStack(spacing: 0) {
                        if !leadBlocks.isEmpty {
                            blockStack(leadBlocks, [])
                        }
                        emptyState(
                            symbol: "sidebar.left",
                            title: section?.nodes.isEmpty == false ? "Pick one from the list" : "Nothing here yet",
                            detail: section?.nodes.isEmpty == false
                                ? "Each page in \(section?.title ?? "this section") opens here."
                                : "The feed that owns this section has not sent pages yet."
                        )
                    }
                    .padding(.bottom, 36)
                } else if blocks.isEmpty && leadBlocks.isEmpty {
                    emptyState(
                        symbol: "tray",
                        title: "No content in this payload yet.",
                        detail: "The page fills in when its feed pushes blocks."
                    )
                } else {
                    blockStack(leadBlocks, blocks)
                        .padding(.bottom, 36)
                }
            }
            .scrollIndicators(.hidden)
            .refreshable { await store.refresh() }
        }
        .orbitalBackdrop(accent)
        .navigationTitle(node?.title ?? section?.title ?? "")
        .orbitalNavigationBar(pushed: pushed)
        .onAppear(perform: takePendingTab)
        .onChange(of: router.navigationToken) {
            // A notification for a tab of the page already on screen reuses this view, so take the tab here too.
            if isPendingTarget, let tab = router.pendingTabID {
                localTab = tab
                router.pendingTabID = nil
            }
        }
        .task(id: sectionID ?? store.selectedSectionID) {
            guard let id = sectionID ?? store.selectedSectionID else { return }
            if store.sectionsByID[id] == nil {
                await store.load(id)
            } else if !pinned, blocks.isEmpty, section?.layout != .page {
                await store.load(id)
            }
        }
    }

    /// A notification or Home tile can ask for a tab. Take it once, on the page it was meant for.
    private func takePendingTab() {
        guard localTab == nil, isPendingTarget, let tab = router.pendingTabID else { return }
        localTab = tab
        router.pendingTabID = nil
    }

    /// The pending tab is for this page only on the stack it was opened on.
    private var isPendingTarget: Bool {
        pinned && router.pendingStack == stack && router.pendingSectionID == sectionID && router.pendingNodeID == nodeID
    }

    // MARK: - Header

    private var header: some View {
        VStack(alignment: .leading, spacing: 10) {
            if section?.layout == .sidebar, node != nil, !listStaysVisible, !pushed {
                Button {
                    store.select(section: section?.id, node: nil, tab: nil, autofillNode: false)
                } label: {
                    HStack(spacing: 5) {
                        Image(systemName: "chevron.left")
                            .font(.caption.weight(.bold))
                        Text(section?.title ?? "Back")
                            .lineLimit(1)
                    }
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(accent)
                    .padding(.horizontal, 12)
                    .frame(minHeight: 34)
                    .background(accent.opacity(0.12), in: Capsule())
                    .overlay(Capsule().strokeBorder(accent.opacity(0.25), lineWidth: 1))
                    .frame(minHeight: 44)
                    .contentShape(Rectangle())
                }
                .buttonStyle(OrbitalPressStyle())
            }
            HStack(alignment: .top, spacing: 12) {
                VStack(alignment: .leading, spacing: 5) {
                    if !pushed {
                        breadcrumbView
                    }
                    Text(pageTitle)
                        .font(.title2.weight(.bold))
                        .foregroundStyle(OrbitalTheme.text)
                        .lineLimit(3)
                        .fixedSize(horizontal: false, vertical: true)
                    if let pageSubtitle {
                        Text(pageSubtitle)
                            .font(.subheadline)
                            .foregroundStyle(OrbitalTheme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let updated = OrbitalDates.relative(section?.updatedAt) {
                        HStack(spacing: 5) {
                            Image(systemName: "clock.arrow.circlepath")
                                .font(.system(size: 10, weight: .bold))
                            Text("Updated \(updated)")
                                .font(.caption2.weight(.medium))
                        }
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .padding(.top, 1)
                    }
                    if let location, let feeds = store.jobs?.jobs(for: location), !feeds.isEmpty {
                        PageFeedStrip(jobs: feeds, accent: accent) {
                            Task { await store.refreshPage(location) }
                        } onOpen: {
                            router.openAutomations(job: feeds.first { $0.health == .error || $0.health == .warn }?.id ?? feeds.first?.id)
                        }
                    }
                }
                Spacer(minLength: 0)
                VStack(spacing: 8) {
                    AppIcon(image: pageImage, symbol: pageSymbol, tint: accent, size: 48)
                        .shadow(color: accent.opacity(0.35), radius: 12)
                    if let id = section?.id {
                        pinButton(id)
                    }
                }
            }
            if !tabs.isEmpty {
                tabStrip
            }
        }
        .padding(.horizontal, 16)
        .padding(.top, 10)
        .padding(.bottom, 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(alignment: .bottom) { OrbitalRule() }
    }

    @ViewBuilder
    private var breadcrumbView: some View {
        if let section {
            HStack(spacing: 5) {
                Text(section.title.uppercased())
                    .foregroundStyle(accent)
                if let node {
                    Image(systemName: "chevron.right")
                        .font(.system(size: 8, weight: .heavy))
                        .foregroundStyle(OrbitalTheme.textTertiary)
                    Text(node.title.uppercased())
                        .foregroundStyle(OrbitalTheme.textSecondary)
                }
            }
            .font(.caption2.weight(.bold))
            .tracking(0.8)
            .lineLimit(1)
            .truncationMode(.middle)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(breadcrumb)
        }
    }

    private var tabStrip: some View {
        ScrollViewReader { proxy in
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(tabs) { tab in
                        let selected = currentTabID == tab.id
                        Button {
                            choose(tab: tab.id)
                        } label: {
                            HStack(spacing: 6) {
                                if let icon = tab.icon {
                                    Image(systemName: orbitalSymbol(icon))
                                        .font(.caption.weight(.bold))
                                }
                                Text(tab.title)
                                    .font(.subheadline.weight(.semibold))
                                    .lineLimit(1)
                            }
                            .padding(.horizontal, 14)
                            .frame(minHeight: 38)
                            .foregroundStyle(selected ? OrbitalTheme.backgroundDeep : OrbitalTheme.textSecondary)
                            .background {
                                Capsule()
                                    .fill(Color.white.opacity(0.05))
                                    .overlay(Capsule().strokeBorder(Color.white.opacity(0.07), lineWidth: 1))
                                if selected {
                                    Capsule()
                                        .fill(LinearGradient(colors: [accent, accent.opacity(0.82)], startPoint: .top, endPoint: .bottom))
                                        .shadow(color: accent.opacity(0.45), radius: 8, y: 2)
                                        .matchedGeometryEffect(id: "pill", in: tabPill)
                                }
                            }
                            .contentShape(Capsule())
                        }
                        .buttonStyle(.plain)
                        .id(tab.id)
                        .accessibilityAddTraits(selected ? .isSelected : [])
                    }
                }
                .padding(.vertical, 3)
                .animation(.spring(response: 0.32, dampingFraction: 0.82), value: currentTabID)
            }
            .onChange(of: currentTabID) { _, id in
                guard let id else { return }
                withAnimation(.easeInOut(duration: 0.25)) { proxy.scrollTo(id, anchor: .center) }
            }
        }
    }

    /// Pins the section to Home (here and on every device), or unpins it.
    private func pinButton(_ id: String) -> some View {
        let pinned = store.isPinned(id)
        return Button {
            UISelectionFeedbackGenerator().selectionChanged()
            store.togglePin(id)
        } label: {
            Image(systemName: pinned ? "pin.fill" : "pin")
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(pinned ? accent : OrbitalTheme.textTertiary)
                .frame(width: 30, height: 30)
                .background(Color.white.opacity(pinned ? 0.1 : 0.05), in: Circle())
                .frame(width: 44, height: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(OrbitalPressStyle())
        .padding(.vertical, -8)
        .accessibilityLabel(pinned ? "Unpin from Home" : "Pin to Home")
    }

    private func emptyState(symbol: String, title: String, detail: String) -> some View {
        VStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(accent.opacity(0.10))
                    .frame(width: 76, height: 76)
                Image(systemName: symbol)
                    .font(.system(size: 28, weight: .semibold))
                    .foregroundStyle(accent)
            }
            Text(title)
                .font(.headline)
                .foregroundStyle(OrbitalTheme.text)
                .multilineTextAlignment(.center)
            Text(detail)
                .font(.footnote)
                .foregroundStyle(OrbitalTheme.textTertiary)
                .multilineTextAlignment(.center)
        }
        .padding(.horizontal, 24)
        .frame(maxWidth: .infinity)
        .padding(.top, 56)
    }

    private var breadcrumb: String {
        if let section, let node {
            return "\(section.title)  /  \(node.title)"
        }
        return section?.title ?? ""
    }

    private var accent: Color {
        OrbitalTheme.accentColor(section?.accent)
    }
}
