import Foundation
import UIKit

/// Everything the screens draw, kept in step with the hub: the catalog and sections, alerts, pulses,
/// the scheduler, satellites and their agent jobs. One sync on launch and on return to the front,
/// then the live socket. Every section is cached on disk, so the app opens with the last data offline.
@MainActor
final class OrbitalStore: ObservableObject {
    @Published var hubURLString: String {
        didSet { UserDefaults.standard.set(hubURLString, forKey: Keys.hubURL) }
    }
    @Published var username: String {
        didSet { UserDefaults.standard.set(username, forKey: Keys.username) }
    }
    @Published private(set) var isLoggedIn = false
    @Published var isLoggingIn = false
    @Published var loginError: String?
    /// A line above the sign-in form, e.g. after an orbital://connect link filled in the address.
    @Published var signInNotice: String?
    /// A hub address from an orbital://connect link while already signed in. Settings offers to switch.
    @Published var pendingHubURL: String?
    /// Who this hub is (`/v1/hub/info`), once it has answered.
    @Published private(set) var hubInfo: HubInfo?
    @Published private(set) var catalog: [CatalogEntry] = []
    /// Spaces the hub declares in its catalog.
    @Published private(set) var declaredSpaces: [SpaceInfo] = []
    @Published private(set) var sectionsByID: [String: SectionDocument] = [:] {
        didSet { rebuildInsights() }
    }
    /// Every product block across sections.
    @Published private(set) var products: [ProductRef] = []
    /// Every ledger block across sections.
    @Published private(set) var ledgers: [LedgerRef] = []
    /// Every to-do across tasks blocks, deduped by section and item id.
    @Published private(set) var tasks: [TaskRef] = []
    /// Every series block. Home, the space pages, and the product pages chart these.
    @Published private(set) var series: [SeriesRef] = []
    /// Spaces and their products, from the catalog and the blocks.
    @Published private(set) var directory = SpaceDirectory()
    /// Sections filed under System (machine health and the like), shown there instead of as a space.
    @Published private(set) var systemSectionIDs: [String] = []
    /// Extra bottom-bar items and the sidebar tree, from the hub.
    @Published var nav = NavDoc.empty
    /// Subscription plans and the AI work that used the most (`/v1/usage`).
    @Published var usageReport: UsageReport?
    /// One short line at the bottom of the shell ("Marked done · syncing"). Clears itself.
    @Published private(set) var toast: String?
    /// Bumped when a scheduled reminder comes due, so the badge and the inbox move it to Waiting.
    @Published private(set) var alertClock = Date()
    /// Realtime values by key, from sync and the live socket.
    @Published private(set) var pulses: [String: PulseItem] = [:]
    @Published var selectedSectionID: String?
    @Published var selectedNodeID: String?
    @Published var selectedTabID: String?
    @Published private(set) var alerts: [OrbitalAlert] = [] {
        didSet { scheduleAlertClock() }
    }
    /// Every feed's schedule, last run, and next run, from the scheduler. Nil until the hub reports one.
    @Published private(set) var jobs: JobsSnapshot?
    /// Machines that joined the hub, the hub's own satellite included.
    @Published private(set) var satellites: [Satellite] = []
    /// Agent jobs across every machine, running first.
    @Published private(set) var agentJobs: [AgentJob] = []
    @Published private(set) var agentsLoaded = false
    @Published private(set) var agentsError: String?
    /// Sections pinned to Home: shared through the hub's prefs, kept here too.
    @Published private(set) var pinnedSections: [String] = []
    @Published var connectionLabel = "Offline"
    @Published var inAppBanner: OrbitalAlert?
    @Published var editorError: String?

    let deviceID: String
    private let api: OrbitalAPI
    private let socket = LiveSocket()
    private let keychain = OrbitalKeychain()
    private var bannerTask: Task<Void, Never>?
    private var toastTask: Task<Void, Never>?
    private var alertClockTask: Task<Void, Never>?
    private var agentsRefresh: Task<Void, Never>?
    private var refreshObserver: NSObjectProtocol?
    /// Jobs this device asked to run, with when, so their finish shows a toast.
    private var requestedJobs: [String: Date] = [:]
    /// Agent jobs started from this device, so their finish shows a toast.
    private var startedAgentJobs: Set<String> = []
    /// Compact width opens a sidebar section on its node list, with no page chosen yet.
    private var keepNodeEmpty = false
    /// When the last full sync finished, so coming back to the front or a socket reconnect catches up once.
    private var lastSyncAt: Date?
    private var syncing = false
    /// This session has told the hub who this device is (and its push token, if any).
    private var deviceRegistered = false
    private var pushObserver: NSObjectProtocol?

    enum Keys {
        static let hubURL = "orbital.hubURL"
        static let username = "orbital.username"
        static let deviceID = "orbital.deviceID"
        static let pinned = "orbital.pinnedSections"
        static let pinnedUnsynced = "orbital.pinnedSections.unsynced"
        static let retracted = "orbital.retractedAlertIDs"
        static let apnsToken = "orbital.apnsToken"
    }

    var baseURL: URL {
        Self.parse(hubURLString) ?? URL(string: "http://127.0.0.1:\(OrbitalBrand.defaultHubPort)")!
    }

    /// The hub's `system.name`, or the app's name before the hub has answered.
    var systemName: String { Insights.nonEmpty(hubInfo?.name) ?? OrbitalBrand.appName }

    /// Unread alerts that have fired. A reminder scheduled for later does not count until its time.
    var unackedCount: Int {
        let now = max(alertClock, Date())
        return alerts.filter { $0.isWaiting(at: now) }.count
    }

    /// Unread reminders still waiting for their `deliver_at`, soonest first.
    var scheduledAlerts: [OrbitalAlert] {
        let now = max(alertClock, Date())
        return alerts
            .filter { !$0.isAcked && $0.isScheduled(at: now) }
            .sorted { ($0.deliverDate ?? .distantFuture) < ($1.deliverDate ?? .distantFuture) }
    }

    var sortedCatalog: [CatalogEntry] {
        catalog.sorted { lhs, rhs in
            if lhs.order != rhs.order { return lhs.order < rhs.order }
            return lhs.title.localizedCaseInsensitiveCompare(rhs.title) == .orderedAscending
        }
    }

    var onlineSatellites: [Satellite] { satellites.filter(\.isOnline) }
    var activeAgentJobs: [AgentJob] { agentJobs.filter(\.isActive) }

