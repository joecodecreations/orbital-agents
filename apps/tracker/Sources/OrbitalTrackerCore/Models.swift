import Foundation

public enum ItemSource: String, Codable, Sendable { case voice, manual, agent }
public enum ItemStatus: String, Codable, Sendable { case active, done }
public enum MediaKind: String, Codable, Sendable { case image, video, file }

/// Where a tracker item was started. Only set when something real recorded it; older items
/// leave this nil rather than guessing.
/// - ios: the user through the Orbital Agents iPhone app (Desk)
/// - mac: the user through the Mac/OS Orbital Agents app (Catalyst or desktop) or the Mac tracker panel
/// - direct: the user typing in a proprietary AI CLI (Claude, Codex, Grok, Gemini, …)
/// - agent: an agent or automation started the item (satellite job, subagent, API)
public enum ItemOrigin: String, Codable, Sendable {
    case ios, mac, direct, agent

    public static let known = ["ios", "mac", "direct", "agent"]

    public static func parse(_ raw: String?) -> ItemOrigin? {
        guard let raw else { return nil }
        let s = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        switch s {
        case "ios", "iphone", "ipad": return .ios
        case "mac", "macos", "os", "catalyst", "desktop", "app": return .mac
        case "direct", "chat", "cli", "ai": return .direct
        case "agent", "satellite", "automation", "api", "sub": return .agent
        default: return nil
        }
    }

    public var label: String {
        switch self {
        case .ios: return "iOS app"
        case .mac: return "Mac app"
        case .direct: return "Direct AI chat"
        case .agent: return "Agent"
        }
    }
}

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

/// What a piece of AI work cost: tokens, and how much of the subscription it used.
/// Measured by the satellite that ran the agent (Grok's log by process, Claude's session transcript),
/// or reported by the agent. Each report replaces the last: it is a running total, not a delta.
public struct TokenUsage: Codable, Hashable, Sendable {
    public var engine: String?
    public var model: String?
    public var inputTokens: Int?
    public var cachedTokens: Int?
    public var outputTokens: Int?
    public var reasoningTokens: Int?
    public var costUSD: Double?
    /// Share of the subscription period this work used, in percent (Grok: change in the plan's usage).
    public var planPercent: Double?
    /// The whole plan's usage right now, in percent, and its period.
    public var planUsedPercent: Double?
    public var planPeriod: String?
    public var planResetsAt: Date?
    public var measuredBy: String?
    public var updatedAt: Date?

    public init(engine: String? = nil, inputTokens: Int? = nil, outputTokens: Int? = nil) {
        self.engine = engine
        self.inputTokens = inputTokens
        self.outputTokens = outputTokens
    }

    public var totalTokens: Int { (inputTokens ?? 0) + (outputTokens ?? 0) + (reasoningTokens ?? 0) }
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
    /// Percent complete as the agent reports it (0–100).
    public var progress: Int?
    public var usage: TokenUsage?
    /// Model this subagent used, when known.
    public var model: String?

    public init(id: String, agent: String, agentLabel: String? = nil, machine: String? = nil, title: String,
                details: String = "", createdAt: Date = Date(), model: String? = nil) {
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
        self.model = model
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
        progress = try? c.decodeIfPresent(Int.self, forKey: .progress)
        usage = try? c.decodeIfPresent(TokenUsage.self, forKey: .usage)
        model = try c.decodeIfPresent(String.self, forKey: .model)
    }

