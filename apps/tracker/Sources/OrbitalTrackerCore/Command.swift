import Foundation

/// A file attached to a command. The same JSON travels over HTTP, through the inbox folders
/// and through the hub relay, so every path behaves the same.
public struct MediaInput: Codable, Sendable, Equatable {
    public var path: String
    public var caption: String?
    /// Filled when the CLI already made the compressed copy.
    public var archived: MediaRef?
    /// Base64 JPEG of that compressed copy. Only set on commands relayed to the hub, whose
    /// tracker can't read the satellite's files; the receiving tracker stores it in its own media/.
    public var inlineArchive: String?

    public init(path: String, caption: String? = nil, archived: MediaRef? = nil, inlineArchive: String? = nil) {
        self.path = path
        self.caption = caption
        self.archived = archived
        self.inlineArchive = inlineArchive
    }
}

/// One instruction from an agent, the CLI, or another machine (through the hub).
public struct TrackerCommand: Codable, Sendable, Equatable {
    /// start | sub | update | done (aliases: add, note, complete, finish)
    public var op: String
    public var id: String?
    public var parent: String?
    public var agent: String?
    public var agentLabel: String?
    /// The machine the command comes from (config `machine_name`).
    public var machine: String?
    public var title: String?
    public var details: String?
    public var appendDetails: Bool?
    public var note: String?
    public var summary: String?
    public var workspace: String?
    public var media: [MediaInput]?
    public var sentAt: Date?

    public init(op: String, id: String? = nil) {
        self.op = op
        self.id = id
        self.sentAt = Date()
    }

    /// The canonical op: start, sub, update or done (nil when unknown).
    public static func canonicalOp(_ raw: String) -> String? {
        switch raw.lowercased() {
        case "start", "add": return "start"
        case "sub": return "sub"
        case "update", "note": return "update"
        case "done", "complete", "finish": return "done"
        default: return nil
        }
    }

    /// The friendly curl form: `POST /v1/<route>[/<id>]` with a JSON body; the op comes from the path.
    /// `POST /v1/sub/<parent>` sets the parent; the others set the id. start/sub get an id when none is given.
    public static func fromRoute(_ route: String, pathID: String?, body: Data) throws -> TrackerCommand {
        var obj: [String: Any] = [:]
        if !body.isEmpty {
            guard let parsed = try JSONSerialization.jsonObject(with: body) as? [String: Any] else {
                throw CommandError("the body must be a JSON object")
            }
            obj = parsed
        }
        obj["op"] = route
        if let pathID, !pathID.isEmpty { obj[route == "sub" ? "parent" : "id"] = pathID }
        if ["start", "sub", "add"].contains(route), obj["id"] == nil { obj["id"] = IDGen.make() }
        let data = try JSONSerialization.data(withJSONObject: obj)
        return try TrackerJSON.decoder().decode(TrackerCommand.self, from: data)
    }

    /// Ids and parents must be plain tokens: they end up in archive file names.
    public func validate() throws {
        guard Self.canonicalOp(op) != nil else {
            throw CommandError("unknown op '\(op)' (use start, sub, update, done)")
        }
        for (name, value) in [("id", id), ("parent", parent)] {
            if let value, !IDGen.isValid(value) {
                throw CommandError("\(name) must be 2-64 letters, digits, '-' or '_'")
            }
        }
    }
}

public struct CommandError: LocalizedError, Equatable {
    public let message: String
    public init(_ message: String) { self.message = message }
    public var errorDescription: String? { message }
}

public enum Inbox {
    /// Atomically drops a command file into an inbox folder.
    @discardableResult
    public static func write(_ command: TrackerCommand, to dir: URL) throws -> URL {
        try writeData(TrackerJSON.encoder().encode(command), to: dir)
    }

    /// File names are `<unix microseconds, 16 digits>-<sequence>-<random>.json`, so they sort in
    /// the order they were written, even within one millisecond.
    @discardableResult
    public static func writeData(_ data: Data, to dir: URL) throws -> URL {
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let name = nextName()
        let tmp = dir.appendingPathComponent(".\(name).tmp")
        let final = dir.appendingPathComponent(name)
        try data.write(to: tmp)
        try FileManager.default.moveItem(at: tmp, to: final)
        return final
    }

