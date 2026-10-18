import Foundation

/// One top-level payload: a page (or a set of pages) a feed pushed to the hub.
/// Decoding is lenient: a missing array is empty and a missing number takes a default, so a hand-written
/// payload with only `id`, `title`, and `blocks` still draws.
struct SectionDocument: Codable, Identifiable, Equatable {
    var schema: String
    var id: String
    var title: String
    var subtitle: String?
    var icon: String
    var iconImage: String?
    var order: Int
    var accent: String?
    var layout: SectionLayout
    var summary: String?
    /// The space (home tile) this section belongs to, by space id or name.
    var space: String?
    /// The space's name and SF Symbol, when this section is the one that names them.
    var spaceTitle: String?
    var spaceIcon: String?
    /// Shown on the Home dashboard.
    var pinned: Bool?
    var revision: Int
    var updatedAt: String?
    var blocks: [ContentBlock]
    var tabs: [PageTab]
    var nodes: [NavNode]

    enum CodingKeys: String, CodingKey {
        case schema, id, title, subtitle, icon, order, accent, layout, summary, space, pinned, revision
        case iconImage = "icon_image"
        case spaceTitle = "space_title"
        case spaceIcon = "space_icon"
        case updatedAt = "updated_at"
        case blocks, tabs, nodes
    }

    init(schema: String = OrbitalSchema.section, id: String, title: String, subtitle: String? = nil, icon: String,
         iconImage: String? = nil, order: Int, accent: String? = nil, layout: SectionLayout, summary: String? = nil,
         space: String? = nil, pinned: Bool? = nil, revision: Int = 0, updatedAt: String? = nil,
         blocks: [ContentBlock] = [], tabs: [PageTab] = [], nodes: [NavNode] = []) {
        self.schema = schema
        self.id = id
        self.title = title
        self.subtitle = subtitle
        self.icon = icon
        self.iconImage = iconImage
        self.order = order
        self.accent = accent
        self.layout = layout
        self.summary = summary
        self.space = space
        self.spaceTitle = nil
        self.spaceIcon = nil
        self.pinned = pinned
        self.revision = revision
        self.updatedAt = updatedAt
        self.blocks = blocks
        self.tabs = tabs
        self.nodes = nodes
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        schema = c.lenientString(.schema) ?? OrbitalSchema.section
        title = Insights.nonEmpty(c.lenientString(.title)) ?? id
        subtitle = c.lenientString(.subtitle)
        icon = Insights.nonEmpty(c.lenientString(.icon)) ?? "square.grid.2x2"
        iconImage = c.lenientString(.iconImage)
        order = c.lenientInt(.order) ?? 100
        accent = c.lenientString(.accent)
        summary = c.lenientString(.summary)
        space = Insights.nonEmpty(c.lenientString(.space))
        spaceTitle = Insights.nonEmpty(c.lenientString(.spaceTitle))
        spaceIcon = Insights.nonEmpty(c.lenientString(.spaceIcon))
        pinned = c.lenientBool(.pinned)
        revision = c.lenientInt(.revision) ?? 0
        updatedAt = c.lenientString(.updatedAt)
        blocks = (try? c.decodeIfPresent([ContentBlock].self, forKey: .blocks)) ?? []
        tabs = c.lossy(PageTab.self, .tabs)
        nodes = c.lossy(NavNode.self, .nodes)
        let declared = (try? c.decodeIfPresent(SectionLayout.self, forKey: .layout)) ?? nil
        layout = declared ?? (nodes.isEmpty ? (tabs.isEmpty ? .page : .tabs) : .sidebar)
    }

    var sortedNodes: [NavNode] {
        nodes.sorted { lhs, rhs in
            if lhs.order != rhs.order { return lhs.order < rhs.order }
            return lhs.id < rhs.id
        }
    }
}

/// Schema names the app sends. The hub also accepts documents without one.
enum OrbitalSchema {
    static let section = "orbital.section.v1"
    static let ops = "orbital.ops.v1"
    static let action = "orbital.action.v1"
    static let alert = "orbital.alert.v1"
}

enum SectionLayout: String, Codable {
    case page
    case tabs
    case sidebar
}

