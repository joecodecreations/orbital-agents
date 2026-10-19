import Foundation

/// One block of a page. Unknown types are kept as they came, so a newer hub's block survives a cache
/// round trip and an older app simply skips drawing it.
enum ContentBlock: Identifiable, Equatable {
    case heading(HeadingBlock)
    case paragraph(ParagraphBlock)
    case bullets(BulletsBlock)
    case callout(CalloutBlock)
    case metrics(MetricsBlock)
    case keyValue(KeyValueBlock)
    case table(TableBlock)
    case timeline(TimelineBlock)
    case divider(DividerBlock)
    case chart(ChartBlock)
    case image(ImageBlock)
    case hero(HeroBlock)
    case buttons(ButtonsBlock)
    case progress(ProgressBlock)
    case status(StatusBlock)
    case links(LinksBlock)
    case product(ProductBlock)
    case ledger(LedgerBlock)
    case tasks(TasksBlock)
    case series(SeriesBlock)
    case insightCards(InsightCardsBlock)
    case videos(VideosBlock)
    case docket(DocketBlock)
    case marketing(MarketingBlock)
    case unsupported(id: String, raw: Data)

    /// Every type this build draws, as the payload names it.
    static let knownTypes: Set<String> = [
        "heading", "paragraph", "bullets", "callout", "metrics", "key_value", "table", "timeline", "divider",
        "chart", "image", "hero", "buttons", "progress", "status", "links", "product", "ledger", "tasks",
        "series", "insight_cards", "videos", "docket", "marketing",
    ]

    var id: String {
        switch self {
        case .heading(let b): return b.id
        case .paragraph(let b): return b.id
        case .bullets(let b): return b.id
        case .callout(let b): return b.id
        case .metrics(let b): return b.id
        case .keyValue(let b): return b.id
        case .table(let b): return b.id
        case .timeline(let b): return b.id
        case .divider(let b): return b.id
        case .chart(let b): return b.id
        case .image(let b): return b.id
        case .hero(let b): return b.id
        case .buttons(let b): return b.id
        case .progress(let b): return b.id
        case .status(let b): return b.id
        case .links(let b): return b.id
        case .product(let b): return b.id
        case .ledger(let b): return b.id
        case .tasks(let b): return b.id
        case .series(let b): return b.id
        case .insightCards(let b): return b.id
        case .videos(let b): return b.id
        case .docket(let b): return b.id
        case .marketing(let b): return b.id
        case .unsupported(let id, _): return id
        }
    }

    /// The payload's `type` string.
    var typeName: String {
        switch self {
        case .heading: return "heading"
        case .paragraph: return "paragraph"
        case .bullets: return "bullets"
        case .callout: return "callout"
        case .metrics: return "metrics"
        case .keyValue: return "key_value"
        case .table: return "table"
        case .timeline: return "timeline"
        case .divider: return "divider"
        case .chart: return "chart"
        case .image: return "image"
        case .hero: return "hero"
        case .buttons: return "buttons"
        case .progress: return "progress"
        case .status: return "status"
        case .links: return "links"
        case .product: return "product"
        case .ledger: return "ledger"
        case .tasks: return "tasks"
        case .series: return "series"
        case .insightCards: return "insight_cards"
        case .videos: return "videos"
        case .docket: return "docket"
        case .marketing: return "marketing"
        case .unsupported(_, let raw):
            let object = try? JSONSerialization.jsonObject(with: raw) as? [String: Any]
            return object?["type"] as? String ?? "unknown"
        }
    }
}

