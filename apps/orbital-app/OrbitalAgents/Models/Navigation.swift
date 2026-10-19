import Foundation
import SwiftUI

// The hub's navigation document (`GET /v1/nav`, schema orbital.nav.v1): extra bottom-bar items and the
// sidebar tree. The five fixed tabs (Home, System, Agent, Tracker, Alerts) are not in it. An item with
// children and no target is a group, a bucket of anything, nested as deep as the hub allows.

enum NavTarget: Equatable, Hashable {
    case section(String, node: String?, tab: String?)
    case space(String)
    case view(String)
    case link(String)
}

struct NavItem: Identifiable, Equatable, Hashable {
    var id: String
    var title: String
    var icon: String?
    var accent: String?
    var target: NavTarget?
    var children: [NavItem]?

    var isGroup: Bool { target == nil }

    /// An SF Symbol to draw: the item's own, else one for what it opens.
    var symbol: String {
        if let icon, !icon.hasPrefix("/") { return icon }
        switch target {
        case .section: return "doc.text"
        case .space: return "square.stack.3d.up"
        case .view(let view): return NavItem.viewSymbol(view)
        case .link: return "link"
        case nil: return "folder.fill"
        }
    }

    /// An uploaded image (`/v1/media/<id>`) to draw instead of a symbol.
    var imagePath: String? { icon.flatMap { $0.hasPrefix("/") ? $0 : nil } }

    var tint: Color { accent.map { OrbitalTheme.accentColor($0) } ?? OrbitalTheme.accent }

    static func viewSymbol(_ view: String) -> String {
        switch view {
        case "home": return "house.fill"
        case "system", "satellites": return "cpu"
        case "automations": return "clock.arrow.2.circlepath"
        case "manage": return "slider.horizontal.3"
        case "settings": return "gearshape.fill"
        case "agent": return "sparkles"
        case "tracker": return "dot.radiowaves.left.and.right"
        case "alerts": return "bell.fill"
        case "spaces": return "square.stack.3d.up.fill"
        default: return "square.grid.2x2"
        }
    }

    init(id: String, title: String, icon: String? = nil, accent: String? = nil, target: NavTarget? = nil, children: [NavItem]? = nil) {
        self.id = id
        self.title = title
        self.icon = icon
        self.accent = accent
        self.target = target
        self.children = children
    }

    init?(json: [String: Any]) {
        guard let id = json["id"] as? String, let title = json["title"] as? String else { return nil }
        self.id = id
        self.title = title
        icon = json["icon"] as? String
        accent = json["accent"] as? String
        if let raw = json["target"] as? [String: Any], let kind = raw["kind"] as? String {
            switch kind {
            case "section":
                target = (raw["section"] as? String).map { .section($0, node: raw["node"] as? String, tab: raw["tab"] as? String) }
            case "space":
                target = (raw["space"] as? String).map { .space($0) }
            case "view":
                target = (raw["view"] as? String).map { .view($0) }
            case "link":
                target = (raw["url"] as? String).map { .link($0) }
            default:
                target = nil
            }
        }
        children = (json["children"] as? [[String: Any]])?.compactMap(NavItem.init(json:))
        if target == nil && children == nil { return nil }
    }
}

struct NavDoc: Equatable {
    var tabs: [NavItem] = []
    /// nil: the default tree (spaces, then their sections).
    var sidebar: [NavItem]?

    static let empty = NavDoc()

    init(tabs: [NavItem] = [], sidebar: [NavItem]? = nil) {
        self.tabs = tabs
        self.sidebar = sidebar
    }

    init(json: [String: Any]) {
        let doc = (json["nav"] as? [String: Any]) ?? json
        tabs = (doc["tabs"] as? [[String: Any]])?.compactMap(NavItem.init(json:)) ?? []
        sidebar = (doc["sidebar"] as? [[String: Any]])?.compactMap(NavItem.init(json:))
    }

    func tab(_ id: String) -> NavItem? { tabs.first { $0.id == id } }

    /// An item anywhere in the tabs or the sidebar, by its key (`tab:<id>/<id>…` or `side:<id>/<id>…`).
    func item(key: String, sidebar fallback: [NavItem]) -> NavItem? {
        let parts = key.split(separator: ":", maxSplits: 1).map(String.init)
        guard parts.count == 2 else { return nil }
        var level = parts[0] == "tab" ? tabs : (sidebar ?? fallback)
        var found: NavItem?
        for id in parts[1].split(separator: "/").map(String.init) {
            guard let next = level.first(where: { $0.id == id }) else { return nil }
            found = next
            level = next.children ?? []
        }
        return found
    }
}

/// Spaces the app files under System instead of showing as a space (machine health pages and the like).
enum SystemSpace {
    static let ids: Set<String> = ["system"]
    static func contains(_ space: String?) -> Bool { space.map { ids.contains(Spaces.key($0)) } ?? false }
}