    init() {
        hubURLString = UserDefaults.standard.string(forKey: Keys.hubURL) ?? ""
        username = UserDefaults.standard.string(forKey: Keys.username) ?? ""
        if let existing = UserDefaults.standard.string(forKey: Keys.deviceID) {
            deviceID = existing
        } else {
            let created = UUID().uuidString.lowercased()
            UserDefaults.standard.set(created, forKey: Keys.deviceID)
            deviceID = created
        }
        let base = Self.parse(UserDefaults.standard.string(forKey: Keys.hubURL) ?? "")
            ?? URL(string: "http://127.0.0.1:\(OrbitalBrand.defaultHubPort)")!
        api = OrbitalAPI(baseURL: base)
        api.deviceID = deviceID
        pinnedSections = UserDefaults.standard.stringArray(forKey: Keys.pinned) ?? []
        let cachedCatalog = SectionCache.loadCatalog()
        catalog = cachedCatalog.sections
        declaredSpaces = cachedCatalog.spaces ?? []
        sectionsByID = SectionCache.loadSections()
        alerts = SectionCache.loadAlerts()
        pulses = Dictionary(SectionCache.loadPulses().map { ($0.key, $0) }, uniquingKeysWith: { _, new in new })
        #if DEBUG
        if OrbitalPreview.isActive {
            loadPreview()
            return
        }
        #endif
        rebuildInsights()
        if let token = keychain.get(), Self.parse(hubURLString) != nil {
            api.token = token
            OrbitalImageToken.current = token
            isLoggedIn = true
            Task { await self.refresh() }
        }
        refreshObserver = NotificationCenter.default.addObserver(
            forName: .orbitalRefresh,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in await self?.refresh() }
        }
        OrbitalNotifications.backgroundSync = { [weak self] in await self?.refresh() }
        pushObserver = NotificationCenter.default.addObserver(forName: .orbitalPushToken, object: nil, queue: .main) { [weak self] note in
            guard let token = note.userInfo?["token"] as? String else { return }
            MainActor.assumeIsolated {
                UserDefaults.standard.set(token, forKey: Keys.apnsToken)
                self?.deviceRegistered = false
                Task { await self?.registerDevice() }
            }
        }
    }

    #if DEBUG
    /// Simulator and screenshot runs: everything from the preview folder, no hub, no sign-in.
    private func loadPreview() {
        pulses = Dictionary(OrbitalPreview.pulses().map { ($0.key, $0) }, uniquingKeysWith: { _, new in new })
        let documents = OrbitalPreview.sections()
        sectionsByID = Dictionary(documents.map { ($0.id, $0) }, uniquingKeysWith: { _, new in new })
        catalog = documents.map(Self.catalogEntry(for:))
        declaredSpaces = OrbitalPreview.spaces()
        alerts = OrbitalPreview.alerts()
        jobs = OrbitalPreview.jobs()
        satellites = OrbitalPreview.satellites()
        nav = OrbitalPreview.nav()
        agentJobs = AgentJobsSort.ordered(OrbitalPreview.agentJobs())
        agentsLoaded = true
        hubInfo = OrbitalPreview.hubInfo()
        if pinnedSections.isEmpty {
            pinnedSections = OrbitalPreview.prefs()?.pinnedSections ?? documents.filter { $0.pinned == true }.map(\.id)
        }
        isLoggedIn = true
        username = "preview"
        hubURLString = "http://preview.local:\(OrbitalBrand.defaultHubPort)"
        connectionLabel = "Preview"
        rebuildInsights()
    }
    #endif

    // MARK: Signing in

    /// Checks the address is an Orbital Agents hub, then signs in. Errors say what to check.
    func login(username: String, password: String) async {
        isLoggingIn = true
        loginError = nil
        defer { isLoggingIn = false }
        let name = username.trimmingCharacters(in: .whitespacesAndNewlines)
        self.username = name
        guard let base = Self.parse(hubURLString) else {
            loginError = "Enter your hub's address, like \(OrbitalBrand.hubPlaceholder)."
            return
        }
        hubURLString = base.absoluteString
        api.baseURL = base
        api.token = nil
        do {
            hubInfo = try await api.hubInfo()
        } catch let error as OrbitalAPIError where error.status == 404 {
            // A hub older than /v1/hub/info still signs in.
        } catch {
            loginError = OrbitalAPIError.friendly(error)
            return
        }
        do {
            let token = try await api.login(username: name, password: password)
            await finishSignIn(token: token)
        } catch {
            loginError = OrbitalAPIError.friendly(error)
            isLoggedIn = false
        }
    }

    /// Signs in with the one-time code of an orbital://connect link: no username or password typed.
    func pairSignIn(hub base: URL, code: String) async {
        isLoggingIn = true
        loginError = nil
        defer { isLoggingIn = false }
        if isLoggedIn {
            socket.disconnect()
            keychain.clear()
            isLoggedIn = false
        }
        hubURLString = base.absoluteString
        api.baseURL = base
        api.token = nil
        do {
            hubInfo = try? await api.hubInfo()
            let answer = try await api.pair(code: code)
            if let name = answer.username { username = name }
            await finishSignIn(token: answer.token)
            showToast("Signed in to \(base.host ?? "your hub")")
        } catch {
            loginError = "That sign-in link didn't work (\(OrbitalAPIError.friendly(error))). Run `orbital pair` on the hub for a new one."
        }
    }

    private func finishSignIn(token: String) async {
        keychain.set(token)
        isLoggedIn = true
        signInNotice = nil
        OrbitalImageToken.current = token
        // On a Mac the permission prompt is a banner that can sit unanswered. The first sync does not wait on it.
        Task { await OrbitalNotifications.requestAuthorization() }
        await refresh()
        if !deviceRegistered { await registerDevice() }
    }

    func logout() {
        let api = api
        Task { await api.logout() }
        deviceRegistered = false
        socket.disconnect()
        keychain.clear()
        api.token = nil
        OrbitalImageToken.current = nil
        isLoggedIn = false
        connectionLabel = "Offline"
        satellites = []
        agentJobs = []
        agentsLoaded = false
    }

    /// `orbital://connect?hub=<url>[&pair=<code>]`: with a pairing code it signs in straight away; without one it
    /// fills in the hub address on the sign-in screen, or offers it in Settings.
    func connectLink(hub raw: String, pair: String? = nil) {
        guard let url = Self.parse(raw) else {
            showToast("Couldn't read the hub address in that link")
            return
        }
        if let code = pair, !code.isEmpty {
            Task { await pairSignIn(hub: url, code: code) }
            return
        }
        if isLoggedIn {
            pendingHubURL = url.absoluteString
        } else {
            hubURLString = url.absoluteString
            signInNotice = "Hub address filled in from the link. Sign in to connect."
        }
    }

    /// Switches to another hub address. A different hub asks for its own sign-in.
    func updateHubURL(_ raw: String) {
        guard let base = Self.parse(raw) else {
            editorError = "That hub address is not a valid URL."
            return
        }
        editorError = nil
        pendingHubURL = nil
        let changed = base.absoluteString != Self.parse(hubURLString)?.absoluteString
        hubURLString = base.absoluteString
        api.baseURL = base
        if changed {
            logout()
            signInNotice = "Sign in to \(base.host ?? base.absoluteString)."
        } else {
            Task { await refresh() }
        }
    }

    // MARK: Sync

    func refresh() async {
        guard isLoggedIn else { return }
        #if DEBUG
        if OrbitalPreview.isActive { return }
        #endif
        connectionLabel = "Connecting"
        syncing = true
        defer { syncing = false }
        do {
            // A section cached with a block this build can draw but an older build could not is fetched again in full.
            let known = Dictionary(uniqueKeysWithValues: sectionsByID.filter { !Self.hasNewlyKnownBlocks($0.value) }.map { ($0.key, $0.value.revision) })
            let sync = try await api.sync(known: known)
            catalog = sync.catalog.sections
            if let spaces = sync.catalog.spaces { declaredSpaces = spaces }
            SectionCache.saveCatalog(CatalogResponse(sections: catalog, spaces: declaredSpaces.isEmpty ? nil : declaredSpaces))
            let liveIDs = Set(catalog.map(\.id))
            for id in sectionsByID.keys where !liveIDs.contains(id) {
                sectionsByID.removeValue(forKey: id)
                SectionCache.deleteSection(id)
            }
            for document in sync.sections {
                apply(section: document)
            }
            mergeAlerts(sync.alerts)
            if let retracted = sync.retracted {
                applyRetracted(retracted)
            }
            if let acked = sync.acked {
                applyAcked(acked)
            }
            if let prefs = sync.prefs {
                applyPrefs(prefs, fromSync: true)
            }
            if let rows = sync.pulse {
                pulses = Dictionary(rows.map { ($0.key, $0) }, uniquingKeysWith: { _, new in new })
                SectionCache.savePulses(Array(pulses.values))
            }
            if let snapshot = sync.jobs {
                applyJobs(snapshot)
            } else if jobs == nil {
                await refreshJobs()
            }
            if let machines = sync.satellites {
                satellites = Self.sortedSatellites(machines)
            }
            if let info = sync.hub { hubInfo = info }
            lastSyncAt = Date()
            connectionLabel = "Live"
            OrbitalImageToken.current = api.token
            openSocket()
            if selectedSectionID == nil || !liveIDs.contains(selectedSectionID ?? "") {
                select(section: sortedCatalog.first?.id, node: nil, tab: nil, autofillNode: false)
            } else {
                select(section: selectedSectionID, node: selectedNodeID, tab: selectedTabID, autofillNode: false)
            }
            if hubInfo == nil { hubInfo = try? await api.hubInfo(timeout: 4) }
            if !deviceRegistered { await registerDevice() }
            await refreshNav()
            await refreshAgents()
        } catch OrbitalAPIError.unauthorized {
            // The hub forgot this session (password changed, hub reset). Ask again.
            logout()
            signInNotice = "The hub asked you to sign in again."
        } catch {
            connectionLabel = "Offline"
        }
    }

    func select(section: String?, node: String?, tab: String?, autofillNode: Bool = false) {
        selectedSectionID = section
        guard let section else {
            selectedNodeID = nil
            selectedTabID = nil
            keepNodeEmpty = false
            return
        }
        keepNodeEmpty = node == nil && !autofillNode
        guard let document = sectionsByID[section] else {
            selectedNodeID = node
            selectedTabID = tab
            Task { await self.load(section) }
            return
        }
        let previousNode = selectedNodeID
        if document.layout == .sidebar {
            let nodes = document.sortedNodes
            let explicit = node.flatMap { id in nodes.first { $0.id == id } }
            let resolvedNode: NavNode?
            if let explicit {
                resolvedNode = explicit
            } else if keepNodeEmpty {
                resolvedNode = nil
            } else if autofillNode {
                resolvedNode = nodes.first { $0.id == previousNode } ?? nodes.first
            } else {
                resolvedNode = nil
            }
            selectedNodeID = resolvedNode?.id
            if resolvedNode?.layout == .tabs {
                let tabs = resolvedNode?.tabs ?? []
                let prefer: String? = (node != nil && node != previousNode) ? tab : (tab ?? selectedTabID)
                selectedTabID = tabs.first { $0.id == prefer }?.id ?? tabs.first?.id
            } else {
                selectedTabID = nil
            }
        } else if document.layout == .tabs {
            selectedNodeID = nil
            let prefer = tab ?? selectedTabID
            selectedTabID = document.tabs.first { $0.id == prefer }?.id ?? document.tabs.first?.id
        } else {
            selectedNodeID = nil
            selectedTabID = nil
        }
    }

    func load(_ id: String) async {
        #if DEBUG
        if OrbitalPreview.isActive { return }
        #endif
        guard isLoggedIn else { return }
        do {
            let document = try await api.section(id)
            apply(section: document)
            select(section: id, node: selectedNodeID, tab: selectedTabID, autofillNode: false)
        } catch {
            connectionLabel = "Offline"
        }
    }

    func apply(section document: SectionDocument) {
        sectionsByID[document.id] = document
        SectionCache.save(document)
        if let index = catalog.firstIndex(where: { $0.id == document.id }) {
            catalog[index] = Self.catalogEntry(for: document)
        }
        if selectedSectionID == document.id {
            select(section: document.id, node: selectedNodeID, tab: selectedTabID, autofillNode: false)
        }
    }

    static func catalogEntry(for document: SectionDocument) -> CatalogEntry {
        CatalogEntry(
            id: document.id,
            title: document.title,
            subtitle: document.subtitle,
            icon: document.icon,
            iconImage: document.iconImage,
            order: document.order,
            accent: document.accent,
            layout: document.layout,
            revision: document.revision,
            updatedAt: document.updatedAt,
            summary: document.summary,
            nodeCount: document.nodes.count,
            space: document.space,
            pinned: document.pinned
        )
    }

    /// A cached section holding a block an older build stored without drawing it: fetch it again in full.
    private static func hasNewlyKnownBlocks(_ document: SectionDocument) -> Bool {
        let blocks = document.blocks + document.tabs.flatMap(\.blocks) + document.nodes.flatMap { $0.blocks + $0.tabs.flatMap(\.blocks) }
        return blocks.contains { block in
            guard case .unsupported = block else { return false }
            return ContentBlock.knownTypes.contains(block.typeName)
        }
    }

    private func rebuildInsights() {
        let found = Insights.collect(Array(sectionsByID.values))
        if found.products != products { products = found.products }
        if found.ledgers != ledgers { ledgers = found.ledgers }
        if found.tasks != tasks { tasks = found.tasks }
        if found.series != series { series = found.series }
        var built = SpaceDirectory.build(catalog: catalog, declared: declaredSpaces, sections: sectionsByID, collected: found)
        let system = built.spaces.filter { SystemSpace.ids.contains($0.id) }.flatMap(\.sectionIDs)
        built.spaces.removeAll { SystemSpace.ids.contains($0.id) }
        if system != systemSectionIDs { systemSectionIDs = system }
        if built != directory { directory = built }
    }

    /// The sidebar when the hub sets none: each space with its sections, then sections in no space.
    var defaultSidebar: [NavItem] {
        let system = Set(systemSectionIDs)
        var filed = system
        var out: [NavItem] = directory.spaces.map { space in
            filed.formUnion(space.sectionIDs)
            let pages = space.sectionIDs.compactMap { id in entry(id).map { NavItem(id: id, title: $0.title, icon: $0.icon, accent: $0.accent, target: .section(id, node: nil, tab: nil)) } }
            return NavItem(id: "space-\(space.id)", title: space.name, icon: space.symbol, target: nil, children: pages.isEmpty ? [NavItem(id: "open", title: "Open \(space.name)", target: .space(space.id))] : [NavItem(id: "overview", title: "Overview", icon: "square.grid.2x2", target: .space(space.id))] + pages)
        }
        let loose = sortedCatalog.filter { !filed.contains($0.id) }
        if !loose.isEmpty {
            out.append(NavItem(id: "sections", title: "Sections", icon: "doc.on.doc", children: loose.map {
                NavItem(id: $0.id, title: $0.title, icon: $0.icon, accent: $0.accent, target: .section($0.id, node: nil, tab: nil))
            }))
        }
        return out
    }

    /// The sidebar to draw: the hub's tree, else the default.
    var sidebarTree: [NavItem] { nav.sidebar ?? defaultSidebar }

    func refreshNav() async {
        #if DEBUG
        if OrbitalPreview.isActive { return }
        #endif
        if let doc = try? await api.nav(), doc != nav { nav = doc }
    }

    /// The catalog row or loaded document for a section, for titles and icons.
    func entry(_ sectionID: String?) -> CatalogEntry? {
        guard let sectionID else { return nil }
        if let document = sectionsByID[sectionID] { return Self.catalogEntry(for: document) }
        return catalog.first { $0.id == sectionID }
    }

    func node(_ location: PageLocation) -> NavNode? {
        guard let nodeID = location.nodeID else { return nil }
        return sectionsByID[location.sectionID]?.nodes.first { $0.id == nodeID }
    }

    var imageToken: String? { api.token }

    /// Realtime values for one page, in the order the feed asked for.
    func pulses(for location: PageLocation) -> [PulseItem] {
        pulses.values
            .filter { $0.belongs(to: location) }
            .sorted { ($0.order ?? 100, $0.label) < ($1.order ?? 100, $1.label) }
    }

    // MARK: Pinned sections

    /// Pinned on this device or by the hub (`pinned: true` on the section), in pin order.
    var homeSections: [CatalogEntry] {
        let byID = Dictionary(catalog.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        var out = pinnedSections.compactMap { byID[$0] }
        for entry in sortedCatalog where (entry.pinned == true || sectionsByID[entry.id]?.pinned == true) && !out.contains(entry) {
            out.append(entry)
        }
        return out
    }

    func isPinned(_ sectionID: String) -> Bool {
        homeSections.contains { $0.id == sectionID }
    }

    /// Pins or unpins a section on Home, here and on every other device.
    func togglePin(_ sectionID: String) {
        if pinnedSections.contains(sectionID) {
            pinnedSections.removeAll { $0 == sectionID }
        } else {
            pinnedSections.append(sectionID)
        }
        UserDefaults.standard.set(pinnedSections, forKey: Keys.pinned)
        #if DEBUG
        if OrbitalPreview.isActive { return }
        #endif
        UserDefaults.standard.set(true, forKey: Keys.pinnedUnsynced)
        Task { await pushPins() }
    }

    private func pushPins() async {
        guard isLoggedIn else { return }
        do {
            _ = try await api.setPrefs(["pinned_sections": pinnedSections])
            UserDefaults.standard.removeObject(forKey: Keys.pinnedUnsynced)
        } catch {
            showToast("Pinned here · \(Self.reason(error)), syncs later")
        }
    }

    /// Shared settings from the hub. Pins changed here that the hub has not stored yet are sent instead.
    func applyPrefs(_ prefs: SharedPrefs, fromSync: Bool) {
        if UserDefaults.standard.bool(forKey: Keys.pinnedUnsynced) {
            if fromSync { Task { await pushPins() } }
            return
        }
        if let pins = prefs.pinnedSections, pins != pinnedSections {
            pinnedSections = pins
            UserDefaults.standard.set(pins, forKey: Keys.pinned)
        }
    }

    // MARK: Alerts

    func dismissBanner() {
        bannerTask?.cancel()
        inAppBanner = nil
    }

    /// Marks alerts done here and on the hub, which tells every other device to clear them too.
    /// A failed post leaves them done here; the next sync sees the hub still has them waiting and sends the ack again.
    func ack(_ ids: [String]) async {
        guard !ids.isEmpty else { return }
        markAckedLocally(ids)
        #if DEBUG
        if OrbitalPreview.isActive { return }
        #endif
        // Test alerts never reached the hub, so there is nothing to ack there.
        let remote = ids.filter { !OrbitalTestAlert.isTest($0) }
        if !remote.isEmpty {
            try? await api.ack(ids: remote)
        }
    }

    /// Another device marked these done (the `alerts_acked` event, or `acked` on sync). Clear them here.
    func applyAcked(_ ids: [String]) {
        guard !ids.isEmpty else { return }
        markAckedLocally(ids)
    }

    /// Marks alerts done on this device: the inbox row, the cache, the badge, the banner, and its notification.
    private func markAckedLocally(_ ids: [String]) {
        let wanted = Set(ids)
        let changed = alerts.filter { wanted.contains($0.id) && !$0.isAcked }.map(\.id)
        guard !changed.isEmpty else {
            OrbitalNotifications.setBadge(unackedCount)
            return
        }
        alerts = alerts.map { alert in
            guard wanted.contains(alert.id), !alert.isAcked else { return alert }
            var copy = alert
            copy.acked = true
            return copy
        }
        SectionCache.saveAlerts(alerts)
        OrbitalNotifications.setBadge(unackedCount)
        // Done means gone from Notification Center too, with any snoozed copy and any reminder still scheduled.
        OrbitalNotifications.withdraw(changed)
        if let banner = inAppBanner, wanted.contains(banner.id) {
            dismissBanner()
        }
    }

    /// Fires the Alerts test card's notification through the normal delivery path. Local to this device.
    func sendTestAlert(delay: TimeInterval) async {
        if await OrbitalNotifications.authorizationStatus() == .notDetermined {
            await OrbitalNotifications.requestAuthorization()
        }
        let previous = alerts.filter { OrbitalTestAlert.isTest($0.id) }.map(\.id)
        OrbitalNotifications.withdraw(previous)
        if let banner = inAppBanner, previous.contains(banner.id) {
            dismissBanner()
        }
        alerts.removeAll { OrbitalTestAlert.isTest($0.id) }
        OrbitalTestAlert.removeFiles()
        let alert = OrbitalTestAlert.make(delay: delay, badge: unackedCount + 1)
        alerts.insert(alert, at: 0)
        SectionCache.saveAlerts(alerts)
        OrbitalNotifications.setBadge(unackedCount)
        await OrbitalNotifications.deliver([alert], baseURL: api.baseURL, authToken: api.token)
        if delay <= 0 {
            showBanner(alert)
        }
    }

    private func mergeAlerts(_ incoming: [OrbitalAlert]) {
        let local = Dictionary(alerts.map { ($0.id, $0) }, uniquingKeysWith: { first, _ in first })
        var byID = local
        var fresh: [OrbitalAlert] = []
        var revived: Set<String> = []
        var retracted = retractedIDs
        for alert in incoming {
            if byID[alert.id] == nil {
                fresh.append(alert)
                byID[alert.id] = alert
            } else if retracted.contains(alert.id), !alert.isAcked {
                // Retracted, then sent again: new again. Schedule it once more.
                retracted.remove(alert.id)
                revived.insert(alert.id)
                OrbitalNotifications.forgetDelivered([alert.id])
                fresh.append(alert)
            }
        }
        if retracted != retractedIDs { retractedIDs = retracted }
        // Done here but still waiting on the hub: the ack was lost (offline) or crossed this sync.
        // Keep it done and send the ack again, so the other devices clear it too.
        var resend: [String] = []
        for alert in incoming {
            if let mine = local[alert.id], mine.isAcked, !alert.isAcked, !revived.contains(alert.id) {
                var copy = alert
                copy.acked = true
                byID[alert.id] = copy
                resend.append(alert.id)
            } else {
                byID[alert.id] = alert
            }
        }
        if !resend.isEmpty {
            let api = api
            Task { try? await api.ack(ids: resend) }
        }
        alerts = byID.values.sorted { ($0.createdAt ?? "") > ($1.createdAt ?? "") }
        SectionCache.saveAlerts(alerts)
        OrbitalNotifications.setBadge(unackedCount)
        guard !fresh.isEmpty else { return }
        let base = api.baseURL
        let token = api.token
        Task { await OrbitalNotifications.deliver(fresh, baseURL: base, authToken: token) }
        if let banner = fresh.first(where: shouldBanner) {
            showBanner(banner)
        }
    }

    private func showBanner(_ alert: OrbitalAlert) {
        inAppBanner = alert
        bannerTask?.cancel()
        bannerTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 8_000_000_000)
            guard !Task.isCancelled else { return }
            await MainActor.run { self?.inAppBanner = nil }
        }
    }

    private func shouldBanner(_ alert: OrbitalAlert) -> Bool {
        if alert.isAcked { return false }
        guard let date = alert.deliverDate else { return true }
        return date.timeIntervalSinceNow < 2
    }

    // MARK: Sections (Manage)

    func createSection(id: String, title: String, subtitle: String, icon: String, accent: String, order: Int, layout: SectionLayout, space: String) async {
        editorError = nil
        let waiting = ContentBlock.callout(
            CalloutBlock(
                id: "waiting",
                tone: "info",
                title: "Waiting for a feed",
                text: "This section is ready. The feed that owns \(id) can replace or patch it with `orbital push`."
            )
        )
        var document = SectionDocument(
            id: id,
            title: title,
            subtitle: subtitle.isEmpty ? nil : subtitle,
            icon: icon.isEmpty ? "square.dashed" : icon,
            order: order,
            accent: accent.isEmpty ? nil : accent,
            layout: layout,
            space: space.isEmpty ? nil : space
        )
        switch layout {
        case .page:
            document.blocks = [waiting]
        case .tabs:
            document.tabs = [PageTab(id: "overview", title: "Overview", icon: "square.grid.2x2", blocks: [waiting])]
        case .sidebar:
            document.nodes = [
                NavNode(id: "overview", title: "Overview", icon: "list.bullet", order: 10, layout: .page, blocks: [waiting], tabs: [])
            ]
        }
        #if DEBUG
        if OrbitalPreview.isActive {
            apply(section: document)
            catalog.append(Self.catalogEntry(for: document))
            return
        }
        #endif
        do {
            let saved = try await api.putSection(document)
            apply(section: saved)
            if !catalog.contains(where: { $0.id == saved.id }) {
                await refresh()
            }
            select(section: saved.id, node: nil, tab: nil)
        } catch {
            editorError = OrbitalAPIError.friendly(error)
        }
    }

    func updateSectionMeta(id: String, title: String, subtitle: String, icon: String, accent: String, order: Int, layout: SectionLayout, space: String) async {
        editorError = nil
        var value: [String: Any] = [
            "title": title,
            "icon": icon,
            "order": order,
            "layout": layout.rawValue,
        ]
        value["subtitle"] = subtitle.isEmpty ? NSNull() : subtitle
        value["accent"] = accent.isEmpty ? NSNull() : accent
        value["space"] = space.isEmpty ? NSNull() : space
        #if DEBUG
        if OrbitalPreview.isActive {
            if var document = sectionsByID[id] {
                document.title = title
                document.subtitle = subtitle.isEmpty ? nil : subtitle
                document.icon = icon
                document.order = order
                document.accent = accent.isEmpty ? nil : accent
                document.layout = layout
                document.space = space.isEmpty ? nil : space
                apply(section: document)
            }
            return
        }
        #endif
        do {
            if let saved = try await api.ops(sectionID: id, body: [
                "schema": OrbitalSchema.ops,
                "ops": [["op": "merge_section", "value": value]],
            ]) {
                apply(section: saved)
            }
            await refresh()
        } catch {
            editorError = OrbitalAPIError.friendly(error)
        }
    }

    func deleteSection(_ id: String) async {
        editorError = nil
        do {
            #if DEBUG
            if !OrbitalPreview.isActive { try await api.deleteSection(id) }
            #else
            try await api.deleteSection(id)
            #endif
            sectionsByID.removeValue(forKey: id)
            catalog.removeAll { $0.id == id }
            SectionCache.deleteSection(id)
            if selectedSectionID == id {
                select(section: sortedCatalog.first?.id, node: nil, tab: nil)
            }
        } catch {
            editorError = OrbitalAPIError.friendly(error)
        }
    }

    // MARK: Live socket

    private func openSocket() {
        socket.onState = { [weak self] label in
            self?.connectionLabel = label
        }
        guard let token = api.token else { return }
        socket.connect(baseURL: api.baseURL, token: token) { [weak self] event in
            Task { @MainActor in self?.handle(event) }
        }
    }

    private func handle(_ event: [String: Any]) {
        let type = event["type"] as? String ?? ""
        switch type {
        case "section":
            guard let section = event["section"],
                  let data = try? JSONSerialization.data(withJSONObject: section),
                  let document = try? JSONDecoder().decode(SectionDocument.self, from: data)
            else { return }
            let previous = sectionsByID[document.id]?.revision ?? -1
            guard document.revision > previous else { return }
            apply(section: document)
            if !catalog.contains(where: { $0.id == document.id }) {
                catalog.append(Self.catalogEntry(for: document))
            }
        case "section_deleted":
            if let id = event["id"] as? String {
                sectionsByID.removeValue(forKey: id)
                catalog.removeAll { $0.id == id }
                SectionCache.deleteSection(id)
            }
        case "alerts", "alert":
            let raw: Any? = event["alerts"] ?? event["alert"].map { [$0] }
            guard let raw, let data = try? JSONSerialization.data(withJSONObject: raw),
                  let decoded = try? JSONDecoder().decode([OrbitalAlert].self, from: data)
            else { return }
            mergeAlerts(decoded)
        case "catalog":
            Task { await self.reloadCatalog() }
        case "pulse":
            var next = pulses
            for key in event["removed"] as? [String] ?? [] {
                next.removeValue(forKey: key)
            }
            if let raw = event["items"],
               let data = try? JSONSerialization.data(withJSONObject: raw),
               let rows = try? JSONDecoder().decode([PulseItem].self, from: data) {
                for row in rows { next[row.key] = row }
            }
            if next != pulses {
                pulses = next
                SectionCache.savePulses(Array(next.values))
            }
        case "action_result":
            guard let action = event["action"] as? [String: Any] else { return }
            switch action["status"] as? String {
            case "applied":
                showToast("Saved")
            case "failed":
                let message = (action["message"] as? String).flatMap(Insights.nonEmpty) ?? "the feed could not apply it"
                showToast("Couldn't save: \(message)")
            default:
                break
            }
        case "nav":
            if let raw = event["nav"] as? [String: Any] {
                let doc = NavDoc(json: raw)
                if doc != nav { nav = doc }
            }
        case "alerts_retracted":
            applyRetracted(event["ids"] as? [String] ?? [])
        case "alerts_acked":
            // Cleared on another device. This one follows.
            applyAcked(event["ids"] as? [String] ?? [])
        case "prefs":
            guard let raw = event["prefs"],
                  let data = try? JSONSerialization.data(withJSONObject: raw),
                  let prefs = try? JSONDecoder().decode(SharedPrefs.self, from: data)
            else { return }
            applyPrefs(prefs, fromSync: false)
        case "hello":
            // The socket came back after a drop. Anything sent while it was down arrives with one sync.
            Task { await self.catchUp() }
        case "desk_message", "desk_task", "tracker", "tracker_settings":
            // The Agent desk (DeskState) owns these.
            NotificationCenter.default.post(name: .orbitalDeskEvent, object: nil, userInfo: event)
        case "jobs":
            guard let raw = event["jobs"],
                  let data = try? JSONSerialization.data(withJSONObject: raw),
                  let snapshot = try? JSONDecoder().decode(JobsSnapshot.self, from: data)
            else { return }
            applyJobs(snapshot)
        default:
            if type.hasPrefix("satellite") || type.hasPrefix("agent") {
                applyAgentEvent(event)
            }
        }
    }

    private func reloadCatalog() async {
        guard let response = try? await api.catalog() else { return }
        catalog = response.sections
        if let spaces = response.spaces { declaredSpaces = spaces }
        SectionCache.saveCatalog(CatalogResponse(sections: catalog, spaces: declaredSpaces.isEmpty ? nil : declaredSpaces))
        rebuildInsights()
    }

    /// "my-hub.tailnet.ts.net" or "100.101.102.103" → http://…:7700. An https address keeps its own port.
    nonisolated static func parse(_ raw: String) -> URL? {
        var text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if text.isEmpty { return nil }
        if !text.contains("://") {
            text = "http://\(text)"
        }
        while text.hasSuffix("/") { text.removeLast() }
        guard var components = URLComponents(string: text), let host = components.host, !host.isEmpty else { return nil }
        guard ["http", "https"].contains(components.scheme?.lowercased() ?? "") else { return nil }
        if components.scheme?.lowercased() == "http", components.port == nil {
            components.port = OrbitalBrand.defaultHubPort
        }
        components.query = nil
        components.fragment = nil
        return components.url
    }
}