extension ContentBlock: Codable {
    private enum TypeKey: String, CodingKey { case type, id }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: TypeKey.self)
        let type = try container.decode(String.self, forKey: .type)
        do {
            switch type {
            case "heading": self = .heading(try HeadingBlock(from: decoder))
            case "paragraph", "text", "markdown": self = .paragraph(try ParagraphBlock(from: decoder))
            case "bullets", "list": self = .bullets(try BulletsBlock(from: decoder))
            case "callout": self = .callout(try CalloutBlock(from: decoder))
            case "metrics", "metric": self = .metrics(try MetricsBlock(from: decoder))
            case "key_value": self = .keyValue(try KeyValueBlock(from: decoder))
            case "table": self = .table(try TableBlock(from: decoder))
            case "timeline": self = .timeline(try TimelineBlock(from: decoder))
            case "divider": self = .divider(try DividerBlock(from: decoder))
            case "chart": self = .chart(try ChartBlock(from: decoder))
            case "image", "media": self = .image(try ImageBlock(from: decoder))
            case "hero": self = .hero(try HeroBlock(from: decoder))
            case "buttons", "actions": self = .buttons(try ButtonsBlock(from: decoder))
            case "progress": self = .progress(try ProgressBlock(from: decoder))
            case "status": self = .status(try StatusBlock(from: decoder))
            case "links": self = .links(try LinksBlock(from: decoder))
            case "product": self = .product(try ProductBlock(from: decoder))
            case "ledger": self = .ledger(try LedgerBlock(from: decoder))
            case "tasks": self = .tasks(try TasksBlock(from: decoder))
            case "series": self = .series(try SeriesBlock(from: decoder))
            case "insight_cards": self = .insightCards(try InsightCardsBlock(from: decoder))
            case "videos": self = .videos(try VideosBlock(from: decoder))
            case "docket": self = .docket(try DocketBlock(from: decoder))
            case "marketing": self = .marketing(try MarketingBlock(from: decoder))
            default:
                self = Self.preserved(container, decoder)
            }
        } catch {
            // One malformed block must not drop the whole section. Keep it and skip drawing it.
            self = Self.preserved(container, decoder)
        }
    }

    private static func preserved(_ container: KeyedDecodingContainer<TypeKey>, _ decoder: Decoder) -> ContentBlock {
        let id = (try? container.decodeIfPresent(String.self, forKey: .id)) ?? "unknown"
        let raw = (try? JSONSerialization.data(withJSONObject: jsonObject(from: decoder))) ?? Data("{}".utf8)
        return .unsupported(id: id, raw: raw)
    }

    func encode(to encoder: Encoder) throws {
        switch self {
        case .heading(let b): try b.encode(to: encoder)
        case .paragraph(let b): try b.encode(to: encoder)
        case .bullets(let b): try b.encode(to: encoder)
        case .callout(let b): try b.encode(to: encoder)
        case .metrics(let b): try b.encode(to: encoder)
        case .keyValue(let b): try b.encode(to: encoder)
        case .table(let b): try b.encode(to: encoder)
        case .timeline(let b): try b.encode(to: encoder)
        case .divider(let b): try b.encode(to: encoder)
        case .chart(let b): try b.encode(to: encoder)
        case .image(let b): try b.encode(to: encoder)
        case .hero(let b): try b.encode(to: encoder)
        case .buttons(let b): try b.encode(to: encoder)
        case .progress(let b): try b.encode(to: encoder)
        case .status(let b): try b.encode(to: encoder)
        case .links(let b): try b.encode(to: encoder)
        case .product(let b): try b.encode(to: encoder)
        case .ledger(let b): try b.encode(to: encoder)
        case .tasks(let b): try b.encode(to: encoder)
        case .series(let b): try b.encode(to: encoder)
        case .insightCards(let b): try b.encode(to: encoder)
        case .videos(let b): try b.encode(to: encoder)
        case .docket(let b): try b.encode(to: encoder)
        case .marketing(let b): try b.encode(to: encoder)
        case .unsupported(_, let raw):
            let obj = try JSONSerialization.jsonObject(with: raw)
            var container = encoder.singleValueContainer()
            try container.encode(AnyCodable(obj))
        }
    }
}

// MARK: - Text

struct HeadingBlock: Codable, Equatable {
    var type: String = "heading"
    var id: String
    var text: String
    var level: Int

    enum CodingKeys: String, CodingKey { case type, id, text, level }

    init(id: String, text: String, level: Int = 2) {
        self.id = id
        self.text = text
        self.level = level
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        text = c.lenientString(.text) ?? ""
        level = min(3, max(1, c.lenientInt(.level) ?? 2))
    }
}

struct ParagraphBlock: Codable, Equatable {
    var type: String = "paragraph"
    var id: String
    var text: String

    enum CodingKeys: String, CodingKey { case type, id, text }

    init(id: String, text: String) {
        self.id = id
        self.text = text
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        text = c.lenientString(.text) ?? ""
    }
}

struct BulletsBlock: Codable, Equatable {
    var type: String = "bullets"
    var id: String
    var title: String?
    var ordered: Bool
    var items: [String]

    enum CodingKeys: String, CodingKey { case type, id, title, ordered, items }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = c.lenientString(.title)
        ordered = c.lenientBool(.ordered) ?? false
        items = c.lossy(String.self, .items)
    }
}

struct CalloutBlock: Codable, Equatable {
    var type: String = "callout"
    var id: String
    var tone: String
    var title: String?
    var text: String

    enum CodingKeys: String, CodingKey { case type, id, tone, title, text }

    init(id: String, tone: String, title: String?, text: String) {
        self.id = id
        self.tone = tone
        self.title = title
        self.text = text
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        tone = Insights.nonEmpty(c.lenientString(.tone)) ?? "info"
        title = c.lenientString(.title)
        text = c.lenientString(.text) ?? ""
    }
}

// MARK: - Figures

struct MetricTile: Codable, Equatable, Identifiable {
    var id: String
    var label: String
    var value: String
    var delta: String?
    var direction: String?
    var caption: String?

    enum CodingKeys: String, CodingKey {
        case id, label, value, delta, direction, caption
    }

    init(id: String = "", label: String, value: String, delta: String? = nil, direction: String? = nil, caption: String? = nil) {
        self.id = id
        self.label = label
        self.value = value
        self.delta = delta
        self.direction = direction
        self.caption = caption
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let label = c.lenientString(.label) ?? ""
        let decoded = c.lenientString(.id) ?? ""
        id = decoded.isEmpty ? label : decoded
        self.label = label
        value = c.lenientString(.value) ?? "—"
        delta = c.lenientString(.delta)
        direction = c.lenientString(.direction)
        caption = c.lenientString(.caption)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(label, forKey: .label)
        try c.encode(value, forKey: .value)
        try c.encodeIfPresent(delta, forKey: .delta)
        try c.encodeIfPresent(direction, forKey: .direction)
        try c.encodeIfPresent(caption, forKey: .caption)
    }
}