enum NodeLayout: String, Codable {
    case page
    case tabs
}

struct PageTab: Codable, Identifiable, Equatable {
    var id: String
    var title: String
    var icon: String?
    var blocks: [ContentBlock]

    enum CodingKeys: String, CodingKey { case id, title, icon, blocks }

    init(id: String, title: String, icon: String? = nil, blocks: [ContentBlock]) {
        self.id = id
        self.title = title
        self.icon = icon
        self.blocks = blocks
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = Insights.nonEmpty(c.lenientString(.title)) ?? id
        icon = c.lenientString(.icon)
        blocks = (try? c.decodeIfPresent([ContentBlock].self, forKey: .blocks)) ?? []
    }
}

struct NavNode: Codable, Identifiable, Equatable {
    var id: String
    var title: String
    var subtitle: String?
    var icon: String?
    var iconImage: String?
    var order: Int
    var layout: NodeLayout
    var blocks: [ContentBlock]
    var tabs: [PageTab]

    enum CodingKeys: String, CodingKey {
        case id, title, subtitle, icon, order, layout, blocks, tabs
        case iconImage = "icon_image"
    }

    init(id: String, title: String, subtitle: String? = nil, icon: String? = nil, iconImage: String? = nil, order: Int,
         layout: NodeLayout, blocks: [ContentBlock], tabs: [PageTab]) {
        self.id = id
        self.title = title
        self.subtitle = subtitle
        self.icon = icon
        self.iconImage = iconImage
        self.order = order
        self.layout = layout
        self.blocks = blocks
        self.tabs = tabs
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = Insights.nonEmpty(c.lenientString(.title)) ?? id
        subtitle = c.lenientString(.subtitle)
        icon = c.lenientString(.icon)
        iconImage = c.lenientString(.iconImage)
        order = c.lenientInt(.order) ?? 100
        blocks = (try? c.decodeIfPresent([ContentBlock].self, forKey: .blocks)) ?? []
        tabs = c.lossy(PageTab.self, .tabs)
        let declared = (try? c.decodeIfPresent(NodeLayout.self, forKey: .layout)) ?? nil
        layout = declared ?? (tabs.isEmpty ? .page : .tabs)
    }

    /// Every block on the node, across tabs, in display order.
    var allBlocks: [(tabID: String?, block: ContentBlock)] {
        if layout == .tabs {
            return tabs.flatMap { tab in tab.blocks.map { (tabID: Optional(tab.id), block: $0) } }
        }
        return blocks.map { (tabID: nil, block: $0) }
    }
}

struct CatalogEntry: Codable, Identifiable, Equatable {
    var id: String
    var title: String
    var subtitle: String?
    var icon: String
    var iconImage: String?
    var order: Int
    var accent: String?
    var layout: SectionLayout
    var revision: Int
    var updatedAt: String?
    var summary: String?
    var nodeCount: Int
    var space: String?
    var pinned: Bool?

    enum CodingKeys: String, CodingKey {
        case id, title, subtitle, icon, order, accent, layout, revision, summary, space, pinned
        case iconImage = "icon_image"
        case updatedAt = "updated_at"
        case nodeCount = "node_count"
    }

    init(id: String, title: String, subtitle: String?, icon: String, iconImage: String?, order: Int, accent: String?,
         layout: SectionLayout, revision: Int, updatedAt: String?, summary: String?, nodeCount: Int,
         space: String? = nil, pinned: Bool? = nil) {
        self.id = id
        self.title = title
        self.subtitle = subtitle
        self.icon = icon
        self.iconImage = iconImage
        self.order = order
        self.accent = accent
        self.layout = layout
        self.revision = revision
        self.updatedAt = updatedAt
        self.summary = summary
        self.nodeCount = nodeCount
        self.space = space
        self.pinned = pinned
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = Insights.nonEmpty(c.lenientString(.title)) ?? id
        subtitle = c.lenientString(.subtitle)
        icon = Insights.nonEmpty(c.lenientString(.icon)) ?? "square.grid.2x2"
        iconImage = c.lenientString(.iconImage)
        order = c.lenientInt(.order) ?? 100
        accent = c.lenientString(.accent)
        layout = ((try? c.decodeIfPresent(SectionLayout.self, forKey: .layout)) ?? nil) ?? .page
        revision = c.lenientInt(.revision) ?? 0
        updatedAt = c.lenientString(.updatedAt)
        summary = c.lenientString(.summary)
        nodeCount = c.lenientInt(.nodeCount) ?? 0
        space = Insights.nonEmpty(c.lenientString(.space))
        pinned = c.lenientBool(.pinned)
    }
}

