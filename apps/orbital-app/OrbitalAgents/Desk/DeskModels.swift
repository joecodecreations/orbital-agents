import Foundation

// Shapes for the Agent desk (`/v1/desk`) and the Agent Satellite Tracker (`/v1/tracker`).

struct DeskMessage: Identifiable, Equatable, Decodable {
    enum Role: String, Decodable { case user, assistant, event }

    let id: String
    var role: Role
    var text: String
    var at: String?
    var source: String?
    var clientId: String?
    var kind: String?
    var speak: String?
    var taskId: String?
    var trackerId: String?
    var model: String?
    var latencyMs: Int?
    var did: [String]?
    var error: String?
    /// Local only: a user message shown before the server echoes it.
    var pending = false

    enum CodingKeys: String, CodingKey {
        case id, role, text, at, source, kind, speak, model, did, error
        case clientId = "client_id"
        case taskId = "task_id"
        case trackerId = "tracker_id"
        case latencyMs = "latency_ms"
    }

    init(id: String, role: Role, text: String, at: String? = nil, source: String? = nil, clientId: String? = nil, pending: Bool = false) {
        self.id = id
        self.role = role
        self.text = text
        self.at = at
        self.source = source
        self.clientId = clientId
        self.pending = pending
    }

    var date: Date? { DeskTime.parse(at) }
}

struct DeskTask: Identifiable, Equatable, Decodable {
    let id: String
    var title: String
    var project: String?
    var projectLabel: String?
    var status: String
    var effort: String?
    var model: String?
    var trackerId: String?
    var createdAt: String?
    var startedAt: String?
    var finishedAt: String?
    var lastNote: String?
    var summary: String?
    var spoken: String?
    var error: String?
    var runs: Int?
    var brief: String?
    var pendingFollowups: Int?

    enum CodingKeys: String, CodingKey {
        case id, title, project, status, effort, model, summary, spoken, error, runs, brief
        case trackerId = "tracker_id"
        case projectLabel = "project_name"
        case createdAt = "created_at"
        case startedAt = "started_at"
        case finishedAt = "finished_at"
        case lastNote = "last_note"
        case pendingFollowups = "pending_followups"
    }

    var isActive: Bool { status == "running" || status == "queued" }

    var projectName: String {
        if let projectLabel, !projectLabel.isEmpty { return projectLabel }
        guard let project, project != "." else { return "Any folder" }
        return project.split(separator: "/").last.map(String.init) ?? project
    }
}

struct DeskModelInfo: Equatable, Decodable {
    var provider: String
    var model: String
    var fast: Bool
    /// "api" (a paid key on the hub) or "subscription".
    var billing: String?
    var note: String?
    /// The hub says a backup is answering because the configured model can't.
    var backup: Bool?
    /// False when the hub has no model key for the desk.
    var configured: Bool?
    /// The agent the desk starts for real work (`desk.worker_agent`).
    var workerAgent: String?

    enum CodingKeys: String, CodingKey {
        case provider, model, fast, billing, note, backup, configured
        case workerAgent = "worker_agent"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        provider = c.lenientString(.provider) ?? "none"
        model = c.lenientString(.model) ?? provider
        fast = c.lenientBool(.fast) ?? true
        billing = c.lenientString(.billing)
        note = c.lenientString(.note)
        backup = c.lenientBool(.backup)
        configured = c.lenientBool(.configured)
        workerAgent = c.lenientString(.workerAgent)
    }

    var isBackup: Bool { backup ?? false }

    var badgeSymbol: String { isBackup ? "lifepreserver.fill" : (fast ? "bolt.fill" : "tortoise.fill") }

    var badgeText: String {
        if isBackup { return "Backup · \(model)" }
        return fast ? model : "\(model) · slower replies"
    }
}

/// You cut into a reply: which one (nil = whatever the desk is still writing) and what you heard.
struct DeskInterrupt: Equatable {
    var replyID: String?
    var heard: String
}

struct DeskSnapshot: Decodable {
    var messages: [DeskMessage]
    var tasks: [DeskTask]
    var model: DeskModelInfo?
    var trackerActive: Int?
    /// False when the hub has no desk model set up (no API key). Nil on a hub that does not say.
    var configured: Bool?
    /// What to do about it, in the hub's words.
    var setup: String?

