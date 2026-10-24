import SwiftUI
import UIKit

// Screens and rows driven by the hub's navigation document (`/v1/nav`): extra bottom-bar tabs, group
// pages, the sidebar tree (left bar on wide windows, a drawer on iPhone), plus System and every space.

// MARK: - Icons

/// A nav item's icon: its SF Symbol, or an image uploaded to the hub.
struct NavIcon: View {
    let item: NavItem
    var size: CGFloat = 20
    @EnvironmentObject private var store: OrbitalStore
    @State private var image: UIImage?

    var body: some View {
        Group {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .scaledToFill()
                    .frame(width: size, height: size)
                    .clipShape(RoundedRectangle(cornerRadius: size * 0.25, style: .continuous))
            } else {
                Image(systemName: orbitalSymbol(item.symbol))
                    .font(.system(size: size * 0.8, weight: .semibold))
                    .frame(width: size, height: size)
            }
        }
        .task(id: item.imagePath) {
            guard let path = item.imagePath else { image = nil; return }
            image = await OrbitalImageLoader.shared.image(src: path, baseURL: store.baseURL, token: OrbitalImageToken.current)
        }
    }
}

// MARK: - A custom tab's root

/// What an extra bottom-bar tab shows: its target, or the list of what is in it.
struct NavTabRoot: View {
    let tabID: String
    @EnvironmentObject private var store: OrbitalStore

    var body: some View {
        if let item = store.nav.tab(tabID) {
            NavTargetView(item: item, key: "tab:\(item.id)")
        } else {
            ContentUnavailableView("This tab was removed", systemImage: "rectangle.dashed", description: Text("The hub's navigation no longer has it."))
        }
    }
}

/// An item drawn as a whole screen.
struct NavTargetView: View {
    let item: NavItem
    let key: String

    var body: some View {
        switch item.target {
        case .section(let section, let node, _):
            if let node {
                PageCanvas(sectionID: section, nodeID: node)
            } else {
                RouteView(route: .section(section)).environment(\.orbitalPushed, false)
            }
        case .space(let space):
            SpaceView(spaceID: Spaces.key(space))
        case .view(let view):
            NavViewScreen(view: view)
        case .link, nil:
            NavGroupView(key: key, root: true)
        }
    }
}

/// A built-in screen named in the nav document.
struct NavViewScreen: View {
    let view: String
    var body: some View {
        switch view {
        case "home": HomeView()
        case "system": SystemView()
        case "satellites": AgentsView()
        case "automations": AutomationsView()
        case "manage": SectionEditorView()
        case "settings": SettingsView()
        case "agent": DeskScreen()
        case "tracker": TrackerListView()
        case "alerts": AlertsInboxView()
        case "spaces": SpacesListView()
        case "money": MoneyView()
        case "social": SocialView()
        case "analytics": AnalyticsView()
        case "videos": VideosHubView()
        case "marketing": RouteView(route: .section("marketing")).environment(\.orbitalPushed, false)
        default: BrowseView()
        }
    }
}

// MARK: - A group (bucket)

struct NavGroupView: View {
    let key: String
    var root = false
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @Environment(\.orbitalPushed) private var pushed

    private var item: NavItem? { store.nav.item(key: key, sidebar: store.defaultSidebar) }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                OrbitalScreenHeader(eyebrow: store.systemName, title: item?.title ?? "Group", accent: item?.tint ?? OrbitalTheme.accent) {
                    if root { ShellHeaderButtons() }
                }
                if let item, item.target == nil {
                    let children = item.children ?? []
                    if children.isEmpty {
                        Text("Nothing in here yet. Add items to it in the hub's navigation (`orbital nav`).")
                            .font(.footnote)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .orbitalCard(padding: 14)
                    } else {
                        VStack(spacing: 0) {
                            ForEach(Array(children.enumerated()), id: \.element.id) { index, child in
                                if index > 0 { Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1) }
                                Button {
                                    router.open(nav: child, key: "\(key)/\(child.id)")
                                } label: {
                                    NavRow(item: child)
                                }
                                .buttonStyle(OrbitalPressStyle())
                            }
                        }
                        .orbitalCard(padding: 12)
                    }
                } else if let item, case .link(let url) = item.target {
                    OrbitalActionButton(title: "Open \(item.title)", symbol: "arrow.up.right.square", kind: .primary) {
                        router.open(nav: item, key: key)
                    }
                    Text(url).font(.caption.monospaced()).foregroundStyle(OrbitalTheme.textTertiary)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, 36)
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
        }
        .scrollIndicators(.hidden)
        .orbitalBackdrop(item?.tint ?? OrbitalTheme.accent)
        .navigationTitle(item?.title ?? "Group")
        .orbitalNavigationBar(pushed: pushed)
    }
}

