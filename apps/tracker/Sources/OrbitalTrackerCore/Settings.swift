import Foundation

/// How much screenshot / video evidence agents must attach when their work changes something
/// you can see (an app UI, a website, a game, a render). Set from the tracker's camera button,
/// its … menu, or the Orbital Agents app; agents read it with `orbital-track evidence`.
public enum EvidenceLevel: String, Codable, CaseIterable, Sendable {
    /// Agents attach media when they'd make it anyway.
    case off
    /// At least one screenshot of every visual change (a video too for game features in motion).
    case min1
    /// Every visual feature imaged, so the whole change can be reviewed from the phone.
    case all

    /// Accepts the spellings people and agents type: "min 1", "Min1", "1", "every", "none".
    public init?(loose raw: String) {
        let s = raw.lowercased().filter { $0.isLetter || $0.isNumber }
        switch s {
        case "off", "none", "0", "no", "false", "normal": self = .off
        case "min1", "min", "1", "one", "minimum", "atleast1", "atleastone": self = .min1
        case "all", "every", "everything", "full", "max": self = .all
        default: return nil
        }
    }

    public var label: String {
        switch self {
        case .off: return "Off"
        case .min1: return "Min 1"
        case .all: return "All"
        }
    }

    /// One line for menus and the phone.
    public var blurb: String {
        switch self {
        case .off: return "Agents attach screenshots when they normally would"
        case .min1: return "At least 1 screenshot for every UI, website or game change"
        case .all: return "Every feature screenshotted (and filmed for games) for phone review"
        }
    }

    /// What an agent is told to do at this level (printed by `orbital-track evidence`, `start` and `done`).
    public var agentInstruction: String {
        switch self {
        case .off:
            return """
            Screenshot evidence: OFF. Nothing is required. Attach screenshots or recordings with --media when you make them anyway.
            """
        case .min1:
            return """
            Screenshot evidence: MIN 1 (the user turned this on).
            If this task changes anything visual (an app or iOS screen, a website or page, a game, a map, a render, a chart), \
            attach at least ONE screenshot of the finished result before you report done:
              orbital-track update <ID> --media /abs/after.png    (or --media on `done`)
            Games: when you test a new feature where seeing the action matters (movement, combat, animation, physics, effects), \
            also attach a short video of it (--media /abs/clip.mp4).
            Non-visual work (backend, data, scripts, docs) needs nothing.
            """
        case .all:
            return """
            Screenshot evidence: ALL (the user turned this on and reviews from the Orbital Agents app, away from the Mac).
            If this task changes anything visual (an app or iOS screen, a website or page, a game, a map, a render, a chart):
              - Screenshot EVERY feature, screen, page and state you added or changed (before/after when it helps).
              - Games: a short video of every new or changed feature that moves or plays (movement, combat, animation, \
            physics, effects, UI flow).
              - Attach each as you go: orbital-track update <ID> --note "<what it shows>" --media /abs/shot.png
              - In the done summary, list each feature next to its picture: ![Settings sheet, dark mode](/abs/shot.png)
            Non-visual work (backend, data, scripts, docs) needs nothing.
            """
        }
    }
}

/// `settings.json` in the tracker data folder. The Mac app, the CLI and the hub (for the
/// Orbital Agents app) all read and write it atomically; whoever changed it last is `updatedBy`.
public struct TrackerSettings: Codable, Equatable, Sendable {
    public var evidence: EvidenceLevel
    public var updatedAt: Date?
    /// "tracker" (the Mac panel), "app" (Orbital Agents on a phone or Mac), or "cli".
    public var updatedBy: String?

    public static let writers = ["tracker", "app", "cli"]

    public init(evidence: EvidenceLevel = .off, updatedAt: Date? = nil, updatedBy: String? = nil) {
        self.evidence = evidence
        self.updatedAt = updatedAt
        self.updatedBy = updatedBy.map(Self.normalizeWriter)
    }

    /// "tracker" and "cli" stay; everything else is the companion app, including the
    /// name older builds of that app wrote.
    public static func normalizeWriter(_ raw: String) -> String {
        let s = raw.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        switch s {
        case "tracker", "cli": return s
        default: return "app"
        }
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        // An unknown level (a newer writer, a hand edit) reads as off rather than failing.
        let raw = try? c.decodeIfPresent(String.self, forKey: .evidence)
        evidence = raw.flatMap(EvidenceLevel.init(loose:)) ?? .off
        updatedAt = try? c.decodeIfPresent(Date.self, forKey: .updatedAt)
        updatedBy = (try? c.decodeIfPresent(String.self, forKey: .updatedBy)).flatMap { $0 }.map(Self.normalizeWriter)
    }

    /// Missing or unreadable file → defaults (evidence off).
    public static func load(_ paths: TrackerPaths) -> TrackerSettings {
        load(from: paths.settingsFile)
    }

    public static func load(from file: URL) -> TrackerSettings {
        guard let data = try? Data(contentsOf: file),
              let settings = try? TrackerJSON.decoder().decode(TrackerSettings.self, from: data) else { return TrackerSettings() }
        return settings
    }

    public func save(_ paths: TrackerPaths) throws {
        try save(to: paths.settingsFile)
    }

    public func save(to file: URL) throws {
        try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        try TrackerJSON.encoder(pretty: true).encode(self).write(to: file, options: .atomic)
    }

    /// Reads the `settings` object (or a bare settings object) from an HTTP reply.
    public static func fromReply(_ data: Data) -> TrackerSettings? {
        guard let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        let node = (obj["settings"] as? [String: Any]) ?? obj
        guard let raw = node["evidence"] as? String, let level = EvidenceLevel(loose: raw) else { return nil }
        return TrackerSettings(evidence: level,
                               updatedAt: (node["updatedAt"] as? String).flatMap(TrackerJSON.parseDate),
                               updatedBy: node["updatedBy"] as? String)
    }

    /// Of two copies (this Mac and the hub), the one changed last.
    public static func newest(_ a: TrackerSettings, _ b: TrackerSettings?) -> TrackerSettings {
        guard let b else { return a }
        switch (a.updatedAt, b.updatedAt) {
        case let (x?, y?): return y > x ? b : a
        case (nil, _?): return b
        default: return a
        }
    }

    /// The JSON the HTTP API and the CLI print.
    public var json: [String: Any] {
        var out: [String: Any] = ["evidence": evidence.rawValue, "label": evidence.label, "blurb": evidence.blurb]
        if let updatedAt { out["updatedAt"] = TrackerJSON.string(updatedAt) }
        if let updatedBy { out["updatedBy"] = updatedBy }
        return out
    }
}
