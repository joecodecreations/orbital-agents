import Foundation

public enum ItemSource: String, Codable, Sendable { case voice, manual, agent }
public enum ItemStatus: String, Codable, Sendable { case active, done }
public enum MediaKind: String, Codable, Sendable { case image, video, file }

/// A screenshot, video or file attached to an item. `archivePath` is a small compressed copy
/// (relative to the tracker data folder), so the record survives when the original temp file
/// is deleted. Archives older than `tracker.media_retention_days` are removed.
public struct MediaRef: Codable, Identifiable, Hashable, Sendable {
    public var id: String
    public var kind: MediaKind
    public var originalPath: String
    public var archivePath: String?
    public var caption: String?
    public var addedAt: Date
    public var originalBytes: Int64?
    public var durationSeconds: Double?
    /// Machine whose disk holds `originalPath` (nil = this one). Other machines show the archive.
    public var machine: String?

    public init(id: String = IDGen.make(), kind: MediaKind, originalPath: String,
                archivePath: String? = nil, caption: String? = nil, addedAt: Date = Date(),
                originalBytes: Int64? = nil, durationSeconds: Double? = nil, machine: String? = nil) {
        self.id = id
        self.kind = kind
        self.originalPath = originalPath
        self.archivePath = archivePath
        self.caption = caption
        self.addedAt = addedAt
        self.originalBytes = originalBytes
        self.durationSeconds = durationSeconds
        self.machine = machine
    }

    public var fileName: String { (originalPath as NSString).lastPathComponent }
}

public struct ProgressNote: Codable, Hashable, Sendable {
    public var at: Date
    public var text: String

    public init(at: Date = Date(), text: String) {
        self.at = at
        self.text = text
    }
}

/// Work a main agent delegated to one of its subagents. Shown only inside the
/// parent's details, never as its own bubble.
public struct SubTask: Codable, Identifiable, Hashable, Sendable {
    public var id: String
    public var agent: String
    public var agentLabel: String?
    /// Machine the subagent ran on (config `machine_name`).
    public var machine: String?
    public var title: String
    public var details: String
    public var status: ItemStatus
    public var createdAt: Date
    public var updatedAt: Date
    public var completedAt: Date?
    public var summary: String?
    public var notes: [ProgressNote]
    public var media: [MediaRef]

    public init(id: String, agent: String, agentLabel: String? = nil, machine: String? = nil, title: String,
                details: String = "", createdAt: Date = Date()) {
        self.id = id
        self.agent = agent
        self.agentLabel = agentLabel
        self.machine = machine
        self.title = title
        self.details = details
        self.status = .active
        self.createdAt = createdAt
        self.updatedAt = createdAt
        self.notes = []
        self.media = []
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        agent = try c.decodeIfPresent(String.self, forKey: .agent) ?? "other"
        agentLabel = try c.decodeIfPresent(String.self, forKey: .agentLabel)
        machine = try c.decodeIfPresent(String.self, forKey: .machine)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? "Subtask"
        details = try c.decodeIfPresent(String.self, forKey: .details) ?? ""
        status = try c.decodeIfPresent(ItemStatus.self, forKey: .status) ?? .active
        createdAt = try c.decodeIfPresent(Date.self, forKey: .createdAt) ?? Date()
        updatedAt = try c.decodeIfPresent(Date.self, forKey: .updatedAt) ?? createdAt
        completedAt = try c.decodeIfPresent(Date.self, forKey: .completedAt)
        summary = try c.decodeIfPresent(String.self, forKey: .summary)
        notes = try c.decodeIfPresent([ProgressNote].self, forKey: .notes) ?? []
        media = try c.decodeIfPresent([MediaRef].self, forKey: .media) ?? []
    }
}

public struct TrackedItem: Codable, Identifiable, Hashable, Sendable {
    public var id: String
    public var title: String
    public var details: String
    public var transcript: String?
    public var source: ItemSource
    public var agent: String?
    public var agentLabel: String?
    /// Machine the work happens on (config `machine_name`). On the hub, items relayed from
    /// satellites carry the satellite's name.
    public var machine: String?
    public var workspace: String?
    public var colorIndex: Int
    public var status: ItemStatus
    public var createdAt: Date
    public var updatedAt: Date
    public var completedAt: Date?
    public var completedBy: String?
    public var summary: String?
    /// Gone from the live panel. Set when the item is marked done, or when the user clicks X.
    /// History still keeps the item.
    public var dismissed: Bool
    /// A title is still being worked out for a spoken phrase.
    public var processing: Bool
    public var notes: [ProgressNote]
    public var media: [MediaRef]
    public var subagents: [SubTask]

    public init(id: String = IDGen.make(), title: String, details: String = "",
                transcript: String? = nil, source: ItemSource, agent: String? = nil,
                agentLabel: String? = nil, machine: String? = nil, workspace: String? = nil, colorIndex: Int,
                createdAt: Date = Date()) {
        self.id = id
        self.title = title
        self.details = details
        self.transcript = transcript
        self.source = source
        self.agent = agent
        self.agentLabel = agentLabel
        self.machine = machine
        self.workspace = workspace
        self.colorIndex = colorIndex
        self.status = .active
        self.createdAt = createdAt
        self.updatedAt = createdAt
        self.dismissed = false
        self.processing = false
        self.notes = []
        self.media = []
        self.subagents = []
    }