struct MetricsBlock: Codable, Equatable {
    var type: String = "metrics"
    var id: String
    var items: [MetricTile]

    enum CodingKeys: String, CodingKey { case type, id, items }

    init(id: String, items: [MetricTile]) {
        self.id = id
        self.items = items
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        items = c.lossy(MetricTile.self, .items)
    }
}

struct KeyValueItem: Codable, Equatable {
    var label: String
    var value: String

    enum CodingKeys: String, CodingKey { case label, value }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        label = c.lenientString(.label) ?? ""
        value = c.lenientString(.value) ?? ""
    }
}

struct KeyValueBlock: Codable, Equatable {
    var type: String = "key_value"
    var id: String
    var title: String?
    var items: [KeyValueItem]

    enum CodingKeys: String, CodingKey { case type, id, title, items }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = c.lenientString(.title)
        items = c.lossy(KeyValueItem.self, .items)
    }
}

struct TableBlock: Codable, Equatable {
    var type: String = "table"
    var id: String
    var title: String?
    var columns: [String]
    var rows: [[String]]
    /// Show this many rows, then "Show all". Nil or 0 shows every row.
    var limit: Int?

    enum CodingKeys: String, CodingKey { case type, id, title, columns, rows, limit }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = c.lenientString(.title)
        columns = c.lossy(String.self, .columns)
        // Cells may be numbers or strings; every row is read as text.
        let raw = (try? c.decodeIfPresent([[AnyCodable]].self, forKey: .rows)) ?? []
        rows = raw.map { row in row.map { AnyCodable.text($0.value) } }
        limit = c.lenientInt(.limit)
    }
}

struct TimelineItem: Codable, Equatable, Identifiable {
    var id: String
    var at: String
    var title: String
    var detail: String?
    var tone: String?

    enum CodingKeys: String, CodingKey {
        case id, at, title, detail, tone
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        title = c.lenientString(.title) ?? ""
        at = c.lenientString(.at) ?? ""
        let decoded = c.lenientString(.id) ?? ""
        id = decoded.isEmpty ? "\(at)|\(title)" : decoded
        detail = c.lenientString(.detail)
        tone = c.lenientString(.tone)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(at, forKey: .at)
        try c.encode(title, forKey: .title)
        try c.encodeIfPresent(detail, forKey: .detail)
        try c.encodeIfPresent(tone, forKey: .tone)
    }

    /// The `at` text as shown: a relative time when it is an ISO timestamp, the text itself otherwise.
    var shownAt: String {
        guard at.contains("T"), let date = OrbitalDates.parse(at) else { return at }
        return OrbitalDates.relative(OrbitalDates.iso(date)) ?? at
    }
}

struct TimelineBlock: Codable, Equatable {
    var type: String = "timeline"
    var id: String
    var title: String?
    var items: [TimelineItem]

    enum CodingKeys: String, CodingKey { case type, id, title, items }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = c.lenientString(.title)
        items = c.lossy(TimelineItem.self, .items)
    }
}

struct DividerBlock: Codable, Equatable {
    var type: String = "divider"
    var id: String
}

// MARK: - Chart

enum ChartX: Equatable {
    case text(String)
    case number(Double)
}

extension ChartX: Codable {
    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let n = try? c.decode(Double.self) {
            self = .number(n)
        } else {
            self = .text(try c.decode(String.self))
        }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .text(let s): try c.encode(s)
        case .number(let n): try c.encode(n)
        }
    }
}

struct ChartPoint: Codable, Equatable {
    var x: ChartX
    var y: Double
    var label: String?
}

struct ChartSeries: Codable, Equatable, Identifiable {
    var id: String { name }
    var name: String
    var color: String?
    var points: [ChartPoint]

    enum CodingKeys: String, CodingKey { case name, color, points }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = c.lenientString(.name) ?? "Series"
        color = c.lenientString(.color)
        points = c.lossy(ChartPoint.self, .points)
    }
}

struct ChartBlock: Codable, Equatable {
    var type: String = "chart"
    var id: String
    var title: String?
    var caption: String?
    var chart: String
    var xLabel: String?
    var yLabel: String?
    var yFormat: String?
    var series: [ChartSeries]

    enum CodingKeys: String, CodingKey {
        case type, id, title, caption, chart, series
        case xLabel = "x_label"
        case yLabel = "y_label"
        case yFormat = "y_format"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = c.lenientString(.title)
        caption = c.lenientString(.caption)
        chart = (Insights.nonEmpty(c.lenientString(.chart)) ?? "line").lowercased()
        xLabel = c.lenientString(.xLabel)
        yLabel = c.lenientString(.yLabel)
        yFormat = c.lenientString(.yFormat)
        series = c.lossy(ChartSeries.self, .series)
    }
}

// MARK: - Media and actions

struct ImageBlock: Codable, Equatable {
    var type: String = "image"
    var id: String
    var src: String
    var alt: String
    var caption: String?
    var aspect: String?