// MARK: - Device registration and optional Apple push

extension OrbitalStore {
    /// Tells the hub this device's name and platform, and its Apple push token when it has one. When
    /// the hub sends pushes (`notifications.apns` on the hub, and this build signed with the push
    /// entitlement), asks the system for a token; the token comes back through the app delegate and
    /// this runs again with it. Without push, alerts still arrive by the live socket and background sync.
    func registerDevice() async {
        guard isLoggedIn else { return }
        #if DEBUG
        if OrbitalPreview.isActive { return }
        #endif
        #if targetEnvironment(macCatalyst)
        let platform = "mac"
        #else
        let platform = "ios"
        #endif
        let token = UserDefaults.standard.string(forKey: Keys.apnsToken)
        do {
            var pushes = try await api.registerDevice(id: deviceID, name: UIDevice.current.name, platform: platform, apnsToken: token)
            deviceRegistered = true
            if !pushes, let health = try? await api.health() {
                pushes = health.apns?.enabled ?? false
            }
            if pushes, token == nil {
                UIApplication.shared.registerForRemoteNotifications()
            }
        } catch {
            deviceRegistered = false
        }
    }
}

// MARK: - Every device in step

extension OrbitalStore {
    /// One sync when the app comes back to the front or the socket reconnects, unless one just ran.
    func catchUp() async {
        guard isLoggedIn, !syncing else { return }
        if let last = lastSyncAt, Date().timeIntervalSince(last) < 3 { return }
        await refresh()
    }
}

