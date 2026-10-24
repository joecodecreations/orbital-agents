import Foundation
import SwiftUI
import UIKit

/// Bottom tabs on an iPhone in portrait: the fixed five (Home, System, Agent, Tracker, Alerts), then the
/// hub's extra items (`/v1/nav` tabs) by id. `.agents` is the System tab and `.desk` the Agent tab.
enum RootTab: Hashable {
    case home
    case money
    case agents
    case desk
    case tracker
    case alerts
    case custom(String)
}

/// The panes inside System.
enum SystemPane: String, CaseIterable, Identifiable {
    case satellites, automations, manage, settings
    var id: String { rawValue }
    var title: String {
        switch self {
        case .satellites: return "Satellites"
        case .automations: return "Automations"
        case .manage: return "Manage"
        case .settings: return "Settings"
        }
    }
}

/// One push inside a tab's navigation stack on iPhone, or inside the detail column on a wide window.
enum BrowseRoute: Hashable {
    /// A section. Shows its node list when the layout is sidebar, otherwise its page.
    case section(String)
    /// A node page inside a sidebar section.
    case node(String, String)
    /// A space with everything filed under it (SpaceDirectory id).
    case space(String)
    /// A navigation group (a bucket from the hub's nav document), by key.
    case navGroup(String)
    /// Every space, as tiles.
    case spaces
    /// One product: its charts, money, and pages (SpaceDirectory product id).
    case product(String)
    /// Every section the hub has.
    case browse
    case alerts
    case manage
    case settings
    case automations
    case agents
    /// One machine and what it is running.
    case satellite(String)
    /// One agent job: status, output, and Stop.
    case agentJob(String)
    /// The tracker's items, and one item with its notes, media, and subagents.
    case tracker
    case trackerItem(String)
    /// Money narrowed to a business, product, source or category (the Money tab's drill-down).
    case money(MoneyFilter)
    /// Every payment and ledger day for a Money filter.
    case moneyEntries(MoneyFilter)
    /// One product's subscriptions (SpaceDirectory product id).
    case subscriptions(String)
}

/// Shell navigation shared by buttons, notification taps, the tab bar, the left bar, and orbital:// links.
@MainActor
final class AppRouter: ObservableObject {
    enum Destination: Equatable {
        case home
        case money
        case agents
        case desk
        case tracker
        case alerts
        case settings
        case automations
        case manage
        case section(String?)
        case custom(String)
    }

    /// Which pane System shows.
    @Published var systemPane: SystemPane = .satellites
    /// iPhone: the sidebar drawer is up.
    @Published var showDrawer = false
    /// Wide windows: the left bar is hidden.
    @Published var sidebarHidden = UserDefaults.standard.bool(forKey: "orbital.sidebar.hidden") {
        didSet { UserDefaults.standard.set(sidebarHidden, forKey: "orbital.sidebar.hidden") }
    }
    /// iPhone stacks of the hub's extra tabs, by tab id.
    @Published var customPaths: [String: [BrowseRoute]] = [:]

    func customPath(_ id: String) -> Binding<[BrowseRoute]> {
        Binding(get: { self.customPaths[id] ?? [] }, set: { self.customPaths[id] = $0 })
    }

    /// What the wide layout's detail column shows, and on iPhone which tab is up.
    @Published var destination: Destination = .home {
        didSet {
            if destination != oldValue { widePath = [] }
        }
    }
    @Published var deskDraft: String?
    /// The desk scrolls to this task's card, then clears it.
    @Published var deskFocusTask: String?
    @Published var pendingSectionID: String?
    @Published var pendingNodeID: String?
    @Published var pendingTabID: String?
    /// Which iPhone stack the pending tab is for, so a copy of the same page on another tab does not take it.
    @Published var pendingStack: RootTab?
    /// iPhone stacks, one per tab.
    @Published var homePath: [BrowseRoute] = []
    @Published var moneyPath: [BrowseRoute] = []
    @Published var agentsPath: [BrowseRoute] = []
    @Published var deskPath: [BrowseRoute] = []
    @Published var trackerPath: [BrowseRoute] = []
    @Published var alertsPath: [BrowseRoute] = []
    /// Pushes inside the wide layout's detail column. Cleared when the left bar moves.
    @Published var widePath: [BrowseRoute] = []
    /// Set by RootView from the window width.
    @Published var isCompact = true
    /// Bumped when a notification or button asks the shell to move.
    @Published var navigationToken = 0
    /// Automations opens with this feed's detail showing, then clears it.
    @Published var focusedJobID: String?
    /// Run agent, open as a sheet, filled in from a button or a link.
    @Published var runDraft: AgentRunDraft?

    var tab: RootTab {
        switch destination {
        case .home, .section: return .home
        case .money: return .money
        case .agents, .settings, .automations, .manage: return .agents
        case .desk: return .desk
        case .tracker: return .tracker
        case .alerts: return .alerts
        case .custom(let id): return .custom(id)
        }
    }