    /// Prefer the stored model, else the one measured in usage.
    public var shownModel: String? {
        if let model, !model.isEmpty { return model }
        if let m = usage?.model, !m.isEmpty { return m }
        return nil
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
    /// Where the item was started (ios / mac / direct / agent). Nil on older items.
    public var origin: ItemOrigin?
    /// Model name when known at start or reported later (also see usage.model).
    public var model: String?
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
    /// Percent complete as the agent reports it (0–100); 100 when done.
    public var progress: Int?
    /// Tokens and subscription share, for this item and its subagents together.
    public var usage: TokenUsage?

    public init(id: String = IDGen.make(), title: String, details: String = "",
                transcript: String? = nil, source: ItemSource, agent: String? = nil,
                agentLabel: String? = nil, origin: ItemOrigin? = nil, model: String? = nil,
                machine: String? = nil, workspace: String? = nil, colorIndex: Int,
                createdAt: Date = Date()) {
        self.id = id
        self.title = title
        self.details = details
        self.transcript = transcript
        self.source = source
        self.agent = agent
        self.agentLabel = agentLabel
        self.origin = origin
        self.model = model
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

    /// Prefer the stored model, else the one measured in usage.
    public var shownModel: String? {
        if let model, !model.isEmpty { return model }
        if let m = usage?.model, !m.isEmpty { return m }
        return nil
    }

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
        origin = try c.decodeIfPresent(ItemOrigin.self, forKey: .origin)
        model = try c.decodeIfPresent(String.self, forKey: .model)
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
        progress = try? c.decodeIfPresent(Int.self, forKey: .progress)
        usage = try? c.decodeIfPresent(TokenUsage.self, forKey: .usage)
    }
}

/// What the progress bar shows and where the number came from. The iPhone app and the PC
/// tracker use the same rule.
public struct ShownProgress: Hashable, Sendable {
    public enum Source: String, Sendable { case done, reported, checklist, subagents }
    public var percent: Int
    public var source: Source

    public init(percent: Int, source: Source) {
        self.percent = percent
        self.source = source
    }

    /// Checklist and subagent figures are estimates and show as "~40%".
    public var isEstimate: Bool { source == .checklist || source == .subagents }
    public var label: String { (isEstimate ? "~" : "") + "\(percent)%" }

    public var caption: String {
        switch source {
        case .done: return "done"
        case .reported: return "reported by the agent"
        case .checklist: return "estimated from the task list"
        case .subagents: return "estimated from subagents"
        }
    }

    /// Done → 100; the agent's own figure; else checked boxes in the markdown; else the subagents'
    /// average. Estimates stop at 99 until the work is marked done.
    public static func of(status: ItemStatus, progress: Int?, details: String, subagents: [SubTask] = []) -> ShownProgress? {
        if status == .done { return ShownProgress(percent: 100, source: .done) }
        if let progress { return ShownProgress(percent: min(100, max(0, progress)), source: .reported) }
        if let (checked, total) = checklist(details), total > 0 {
            return ShownProgress(percent: min(99, Int((Double(checked) / Double(total) * 100).rounded())), source: .checklist)
        }
        if !subagents.isEmpty {
            let sum = subagents.reduce(0) { $0 + ($1.status == .done ? 100 : min(100, max(0, $1.progress ?? 0))) }
            return ShownProgress(percent: min(99, Int((Double(sum) / Double(subagents.count)).rounded())), source: .subagents)
        }
        return nil
    }

    /// Markdown task boxes: `- [ ]`, `* [x]`, `+ [X]`.
    public static func checklist(_ markdown: String) -> (checked: Int, total: Int)? {
        var checked = 0, total = 0
        for line in markdown.split(separator: "\n", omittingEmptySubsequences: true) {
            let t = line.drop { $0 == " " || $0 == "\t" }
            guard let bullet = t.first, "-*+".contains(bullet) else { continue }
            let rest = t.dropFirst()
            guard rest.first == " " else { continue }
            let box = rest.drop { $0 == " " }.prefix(3)
            if box == "[ ]" { total += 1 } else if box == "[x]" || box == "[X]" { total += 1; checked += 1 }
        }
        return total > 0 ? (checked, total) : nil
    }
}

public extension TrackedItem {
    var shownProgress: ShownProgress? { ShownProgress.of(status: status, progress: progress, details: details, subagents: subagents) }
}

public extension SubTask {
    var shownProgress: ShownProgress? { ShownProgress.of(status: status, progress: progress, details: details) }
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