// MARK: - Feed schedules

extension OrbitalStore {
    func applyJobs(_ snapshot: JobsSnapshot) {
        if snapshot != jobs { jobs = snapshot }
        // A run this device asked for has finished: say how it went.
        for (id, asked) in requestedJobs {
            guard let job = snapshot.job(id), !job.isBusy,
                  let finished = job.lastFinishedDate, finished >= asked.addingTimeInterval(-2)
            else { continue }
            requestedJobs.removeValue(forKey: id)
            switch job.health {
            case .ok: showToast("Saved · \(job.name) updated")
            case .warn: showToast("\(job.name) updated with problems")
            default: showToast("Couldn't update \(job.name): \(job.last?.summary ?? "failed")")
            }
        }
    }

    func refreshJobs() async {
        #if DEBUG
        if OrbitalPreview.isActive { return }
        #endif
        guard isLoggedIn, let snapshot = try? await api.jobs() else { return }
        applyJobs(snapshot)
        if let report = try? await api.usage(), report != usageReport { usageReport = report }
    }

    /// Marks jobs queued on screen at once; the scheduler's next snapshot replaces it within a second.
    private func markQueued(_ ids: [String]) {
        guard var snapshot = jobs else { return }
        let wanted = Set(ids)
        snapshot.jobs = snapshot.jobs.map { job in
            guard wanted.contains(job.id), !job.isBusy else { return job }
            var copy = job
            copy.state = "queued"
            copy.queuedReason = "manual"
            return copy
        }
        jobs = snapshot
        let now = Date()
        for id in ids { requestedJobs[id] = now }
    }

