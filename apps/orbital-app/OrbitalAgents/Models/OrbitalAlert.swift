import Foundation

/// One notification plus its inbox row.
struct OrbitalAlert: Codable, Identifiable, Equatable {
    var schema: String?
    var id: String
    var kind: String
    var sectionId: String?
    var nodeId: String?
    var tabId: String?
    var title: String
    var subtitle: String?
    var body: String
    var badge: Int?
    var sound: String?
    var threadId: String?
    var categoryId: String?
    var interruptionLevel: String?
    var relevanceScore: Double?
    var targetContentId: String?
    var summaryArgument: String?
    var summaryArgumentCount: Int?
    var userInfo: [String: AlertJSON]?
    var attachments: [AlertAttachment]?
    var actions: [AlertAction]?
    var launchURL: String?
    var deliverAt: String?
    var repeats: AlertRepeat?
    var createdAt: String?
    var acked: Bool?
    var responseText: String?

    enum CodingKeys: String, CodingKey {
        case schema, id, kind, title, subtitle, body, badge, sound, attachments, actions, repeats, acked
        case sectionId = "section_id"
        case nodeId = "node_id"
        case tabId = "tab_id"
        case threadId = "thread_id"
        case categoryId = "category_id"
        case interruptionLevel = "interruption_level"
        case relevanceScore = "relevance_score"
        case targetContentId = "target_content_id"
        case summaryArgument = "summary_argument"
        case summaryArgumentCount = "summary_argument_count"
        case userInfo = "user_info"
        case launchURL = "launch_url"
        case deliverAt = "deliver_at"
        case createdAt = "created_at"
        case responseText = "response_text"
    }

    var isAcked: Bool { acked == true }

    /// When a reminder with a future `deliver_at` fires. Nil for alerts that fire on arrival.
    var deliverDate: Date? { OrbitalDates.parse(deliverAt) }

    /// A reminder scheduled for later. It is not unread, not badged, and not in the Waiting list until it fires.
    func isScheduled(at now: Date = Date()) -> Bool {
        guard let date = deliverDate else { return false }
        return date > now
    }

    /// Unread and already due: what the badge and the Waiting list count.
    func isWaiting(at now: Date = Date()) -> Bool {
        !isAcked && !isScheduled(at: now)
    }

    var resolvedLaunchURL: URL? {
        if let launchURL, let url = URL(string: launchURL) { return url }
        guard let sectionId else { return nil }
        var components = URLComponents()
        components.scheme = OrbitalBrand.urlScheme
        components.host = "open"
        var items = [URLQueryItem(name: "section", value: sectionId)]
        if let nodeId { items.append(URLQueryItem(name: "node", value: nodeId)) }
        if let tabId { items.append(URLQueryItem(name: "tab", value: tabId)) }
        components.queryItems = items
        return components.url
    }
}

struct AlertAttachment: Codable, Equatable, Identifiable {
    var id: String?
    var url: String
    var typeHint: String?

    var stableId: String { id ?? url }

    enum CodingKeys: String, CodingKey {
        case id, url
        case typeHint = "type_hint"
    }
}

struct AlertTextInput: Codable, Equatable {
    var buttonTitle: String
    var placeholder: String?

    enum CodingKeys: String, CodingKey {
        case buttonTitle = "button_title"
        case placeholder
    }
}

struct AlertAction: Codable, Equatable, Identifiable {
    var id: String
    var title: String
    var style: String?
    var foreground: Bool?
    var textInput: AlertTextInput?

    enum CodingKeys: String, CodingKey {
        case id, title, style, foreground
        case textInput = "text_input"
    }
}

struct AlertRepeat: Codable, Equatable {
    var hour: Int
    var minute: Int
    var weekday: Int?
    var second: Int?
}

/// Small JSON values allowed in alert user_info.
enum AlertJSON: Codable, Equatable {
    case string(String)
    case number(Double)
    case bool(Bool)

    init(from decoder: Decoder) throws {
        let c = try decoder.singleValueContainer()
        if let b = try? c.decode(Bool.self) {
            self = .bool(b)
        } else if let n = try? c.decode(Double.self) {
            self = .number(n)
        } else {
            self = .string(try c.decode(String.self))
        }
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        switch self {
        case .string(let s): try c.encode(s)
        case .number(let n): try c.encode(n)
        case .bool(let b): try c.encode(b)
        }
    }
}