    enum CodingKeys: String, CodingKey {
        case messages, tasks, model, configured, setup, reason
        case trackerActive = "tracker_active"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        messages = c.lossy(DeskMessage.self, .messages)
        tasks = c.lossy(DeskTask.self, .tasks)
        model = try? c.decodeIfPresent(DeskModelInfo.self, forKey: .model)
        trackerActive = c.lenientInt(.trackerActive)
        configured = c.lenientBool(.configured)
        setup = c.lenientString(.setup) ?? c.lenientString(.reason)
    }
}

/// `GET /v1/speech/status`: Fish voices run on the hub with its FISH_API_KEY.
struct SpeechStatus: Decodable, Equatable {
    struct Voice: Decodable, Equatable, Identifiable, Hashable {
        var id: String
        var name: String
        var language: String?

        enum CodingKeys: String, CodingKey { case id, name, language
            case referenceID = "reference_id"
        }

        init(id: String, name: String, language: String? = nil) {
            self.id = id
            self.name = name
            self.language = language
        }

        init(from decoder: Decoder) throws {
            let c = try decoder.container(keyedBy: CodingKeys.self)
            guard let id = Insights.nonEmpty(c.lenientString(.referenceID)) ?? Insights.nonEmpty(c.lenientString(.id)) else {
                throw DecodingError.keyNotFound(CodingKeys.id, .init(codingPath: c.codingPath, debugDescription: "voice id"))
            }
            self.id = id
            name = Insights.nonEmpty(c.lenientString(.name)) ?? id
            language = c.lenientString(.language)
        }
    }

    var tts: Bool = false
    var providerTTS: String = "none"
    var voices: [Voice] = []
    var defaultVoice: String?
    /// The hub's default voice id per language code ("en", "pt").
    var defaults: [String: String] = [:]

    enum CodingKeys: String, CodingKey {
        case tts, voices, defaults
        case providerTTS = "provider_tts"
        case defaultVoice = "default_voice"
    }

    /// The hub's voice for a language: its per-language default, else (English) the overall default.
    func defaultVoice(for language: SpeechLanguage) -> String? {
        let code = String(language.bcp47.prefix(2)).lowercased()
        if let id = Insights.nonEmpty(defaults[code]) { return id }
        return language == .english ? Insights.nonEmpty(defaultVoice) : nil
    }

    init() {}

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        tts = c.lenientBool(.tts) ?? false
        providerTTS = c.lenientString(.providerTTS) ?? (tts ? "fish" : "none")
        voices = c.lossy(Voice.self, .voices)
        defaultVoice = c.lenientString(.defaultVoice)
        defaults = (try? c.decodeIfPresent([String: String].self, forKey: .defaults)) ?? [:]
    }
}

// MARK: Tracker

struct TrackerMedia: Identifiable, Equatable, Decodable {
    let id: String
    var kind: String
    var caption: String?
    var fileName: String?
    var addedAt: String?
    var bytes: Int?
    var duration: Double?
    var archivedOnly: Bool?
    var thumbURL: String?
    var fullURL: String?
    var streamURL: String?

    enum CodingKeys: String, CodingKey {
        case id, kind, caption, bytes, duration
        case fileName = "file_name"
        case addedAt = "added_at"
        case archivedOnly = "archived_only"
        case thumbURL = "thumb_url"
        case fullURL = "full_url"
        case streamURL = "stream_url"
    }

    var isVideo: Bool { kind == "video" }
    /// Stable across URL re-signing, so the image cache keeps hitting.
    var cacheKey: String { "tracker:\(id):" }
}

struct TrackerNote: Equatable, Decodable, Hashable {
    var at: String?
    var text: String
}

struct TrackerCount: Equatable, Decodable {
    var total: Int
    var active: Int
}