    func runJob(_ id: String) async {
        let name = jobs?.job(id)?.name ?? id
        markQueued([id])
        showToast("Updating \(name) · queued")
        #if DEBUG
        if OrbitalPreview.isActive { return }
        #endif
        do {
            try await api.runJob(id)
        } catch {
            requestedJobs.removeValue(forKey: id)
            showToast("Not sent: \(Self.reason(error))")
            await refreshJobs()
        }
    }

    /// Refresh everything (or one group), in stages: one job at a time per account, accounts side by side.
    func runAll(group: String? = nil) async {
        let ids = (jobs?.jobs ?? []).filter { group == nil || $0.group == group }.map(\.id)
        markQueued(ids)
        let title = group.flatMap { id in jobs?.groups.first { $0.id == id }?.title } ?? "everything"
        showToast("Refreshing \(title) · \(ids.count) feeds in stages")
        #if DEBUG
        if OrbitalPreview.isActive { return }
        #endif
        do {
            _ = try await api.runAll(group: group)
        } catch {
            for id in ids { requestedJobs.removeValue(forKey: id) }
            showToast("Not sent: \(Self.reason(error))")
            await refreshJobs()
        }
    }

    /// Runs every feed that writes this page.
    func refreshPage(_ location: PageLocation) async {
        let ids = (jobs?.jobs(for: location) ?? []).map(\.id)
        guard !ids.isEmpty else {
            await refresh()
            return
        }
        markQueued(ids)
        showToast(ids.count == 1 ? "Updating this page · queued" : "Updating this page · \(ids.count) feeds queued")
        #if DEBUG
        if OrbitalPreview.isActive { return }
        #endif
        do {
            for id in ids {
                try await api.runJob(id)
            }
        } catch {
            for id in ids { requestedJobs.removeValue(forKey: id) }
            showToast("Not sent: \(Self.reason(error))")
        }
    }

