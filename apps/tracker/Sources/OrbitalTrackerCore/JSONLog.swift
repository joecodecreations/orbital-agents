import Foundation

public enum LogLevel: Int, Comparable, Sendable {
    case debug = 10, info = 20, warning = 30, error = 40

    public init(name: String) {
        switch name.uppercased() {
        case "DEBUG": self = .debug
        case "WARNING", "WARN": self = .warning
        case "ERROR", "CRITICAL": self = .error
        default: self = .info
        }
    }

    /// Python's level names, so `orbital logs --level WARNING` filters Swift and Python logs alike.
    public var name: String {
        switch self {
        case .debug: return "DEBUG"
        case .info: return "INFO"
        case .warning: return "WARNING"
        case .error: return "ERROR"
        }
    }

    public static func < (a: LogLevel, b: LogLevel) -> Bool { a.rawValue < b.rawValue }
}

/// The same log format as orbital/core/logs.py: one JSON object per line,
/// `{"ts":"2026-01-02T03:04:05.678Z","level":"INFO","component":"tracker","msg":"...",...fields}`,
/// rolled by size like Python's RotatingFileHandler (`tracker.log` → `.1` → … → `.<backups>`).
/// Never pass secrets, tokens or whole request bodies as fields.
public final class JSONLineLogger: @unchecked Sendable {
    public let file: URL
    public let component: String
    public let maxBytes: Int
    public let backups: Int
    public let minimum: LogLevel
    /// Also print each line to stderr (handy under `swift run`).
    public var echo = false
    private let lock = NSLock()

    public init(file: URL, component: String, maxBytes: Int, backups: Int, level: LogLevel = .info) {
        self.file = file
        self.component = component
        self.maxBytes = max(maxBytes, 256)
        self.backups = max(backups, 0)
        self.minimum = level
    }

    public convenience init(config: OrbitalConfig, component: String = "tracker") {
        self.init(file: config.logsDir.appendingPathComponent("\(component).log"), component: component,
                  maxBytes: config.logMaxBytes, backups: config.logBackups, level: LogLevel(name: config.logLevel))
    }

    public func debug(_ msg: String, _ fields: [String: Any?] = [:]) { log(.debug, msg, fields) }
    public func info(_ msg: String, _ fields: [String: Any?] = [:]) { log(.info, msg, fields) }
    public func warning(_ msg: String, _ fields: [String: Any?] = [:]) { log(.warning, msg, fields) }
    public func error(_ msg: String, _ fields: [String: Any?] = [:]) { log(.error, msg, fields) }

    public func log(_ level: LogLevel, _ msg: String, _ fields: [String: Any?] = [:], at date: Date = Date()) {
        guard level >= minimum else { return }
        let line = Self.formatLine(date: date, level: level, component: component, msg: msg, fields: fields) + "\n"
        let data = Data(line.utf8)
        lock.lock()
        defer { lock.unlock() }
        if echo { FileHandle.standardError.write(data) }
        write(data)
    }

    private func write(_ data: Data) {
        let fm = FileManager.default
        try? fm.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
        let size = ((try? fm.attributesOfItem(atPath: file.path))?[.size] as? NSNumber)?.intValue ?? 0
        if size > 0, size + data.count > maxBytes { rollLocked() }
        if !fm.fileExists(atPath: file.path) { fm.createFile(atPath: file.path, contents: nil) }
        guard let handle = try? FileHandle(forWritingTo: file) else { return }
        defer { try? handle.close() }
        _ = try? handle.seekToEnd()
        try? handle.write(contentsOf: data)
    }

    /// Rolls now: tracker.log → tracker.log.1, .1 → .2 …; the oldest past `backups` is deleted.
    public func roll() {
        lock.lock()
        defer { lock.unlock() }
        rollLocked()
    }

    private func rollLocked() {
        Self.roll(file, backups: backups)
    }

    /// The rolling rule on its own, also used for the speech sidecar's raw output file.
    public static func roll(_ file: URL, backups: Int) {
        let fm = FileManager.default
        guard fm.fileExists(atPath: file.path) else { return }
        guard backups > 0 else {
            try? fm.removeItem(at: file)
            return
        }
        func numbered(_ n: Int) -> URL { URL(fileURLWithPath: file.path + ".\(n)") }
        try? fm.removeItem(at: numbered(backups))
        if backups > 1 {
            for n in stride(from: backups - 1, through: 1, by: -1) where fm.fileExists(atPath: numbered(n).path) {
                try? fm.moveItem(at: numbered(n), to: numbered(n + 1))
            }
        }
        try? fm.moveItem(at: file, to: numbered(1))
    }

    /// Rolls `file` when it is already past `maxBytes` (for files other processes append to).
    public static func rollIfLarge(_ file: URL, maxBytes: Int, backups: Int) {
        let size = ((try? FileManager.default.attributesOfItem(atPath: file.path))?[.size] as? NSNumber)?.intValue ?? 0
        if size > maxBytes { roll(file, backups: backups) }
    }

    public static func timestamp(_ date: Date) -> String { tsFormatter.string(from: date) }

    /// `{"ts","level","component","msg"}` first, then the fields sorted by name. nil fields are dropped.
    public static func formatLine(date: Date, level: LogLevel, component: String, msg: String, fields: [String: Any?]) -> String {
        var parts = [
            "\"ts\":\(encode(timestamp(date)))",
            "\"level\":\(encode(level.name))",
            "\"component\":\(encode(component))",
            "\"msg\":\(encode(msg))",
        ]
        let reserved: Set<String> = ["ts", "level", "component", "msg"]
        for key in fields.keys.sorted() where !reserved.contains(key) {
            guard let value = fields[key] ?? nil else { continue }
            parts.append("\(encode(key)):\(encode(value))")
        }
        return "{" + parts.joined(separator: ",") + "}"
    }

    private static func encode(_ value: Any) -> String {
        let safe: Any
        switch value {
        case let d as Date: safe = timestamp(d)
        case let u as URL: safe = u.path
        case is String, is NSNumber, is Bool, is Int, is Double, is NSNull: safe = value
        case let a as [Any]: safe = a.map { ($0 as? String) ?? "\($0)" }
        case let d as [String: Any]: safe = d.mapValues { ($0 as? String) ?? "\($0)" }
        default: safe = "\(value)"
        }
        if let data = try? JSONSerialization.data(withJSONObject: safe, options: [.fragmentsAllowed, .withoutEscapingSlashes]),
           let s = String(data: data, encoding: .utf8) {
            return s
        }
        return "\"\(String(describing: value).replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\""))\""
    }

    private static let tsFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyy-MM-dd'T'HH:mm:ss.SSS'Z'"
        return f
    }()
}
