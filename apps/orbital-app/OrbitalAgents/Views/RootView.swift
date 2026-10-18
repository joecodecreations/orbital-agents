import SwiftUI
import UIKit

private struct OrbitalDetailBesideListKey: EnvironmentKey {
    static let defaultValue = false
}

extension EnvironmentValues {
    /// Wide layout keeps a sidebar section's page list on screen next to the page.
    var orbitalDetailBesideList: Bool {
        get { self[OrbitalDetailBesideListKey.self] }
        set { self[OrbitalDetailBesideListKey.self] = newValue }
    }
}

/// The shell: sign-in until there is a session, then a tab bar on a narrow window (iPhone portrait)
/// or the left bar, page list, and page side by side on a wide one (iPad, Mac, iPhone landscape).
struct RootView: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @EnvironmentObject private var desk: DeskState
    @Environment(\.scenePhase) private var scenePhase
    /// Tabs opened at least once on iPhone. Their stacks stay alive behind the visible one.
    @State private var visitedTabs: Set<RootTab> = [.home]
    /// The tab bar steps aside while typing, as the system one is covered by the keyboard.
    @State private var keyboardUp = false

    var body: some View {
        Group {
            if store.isLoggedIn {
                shell
            } else {
                SignInView()
            }
        }
        .background(OrbitalTheme.backgroundDeep.ignoresSafeArea())
        .preferredColorScheme(.dark)
        .tint(accent)
        .onOpenURL { url in
            router.handle(url) { hub in store.connectLink(hub: hub) }
            if store.pendingHubURL != nil { router.openSettings() }
        }
        .onChange(of: router.navigationToken) {
            applyRouter()
        }
        .onReceive(NotificationCenter.default.publisher(for: .orbitalAlertAction)) { note in
            handleAlertAction(note)
        }
        .onReceive(NotificationCenter.default.publisher(for: .orbitalRefresh)) { _ in
            Task { await store.refresh() }
        }
        .onChange(of: store.isLoggedIn) { _, loggedIn in
            if !loggedIn { desk.logout() }
        }
        .sheet(item: $router.runDraft) { draft in
            RunAgentSheet(draft: draft)
                .environmentObject(store)
                .environmentObject(router)
        }
        .onAppear {
            desk.attach(store)
            store.tickAlertClock()
            #if DEBUG
            if let route = OrbitalPreview.launchRoute {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { router.handle(route) }
            }
            #endif
        }
        .onChange(of: scenePhase) { _, phase in
            // Scheduled reminders that came due while the app was away move to Waiting and the badge.
            // Then catch up on what other devices did meanwhile (alerts cleared, pins moved).
            if phase == .active {
                store.tickAlertClock()
                Task { await store.catchUp() }
            }
        }
    }

    private var accent: Color {
        guard case .section = router.destination else { return OrbitalTheme.accent }
        let hex = store.sectionsByID[store.selectedSectionID ?? ""]?.accent
        return OrbitalTheme.accentColor(hex)
    }

    private var shell: some View {
        GeometryReader { geo in
            let wide = geo.size.width >= 760
            Group {
                if wide {
                    wideShell
                } else {
                    compactShell
                }
            }
            .onAppear { router.isCompact = !wide }
            .onChange(of: wide) { _, value in router.isCompact = !value }
        }
        .overlay(alignment: .top) { banner }
        .overlay(alignment: .bottom) { toast }
    }

    /// "Marked done · syncing" and friends, just above the tab bar.
    private var toast: some View {
        ZStack(alignment: .bottom) {
            if let text = store.toast {
                OrbitalToast(text: text) { store.dismissToast() }
                    .padding(.bottom, router.isCompact ? 66 : 20)
                    .transition(.move(edge: .bottom).combined(with: .opacity))
            }
        }
        .animation(.spring(response: 0.36, dampingFraction: 0.82), value: store.toast)
    }

    /// iPhone: the tab bar. Each tab keeps its stack once visited.
    private var compactShell: some View {
        // A VStack, not `.safeAreaInset`: screens with a bottom bar of their own (the desk's mic and
        // text box) must sit above the tab bar, not under it.
        VStack(spacing: 0) {
            compactTabs
            if !keyboardUp {
                OrbitalTabBar(
                    selection: router.tab,
                    badges: [.alerts: store.unackedCount],
                    activity: [
                        .agents: store.activeAgentJobs.count,
                        .desk: desk.tasks.values.filter(\.isActive).count,
                        .tracker: desk.trackerActive,
                    ]
                ) { router.select(tab: $0) }
                .transition(.opacity)
            }
        }
        .onAppear { visitedTabs.insert(router.tab) }
        .onChange(of: router.tab) { _, tab in visitedTabs.insert(tab) }
        .onReceive(NotificationCenter.default.publisher(for: UIResponder.keyboardWillShowNotification)) { _ in
            keyboardUp = true
        }
        .onReceive(NotificationCenter.default.publisher(for: UIResponder.keyboardWillHideNotification)) { _ in
            keyboardUp = false
        }
    }

    private var compactTabs: some View {
        ZStack {
            compactTab(.home) {
                NavigationStack(path: $router.homePath) {
                    HomeView()
                        .navigationDestination(for: BrowseRoute.self) { RouteView(route: $0) }
                }
            }
            compactTab(.agents) {
                NavigationStack(path: $router.agentsPath) {
                    AgentsView()
                        .navigationDestination(for: BrowseRoute.self) { RouteView(route: $0) }
                }
            }
            compactTab(.desk) {
                NavigationStack(path: $router.deskPath) {
                    DeskScreen()
                        .navigationDestination(for: BrowseRoute.self) { RouteView(route: $0) }
                }
            }
            compactTab(.tracker) {
                NavigationStack(path: $router.trackerPath) {
                    TrackerListView()
                        .navigationDestination(for: BrowseRoute.self) { RouteView(route: $0) }
                }
            }
            compactTab(.alerts) {
                NavigationStack(path: $router.alertsPath) {
                    AlertsInboxView()
                        .navigationDestination(for: BrowseRoute.self) { RouteView(route: $0) }
                }
            }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    /// One tab's stack. Built the first time it is opened, then kept so its scroll and pushes survive a switch.
    @ViewBuilder
    private func compactTab<Content: View>(_ tab: RootTab, @ViewBuilder content: () -> Content) -> some View {
        let selected = router.tab == tab
        if selected || visitedTabs.contains(tab) {
            content()
                .environment(\.orbitalStack, tab)
                .opacity(selected ? 1 : 0)
                .allowsHitTesting(selected)
                .accessibilityHidden(!selected)
                .zIndex(selected ? 1 : 0)
        }
    }

    private var wideShell: some View {
        HStack(spacing: 0) {
            SidebarColumn(showsSummaries: true)
                .frame(width: 244)
            columnRule
            if showsNodes {
                NodeColumn()
                    .frame(width: 240)
                columnRule
            }
            NavigationStack(path: $router.widePath) {
                detail
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
                    .navigationDestination(for: BrowseRoute.self) { RouteView(route: $0) }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .environment(\.orbitalDetailBesideList, showsNodes)
        .background(OrbitalTheme.backgroundDeep.ignoresSafeArea())
    }

    private var columnRule: some View {
        LinearGradient(
            colors: [Color.white.opacity(0.09), Color.white.opacity(0.03)],
            startPoint: .top,
            endPoint: .bottom
        )
        .frame(width: 1)
        .ignoresSafeArea()
    }

    private var showsNodes: Bool {
        guard case .section = router.destination, let id = store.selectedSectionID else { return false }
        if let layout = store.sectionsByID[id]?.layout {
            return layout == .sidebar
        }
        return store.catalog.first { $0.id == id }?.layout == .sidebar
    }

    @ViewBuilder
    private var detail: some View {
        switch router.destination {
        case .home:
            HomeView()
        case .agents:
            AgentsView()
        case .desk:
            DeskScreen()
        case .tracker:
            TrackerListView()
        case .alerts:
            AlertsInboxView()
        case .manage:
            SectionEditorView()
        case .settings:
            SettingsView()
        case .automations:
            AutomationsView()
        case .section(let id):
            if id == nil && store.selectedSectionID == nil {
                BrowseView()
            } else {
                PageCanvas()
            }
        }
    }

    private var banner: some View {
        ZStack(alignment: .top) {
            if let alert = store.inAppBanner {
                AlertBanner(
                    alert: alert,
                    entry: store.catalog.first { $0.id == alert.sectionId },
                    onOpen: {
                        if let url = alert.resolvedLaunchURL {
                            router.handle(url)
                        }
                        Task { await store.ack([alert.id]) }
                    },
                    onClose: { store.dismissBanner() }
                )
                .id(alert.id)
                .transition(.move(edge: .top).combined(with: .opacity))
            }
        }
        .animation(.spring(response: 0.42, dampingFraction: 0.8), value: store.inAppBanner?.id)
    }

    private func applyRouter() {
        if case .section = router.destination {
            store.select(
                section: router.pendingSectionID,
                node: router.pendingNodeID,
                tab: router.pendingTabID,
                autofillNode: false
            )
        }
        if let prompt = router.deskDraft {
            desk.draft = prompt
            router.deskDraft = nil
        }
        if let task = router.deskFocusTask {
            desk.focusTaskID = task
            router.deskFocusTask = nil
        }
    }

    private func handleAlertAction(_ note: Notification) {
        let action = note.userInfo?["action"] as? String ?? ""
        let alertID = note.userInfo?["alert_id"] as? String
        if action.hasPrefix("act.") {
            // A write-back button. The delegate already posted it and acked the alert; never navigate.
            store.noteNotificationAction(
                alertID: alertID,
                type: note.userInfo?["action_type"] as? String ?? String(action.dropFirst("act.".count)),
                value: note.userInfo?["action_value"] as? [String: Any] ?? [:],
                failure: note.userInfo?["action_error"] as? String
            )
            return
        }
        if action == "open" || action == "dismiss", let alertID {
            Task { await store.ack([alertID]) }
        }
        if action == "open", let urlString = note.userInfo?["launch_url"] as? String, let url = URL(string: urlString) {
            router.handle(url)
        }
        if let text = note.userInfo?["response_text"] as? String, !text.isEmpty {
            router.openDesk(prompt: text)
        }
    }
}

/// Small capsule that confirms a tap was sent, and later that it was saved. Tap to hide it.
struct OrbitalToast: View {
    let text: String
    let onTap: () -> Void

    private var tint: Color {
        if text.hasPrefix("Not ") || text.hasPrefix("Couldn't") { return OrbitalTheme.danger }
        if text.hasPrefix("Saved") || text.hasPrefix("Started") { return OrbitalTheme.success }
        return OrbitalTheme.accent
    }

    private var symbol: String {
        if text.hasPrefix("Not ") || text.hasPrefix("Couldn't") { return "exclamationmark.triangle.fill" }
        if text.hasPrefix("Saved") || text.hasPrefix("Started") { return "checkmark.circle.fill" }
        return "arrow.triangle.2.circlepath"
    }

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: symbol)
                .font(.footnote.weight(.bold))
                .foregroundStyle(tint)
            Text(text)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(OrbitalTheme.text)
                .lineLimit(2)
                .multilineTextAlignment(.leading)
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 11)
        .background(OrbitalTheme.surfaceRaised.opacity(0.94), in: Capsule())
        .background(.ultraThinMaterial, in: Capsule())
        .overlay(Capsule().strokeBorder(tint.opacity(0.35), lineWidth: 1))
        .shadow(color: .black.opacity(0.5), radius: 16, y: 8)
        .padding(.horizontal, 24)
        .frame(maxWidth: 520)
        .contentShape(Capsule())
        .onTapGesture(perform: onTap)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.updatesFrequently)
    }
}