    enum CodingKeys: String, CodingKey { case type, id, src, url, alt, caption, aspect }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        guard let src = Insights.nonEmpty(c.lenientString(.src)) ?? Insights.nonEmpty(c.lenientString(.url)) else {
            throw DecodingError.keyNotFound(CodingKeys.src, .init(codingPath: c.codingPath, debugDescription: "image src"))
        }
        self.src = src
        alt = c.lenientString(.alt) ?? ""
        caption = c.lenientString(.caption)
        aspect = c.lenientString(.aspect)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(type, forKey: .type)
        try c.encode(id, forKey: .id)
        try c.encode(src, forKey: .src)
        try c.encode(alt, forKey: .alt)
        try c.encodeIfPresent(caption, forKey: .caption)
        try c.encodeIfPresent(aspect, forKey: .aspect)
    }
}

struct HeroImage: Codable, Equatable {
    var src: String
    var alt: String

    enum CodingKeys: String, CodingKey { case src, alt }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        src = try c.decode(String.self, forKey: .src)
        alt = c.lenientString(.alt) ?? ""
    }
}

/// What a button does. `agent` opens the Desk with `prompt`; `open` opens a page; `url` opens a link;
/// `post` sends `action` (e.g. "server.restart") with `value` back to the feed that owns the section,
/// after `confirm` when it is set. `run_agent` (this app) opens Run agent filled in (`agent`, `machine`,
/// `cwd`, `prompt`).
struct ButtonAction: Codable, Equatable {
    var type: String
    var prompt: String?
    var sectionId: String?
    var nodeId: String?
    var tabId: String?
    var url: String?
    var action: String?
    var value: [String: AnyCodable]?
    var confirm: String?
    var agent: String?
    var machine: String?
    var cwd: String?
    var title: String?

    enum CodingKeys: String, CodingKey {
        case type, prompt, url, action, value, confirm, agent, machine, cwd, title
        case sectionId = "section_id"
        case nodeId = "node_id"
        case tabId = "tab_id"
    }

    /// `value` as a plain dictionary for posting.
    var postValue: [String: Any] {
        (value ?? [:]).mapValues(\.value)
    }

    static func == (lhs: ButtonAction, rhs: ButtonAction) -> Bool {
        lhs.type == rhs.type && lhs.prompt == rhs.prompt && lhs.sectionId == rhs.sectionId && lhs.nodeId == rhs.nodeId
            && lhs.tabId == rhs.tabId && lhs.url == rhs.url && lhs.action == rhs.action && lhs.confirm == rhs.confirm
            && lhs.agent == rhs.agent && lhs.machine == rhs.machine && lhs.cwd == rhs.cwd && lhs.title == rhs.title
            && NSDictionary(dictionary: lhs.postValue).isEqual(to: rhs.postValue)
    }
}

struct ButtonItem: Codable, Equatable, Identifiable {
    var id: String
    var label: String
    var style: String
    var url: String?
    var action: ButtonAction?
    /// A `post` button the feed has not answered yet.
    var pending: Bool

    enum CodingKeys: String, CodingKey { case id, label, style, url, action, pending }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        label = c.lenientString(.label) ?? "Open"
        id = Insights.nonEmpty(c.lenientString(.id)) ?? label
        style = Insights.nonEmpty(c.lenientString(.style)) ?? "secondary"
        url = c.lenientString(.url)
        action = try? c.decodeIfPresent(ButtonAction.self, forKey: .action)
        pending = c.lenientBool(.pending) ?? false
    }
}

struct HeroBlock: Codable, Equatable {
    var type: String = "hero"
    var id: String
    var eyebrow: String?
    var title: String
    var subtitle: String?
    var image: HeroImage?
    var actions: [ButtonItem]?

    enum CodingKeys: String, CodingKey { case type, id, eyebrow, title, subtitle, image, actions }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        eyebrow = c.lenientString(.eyebrow)
        title = c.lenientString(.title) ?? ""
        subtitle = c.lenientString(.subtitle)
        image = try? c.decodeIfPresent(HeroImage.self, forKey: .image)
        let actions = c.lossy(ButtonItem.self, .actions)
        self.actions = actions.isEmpty ? nil : actions
    }
}

struct ButtonsBlock: Codable, Equatable {
    var type: String = "buttons"
    var id: String
    var items: [ButtonItem]

    enum CodingKeys: String, CodingKey { case type, id, items }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        items = c.lossy(ButtonItem.self, .items)
    }
}

// MARK: - Progress, status, links

/// Labelled bars: disk use, a goal, a backup's progress. `value` out of `max` (default 100).
struct ProgressItem: Codable, Equatable, Identifiable {
    var id: String
    var label: String
    var value: Double
    var max: Double
    var display: String?
    var caption: String?
    var tone: String?