struct NavRow: View {
    let item: NavItem

    var body: some View {
        HStack(spacing: 12) {
            NavIcon(item: item, size: 22)
                .foregroundStyle(item.tint)
                .frame(width: 34, height: 34)
                .background(item.tint.opacity(0.12), in: RoundedRectangle(cornerRadius: 9, style: .continuous))
            Text(item.title)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(OrbitalTheme.text)
            Spacer(minLength: 0)
            if item.isGroup {
                Text("\(item.children?.count ?? 0)")
                    .font(.caption.weight(.semibold).monospacedDigit())
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            Image(systemName: "chevron.right")
                .font(.caption2.weight(.bold))
                .foregroundStyle(OrbitalTheme.textTertiary)
        }
        .padding(.vertical, 8)
        .contentShape(Rectangle())
    }
}

// MARK: - Every space

struct SpacesListView: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @Environment(\.orbitalPushed) private var pushed

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                OrbitalScreenHeader(eyebrow: store.systemName, title: "Spaces", subtitle: "\(store.directory.spaces.count) spaces") {
                    if !pushed { ShellHeaderButtons() }
                }
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 260), spacing: 10)], spacing: 10) {
                    ForEach(store.directory.spaces) { space in
                        Button { router.openSpace(space.id) } label: { SpaceTile(space: space) }
                            .buttonStyle(OrbitalPressStyle())
                    }
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, 36)
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
        }
        .scrollIndicators(.hidden)
        .orbitalBackdrop(OrbitalTheme.accent)
        .navigationTitle("Spaces")
        .orbitalNavigationBar(pushed: pushed)
    }
}

// MARK: - The sidebar tree

/// The hub's sidebar tree (or the default one), with disclosure for groups. Used by the left bar and the drawer.
struct NavTreeList: View {
    let items: [NavItem]
    let keyPrefix: String
    var depth = 0
    let onOpen: (NavItem, String) -> Void

    var body: some View {
        ForEach(items) { item in
            let key = "\(keyPrefix)\(keyPrefix.hasSuffix(":") ? "" : "/")\(item.id)"
            if item.isGroup {
                NavTreeGroup(item: item, key: key, depth: depth, onOpen: onOpen)
            } else {
                Button { onOpen(item, key) } label: { treeRow(item) }
                    .buttonStyle(.plain)
            }
        }
    }

    private func treeRow(_ item: NavItem) -> some View {
        HStack(spacing: 9) {
            NavIcon(item: item, size: 16)
                .foregroundStyle(item.tint)
            Text(item.title)
                .font(.subheadline)
                .foregroundStyle(OrbitalTheme.text)
                .lineLimit(1)
            Spacer(minLength: 0)
        }
        .padding(.leading, CGFloat(depth) * 14)
        .frame(minHeight: 34)
        .contentShape(Rectangle())
    }
}

private struct NavTreeGroup: View {
    let item: NavItem
    let key: String
    let depth: Int
    let onOpen: (NavItem, String) -> Void
    @AppStorage private var open: Bool

    init(item: NavItem, key: String, depth: Int, onOpen: @escaping (NavItem, String) -> Void) {
        self.item = item
        self.key = key
        self.depth = depth
        self.onOpen = onOpen
        _open = AppStorage(wrappedValue: depth == 0, "orbital.nav.open.\(key)")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(.easeInOut(duration: 0.18)) { open.toggle() }
            } label: {
                HStack(spacing: 9) {
                    NavIcon(item: item, size: 16)
                        .foregroundStyle(item.tint)
                    Text(item.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                        .lineLimit(1)
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .rotationEffect(.degrees(open ? 90 : 0))
                }
                .padding(.leading, CGFloat(depth) * 14)
                .frame(minHeight: 34)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if open {
                NavTreeList(items: item.children ?? [], keyPrefix: key, depth: depth + 1, onOpen: onOpen)
            }
        }
    }
}