    private static let lock = NSLock()
    nonisolated(unsafe) private static var lastMicros: UInt64 = 0
    nonisolated(unsafe) private static var sequence = 0

    static func nextName() -> String {
        lock.lock()
        defer { lock.unlock() }
        var micros = UInt64(Date().timeIntervalSince1970 * 1_000_000)
        if micros <= lastMicros { micros = lastMicros + 1 }
        lastMicros = micros
        sequence = (sequence + 1) % 10_000
        return String(format: "%016llu-%04d-%@.json", micros, sequence, IDGen.make(4))
    }

    /// Queued command files, oldest first. Names start with a Unix timestamp in milliseconds
    /// (13 digits) or microseconds (16 digits); both are compared on the same scale.
    public static func pending(in dir: URL) -> [URL] {
        guard let names = try? FileManager.default.contentsOfDirectory(atPath: dir.path) else { return [] }
        let files: [String] = names.filter { $0.hasSuffix(".json") && !$0.hasPrefix(".") }
        let sorted: [String] = files.sorted { a, b in
            let ka = sortKey(a), kb = sortKey(b)
            return ka == kb ? a < b : ka < kb
        }
        return sorted.map { dir.appendingPathComponent($0) }
    }

    static func sortKey(_ name: String) -> Double {
        guard let n = Double(name.prefix { $0.isASCII && $0.isNumber }) else { return 0 }
        return n < 1e14 ? n * 1000 : n
    }
}

public enum MarkdownMedia {
    private static let imageRegex = try! NSRegularExpression(
        pattern: #"!\[[^\]]*\]\(\s*<?([^)\s>]+)>?(?:\s+"[^"]*")?\s*\)"#)

    /// Local file paths referenced as markdown images: ![alt](/path/to/shot.png), standardized the
    /// same way as --media paths (so the same file attached both ways is kept once).
    public static func localPaths(in markdown: String?) -> [String] {
        guard let markdown, !markdown.isEmpty else { return [] }
        let ns = markdown as NSString
        return imageRegex.matches(in: markdown, range: NSRange(location: 0, length: ns.length)).compactMap { m in
            localPath(ns.substring(with: m.range(at: 1)))
        }
    }

    /// An image source as a standardized local path (nil for web URLs and relative paths).
    public static func localPath(_ raw: String) -> String? {
        var path: String?
        if raw.hasPrefix("/") || raw.hasPrefix("~") { path = (raw as NSString).expandingTildeInPath }
        else if raw.hasPrefix("file://"), let url = URL(string: raw) { path = url.path }
        return path.map { ($0 as NSString).standardizingPath }
    }
}

/// `orbital-track` argument parsing: positionals, `--key value`, `--key=value`, and flags.
public struct CLIArguments: Equatable {
    public static let flagNames: Set<String> = ["append", "all", "json", "help", "quiet"]

    public var positional: [String] = []
    public var options: [String: [String]] = [:]
    public var flags: Set<String> = []

    public init() {}

    public func value(_ key: String) -> String? { options[key]?.last }
    public func values(_ key: String) -> [String] { options[key] ?? [] }
    public var verb: String? { positional.first?.lowercased() }

    public static func parse(_ args: [String]) throws -> CLIArguments {
        var p = CLIArguments()
        var i = 0
        while i < args.count {
            let a = args[i]
            if a == "--" {
                p.positional.append(contentsOf: args[(i + 1)...])
                break
            } else if a.hasPrefix("--"), a.count > 2 {
                var key = String(a.dropFirst(2))
                var inline: String?
                if let eq = key.firstIndex(of: "=") {
                    inline = String(key[key.index(after: eq)...])
                    key = String(key[..<eq])
                }
                if flagNames.contains(key) {
                    p.flags.insert(key)
                } else if let inline {
                    p.options[key, default: []].append(inline)
                } else if i + 1 < args.count {
                    p.options[key, default: []].append(args[i + 1])
                    i += 1
                } else {
                    throw CommandError("--\(key) needs a value")
                }
            } else if a == "-h" {
                p.flags.insert("help")
            } else {
                p.positional.append(a)
            }
            i += 1
        }
        return p
    }
}