    enum CodingKeys: String, CodingKey { case id, label, value, max, display, caption, tone }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        label = c.lenientString(.label) ?? ""
        id = Insights.nonEmpty(c.lenientString(.id)) ?? label
        value = c.lenientDouble(.value) ?? 0
        let top = c.lenientDouble(.max) ?? 100
        max = top > 0 ? top : 100
        display = c.lenientString(.display)
        caption = c.lenientString(.caption)
        tone = c.lenientString(.tone)
    }

    var fraction: Double { Swift.min(1, Swift.max(0, value / max)) }

    var shownValue: String {
        if let display, !display.isEmpty { return display }
        if max == 100 { return "\(Int(value.rounded()))%" }
        return "\(Trends.format(value, format: nil)) / \(Trends.format(max, format: nil))"
    }
}

struct ProgressBlock: Codable, Equatable {
    var type: String = "progress"
    var id: String
    var title: String?
    var items: [ProgressItem]

    enum CodingKeys: String, CodingKey { case type, id, title, items }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = c.lenientString(.title)
        items = c.lossy(ProgressItem.self, .items)
    }
}

/// Up / down rows: services on a home server, sites behind a monitor, devices on a network.
struct StatusItem: Codable, Equatable, Identifiable {
    var id: String
    var label: String
    var status: String
    var detail: String?
    var since: String?
    var url: String?

    enum CodingKeys: String, CodingKey { case id, label, status, detail, since, url }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        label = c.lenientString(.label) ?? ""
        id = Insights.nonEmpty(c.lenientString(.id)) ?? label
        status = (Insights.nonEmpty(c.lenientString(.status)) ?? "unknown").lowercased()
        detail = c.lenientString(.detail)
        since = c.lenientString(.since)
        url = c.lenientString(.url)
    }

    /// up, down, degraded, or unknown, whatever word the feed used.
    var health: String {
        switch status {
        case "up", "ok", "online", "healthy", "running", "pass", "passing", "green": return "up"
        case "down", "error", "offline", "failed", "fail", "failing", "red", "critical": return "down"
        case "degraded", "warn", "warning", "slow", "partial", "yellow": return "degraded"
        default: return "unknown"
        }
    }
}

struct StatusBlock: Codable, Equatable {
    var type: String = "status"
    var id: String
    var title: String?
    var items: [StatusItem]

    enum CodingKeys: String, CodingKey { case type, id, title, items }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = c.lenientString(.title)
        items = c.lossy(StatusItem.self, .items)
    }

    var downCount: Int { items.filter { $0.health == "down" }.count }
    var degradedCount: Int { items.filter { $0.health == "degraded" }.count }
}

struct LinkItem: Codable, Equatable, Identifiable {
    var id: String { url }
    var label: String
    var url: String
    var detail: String?
    var icon: String?

    enum CodingKeys: String, CodingKey { case label, url, detail, icon }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        url = try c.decode(String.self, forKey: .url)
        label = Insights.nonEmpty(c.lenientString(.label)) ?? url
        detail = c.lenientString(.detail)
        icon = c.lenientString(.icon)
    }
}

struct LinksBlock: Codable, Equatable {
    var type: String = "links"
    var id: String
    var title: String?
    var items: [LinkItem]

    enum CodingKeys: String, CodingKey { case type, id, title, items }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = c.lenientString(.title)
        items = c.lossy(LinkItem.self, .items)
    }
}

// MARK: - Product (an app, site, game, or service)

struct ProductRelease: Codable, Equatable, Identifiable {
    var id: String
    var platform: String
    var status: String
    var store: String?
    var version: String?
    var build: String?
    var state: String?
    var since: String?
    var url: String?
    var note: String?

    enum CodingKeys: String, CodingKey {
        case id, platform, status, store, version, build, state, since, url, note
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        platform = c.lenientString(.platform) ?? "web"
        status = (c.lenientString(.status) ?? "draft").lowercased()
        store = c.lenientString(.store)
        version = c.lenientString(.version)
        build = c.lenientString(.build)
        state = c.lenientString(.state)
        since = c.lenientString(.since)
        url = c.lenientString(.url)
        note = c.lenientString(.note)
        let decoded = c.lenientString(.id) ?? ""
        id = decoded.isEmpty ? "\(platform)-\(status)-\(version ?? "")" : decoded
    }
}

struct ProductRating: Codable, Equatable {
    var store: String?
    var value: Double
    var max: Double?
    var count: Int?
    var label: String?
}

struct ProductReview: Codable, Equatable, Identifiable {
    var id: String
    var store: String?
    var rating: Int?
    var title: String?
    var body: String?
    var author: String?
    var territory: String?
    var at: String

    enum CodingKeys: String, CodingKey {
        case id, store, rating, title, body, author, territory, at
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        at = c.lenientString(.at) ?? ""
        store = c.lenientString(.store)
        rating = c.lenientInt(.rating)
        title = c.lenientString(.title)
        body = c.lenientString(.body)
        author = c.lenientString(.author)
        territory = c.lenientString(.territory)
        let decoded = c.lenientString(.id) ?? ""
        id = decoded.isEmpty ? "\(store ?? "")-\(at)-\(title ?? "")" : decoded
    }
}

struct ProductLink: Codable, Equatable, Identifiable {
    var id: String { url }
    var label: String
    var url: String
}