    public var isAI: Bool { source == .agent }

    /// When the item counts as finished for history and retention.
    public var finishedAt: Date { completedAt ?? updatedAt }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? "Untitled"
        details = try c.decodeIfPresent(String.self, forKey: .details) ?? ""
        transcript = try c.decodeIfPresent(String.self, forKey: .transcript)
        source = try c.decodeIfPresent(ItemSource.self, forKey: .source) ?? .manual
        agent = try c.decodeIfPresent(String.self, forKey: .agent)
        agentLabel = try c.decodeIfPresent(String.self, forKey: .agentLabel)
        machine = try c.decodeIfPresent(String.self, forKey: .machine)
        workspace = try c.decodeIfPresent(String.self, forKey: .workspace)
        colorIndex = try c.decodeIfPresent(Int.self, forKey: .colorIndex) ?? 0
        status = try c.decodeIfPresent(ItemStatus.self, forKey: .status) ?? .active
        createdAt = try c.decodeIfPresent(Date.self, forKey: .createdAt) ?? Date()
        updatedAt = try c.decodeIfPresent(Date.self, forKey: .updatedAt) ?? createdAt
        completedAt = try c.decodeIfPresent(Date.self, forKey: .completedAt)
        completedBy = try c.decodeIfPresent(String.self, forKey: .completedBy)
        summary = try c.decodeIfPresent(String.self, forKey: .summary)
        dismissed = try c.decodeIfPresent(Bool.self, forKey: .dismissed) ?? false
        processing = try c.decodeIfPresent(Bool.self, forKey: .processing) ?? false
        notes = try c.decodeIfPresent([ProgressNote].self, forKey: .notes) ?? []
        media = try c.decodeIfPresent([MediaRef].self, forKey: .media) ?? []
        subagents = try c.decodeIfPresent([SubTask].self, forKey: .subagents) ?? []
    }
}

public enum IDGen {
    private static let alphabet = Array("abcdefghjkmnpqrstuvwxyz23456789")

    public static func make(_ length: Int = 8) -> String {
        String((0..<length).map { _ in alphabet.randomElement()! })
    }

    /// Ids from the CLI, the HTTP API and the hub relay: 2–64 of [A-Za-z0-9_-].
    public static func isValid(_ id: String) -> Bool {
        (2...64).contains(id.count) && id.allSatisfy { $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == "_") }
    }
}

public enum TrackerJSON {
    public static func encoder(pretty: Bool = false) -> JSONEncoder {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .iso8601
        e.outputFormatting = pretty
            ? [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
            : [.sortedKeys, .withoutEscapingSlashes]
        return e
    }

    /// Accepts ISO 8601 with or without fractional seconds, and Unix seconds or milliseconds.
    public static func decoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .custom { decoder in
            let c = try decoder.singleValueContainer()
            if let seconds = try? c.decode(Double.self) {
                return Date(timeIntervalSince1970: seconds > 1e12 ? seconds / 1000 : seconds)
            }
            let s = try c.decode(String.self)
            if let date = parseDate(s) { return date }
            throw DecodingError.dataCorruptedError(in: c, debugDescription: "Bad date: \(s)")
        }
        return d
    }

    public static func parseDate(_ s: String) -> Date? {
        plain.date(from: s) ?? fractional.date(from: s)
    }

    public static func string(_ date: Date) -> String { plain.string(from: date) }

    nonisolated(unsafe) private static let plain = ISO8601DateFormatter()
    nonisolated(unsafe) private static let fractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
}

/// Normalises whatever an agent calls itself onto the providers the UI draws icons for.
/// Other names (a custom agent, "echo") are kept as given.
public enum Agents {
    public static let known = ["claude", "chatgpt", "grok", "gemini"]

    public static func normalize(_ raw: String?) -> String {
        let s = (raw ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if s.isEmpty { return "other" }
        if s.contains("claude") || s.contains("anthropic") { return "claude" }
        if s.contains("chatgpt") || s.contains("openai") || s.contains("codex") || s.hasPrefix("gpt") { return "chatgpt" }
        if s.contains("grok") || s == "xai" || s.contains("x.ai") { return "grok" }
        if s.contains("gemini") || s.contains("google") || s.contains("antigravity") || s == "agy" || s.contains("bard") { return "gemini" }
        return s
    }

    public static func displayName(_ agent: String?) -> String {
        switch normalize(agent) {
        case "claude": return "Claude"
        case "chatgpt": return "ChatGPT"
        case "grok": return "Grok"
        case "gemini": return "Gemini"
        case "other": return "AI agent"
        default: return (agent ?? "AI").capitalized
        }
    }

    /// Best guess from the environment the CLI runs in; used only when --agent is missing.
    public static func detectFromEnvironment(_ env: [String: String] = ProcessInfo.processInfo.environment) -> String? {
        let keys = Set(env.keys)
        if keys.contains("CLAUDECODE") || keys.contains("CLAUDE_CODE_ENTRYPOINT") { return "claude" }
        if keys.contains(where: { $0.hasPrefix("CODEX_") }) { return "chatgpt" }
        if keys.contains(where: { $0.hasPrefix("ANTIGRAVITY") || $0.hasPrefix("GEMINI_CLI") }) { return "gemini" }
        if keys.contains(where: { $0.hasPrefix("GROK_") && !$0.contains("API_KEY") }) { return "grok" }
        return nil
    }
}