    func setEvery(_ id: String, seconds: Int) async {
        do {
            let isDefault = jobs?.job(id)?.defaultEveryS == seconds
            try await api.jobSettings(id, every: isDefault ? nil : seconds, resetEvery: isDefault)
            showToast("Saved · \(JobFormat.every(seconds).lowercased())")
        } catch {
            showToast("Not sent: \(Self.reason(error))")
        }
        await refreshJobs()
    }

    func setPaused(_ id: String, paused: Bool) async {
        do {
            try await api.jobSettings(id, paused: paused)
            showToast(paused ? "Saved · paused" : "Saved · back on schedule")
        } catch {
            showToast("Not sent: \(Self.reason(error))")
        }
        await refreshJobs()
    }

    func cancelJob(_ id: String) async {
        requestedJobs.removeValue(forKey: id)
        do {
            try await api.cancelJob(id)
            showToast("Stopping \(jobs?.job(id)?.name ?? id)")
        } catch {
            showToast("Not sent: \(Self.reason(error))")
        }
    }

    func jobLog(_ id: String) async -> [String]? {
        #if DEBUG
        if OrbitalPreview.isActive { return OrbitalPreview.jobLog(id) }
        #endif
        return try? await api.jobLog(id)
    }
}

// MARK: - Satellites and agents

extension OrbitalStore {
    /// Machines and jobs, fresh from the hub.
    func refreshAgents() async {
        #if DEBUG
        if OrbitalPreview.isActive { return }
        #endif
        guard isLoggedIn else { return }
        do {
            async let machines = api.satellites()
            async let jobs = api.agentJobs()
            let (m, j) = try await (machines, jobs)
            satellites = Self.sortedSatellites(m)
            applyAgentJobs(j)
            agentsError = nil
        } catch {
            agentsError = OrbitalAPIError.friendly(error)
        }
        agentsLoaded = true
    }