struct ProductBlock: Codable, Equatable {
    var type: String = "product"
    var id: String
    var kind: String?
    /// The space it belongs to (`space`, or `company` from older feeds).
    var space: String?
    var headline: String?
    var tone: String?
    var checkedAt: String?
    var releases: [ProductRelease]
    var ratings: [ProductRating]
    var reviews: [ProductReview]
    var metrics: [MetricTile]
    var links: [ProductLink]

    enum CodingKeys: String, CodingKey {
        case type, id, kind, space, company, headline, tone, releases, ratings, reviews, metrics, links
        case checkedAt = "checked_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        kind = c.lenientString(.kind)
        space = Insights.nonEmpty(c.lenientString(.space)) ?? Insights.nonEmpty(c.lenientString(.company))
        headline = c.lenientString(.headline)
        tone = c.lenientString(.tone)
        checkedAt = c.lenientString(.checkedAt)
        releases = c.lossy(ProductRelease.self, .releases)
        ratings = c.lossy(ProductRating.self, .ratings)
        reviews = c.lossy(ProductReview.self, .reviews)
        metrics = c.lossy(MetricTile.self, .metrics)
        links = c.lossy(ProductLink.self, .links)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(type, forKey: .type)
        try c.encode(id, forKey: .id)
        try c.encodeIfPresent(kind, forKey: .kind)
        try c.encodeIfPresent(space, forKey: .space)
        try c.encodeIfPresent(headline, forKey: .headline)
        try c.encodeIfPresent(tone, forKey: .tone)
        try c.encodeIfPresent(checkedAt, forKey: .checkedAt)
        try c.encode(releases, forKey: .releases)
        try c.encode(ratings, forKey: .ratings)
        try c.encode(reviews, forKey: .reviews)
        try c.encode(metrics, forKey: .metrics)
        try c.encode(links, forKey: .links)
    }

    var pending: [ProductRelease] { releases.filter { $0.status == "pending" || $0.status == "rejected" } }
    var live: [ProductRelease] { releases.filter { $0.status == "live" } }
}

// MARK: - Ledger (money for one source)

struct LedgerDay: Codable, Equatable {
    var date: String
    var amount: Double
    var net: Double?
    var count: Int?
    var product: String?
    var category: String?
    var source: String?
    var space: String?

    enum CodingKeys: String, CodingKey { case date, amount, net, count, product, category, source, space, company }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        date = try c.decode(String.self, forKey: .date)
        amount = c.lenientDouble(.amount) ?? 0
        net = c.lenientDouble(.net)
        count = c.lenientInt(.count)
        product = c.lenientString(.product)
        category = c.lenientString(.category)
        source = c.lenientString(.source)
        space = Insights.nonEmpty(c.lenientString(.space)) ?? Insights.nonEmpty(c.lenientString(.company))
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(date, forKey: .date)
        try c.encode(amount, forKey: .amount)
        try c.encodeIfPresent(net, forKey: .net)
        try c.encodeIfPresent(count, forKey: .count)
        try c.encodeIfPresent(product, forKey: .product)
        try c.encodeIfPresent(category, forKey: .category)
        try c.encodeIfPresent(source, forKey: .source)
        try c.encodeIfPresent(space, forKey: .space)
    }
}

struct LedgerEntry: Codable, Equatable {
    var id: String?
    var at: String
    var amount: Double
    var net: Double?
    var label: String?
    var status: String?
    var product: String?
    var category: String?
    var source: String?
    var space: String?

    enum CodingKeys: String, CodingKey { case id, at, amount, net, label, status, product, category, source, space, company }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = c.lenientString(.id)
        at = try c.decode(String.self, forKey: .at)
        amount = c.lenientDouble(.amount) ?? 0
        net = c.lenientDouble(.net)
        label = c.lenientString(.label)
        status = c.lenientString(.status)
        product = c.lenientString(.product)
        category = c.lenientString(.category)
        source = c.lenientString(.source)
        space = Insights.nonEmpty(c.lenientString(.space)) ?? Insights.nonEmpty(c.lenientString(.company))
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encodeIfPresent(id, forKey: .id)
        try c.encode(at, forKey: .at)
        try c.encode(amount, forKey: .amount)
        try c.encodeIfPresent(net, forKey: .net)
        try c.encodeIfPresent(label, forKey: .label)
        try c.encodeIfPresent(status, forKey: .status)
        try c.encodeIfPresent(product, forKey: .product)
        try c.encodeIfPresent(category, forKey: .category)
        try c.encodeIfPresent(source, forKey: .source)
        try c.encodeIfPresent(space, forKey: .space)
    }
}

struct LedgerBlock: Codable, Equatable {
    var type: String = "ledger"
    var id: String
    var title: String?
    var currency: String
    var space: String?
    var source: String?
    var connected: Bool
    var note: String?
    var asOf: String?
    var timezone: String?
    var days: [LedgerDay]
    var entries: [LedgerEntry]
    var metrics: [MetricTile]