/// Foreground banner for a fresh alert. Tap opens it, swipe up or the close button hides it.
struct AlertBanner: View {
    let alert: OrbitalAlert
    let entry: CatalogEntry?
    let onOpen: () -> Void
    let onClose: () -> Void
    @State private var drag: CGFloat = 0

    var body: some View {
        let tint = OrbitalTheme.accentColor(entry?.accent)
        let shape = RoundedRectangle(cornerRadius: 22, style: .continuous)
        HStack(alignment: .top, spacing: 12) {
            AppIcon(image: entry?.iconImage, symbol: entry?.icon ?? "bell.badge.fill", tint: tint, size: 38, filled: true)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text((entry?.title ?? OrbitalBrand.appName).uppercased())
                        .font(.caption2.weight(.bold))
                        .tracking(0.6)
                        .foregroundStyle(tint)
                        .lineLimit(1)
                    Text("now")
                        .font(.caption2)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                    Spacer(minLength: 0)
                }
                Text(alert.title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
                    .lineLimit(2)
                if let subtitle = alert.subtitle, !subtitle.isEmpty {
                    Text(subtitle)
                        .font(.footnote.weight(.medium))
                        .foregroundStyle(OrbitalTheme.text.opacity(0.85))
                        .lineLimit(1)
                }
                Text(alert.body)
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .lineLimit(3)
            }
            Button(action: onClose) {
                Image(systemName: "xmark")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .frame(width: 28, height: 28)
                    .background(Color.white.opacity(0.08), in: Circle())
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .padding(.top, -8)
            .padding(.trailing, -8)
            .accessibilityLabel("Dismiss banner")
        }
        .padding(14)
        .frame(maxWidth: 560)
        .background(.ultraThinMaterial, in: shape)
        .background(OrbitalTheme.surface.opacity(0.7), in: shape)
        .overlay(shape.strokeBorder(Color.white.opacity(0.12), lineWidth: 1))
        .overlay(alignment: .leading) {
            Capsule()
                .fill(tint)
                .frame(width: 3)
                .padding(.vertical, 16)
        }
        .shadow(color: .black.opacity(0.55), radius: 24, y: 12)
        .padding(.horizontal, 10)
        .padding(.top, 4)
        .offset(y: min(drag, 0))
        .contentShape(shape)
        .onTapGesture(perform: onOpen)
        .gesture(
            DragGesture(minimumDistance: 8)
                .onChanged { drag = $0.translation.height }
                .onEnded { value in
                    if value.translation.height < -24 {
                        onClose()
                    } else {
                        withAnimation(.spring(response: 0.3, dampingFraction: 0.8)) { drag = 0 }
                    }
                }
        )
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
        .accessibilityHint("Opens the alert")
    }
}
