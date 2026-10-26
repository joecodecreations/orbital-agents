import Foundation

/// Version and service name reported by `GET /v1/health` and `orbital-track ping`.
public enum TrackerInfo {
    public static let version = "0.1.0"
    public static let service = "orbital-tracker"
    /// Window, app and menu name.
    public static let productName = "Orbital Agents - Agent Satellite Tracker"
    /// The bar has room for about 23 characters.
    public static let shortName = "Agent Satellite Tracker"
    /// Label of the LaunchAgent the services installer writes.
    public static let launchAgentLabel = "io.orbitalagents.tracker"
}

/// Who turns a spoken or typed phrase into a short bubble title (`tracker.titles`).
public enum TitleMode: String, Sendable, CaseIterable {
    /// Use the `grok` CLI when it is installed, otherwise a local title.
    case auto
    /// Same as auto, but says so in the menu when grok is missing.
    case grok
    /// Local titles only; never runs grok.
    case none

    public init(loose raw: String?) {
        switch (raw ?? "").trimmingCharacters(in: .whitespaces).lowercased() {
        case "grok": self = .grok
        case "none", "off", "local", "false", "no": self = .none
        default: self = .auto
        }
    }
}

/// The settings the tracker and its CLI use, read from `$ORBITAL_HOME/config.resolved.json`
/// (written by `orbital config --apply` and the setup wizard). Anything missing falls back
/// to the same defaults as `orbital/core/config.py`, so the tracker also runs before setup.
public struct OrbitalConfig: Sendable, Equatable {
    public var home: URL
    public var resolvedFile: URL
    /// True when config.resolved.json existed and parsed.
    public var loadedFromFile: Bool

    public var systemName: String
    public var role: String
    public var machineName: String
    public var hubURL: String

    public var trackerEnabled: Bool
    public var trackerPort: UInt16
    public var sttPort: UInt16
    public var voice: Bool
    public var titles: TitleMode
    public var grokModel: String?
    /// `tracker.hotkeys`: false registers no global shortcuts.
    public var hotkeys: Bool
    /// `tracker.hotkey_modifiers`, parsed; ctrl+option when the value can't be used.
    public var hotkeyModifiers: HotkeyModifiers
    /// The raw `tracker.hotkey_modifiers` text, and whether it parsed (false → log a warning).
    public var hotkeyModifiersRaw: String
    public var hotkeyModifiersValid: Bool
    public var historyRetentionDays: Int
    public var mediaRetentionDays: Int
    /// `tracker.stale_hours`: agent items with no update for this long close themselves (0 = never).
    public var staleHours: Int

    public var logLevel: String
    public var logMaxBytes: Int
    public var logBackups: Int
    public var logRetentionDays: Int

    public var bundlePrefix: String

    public var trackerData: URL
    public var logsDir: URL
    public var runDir: URL
    public var secretsFile: URL

    public static let defaultTrackerPort: UInt16 = 7701
    public static let defaultSTTPort: UInt16 = 7702

    public var isSatellite: Bool { role == "satellite" }
    /// Satellites copy every command to the hub's tracker as well.
    public var relaysToHub: Bool { isSatellite && !hubURL.isEmpty }
    public var trackerBundleID: String { "\(bundlePrefix).orbitalagents.tracker" }

    // MARK: Loading

    /// `ORBITAL_HOME`, else `~/.orbital-agents`.
    public static func homeDirectory(env: [String: String] = ProcessInfo.processInfo.environment) -> URL {
        if let raw = env["ORBITAL_HOME"]?.trimmingCharacters(in: .whitespaces), !raw.isEmpty {
            return URL(fileURLWithPath: (raw as NSString).expandingTildeInPath, isDirectory: true).standardizedFileURL
        }
        return FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent(".orbital-agents", isDirectory: true)
    }

    public static func load(env: [String: String] = ProcessInfo.processInfo.environment) -> OrbitalConfig {
        let home = homeDirectory(env: env)
        let file = home.appendingPathComponent("config.resolved.json")
        return parse(data: try? Data(contentsOf: file), home: home)
    }

    /// Builds the config from the resolved JSON (nil or unreadable → all defaults).
    public static func parse(data: Data?, home: URL) -> OrbitalConfig {
        var root: [String: Any] = [:]
        var loaded = false
        if let data, let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            root = obj
            loaded = true
        }
        let r = Reader(root)

        func dir(_ key: String, _ fallback: URL) -> URL {
            guard let s = r.string(key), !s.isEmpty else { return fallback }
            return URL(fileURLWithPath: (s as NSString).expandingTildeInPath, isDirectory: true)
        }
        func file(_ key: String, _ fallback: URL) -> URL {
            guard let s = r.string(key), !s.isEmpty else { return fallback }
            return URL(fileURLWithPath: (s as NSString).expandingTildeInPath, isDirectory: false)
        }

        let homeDir = dir("paths.home", home)
        let hotkeyRaw = r.string("tracker.hotkey_modifiers") ?? HotkeyModifiers.default.configString
        let hotkeyMods = HotkeyModifiers.resolve(hotkeyRaw)
        let data = homeDir.appendingPathComponent("data", isDirectory: true)
        let machine = (r.string("machine_name") ?? r.string("system.machine_name")).flatMap { $0.isEmpty ? nil : $0 }
            ?? Self.shortHostName()