struct TrackerSummary: Identifiable, Equatable, Decodable {
    let id: String
    var title: String
    var status: String
    var source: String
    var agent: String?
    var agentName: String
    var agentLabel: String?
    /// The machine the work runs on (missing on an older hub).
    var machine: String?
    var project: String?
    var createdAt: String?
    var updatedAt: String?
    var completedAt: String?
    /// Who closed it: the agent, or "user" when it was closed by hand on a Mac's tracker.
    var completedBy: String?
    var lastNote: String?
    var summary: String?
    var mediaCount: Int
    var videoCount: Int
    var cover: TrackerMedia?
    var subagents: TrackerCount?
    var processing: Bool?
    /// The Mac bubble color. Missing on an older payload, which falls back to the first swatch.
    var colorIndex: Int?
    /// Percent complete as the agent reports it, and what the work cost so far.
    var progress: Int?
    var usage: AIUsage?
    /// The hub's bar figure (`{percent, source}`): the agent's own %, or its estimate from the
    /// task list or subagents. Missing from older hubs.
    var shown: ShownFigure?

    struct ShownFigure: Codable, Hashable {
        var percent: Int
        var source: String
    }

    enum CodingKeys: String, CodingKey {
        case id, title, status, source, agent, machine, project, summary, cover, subagents, processing, progress, usage
        case shown = "shown_progress"
        case agentName = "agent_name"
        case agentLabel = "agent_label"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case completedAt = "completed_at"
        case completedBy = "completed_by"
        case lastNote = "last_note"
        case mediaCount = "media_count"
        case videoCount = "video_count"
        case colorIndex = "color_index"
    }

    var isActive: Bool { status == "active" }
    var isAI: Bool { source == "agent" }
}

struct TrackerSub: Identifiable, Equatable, Decodable {
    let id: String
    var title: String
    var agentName: String
    var status: String
    var details: String?
    var summary: String?
    var createdAt: String?
    var completedAt: String?
    var notes: [TrackerNote]
    var media: [TrackerMedia]
    var progress: Int?
    var usage: AIUsage?

    enum CodingKeys: String, CodingKey {
        case id, title, status, details, summary, notes, media, progress, usage
        case agentName = "agent_name"
        case createdAt = "created_at"
        case completedAt = "completed_at"
    }
}

struct TrackerDetail: Identifiable, Equatable, Decodable {
    let id: String
    var title: String
    var status: String
    var source: String
    var agent: String?
    var agentName: String
    var agentLabel: String?
    var machine: String?
    var project: String?
    var createdAt: String?
    var updatedAt: String?
    var completedAt: String?
    var completedBy: String?
    var details: String
    var transcript: String?
    var summary: String?
    var notes: [TrackerNote]
    var media: [TrackerMedia]
    var subagents: [TrackerSub]
    var deskTask: DeskTask?
    var progress: Int?
    var usage: AIUsage?
    /// The Mac bubble color, so the progress bar matches the item's card.
    var colorIndex: Int?

    enum CodingKeys: String, CodingKey {
        case id, title, status, source, agent, machine, project, details, transcript, summary, notes, media, subagents, progress, usage
        case agentName = "agent_name"
        case agentLabel = "agent_label"
        case createdAt = "created_at"
        case updatedAt = "updated_at"
        case completedAt = "completed_at"
        case completedBy = "completed_by"
        case deskTask = "desk_task"
        case colorIndex = "color_index"
    }

    var isActive: Bool { status == "active" }
    var shownProgress: TrackerProgress? {
        TrackerProgress.of(status: status, progress: progress, details: details, subagents: subagents.map { ($0.status, $0.progress) })
    }
}

extension TrackerSummary {
    /// The hub's figure when it sends one; otherwise done or the agent's own %. An older hub sends
    /// no estimates, so those come from the item itself (`TrackerDetail.shownProgress`).
    var shownProgress: TrackerProgress? {
        if let shown, let source = TrackerProgress.Source(rawValue: shown.source) {
            return TrackerProgress(percent: min(100, max(0, shown.percent)), source: status == "done" ? .done : source)
        }
        return TrackerProgress.of(status: status, progress: progress, details: "")
    }

    /// The hub sent its own figure (or nothing to estimate), so no detail read is needed.
    var hubSentProgress: Bool { shown != nil }
}

extension TrackerSub {
    var shownProgress: TrackerProgress? { TrackerProgress.of(status: status, progress: progress, details: details ?? "") }
}