    /// Satellite and agent-job events from the live socket. A payload it understands is applied at
    /// once; anything else triggers one refresh shortly after, so bursts collapse into one fetch.
    private func applyAgentEvent(_ event: [String: Any]) {
        var applied = false
        if let raw = event["satellites"], let data = try? JSONSerialization.data(withJSONObject: raw),
           let list = try? JSONDecoder().decode(SatelliteList.self, from: data) {
            satellites = Self.sortedSatellites(list.satellites)
            applied = true
        }
        if let raw = event["satellite"], let data = try? JSONSerialization.data(withJSONObject: raw),
           let one = try? JSONDecoder().decode(Satellite.self, from: data) {
            var next = satellites.filter { $0.id != one.id }
            next.append(one)
            satellites = Self.sortedSatellites(next)
            applied = true
        }
        if let raw = event["jobs"], let data = try? JSONSerialization.data(withJSONObject: raw),
           let list = try? JSONDecoder().decode(AgentJobList.self, from: data) {
            applyAgentJobs(list.jobs)
            applied = true
        }
        if let raw = event["job"], let data = try? JSONSerialization.data(withJSONObject: raw),
           let one = try? JSONDecoder().decode(AgentJob.self, from: data) {
            var next = agentJobs.filter { $0.id != one.id }
            // Events carry only a few fields; keep what is already known about the job.
            next.append(agentJobs.first { $0.id == one.id }.map { Self.merged($0, with: one) } ?? one)
            applyAgentJobs(next)
            applied = true
            if one.isActive == false || agentJobs.first(where: { $0.id == one.id }) == nil {
                // A start or a finish: fetch the whole record shortly.
                agentsRefresh?.cancel()
                agentsRefresh = Task { [weak self] in
                    try? await Task.sleep(nanoseconds: 1_200_000_000)
                    guard !Task.isCancelled else { return }
                    await self?.refreshAgents()
                }
            }
        }
        if let id = event["id"] as? String, (event["type"] as? String)?.hasSuffix("removed") == true {
            satellites.removeAll { $0.id == id }
            applied = true
        }
        guard !applied else { return }
        agentsRefresh?.cancel()
        agentsRefresh = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 500_000_000)
            guard !Task.isCancelled else { return }
            await self?.refreshAgents()
        }
    }

    private func applyAgentJobs(_ incoming: [AgentJob]) {
        let ordered = AgentJobsSort.ordered(incoming)
        for job in ordered where startedAgentJobs.contains(job.id) && !job.isActive {
            startedAgentJobs.remove(job.id)
            let name = AgentStyle.displayName(job.agent)
            switch job.status {
            case "cancelled", "canceled": showToast("\(name) job stopped")
            default: showToast(job.isFailed ? "Couldn't finish: \(job.shownTitle)" : "Saved · \(name) finished \(job.shownTitle)")
            }
        }
        if ordered != agentJobs { agentJobs = ordered }
    }

    /// `old` with every field `new` carries.
    static func merged(_ old: AgentJob, with new: AgentJob) -> AgentJob {
        var out = old
        out.status = new.status
        out.satelliteID = new.satelliteID ?? old.satelliteID
        out.machine = new.machine ?? old.machine
        out.title = new.title ?? old.title
        out.prompt = Insights.nonEmpty(new.prompt) ?? old.prompt
        out.cwd = new.cwd ?? old.cwd
        out.exitCode = new.exitCode ?? old.exitCode
        out.createdAt = new.createdAt ?? old.createdAt
        out.startedAt = new.startedAt ?? old.startedAt
        out.finishedAt = new.finishedAt ?? old.finishedAt
        out.outputTail = new.outputTail ?? old.outputTail
        out.error = new.error ?? old.error
        out.trackerID = new.trackerID ?? old.trackerID
        if new.agent != "agent" { out.agent = new.agent }
        return out
    }

    static func sortedSatellites(_ rows: [Satellite]) -> [Satellite] {
        rows.sorted { lhs, rhs in
            if lhs.isOnline != rhs.isOnline { return lhs.isOnline }
            if lhs.isHub != rhs.isHub { return lhs.isHub }
            return lhs.name.localizedCaseInsensitiveCompare(rhs.name) == .orderedAscending
        }
    }

    func satellite(_ id: String?) -> Satellite? {
        guard let id else { return nil }
        return satellites.first { $0.id == id || $0.machine == id || $0.name == id }
    }

    /// The machine a job ran on, by id, else by name.
    func satellite(for job: AgentJob) -> Satellite? {
        satellite(job.satelliteID) ?? satellite(job.machine)
    }

    /// Starts an agent. Returns the new job id, or nil after showing why it did not start.
    @discardableResult
    func runAgent(_ request: AgentRunRequest) async -> String? {
        #if DEBUG
        if OrbitalPreview.isActive {
            let machine = satellite(request.satelliteID)
            let job = AgentJob(id: "preview-\(UUID().uuidString.prefix(6).lowercased())", satelliteID: request.satelliteID,
                               machine: machine?.machine ?? machine?.name, agent: request.agent, title: request.title,
                               prompt: request.prompt, cwd: request.cwd, status: "running", startedAt: OrbitalDates.iso(Date()))
            applyAgentJobs(agentJobs + [job])
            showToast("Started \(AgentStyle.displayName(request.agent)) on \(machine?.name ?? request.satelliteID)")
            return job.id
        }
        #endif
        do {
            let id = try await api.runAgent(request)
            startedAgentJobs.insert(id)
            let machine = satellite(request.satelliteID)?.name ?? request.satelliteID
            // Shown at once; the next heartbeat or job event replaces it with the satellite's copy.
            let placeholder = AgentJob(id: id, satelliteID: request.satelliteID, machine: machine, agent: request.agent,
                                       title: request.title, prompt: request.prompt, cwd: request.cwd, status: "queued",
                                       createdAt: OrbitalDates.iso(Date()))
            if !agentJobs.contains(where: { $0.id == id }) { applyAgentJobs(agentJobs + [placeholder]) }
            showToast("Started \(AgentStyle.displayName(request.agent)) on \(machine)")
            Task {
                try? await Task.sleep(nanoseconds: 1_500_000_000)
                await refreshAgents()
            }
            return id
        } catch {
            showToast("Not started: \(Self.reason(error))")
            return nil
        }
    }

    func cancelAgentJob(_ job: AgentJob) async {
        guard let satellite = job.satelliteID ?? satellite(for: job)?.id else {
            showToast("Not sent: which machine ran it is unknown")
            return
        }
        #if DEBUG
        if OrbitalPreview.isActive {
            var copy = job
            copy.status = "cancelled"
            copy.finishedAt = OrbitalDates.iso(Date())
            applyAgentJobs(agentJobs.filter { $0.id != job.id } + [copy])
            return
        }
        #endif
        do {
            try await api.cancelAgentJob(satellite: satellite, job: job.id)
            showToast("Stopping \(job.shownTitle)")
            await refreshAgents()
        } catch {
            showToast("Not sent: \(Self.reason(error))")
        }
    }

    /// The latest copy of one job straight from its machine.
    func agentJobDetail(_ job: AgentJob) async -> AgentJob? {
        #if DEBUG
        if OrbitalPreview.isActive { return agentJobs.first { $0.id == job.id } }
        #endif
        guard let satellite = job.satelliteID ?? satellite(for: job)?.id,
              let fresh = try? await api.agentJob(satellite: satellite, job: job.id) else { return nil }
        var merged = fresh
        merged.satelliteID = merged.satelliteID ?? satellite
        merged.machine = merged.machine ?? job.machine
        if let index = agentJobs.firstIndex(where: { $0.id == job.id }), agentJobs[index] != merged {
            agentJobs[index] = merged
        }
        return merged
    }

    func agentJobLog(_ job: AgentJob) async -> Result<[String], Error> {
        #if DEBUG
        if OrbitalPreview.isActive {
            return .success(OrbitalPreview.agentJobLog(job.id) ?? (job.outputTail ?? "").components(separatedBy: "\n"))
        }
        #endif
        guard let satellite = job.satelliteID ?? satellite(for: job)?.id else {
            return .failure(OrbitalAPIError.http(404, "{\"error\": \"which machine ran it is unknown\"}"))
        }
        do {
            return .success(try await api.agentJobLog(satellite: satellite, job: job.id))
        } catch {
            return .failure(error)
        }
    }

    func removeSatellite(_ machine: Satellite) async {
        do {
            #if DEBUG
            if !OrbitalPreview.isActive { try await api.removeSatellite(machine.id) }
            #else
            try await api.removeSatellite(machine.id)
            #endif
            satellites.removeAll { $0.id == machine.id }
            showToast("Removed \(machine.name)")
        } catch {
            showToast("Not removed: \(Self.reason(error))")
        }
    }
}

// MARK: - Actions (taps that write back)

extension OrbitalStore {
    /// Posts one write-back action: a task checkbox, the Add form, or an insight card button.
    /// The row changes on screen at once; the hub's patched section replaces it, and the owner's push clears `pending`.
    func perform(
        type: String,
        sectionID: String,
        itemID: String? = nil,
        blockID: String? = nil,
        nodeID: String? = nil,
        tabID: String? = nil,
        value: [String: Any],
        source: String = "tap"
    ) async {
        var body: [String: Any] = ["type": type, "value": value, "source": source]
        if let itemID { body["item_id"] = itemID }
        // The hub takes lowercase slugs here. Leave out an id it would refuse rather than fail the tap.
        if let blockID = Self.slug(blockID) { body["block_id"] = blockID }
        if let nodeID = Self.slug(nodeID) { body["node_id"] = nodeID }
        if let tabID = Self.slug(tabID) { body["tab_id"] = tabID }

        let before = sectionsByID[sectionID]
        var patched: SectionDocument?
        if let before {
            let next = Self.optimisticPatch(
                before, type: type, itemID: itemID, blockID: blockID, nodeID: nodeID, tabID: tabID,
                value: value, stamp: OrbitalDates.iso(Date())
            )
            if next != before {
                // Same revision and not cached: the hub's copy (a newer revision) replaces it.
                sectionsByID[sectionID] = next
                patched = next
            }
        }
        showToast(Self.sentToast(type: type, value: value))

        #if DEBUG
        if OrbitalPreview.isActive {
            // No hub in preview: pretend the owner wrote it back a moment later.
            try? await Task.sleep(nanoseconds: 1_200_000_000)
            if let current = sectionsByID[sectionID] {
                sectionsByID[sectionID] = Self.clearPending(current)
            }
            showToast("Saved")
            return
        }
        #endif

        do {
            let (section, _) = try await api.postAction(sectionID: sectionID, body: body)
            if let section, section.revision > (sectionsByID[sectionID]?.revision ?? -1) {
                apply(section: section)
            }
        } catch {
            if let patched, let before, sectionsByID[sectionID] == patched {
                sectionsByID[sectionID] = before
            }
            showToast("Not sent: \(Self.reason(error))")
        }
    }

    /// A notification button posted an action while the app may not have been running. Catch the screen up.
    func noteNotificationAction(alertID: String?, type: String, value: [String: Any], failure: String?) {
        if let failure {
            showToast("Not sent: \(failure)")
        } else {
            if let alertID { markAckedLocally([alertID]) }
            showToast(Self.sentToast(type: type, value: value))
        }
        Task { await refresh() }
    }