        return OrbitalConfig(
            home: homeDir,
            resolvedFile: home.appendingPathComponent("config.resolved.json"),
            loadedFromFile: loaded,
            systemName: r.string("system.name").flatMap { $0.isEmpty ? nil : $0 } ?? "Orbital Agents",
            role: (r.string("system.role") ?? "hub").lowercased() == "satellite" ? "satellite" : "hub",
            machineName: machine,
            hubURL: (r.string("hub_url") ?? r.string("hub.url") ?? "").trimmingCharacters(in: CharacterSet(charactersIn: "/ ")),
            trackerEnabled: r.bool("tracker.enabled") ?? true,
            trackerPort: r.port("tracker.port") ?? defaultTrackerPort,
            sttPort: r.port("tracker.stt_port") ?? defaultSTTPort,
            voice: r.bool("tracker.voice") ?? false,
            titles: TitleMode(loose: r.string("tracker.titles")),
            grokModel: r.string("tracker.grok_model").flatMap { $0.isEmpty ? nil : $0 },
            hotkeys: r.bool("tracker.hotkeys") ?? true,
            hotkeyModifiers: hotkeyMods.modifiers,
            hotkeyModifiersRaw: hotkeyRaw,
            hotkeyModifiersValid: hotkeyMods.valid,
            historyRetentionDays: r.int("tracker.history_retention_days") ?? 365,
            mediaRetentionDays: r.int("tracker.media_retention_days") ?? 60,
            staleHours: r.int("tracker.stale_hours") ?? 24,
            logLevel: (r.string("logging.level") ?? "INFO").uppercased(),
            logMaxBytes: max(r.int("logging.max_bytes") ?? 5_000_000, 1024),
            logBackups: max(r.int("logging.backups") ?? 3, 0),
            logRetentionDays: r.int("logging.retention_days") ?? 14,
            bundlePrefix: r.string("apple.bundle_prefix").flatMap { $0.isEmpty ? nil : $0 } ?? "com.example",
            trackerData: dir("paths.tracker_data", data.appendingPathComponent("tracker", isDirectory: true)),
            logsDir: dir("paths.logs", homeDir.appendingPathComponent("logs", isDirectory: true)),
            runDir: dir("paths.run", homeDir.appendingPathComponent("run", isDirectory: true)),
            secretsFile: file("paths.secrets", homeDir.appendingPathComponent("secrets.env"))
        )
    }

    /// Creates ORBITAL_HOME (mode 0700, like orbital/core/paths.py) when it doesn't exist yet.
    public func ensureHome() {
        guard !FileManager.default.fileExists(atPath: home.path) else { return }
        try? FileManager.default.createDirectory(at: home, withIntermediateDirectories: true,
                                                 attributes: [.posixPermissions: 0o700])
    }

    /// This computer's short host name, like Python's `socket.gethostname().split(".")[0]`.
    public static func shortHostName() -> String {
        var buf = [CChar](repeating: 0, count: 256)
        guard gethostname(&buf, buf.count) == 0 else { return "mac" }
        let name = String(cString: buf).split(separator: ".").first.map(String.init) ?? ""
        return name.isEmpty ? "mac" : name
    }

    /// Dotted-key access into the parsed JSON, tolerant of types.
    struct Reader {
        let root: [String: Any]
        init(_ root: [String: Any]) { self.root = root }

        func value(_ dotted: String) -> Any? {
            var node: Any? = root
            for part in dotted.split(separator: ".") {
                guard let dict = node as? [String: Any] else { return nil }
                node = dict[String(part)]
            }
            return node is NSNull ? nil : node
        }

        func string(_ key: String) -> String? {
            switch value(key) {
            case let s as String: return s
            case let n as NSNumber: return n.stringValue
            default: return nil
            }
        }

        func int(_ key: String) -> Int? {
            switch value(key) {
            case let n as NSNumber: return n.intValue
            case let s as String: return Int(s.trimmingCharacters(in: .whitespaces))
            default: return nil
            }
        }

        func bool(_ key: String) -> Bool? {
            switch value(key) {
            case let n as NSNumber: return n.boolValue
            case let s as String:
                switch s.lowercased() {
                case "true", "yes", "on", "1": return true
                case "false", "no", "off", "0": return false
                default: return nil
                }
            default: return nil
            }
        }

        func port(_ key: String) -> UInt16? {
            guard let n = int(key), n > 0, n < 65536 else { return nil }
            return UInt16(n)
        }
    }
}

/// `secrets.env`: KEY=VALUE lines, `#` comments, optional quotes (same rules as orbital/core/secrets.py).
/// Environment variables win, which keeps CI and tests simple. Values are never logged.
public enum Secrets {
    public static func parse(_ text: String) -> [String: String] {
        var out: [String: String] = [:]
        for raw in text.components(separatedBy: .newlines) {
            let line = raw.trimmingCharacters(in: .whitespaces)
            guard !line.isEmpty, !line.hasPrefix("#"), let eq = line.firstIndex(of: "=") else { continue }
            let key = line[..<eq].trimmingCharacters(in: .whitespaces)
            var value = line[line.index(after: eq)...].trimmingCharacters(in: .whitespaces)
            value = value.trimmingCharacters(in: CharacterSet(charactersIn: "'")).trimmingCharacters(in: CharacterSet(charactersIn: "\""))
            if !key.isEmpty { out[key] = value }
        }
        return out
    }

    public static func get(_ key: String, file: URL, env: [String: String] = ProcessInfo.processInfo.environment) -> String? {
        if let v = env[key], !v.isEmpty { return v }
        guard let text = try? String(contentsOf: file, encoding: .utf8) else { return nil }
        let v = parse(text)[key]
        return (v?.isEmpty ?? true) ? nil : v
    }
}