    func select(tab: RootTab) {
        if tab == self.tab, isCompact {
            // Tapping the current tab again returns to its root, like the system apps.
            setPath([], for: tab)
            return
        }
        switch tab {
        case .home: destination = .home
        case .money: destination = .money
        case .agents: destination = .agents
        case .desk: destination = .desk
        case .tracker: destination = .tracker
        case .alerts: destination = .alerts
        case .custom(let id): destination = .custom(id)
        }
    }

    private func setPath(_ path: [BrowseRoute], for tab: RootTab) {
        switch tab {
        case .home: homePath = path
        case .money: moneyPath = path
        case .agents: agentsPath = path
        case .desk: deskPath = path
        case .tracker: trackerPath = path
        case .alerts: alertsPath = path
        case .custom(let id): customPaths[id] = path
        }
    }

    /// System, on one pane.
    func openSystem(_ pane: SystemPane) {
        systemPane = pane
        destination = .agents
        if isCompact { agentsPath = [] } else { widePath = [] }
        navigationToken += 1
    }

    /// Where a navigation item (a custom tab, a sidebar row) leads. Groups push their list.
    func open(nav item: NavItem, key: String) {
        switch item.target {
        case .section(let section, let node, let tab):
            if isCompact, case .custom = self.tab {
                pendingSectionID = section
                pendingNodeID = node
                pendingTabID = tab
                pendingStack = self.tab
                push(node.map { .node(section, $0) } ?? .section(section))
            } else {
                open(section: section, node: node, tab: tab)
            }
        case .space(let space):
            if isCompact, case .custom = self.tab { push(.space(Spaces.key(space))) } else {
                if isCompact { destination = .home; homePath = [] } else { destination = .home; widePath = [] }
                push(.space(Spaces.key(space)))
                navigationToken += 1
            }
        case .view(let view):
            openView(view)
        case .link(let raw):
            if let url = URL(string: raw) {
                if url.scheme == "https" { UIApplication.shared.open(url) } else { handle(url) }
            }
        case nil:
            push(.navGroup(key))
        }
    }

    func openView(_ view: String) {
        switch view {
        case "home": destination = .home; homePath = []
        case "system", "satellites": openSystem(.satellites)
        case "automations": openSystem(.automations)
        case "manage": openSystem(.manage)
        case "settings": openSystem(.settings)
        case "agent": openDesk(prompt: nil)
        case "tracker": openTracker(item: nil)
        case "alerts": openAlerts()
        case "spaces": push(.spaces)
        case "money": openMoney()
        case "social", "analytics", "videos":
            // Hub nav tabs (orbital nav) host these screens; tab id matches the view name.
            destination = .custom(view)
            customPaths[view] = []
        case "marketing":
            destination = .custom("marketing")
            customPaths["marketing"] = []
        default: openBrowse()
        }
        navigationToken += 1
    }

    func openDesk(prompt: String?, task: String? = nil) {
        deskDraft = prompt
        deskFocusTask = task
        destination = .desk
        if isCompact { deskPath = [] } else { widePath = [] }
        navigationToken += 1
    }

    /// The Tracker, or one item on top of its list.
    func openTracker(item: String?) {
        let path: [BrowseRoute] = item.map { [.trackerItem($0)] } ?? []
        destination = .tracker
        if isCompact { trackerPath = path } else { widePath = path }
        navigationToken += 1
    }

    /// The Agents screen, optionally with one job or machine on top.
    func openAgents(route: BrowseRoute? = nil) {
        destination = .agents
        let path = route.map { [$0] } ?? []
        if isCompact { agentsPath = path } else { widePath = path }
        navigationToken += 1
    }

    /// Run agent, filled in. Any field can be left for the sheet to ask.
    func runAgent(_ draft: AgentRunDraft = AgentRunDraft()) {
        runDraft = draft
    }

    /// Opens a page from a notification, a deep link, or a button. On iPhone it lands on the Home stack.
    func open(section: String?, node: String? = nil, tab: String? = nil) {
        pendingSectionID = section
        pendingNodeID = node
        pendingTabID = tab
        guard isCompact else {
            pendingStack = nil
            destination = .section(section)
            widePath = []
            navigationToken += 1
            return
        }
        pendingStack = .home
        var path: [BrowseRoute] = []
        if let section {
            path.append(.section(section))
            if let node { path.append(.node(section, node)) }
        } else {
            path.append(.browse)
        }
        destination = .home
        homePath = path
        navigationToken += 1
    }

    /// Opens a page from Home or a space page. On iPhone it pushes onto the current tab so Back returns there.
    func show(_ location: PageLocation, tab pageTab: String? = nil) {
        pendingSectionID = location.sectionID
        pendingNodeID = location.nodeID
        pendingTabID = pageTab ?? location.tabID
        let route: BrowseRoute = location.nodeID.map { .node(location.sectionID, $0) } ?? .section(location.sectionID)
        guard isCompact else {
            // Wide: a page pushed in the detail column keeps the space or product page behind it.
            pendingStack = nil
            widePath.append(route)
            navigationToken += 1
            return
        }
        guard tab == .home || tab == .alerts || tab == .money else {
            open(section: location.sectionID, node: location.nodeID, tab: pageTab ?? location.tabID)
            return
        }
        pendingStack = tab
        push(route)
    }