/// A home tile: a project, a team, a household, a server fleet. Named by the hub, never by the app.
struct SpaceInfo: Codable, Identifiable, Equatable {
    var id: String
    var title: String
    var subtitle: String?
    var icon: String?
    var iconImage: String?
    var accent: String?
    var order: Int?
    /// Section ids in this space, when the hub lists them here instead of on each section.
    var sections: [String]?

    enum CodingKeys: String, CodingKey {
        case id, title, name, subtitle, icon, accent, order, sections
        case iconImage = "icon_image"
    }

    init(id: String, title: String, subtitle: String? = nil, icon: String? = nil, iconImage: String? = nil,
         accent: String? = nil, order: Int? = nil, sections: [String]? = nil) {
        self.id = id
        self.title = title
        self.subtitle = subtitle
        self.icon = icon
        self.iconImage = iconImage
        self.accent = accent
        self.order = order
        self.sections = sections
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let name = Insights.nonEmpty(c.lenientString(.title)) ?? Insights.nonEmpty(c.lenientString(.name))
        guard let id = Insights.nonEmpty(c.lenientString(.id)) ?? name else {
            throw DecodingError.keyNotFound(CodingKeys.id, .init(codingPath: c.codingPath, debugDescription: "space id"))
        }
        self.id = id
        title = name ?? id
        subtitle = c.lenientString(.subtitle)
        icon = c.lenientString(.icon)
        iconImage = c.lenientString(.iconImage)
        accent = c.lenientString(.accent)
        order = c.lenientInt(.order)
        sections = (try? c.decodeIfPresent([String].self, forKey: .sections)) ?? nil
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(title, forKey: .title)
        try c.encodeIfPresent(subtitle, forKey: .subtitle)
        try c.encodeIfPresent(icon, forKey: .icon)
        try c.encodeIfPresent(iconImage, forKey: .iconImage)
        try c.encodeIfPresent(accent, forKey: .accent)
        try c.encodeIfPresent(order, forKey: .order)
        try c.encodeIfPresent(sections, forKey: .sections)
    }
}

struct CatalogResponse: Codable, Equatable {
    var sections: [CatalogEntry]
    /// Spaces the hub defines. Absent on a hub that only tags sections with `space`.
    var spaces: [SpaceInfo]?

    enum CodingKeys: String, CodingKey { case sections, spaces }

    init(sections: [CatalogEntry], spaces: [SpaceInfo]? = nil) {
        self.sections = sections
        self.spaces = spaces
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        sections = c.lossy(CatalogEntry.self, .sections)
        let spaces = c.lossy(SpaceInfo.self, .spaces)
        self.spaces = spaces.isEmpty ? nil : spaces
    }
}

struct SyncResponse: Decodable {
    var catalog: CatalogResponse
    var sections: [SectionDocument]
    var alerts: [OrbitalAlert]
    var pulse: [PulseItem]?
    var serverTime: String?
    /// Alert ids retracted lately, so a phone that was offline drops their notifications.
    var retracted: [String]?
    /// Feed schedules and last runs, when the hub runs the scheduler.
    var jobs: JobsSnapshot?
    /// Every alert marked done on any device, so this one clears what it missed while asleep.
    var acked: [String]?
    /// Settings every device shares.
    var prefs: SharedPrefs?
    /// Machines that joined the hub, when the hub includes them in the sync.
    var satellites: [Satellite]?
    /// Who the hub is (same as `/v1/hub/info`).
    var hub: HubInfo?