    enum CodingKeys: String, CodingKey {
        case type, id, title, currency, space, company, source, connected, note, timezone, days, entries, metrics
        case asOf = "as_of"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = c.lenientString(.title)
        currency = (Insights.nonEmpty(c.lenientString(.currency)) ?? "USD").uppercased()
        space = Insights.nonEmpty(c.lenientString(.space)) ?? Insights.nonEmpty(c.lenientString(.company))
        source = c.lenientString(.source)
        connected = c.lenientBool(.connected) ?? true
        note = c.lenientString(.note)
        asOf = c.lenientString(.asOf)
        timezone = c.lenientString(.timezone)
        days = c.lossy(LedgerDay.self, .days)
        entries = c.lossy(LedgerEntry.self, .entries)
        metrics = c.lossy(MetricTile.self, .metrics)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(type, forKey: .type)
        try c.encode(id, forKey: .id)
        try c.encodeIfPresent(title, forKey: .title)
        try c.encode(currency, forKey: .currency)
        try c.encodeIfPresent(space, forKey: .space)
        try c.encodeIfPresent(source, forKey: .source)
        try c.encode(connected, forKey: .connected)
        try c.encodeIfPresent(note, forKey: .note)
        try c.encodeIfPresent(asOf, forKey: .asOf)
        try c.encodeIfPresent(timezone, forKey: .timezone)
        try c.encode(days, forKey: .days)
        try c.encode(entries, forKey: .entries)
        try c.encode(metrics, forKey: .metrics)
    }
}

// MARK: - Tasks (a checklist the app can tick and add to)

/// One to-do. A tick posts `task.set_done`; the feed that owns the list writes it back to its source.
struct TaskItem: Codable, Equatable, Identifiable {
    var id: String
    var title: String
    /// The list or area it belongs to, shown as a chip.
    var group: String?
    var done: Bool
    var doneAt: String?
    var due: String?
    var dueLabel: String?
    var status: String?
    var note: String?
    var tags: [String]
    var canToggle: Bool
    var pending: Bool

    enum CodingKeys: String, CodingKey {
        case id, title, group, done, due, status, note, tags, pending
        case doneAt = "done_at"
        case dueLabel = "due_label"
        case canToggle = "can_toggle"
    }

    init(id: String, title: String, group: String? = nil, done: Bool = false, doneAt: String? = nil, due: String? = nil,
         dueLabel: String? = nil, status: String? = nil, note: String? = nil, tags: [String] = [], canToggle: Bool = true,
         pending: Bool = false) {
        self.id = id
        self.title = title
        self.group = group
        self.done = done
        self.doneAt = doneAt
        self.due = due
        self.dueLabel = dueLabel
        self.status = status
        self.note = note
        self.tags = tags
        self.canToggle = canToggle
        self.pending = pending
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let id = c.lenientString(.id), !id.isEmpty else {
            throw DecodingError.keyNotFound(CodingKeys.id, .init(codingPath: c.codingPath, debugDescription: "task id"))
        }
        self.id = id
        title = c.lenientString(.title) ?? ""
        group = Insights.nonEmpty(c.lenientString(.group))
        done = c.lenientBool(.done) ?? false
        doneAt = c.lenientString(.doneAt)
        due = c.lenientString(.due)
        dueLabel = c.lenientString(.dueLabel)
        status = c.lenientString(.status)
        note = c.lenientString(.note)
        tags = c.lossy(String.self, .tags)
        canToggle = c.lenientBool(.canToggle) ?? true
        pending = c.lenientBool(.pending) ?? false
    }
}

struct RemindOption: Codable, Equatable, Identifiable {
    var days: [Int]
    var label: String

    var id: String { days.map(String.init).joined(separator: ",") }

    init(days: [Int], label: String) {
        self.days = days
        self.label = label
    }

    /// Used when a block's `add` has no `remind_options`.
    static let defaults: [RemindOption] = [
        RemindOption(days: [0], label: "Morning it's due"),
        RemindOption(days: [1, 0], label: "Day before + morning of"),
        RemindOption(days: [3, 1, 0], label: "3 days, 1 day, morning of"),
        RemindOption(days: [7, 3, 1, 0], label: "Week out, then 3, 1, 0"),
    ]
}

/// A `{id, label}` choice in the Add form.
struct TaskChoice: Codable, Equatable, Identifiable {
    var id: String
    var label: String

    enum CodingKeys: String, CodingKey { case id, label }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let id = Insights.nonEmpty(c.lenientString(.id)) else {
            throw DecodingError.keyNotFound(CodingKeys.id, .init(codingPath: c.codingPath, debugDescription: "choice id"))
        }
        self.id = id
        label = Insights.nonEmpty(c.lenientString(.label)) ?? id
    }
}

/// The Add form on a tasks block. Its presence turns the form on.
struct TasksAdd: Codable, Equatable {
    var label: String?
    var remindOptions: [RemindOption]
    /// The group new items land in, and the groups to pick from.
    var group: String?
    var groups: [TaskChoice]

    enum CodingKeys: String, CodingKey {
        case label, group, groups
        case remindOptions = "remind_options"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        label = c.lenientString(.label)
        remindOptions = c.lossy(RemindOption.self, .remindOptions).filter { !$0.days.isEmpty }
        group = Insights.nonEmpty(c.lenientString(.group))
        groups = c.lossy(TaskChoice.self, .groups)
    }

    var reminderChoices: [RemindOption] { remindOptions.isEmpty ? RemindOption.defaults : remindOptions }
}