/// What a tracker progress bar shows and where the number came from. The Mac tracker
/// (`ShownProgress` in apps/tracker) uses the same rule, so both read the same.
struct TrackerProgress: Equatable {
    enum Source: String { case done, reported, checklist, subagents }
    var percent: Int
    var source: Source

    /// Checklist and subagent figures are estimates and show as "~40%".
    var isEstimate: Bool { source == .checklist || source == .subagents }
    var label: String { (isEstimate ? "~" : "") + "\(percent)%" }

    var caption: String {
        switch source {
        case .done: return "done"
        case .reported: return "reported by the agent"
        case .checklist: return "estimated from the task list"
        case .subagents: return "estimated from subagents"
        }
    }

    /// Read out as "Progress, 40 percent" or "about 40 percent, estimated from the task list".
    var spoken: String { isEstimate ? "about \(percent) percent, \(caption)" : "\(percent) percent" }

    static let waitingCaption = "waiting for the agent's first %"

    /// Done → 100; the agent's own figure; else checked boxes in the markdown; else the subagents'
    /// average. Estimates stop at 99 until the work is marked done.
    static func of(status: String, progress: Int?, details: String, subagents: [(status: String, progress: Int?)] = []) -> TrackerProgress? {
        if status == "done" { return TrackerProgress(percent: 100, source: .done) }
        if let progress { return TrackerProgress(percent: min(100, max(0, progress)), source: .reported) }
        if let (checked, total) = checklist(details), total > 0 {
            return TrackerProgress(percent: min(99, Int((Double(checked) / Double(total) * 100).rounded())), source: .checklist)
        }
        if !subagents.isEmpty {
            let sum = subagents.reduce(0) { $0 + ($1.status == "done" ? 100 : min(100, max(0, $1.progress ?? 0))) }
            return TrackerProgress(percent: min(99, Int((Double(sum) / Double(subagents.count)).rounded())), source: .subagents)
        }
        return nil
    }