/// iPhone: the sidebar as a sheet, from the button at the top of each tab.
struct NavDrawer: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 4) {
                    NavTreeList(items: store.sidebarTree, keyPrefix: "side:") { item, key in
                        dismiss()
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { router.open(nav: item, key: key) }
                    }
                    Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1).padding(.vertical, 8)
                    drawerLink("All spaces", "square.stack.3d.up.fill") { router.openView("spaces") }
                    drawerLink("All sections", "square.grid.2x2") { router.openView("browse") }
                }
                .padding(16)
            }
            .orbitalBackdrop(OrbitalTheme.accent)
            .navigationTitle(store.systemName)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
        .preferredColorScheme(.dark)
        .presentationDetents([.medium, .large])
    }

    private func drawerLink(_ title: String, _ symbol: String, action: @escaping () -> Void) -> some View {
        Button {
            dismiss()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25, execute: action)
        } label: {
            Label(title, systemImage: symbol)
                .font(.subheadline)
                .foregroundStyle(OrbitalTheme.textSecondary)
                .frame(maxWidth: .infinity, minHeight: 34, alignment: .leading)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: - System

/// System: the hub at the top, then Satellites, Automations, Manage and Settings.
struct SystemView: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @EnvironmentObject private var desk: DeskState
    @State private var showHub = false

    var body: some View {
        VStack(spacing: 0) {
            VStack(spacing: 10) {
                Button { showHub = true } label: { hubStrip }
                    .buttonStyle(OrbitalPressStyle())
                Picker("System", selection: $router.systemPane) {
                    ForEach(SystemPane.allCases) { Text($0.title).tag($0) }
                }
                .pickerStyle(.segmented)
            }
            .padding(.horizontal, 16)
            .padding(.top, 8)
            .padding(.bottom, 4)
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
            .background(OrbitalTheme.backgroundDeep)
            Group {
                switch router.systemPane {
                case .satellites: AgentsView()
                case .automations: AutomationsView()
                case .manage: SectionEditorView()
                case .settings: SettingsView()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .sheet(isPresented: $showHub) {
            HubSheet()
                .environmentObject(store)
                .environmentObject(router)
                .environmentObject(desk)
        }
    }

    private var hubStrip: some View {
        let online = store.onlineSatellites.count
        return HStack(spacing: 12) {
            IconTile(symbol: "server.rack", tint: OrbitalTheme.accent, size: 38, filled: true)
            VStack(alignment: .leading, spacing: 2) {
                Text(store.hubInfo?.name ?? store.systemName)
                    .font(.subheadline.weight(.bold))
                    .foregroundStyle(OrbitalTheme.text)
                    .lineLimit(1)
                Text([store.baseURL.host, "\(online)/\(store.satellites.count) online"].compactMap { $0 }.joined(separator: " · "))
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            Spacer(minLength: 0)
            ConnectionPill(label: store.connectionLabel)
            Image(systemName: "chevron.right")
                .font(.caption2.weight(.bold))
                .foregroundStyle(OrbitalTheme.textTertiary)
        }
        .padding(12)
        .background(OrbitalTheme.surfaceRaised.opacity(0.7), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .contentShape(Rectangle())
    }
}

/// The hub: address, connection, versions, the Agent model, and sign out.
struct HubSheet: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @EnvironmentObject private var desk: DeskState
    @Environment(\.dismiss) private var dismiss
    @State private var hubURL = ""
    @State private var confirmLogout = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Connection") {
                    LabeledContent("Status", value: store.connectionLabel)
                    LabeledContent("Signed in as", value: store.username.isEmpty ? "—" : store.username)
                    TextField(OrbitalBrand.hubPlaceholder, text: $hubURL)
                        .keyboardType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.body.monospaced())
                    Button("Save and reconnect") { store.updateHubURL(hubURL) }
                    Button("Reconnect now") { Task { await store.refresh() } }
                }
                if let info = store.hubInfo {
                    Section("Hub") {
                        LabeledContent("Name", value: info.name ?? "—")
                        if let machine = info.machine { LabeledContent("Machine", value: machine) }
                        if let version = info.version { LabeledContent("Hub version", value: version) }
                        LabeledContent("Machines online", value: "\(store.onlineSatellites.count) of \(store.satellites.count)")
                        LabeledContent("Agent model", value: desk.isUnavailable ? "Not set up" : (desk.model?.model ?? "Checking…"))
                    }
                }
                Section {
                    Button("Sign out", role: .destructive) { confirmLogout = true }
                } footer: {
                    Text("\(OrbitalBrand.appName) \(OrbitalBrand.version)")
                }
            }
            .scrollContentBackground(.hidden)
            .orbitalBackdrop(OrbitalTheme.accent)
            .navigationTitle("Hub")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            .confirmationDialog("Sign out of \(store.hubInfo?.name ?? "this hub")?", isPresented: $confirmLogout, titleVisibility: .visible) {
                Button("Sign out", role: .destructive) {
                    dismiss()
                    store.logout()
                    desk.logout()
                }
                Button("Cancel", role: .cancel) {}
            }
            .onAppear { hubURL = store.pendingHubURL ?? store.hubURLString }
        }
        .preferredColorScheme(.dark)
    }
}