    func showToast(_ text: String) {
        toast = text
        toastTask?.cancel()
        toastTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            guard !Task.isCancelled else { return }
            self?.toast = nil
        }
    }

    func dismissToast() {
        toastTask?.cancel()
        toast = nil
    }

    /// Re-checks which scheduled reminders have come due. Call when the app comes to the front.
    func tickAlertClock() {
        alertClock = Date()
        OrbitalNotifications.setBadge(unackedCount)
        scheduleAlertClock()
    }

    /// Wakes once, when the next scheduled reminder fires, to move it to Waiting and update the badge.
    func scheduleAlertClock() {
        alertClockTask?.cancel()
        let now = Date()
        guard let next = alerts.compactMap({ $0.isAcked ? nil : $0.deliverDate }).filter({ $0 > now }).min() else { return }
        let wait = min(next.timeIntervalSince(now) + 1, 6 * 3600)
        alertClockTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(max(1, wait) * 1_000_000_000))
            guard !Task.isCancelled else { return }
            self?.tickAlertClock()
        }
    }

    /// The hub retracted these reminders: drop their notifications and mark the rows done.
    func applyRetracted(_ ids: [String]) {
        guard !ids.isEmpty else { return }
        let wanted = Set(ids)
        let known = retractedIDs
        let changed = Set(alerts.filter { wanted.contains($0.id) && !$0.isAcked }.map(\.id))
        let newlyRetracted = wanted.subtracting(known)
        let touched = Array(changed.union(newlyRetracted))
        guard !touched.isEmpty else { return }
        OrbitalNotifications.withdraw(touched)
        OrbitalNotifications.forgetDelivered(touched)
        if !newlyRetracted.isEmpty {
            retractedIDs = known.union(newlyRetracted)
        }
        markAckedLocally(Array(changed))
        OrbitalNotifications.setBadge(unackedCount)
    }

    /// Alert ids this device has seen retracted. An unacked copy of one of them later is a revival.
    var retractedIDs: Set<String> {
        get { Set(UserDefaults.standard.stringArray(forKey: Keys.retracted) ?? []) }
        // `suffix` returns an ArraySlice, which is not a plist type. UserDefaults throws on it.
        set { UserDefaults.standard.set(Array(newValue.sorted().suffix(400)), forKey: Keys.retracted) }
    }

    // MARK: Helpers

    static func sentToast(type: String, value: [String: Any]) -> String {
        switch type {
        case "task.set_done":
            return (value["done"] as? Bool ?? true) ? "Marked done · syncing" : "Marked not done · syncing"
        case "task.add":
            return "Added · syncing"
        case "insight.snooze":
            return "Snoozed · syncing"
        case "insight.dismiss":
            return "Dismissed · syncing"
        default:
            return "Sent · syncing"
        }
    }

    static func reason(_ error: Error) -> String {
        if let api = error as? OrbitalAPIError { return api.shortReason }
        if let url = error as? URLError {
            switch url.code {
            case .notConnectedToInternet, .networkConnectionLost: return "offline"
            case .timedOut: return "the hub did not answer"
            case .cannotConnectToHost, .cannotFindHost: return "can't reach the hub"
            default: break
            }
        }
        return error.localizedDescription
    }

    private static func slug(_ id: String?) -> String? {
        guard let id, !id.isEmpty, id.count <= 64,
              id.range(of: "^[a-z0-9][a-z0-9_-]*$", options: .regularExpression) != nil
        else { return nil }
        return id
    }

    /// The same patch the hub applies, so the tap shows before the round trip.
    static func optimisticPatch(
        _ document: SectionDocument,
        type: String,
        itemID: String?,
        blockID: String?,
        nodeID: String?,
        tabID: String?,
        value: [String: Any],
        stamp: String
    ) -> SectionDocument {
        switch type {
        case "task.set_done":
            guard let itemID, let done = value["done"] as? Bool else { return document }
            return document.mapBlocks { _, _, block in
                guard case .tasks(var tasks) = block, tasks.items.contains(where: { $0.id == itemID }) else { return block }
                tasks.items = tasks.items.map { item in
                    guard item.id == itemID else { return item }
                    var copy = item
                    copy.done = done
                    copy.doneAt = done ? stamp : nil
                    copy.pending = true
                    return copy
                }
                return .tasks(tasks)
            }
        case "task.add":
            guard let blockID, let title = value["title"] as? String else { return document }
            var inserted = false
            return document.mapBlocks { node, tab, block in
                guard !inserted, case .tasks(var tasks) = block, tasks.id == blockID else { return block }
                if let nodeID, node != nodeID { return block }
                if let tabID, tab != tabID { return block }
                let row = TaskItem(
                    id: "local-\(UUID().uuidString.prefix(8).lowercased())",
                    title: title,
                    group: value["group"] as? String,
                    due: value["due"] as? String,
                    note: value["note"] as? String,
                    tags: ["new"],
                    canToggle: false,
                    pending: true
                )
                tasks.items.insert(row, at: 0)
                inserted = true
                return .tasks(tasks)
            }
        case "insight.email.approve", "insight.agent.approve", "insight.snooze", "insight.dismiss":
            guard let itemID else { return document }
            return document.mapBlocks { _, _, block in
                guard case .insightCards(var insights) = block, insights.cards.contains(where: { $0.id == itemID }) else { return block }
                insights.cards = insights.cards.map { card in
                    guard card.id == itemID else { return card }
                    var copy = card
                    copy.pending = true
                    switch type {
                    case "insight.snooze":
                        let days = (value["days"] as? Int) ?? 1
                        copy.state = "snoozed"
                        copy.snoozedUntil = OrbitalDates.iso(Date().addingTimeInterval(Double(days) * 86_400))
                    case "insight.dismiss":
                        copy.state = "dismissed"
                    default:
                        copy.state = "pending"
                        let actionID = value["action_id"] as? String
                        copy.actions = copy.actions.map { action in
                            guard action.id == actionID else { return action }
                            var next = action
                            next.state = "pending"
                            return next
                        }
                    }
                    return copy
                }
                return .insightCards(insights)
            }
        default:
            // A `post` button: the tapped button shows pending until the feed pushes the page again.
            guard let itemID, let blockID else { return document }
            return document.mapBlocks { node, tab, block in
                if let nodeID, node != nodeID { return block }
                if let tabID, tab != tabID { return block }
                switch block {
                case .buttons(var buttons) where buttons.id == blockID:
                    guard let index = buttons.items.firstIndex(where: { $0.id == itemID }) else { return block }
                    buttons.items[index].pending = true
                    return .buttons(buttons)
                case .hero(var hero) where hero.id == blockID:
                    guard var actions = hero.actions, let index = actions.firstIndex(where: { $0.id == itemID }) else { return block }
                    actions[index].pending = true
                    hero.actions = actions
                    return .hero(hero)
                default:
                    return block
                }
            }
        }
    }

    /// Preview only: what the owner's re-push looks like once it has written the change.
    static func clearPending(_ document: SectionDocument) -> SectionDocument {
        document.mapBlocks { _, _, block in
            switch block {
            case .tasks(var tasks):
                guard tasks.items.contains(where: \.pending) else { return block }
                tasks.items = tasks.items.map { item in
                    var copy = item
                    copy.pending = false
                    if copy.id.hasPrefix("local-") {
                        copy.canToggle = true
                        copy.tags.removeAll { $0 == "new" }
                    }
                    return copy
                }
                return .tasks(tasks)
            case .insightCards(var insights):
                guard insights.cards.contains(where: \.pending) else { return block }
                insights.cards = insights.cards.map { card in
                    var copy = card
                    copy.pending = false
                    return copy
                }
                return .insightCards(insights)
            case .buttons(var buttons):
                guard buttons.items.contains(where: \.pending) else { return block }
                for index in buttons.items.indices { buttons.items[index].pending = false }
                return .buttons(buttons)
            default:
                return block
            }
        }
    }
}

/// The session token in the keychain. Internal so the notification delegate can post a button's action without the store.
struct OrbitalKeychain {
    static var service: String { OrbitalBrand.bundleID }
    static let account = "session"

    /// The signed-in session token, or nil. Readable after first unlock, so background notification actions work.
    static func storedSessionToken() -> String? {
        OrbitalKeychain().get()
    }

    func set(_ value: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service,
            kSecAttrAccount as String: Self.account,
        ]
        SecItemDelete(query as CFDictionary)
        var add = query
        add[kSecValueData as String] = Data(value.utf8)
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        if SecItemAdd(add as CFDictionary, nil) != errSecSuccess {
            MacSecretFile.set(value, service: Self.service, account: Self.account)
        }
    }

    func get() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service,
            kSecAttrAccount as String: Self.account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var out: AnyObject?
        guard SecItemCopyMatching(query as CFDictionary, &out) == errSecSuccess,
              let data = out as? Data else {
            return MacSecretFile.get(service: Self.service, account: Self.account)
        }
        return String(data: data, encoding: .utf8)
    }

    func clear() {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: Self.service,
            kSecAttrAccount as String: Self.account,
        ]
        SecItemDelete(query as CFDictionary)
        MacSecretFile.delete(service: Self.service, account: Self.account)
    }
}