    /// Markdown task boxes: `- [ ]`, `* [x]`, `+ [X]`.
    static func checklist(_ markdown: String) -> (checked: Int, total: Int)? {
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

struct TrackerPage: Decodable {
    var items: [TrackerSummary]
    /// Items in this range and search, working and done.
    var total: Int
    /// Finished items in this range and search. Missing from servers older than the Tracker tab.
    var done: Int?
    /// Everything being worked on, any range.
    var active: Int
}

enum TrackerScope: String, CaseIterable, Identifiable {
    case active, today, week, month, all
    var id: String { rawValue }
    var title: String {
        switch self {
        case .active: return "Now"
        case .today: return "Today"
        case .week: return "Week"
        case .month: return "Month"
        case .all: return "All"
        }
    }
}

/// The Tracker tab's two panes, matching the Mac panel: flashing work, then finished history.
enum TrackerPane: String, CaseIterable, Identifiable {
    case live, history
    var id: String { rawValue }
    var title: String {
        switch self {
        case .live: return "Live"
        case .history: return "History"
        }
    }

    static let historyScopes: [TrackerScope] = [.today, .week, .month, .all]
}

enum TrackerWho: String, CaseIterable, Identifiable {
    case all, ai, mine
    var id: String { rawValue }
    var title: String {
        switch self {
        case .all: return "All"
        case .ai: return "AI"
        case .mine: return "Mine"
        }
    }
}

/// How much screenshot / video proof AI agents attach for UI, website and game work. One level for
/// every agent, shared with every Mac's tracker (its camera button) through the hub.
enum TrackerEvidence: String, CaseIterable, Identifiable, Decodable {
    case off, min1, all
    var id: String { rawValue }

    var title: String {
        switch self {
        case .off: return "Off"
        case .min1: return "Min 1"
        case .all: return "All"
        }
    }

    var blurb: String {
        switch self {
        case .off: return "Agents attach screenshots when they normally would."
        case .min1: return "At least 1 screenshot for every UI, website or game change, plus a video for game features in motion."
        case .all: return "Every feature screenshotted and every game feature filmed, so you can review it all from here."
        }
    }

    var symbol: String {
        switch self {
        case .off: return "camera"
        case .min1: return "camera.fill"
        case .all: return "photo.stack.fill"
        }
    }
}

/// `GET /v1/tracker/settings` and the `tracker_settings` live event.
struct TrackerSettings: Decodable, Equatable {
    var evidence: TrackerEvidence
    /// "tracker" (a Mac's Agent Satellite Tracker), "app" (this app), or "cli" (an agent's orbital-track).
    var updatedBy: String?

    enum CodingKeys: String, CodingKey {
        case evidence
        case updatedBy = "updated_by"
    }

    init(evidence: TrackerEvidence, updatedBy: String? = nil) {
        self.evidence = evidence
        self.updatedBy = updatedBy
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // A level this build doesn't know reads as off instead of failing the whole payload.
        evidence = (try? c.decode(TrackerEvidence.self, forKey: .evidence)) ?? .off
        updatedBy = try c.decodeIfPresent(String.self, forKey: .updatedBy)
    }
}

/// What the Tracker tab's "say when it finishes" reads out.
enum TrackerSpeech {
    /// "Claude finished Nightly backup check. All three disks passed."
    static func finished(_ item: TrackerSummary) -> String {
        let title = plain(item.title).trimmingCharacters(in: CharacterSet(charactersIn: ".!? "))
        var line = "\(item.agentName) finished \(title.isEmpty ? "a task" : title)."
        if let result = firstSentence(plain(item.summary ?? "")), result.lowercased() != title.lowercased() {
            line += " " + result
        }
        return line
    }

    /// Markdown as it should sound: no stars, backticks, headings, or link targets.
    static func plain(_ text: String) -> String {
        var out = text.replacingOccurrences(of: #"\[([^\]]*)\]\([^)]*\)"#, with: "$1", options: .regularExpression)
        out = out.replacingOccurrences(of: #"[*`#>]"#, with: "", options: .regularExpression)
        out = out.replacingOccurrences(of: #"\s+"#, with: " ", options: .regularExpression)
        return out.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// The first sentence, at most about 200 characters, or nil.
    static func firstSentence(_ text: String) -> String? {
        guard !text.isEmpty else { return nil }
        var sentence = text
        if let end = text.range(of: #"[.!?](\s|$)"#, options: .regularExpression) {
            sentence = String(text[..<end.upperBound])
        }
        sentence = sentence.trimmingCharacters(in: .whitespaces)
        if sentence.count > 200 {
            let cut = sentence.prefix(200)
            sentence = (cut.lastIndex(of: " ").map { String(cut[..<$0]) } ?? String(cut)) + "."
        }
        return sentence.isEmpty ? nil : sentence
    }
}

enum DeskTime {
    static func parse(_ raw: String?) -> Date? {
        OrbitalDates.parse(raw)
    }

    /// "now", "4m", "2h", "Tue", "Oct 3".
    static func short(_ raw: String?, now: Date = Date()) -> String {
        guard let date = parse(raw) else { return "" }
        let seconds = now.timeIntervalSince(date)
        if seconds < 60 { return "now" }
        if seconds < 3600 { return "\(Int(seconds / 60))m" }
        if seconds < 86400 { return "\(Int(seconds / 3600))h" }
        let formatter = DateFormatter()
        formatter.dateFormat = seconds < 7 * 86400 ? "EEE" : "MMM d"
        return formatter.string(from: date)
    }

    /// "3:42 PM" today, "Tue 3:42 PM" this week, else "Oct 3, 3:42 PM".
    static func clock(_ raw: String?) -> String {
        guard let date = parse(raw) else { return "" }
        let formatter = DateFormatter()
        if Calendar.current.isDateInToday(date) {
            formatter.dateFormat = "h:mm a"
        } else if Date().timeIntervalSince(date) < 6 * 86400 {
            formatter.dateFormat = "EEE h:mm a"
        } else {
            formatter.dateFormat = "MMM d, h:mm a"
        }
        return formatter.string(from: date)
    }

    static func elapsed(since raw: String?, now: Date = Date()) -> String {
        guard let date = parse(raw) else { return "" }
        let seconds = Int(max(0, now.timeIntervalSince(date)))
        if seconds < 60 { return "\(seconds)s" }
        if seconds < 3600 { return "\(seconds / 60)m" }
        return "\(seconds / 3600)h \((seconds % 3600) / 60)m"
    }
}