    enum CodingKeys: String, CodingKey {
        case catalog, sections, alerts, pulse, retracted, jobs, acked, prefs, satellites, hub
        case serverTime = "server_time"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        catalog = (try? c.decode(CatalogResponse.self, forKey: .catalog)) ?? CatalogResponse(sections: [])
        sections = c.lossy(SectionDocument.self, .sections)
        alerts = c.lossy(OrbitalAlert.self, .alerts)
        pulse = (try? c.decodeIfPresent([PulseItem].self, forKey: .pulse)) ?? nil
        serverTime = c.lenientString(.serverTime)
        retracted = (try? c.decodeIfPresent([String].self, forKey: .retracted)) ?? nil
        jobs = (try? c.decodeIfPresent(JobsSnapshot.self, forKey: .jobs)) ?? nil
        acked = (try? c.decodeIfPresent([String].self, forKey: .acked)) ?? nil
        prefs = (try? c.decodeIfPresent(SharedPrefs.self, forKey: .prefs)) ?? nil
        satellites = c.contains(.satellites) ? c.lossy(Satellite.self, .satellites) : nil
        hub = (try? c.decodeIfPresent(HubInfo.self, forKey: .hub)) ?? nil
    }
}

/// Settings the Mac app and the iPhone share through the hub (`/v1/prefs`). Keys this build does not know are ignored.
struct SharedPrefs: Codable, Equatable {
    /// Sections pinned to Home on any device, in pin order. Nil until a device has pinned one.
    var pinnedSections: [String]?

    enum CodingKeys: String, CodingKey {
        case pinnedSections = "pinned_sections"
    }
}

/// `GET /v1/hub/info`: who this hub is. Needs no sign-in, so the sign-in screen can check an address first.
struct HubInfo: Codable, Equatable {
    var service: String?
    var name: String?
    var version: String?
    var hubID: String?
    var machine: String?

    enum CodingKeys: String, CodingKey {
        case service, name, version, machine
        case hubID = "hub_id"
    }

    var isOrbitalHub: Bool { service == nil || service == "orbital-hub" }
}

/// `GET /v1/health`. `apns` says whether the hub sends Apple pushes (optional; off by default).
struct HubHealth: Decodable, Equatable {
    struct APNs: Decodable, Equatable {
        var enabled: Bool
        var configured: Bool?
        var environment: String?
    }

    var ok: Bool?
    var version: String?
    var apns: APNs?
}

extension SectionDocument {
    /// Rewrites every block on every page (section, tabs, nodes, node tabs). `node` and `tab` say where the block sits.
    func mapBlocks(_ transform: (_ node: String?, _ tab: String?, ContentBlock) -> ContentBlock) -> SectionDocument {
        var copy = self
        copy.blocks = blocks.map { transform(nil, nil, $0) }
        copy.tabs = tabs.map { tab in
            var t = tab
            t.blocks = tab.blocks.map { transform(nil, tab.id, $0) }
            return t
        }
        copy.nodes = nodes.map { node in
            var n = node
            n.blocks = node.blocks.map { transform(node.id, nil, $0) }
            n.tabs = node.tabs.map { tab in
                var t = tab
                t.blocks = tab.blocks.map { transform(node.id, tab.id, $0) }
                return t
            }
            return n
        }
        return copy
    }

    /// Every page the layout shows, with its blocks: the section page, its tabs, or each node and its tabs.
    /// A tabs or sidebar section's own `blocks` (a summary drawn above the tab or node content) come
    /// first, as a page with no node and no tab.
    var pages: [(node: NavNode?, tabID: String?, blocks: [ContentBlock])] {
        let lead: [(node: NavNode?, tabID: String?, blocks: [ContentBlock])] = blocks.isEmpty ? [] : [(nil, nil, blocks)]
        switch layout {
        case .page:
            return [(nil, nil, blocks)]
        case .tabs:
            return lead + tabs.map { (nil, $0.id, $0.blocks) }
        case .sidebar:
            return lead + sortedNodes.flatMap { node -> [(node: NavNode?, tabID: String?, blocks: [ContentBlock])] in
                node.layout == .tabs ? node.tabs.map { (node, $0.id, $0.blocks) } : [(node, nil, node.blocks)]
            }
        }
    }
}