    /// Push onto the visible tab's stack, or the wide detail column. A page opened from Agents, the desk,
    /// or the tracker lands on Home, so those tabs keep their own screens.
    func push(_ route: BrowseRoute) {
        if route == .tracker {
            openTracker(item: nil)
            return
        }
        if route == .agents {
            openAgents()
            return
        }
        guard isCompact else {
            widePath.append(route)
            return
        }
        switch tab {
        case .home: homePath.append(route)
        case .money: moneyPath.append(route)
        case .alerts: alertsPath.append(route)
        case .agents: agentsPath.append(route)
        case .custom(let id): customPaths[id, default: []].append(route)
        case .desk, .tracker:
            switch route {
            case .trackerItem, .agentJob:
                if tab == .desk { deskPath.append(route) } else { trackerPath.append(route) }
            default:
                homePath = [route]
                destination = .home
            }
        }
    }

    func openSpace(_ id: String) { push(.space(id)) }

    func openProduct(_ id: String) { push(.product(id)) }

    /// The Money tab, optionally narrowed (a business, a product) with that view on top.
    func openMoney(_ filter: MoneyFilter? = nil) {
        destination = .money
        let path: [BrowseRoute] = filter.map { [.money($0)] } ?? []
        if isCompact { moneyPath = path } else { widePath = path }
        navigationToken += 1
    }

    /// One product's subscriptions, pushed on the current stack.
    func openSubscriptions(_ productID: String) { push(.subscriptions(productID)) }

    func openAlerts() {
        destination = .alerts
        if isCompact { alertsPath = [] }
        navigationToken += 1
    }

    func openBrowse() {
        if isCompact { push(.browse) } else { destination = .section(nil) }
    }

    func openManage() { openSystem(.manage) }

    func openSettings() { openSystem(.settings) }

    /// Every feed's schedule, countdown, last run, and run buttons. `job` opens that feed's detail.
    func openAutomations(job: String? = nil) {
        focusedJobID = job
        openSystem(.automations)
    }

    /// Handles an orbital:// link. `onConnect` receives the hub address of an orbital://connect link.
    func handle(_ url: URL, onConnect: ((String, String?) -> Void)? = nil) {
        guard url.scheme?.lowercased() == OrbitalBrand.urlScheme else { return }
        let host = (url.host ?? "").lowercased()
        let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems ?? []
        func q(_ name: String) -> String? {
            items.first { $0.name == name }?.value.flatMap { $0.isEmpty ? nil : $0 }
        }
        switch host {
        case "connect":
            if let hub = q("hub") ?? q("url") { onConnect?(hub, q("pair")) }
        case "agent", "desk":
            openDesk(prompt: q("prompt"), task: q("task"))
        case "tracker":
            openTracker(item: q("id"))
        case "agents":
            if let job = q("job") {
                openAgents(route: .agentJob(job))
            } else if let machine = q("machine"), q("run") == nil {
                openAgents(route: .satellite(machine))
            } else {
                openAgents()
            }
            if q("run") != nil || q("prompt") != nil {
                runAgent(AgentRunDraft(machine: q("machine"), agent: q("agent"), prompt: q("prompt") ?? "", cwd: q("cwd") ?? "", title: q("title") ?? ""))
            }
        case "run":
            openAgents()
            runAgent(AgentRunDraft(machine: q("machine"), agent: q("agent"), prompt: q("prompt") ?? "", cwd: q("cwd") ?? "", title: q("title") ?? ""))
        case "job":
            if let job = q("id") { openAgents(route: .agentJob(job)) }
        case "alerts":
            openAlerts()
        case "money":
            // orbital://money?business=<space>&product=<name>
            var filter = MoneyFilter()
            filter.business = q("business").map(Spaces.key)
            filter.product = q("product").map(Spaces.key)
            openMoney(filter.isNarrowed ? filter : nil)
        case "subscriptions":
            // orbital://subscriptions?product=<name>: on the Money tab, over that product's money.
            guard let product = q("product").map(Spaces.key) else { return }
            var filter = MoneyFilter()
            filter.product = product
            openMoney(filter)
            if isCompact { moneyPath.append(.subscriptions(product)) } else { widePath.append(.subscriptions(product)) }
        case "system", "satellites":
            openSystem(.satellites)
        case "spaces":
            openView("spaces")
        case "manage", "settings", "automations", "jobs", "browse":
            if isCompact, host == "browse" {
                destination = .home
                homePath = []
            }
            switch host {
            case "manage": openManage()
            case "settings": openSettings()
            case "browse": openBrowse()
            default: openAutomations(job: q("job"))
            }
            navigationToken += 1
        case "home", "":
            if url.path.isEmpty || host == "home" {
                homePath = []
                destination = .home
                navigationToken += 1
            } else {
                open(section: q("section"), node: q("node"), tab: q("tab"))
            }
        case "space", "product":
            guard let id = q("id") else { return }
            destination = .home
            homePath = []
            push(host == "space" ? .space(Spaces.key(id)) : .product(Spaces.key(id)))
            navigationToken += 1
        default:
            open(section: q("section"), node: q("node"), tab: q("tab"))
        }
    }
}