struct TasksBlock: Codable, Equatable {
    var type: String = "tasks"
    var id: String
    var title: String?
    var subtitle: String?
    var collapsed: Bool
    var limit: Int
    var add: TasksAdd?
    var emptyText: String?
    var items: [TaskItem]

    enum CodingKeys: String, CodingKey {
        case type, id, title, subtitle, collapsed, limit, add, items
        case emptyText = "empty_text"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = c.lenientString(.title)
        subtitle = c.lenientString(.subtitle)
        collapsed = c.lenientBool(.collapsed) ?? false
        limit = max(0, c.lenientInt(.limit) ?? 0)
        add = try? c.decodeIfPresent(TasksAdd.self, forKey: .add)
        emptyText = c.lenientString(.emptyText)
        items = c.lossy(TaskItem.self, .items)
    }
}

// MARK: - Lenient decoding

/// Skips one malformed row instead of dropping the whole block.
private struct LossyElement<T: Decodable>: Decodable {
    let value: T?

    init(from decoder: Decoder) throws {
        value = try? T(from: decoder)
    }
}

extension KeyedDecodingContainer {
    /// A string, or a number written as one. Nil when absent, null, or another type.
    func lenientString(_ key: Key) -> String? {
        if let text = try? decodeIfPresent(String.self, forKey: key) { return text }
        if let number = try? decodeIfPresent(Int.self, forKey: key) { return String(number) }
        if let number = try? decodeIfPresent(Double.self, forKey: key) { return String(number) }
        return nil
    }

    func lenientBool(_ key: Key) -> Bool? {
        if let flag = try? decodeIfPresent(Bool.self, forKey: key) { return flag }
        if let number = try? decodeIfPresent(Int.self, forKey: key) { return number != 0 }
        if let text = try? decodeIfPresent(String.self, forKey: key) {
            switch text.lowercased() {
            case "true", "yes", "1": return true
            case "false", "no", "0": return false
            default: return nil
            }
        }
        return nil
    }

    func lenientInt(_ key: Key) -> Int? {
        if let number = try? decodeIfPresent(Int.self, forKey: key) { return number }
        if let number = try? decodeIfPresent(Double.self, forKey: key), number.isFinite { return Int(number) }
        if let text = try? decodeIfPresent(String.self, forKey: key) { return Int(text) }
        return nil
    }

    func lenientDouble(_ key: Key) -> Double? {
        if let number = try? decodeIfPresent(Double.self, forKey: key), number.isFinite { return number }
        if let text = try? decodeIfPresent(String.self, forKey: key) {
            return Double(text.replacingOccurrences(of: ",", with: ""))
        }
        return nil
    }

    /// Every element that decodes. An absent or non-array value is an empty list.
    func lossy<T: Decodable>(_ type: T.Type, _ key: Key) -> [T] {
        guard let rows = try? decodeIfPresent([LossyElement<T>].self, forKey: key) else { return [] }
        return rows.compactMap(\.value)
    }
}

/// Encodes an arbitrary JSON value. Used to round-trip unknown blocks.
struct AnyCodable: Codable {
    let value: Any

    init(_ value: Any) { self.value = value }

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if c.decodeNil() {
            value = NSNull()
        } else if let b = try? c.decode(Bool.self) {
            value = b
        } else if let i = try? c.decode(Int.self) {
            value = i
        } else if let d = try? c.decode(Double.self) {
            value = d
        } else if let s = try? c.decode(String.self) {
            value = s
        } else if let a = try? c.decode([AnyCodable].self) {
            value = a.map(\.value)
        } else if let o = try? c.decode([String: AnyCodable].self) {
            value = o.mapValues(\.value)
        } else {
            throw DecodingError.dataCorruptedError(in: c, debugDescription: "Unsupported JSON")
        }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch value {
        case is NSNull:
            try c.encodeNil()
        case let n as NSNumber where CFGetTypeID(n) != CFBooleanGetTypeID():
            // JSONSerialization hands back NSNumber, which also matches Bool. Keep numbers numbers.
            if CFNumberIsFloatType(n) {
                try c.encode(n.doubleValue)
            } else {
                try c.encode(n.int64Value)
            }
        case let b as Bool:
            try c.encode(b)
        case let i as Int:
            try c.encode(i)
        case let d as Double:
            try c.encode(d)
        case let s as String:
            try c.encode(s)
        case let a as [Any]:
            try c.encode(a.map(AnyCodable.init))
        case let o as [String: Any]:
            try c.encode(o.mapValues(AnyCodable.init))
        default:
            try c.encodeNil()
        }
    }

    /// A table cell or any scalar as display text.
    static func text(_ value: Any) -> String {
        switch value {
        case is NSNull: return ""
        case let b as Bool: return b ? "Yes" : "No"
        case let i as Int: return i.formatted()
        case let d as Double:
            return d.rounded() == d && abs(d) < 1e15 ? Int(d).formatted() : d.formatted(.number.precision(.fractionLength(0 ... 2)))
        case let s as String: return s
        default: return String(describing: value)
        }
    }
}

private func jsonObject(from decoder: Decoder) throws -> Any {
    let obj = try AnyCodable(from: decoder).value
    return obj
}
