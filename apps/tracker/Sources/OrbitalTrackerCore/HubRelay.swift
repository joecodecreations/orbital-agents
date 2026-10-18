import Foundation

/// Blocking HTTP for the CLI (one short request at a time). Returns nil when nothing answered.
public enum SyncHTTP {
    final class Box: @unchecked Sendable { var status = 0; var data = Data() }

    public static func send(_ request: URLRequest, timeout: TimeInterval) -> (status: Int, body: Data)? {
        var req = request
        req.timeoutInterval = timeout
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = timeout
        config.timeoutIntervalForResource = timeout + 1
        config.connectionProxyDictionary = [:]   // loopback and tailnet traffic never goes through a proxy
        let session = URLSession(configuration: config)
        defer { session.finishTasksAndInvalidate() }
        let box = Box()
        let done = DispatchSemaphore(value: 0)
        session.dataTask(with: req) { data, response, _ in
            if let r = response as? HTTPURLResponse {
                box.status = r.statusCode
                box.data = data ?? Data()
            }
            done.signal()
        }.resume()
        _ = done.wait(timeout: .now() + timeout + 1.5)
        return box.status == 0 ? nil : (box.status, box.data)
    }

    public static func request(_ method: String, _ url: URL, json: Data? = nil, bearer: String? = nil) -> URLRequest {
        var req = URLRequest(url: url)
        req.httpMethod = method
        if let json {
            req.httpBody = json
            req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if let bearer, !bearer.isEmpty { req.setValue("Bearer \(bearer)", forHTTPHeaderField: "Authorization") }
        req.setValue("orbital-track/\(TrackerInfo.version)", forHTTPHeaderField: "User-Agent")
        return req
    }
}

/// Satellites send every `orbital-track` command to the hub as well, so the hub's tracker (and the
/// Orbital Agents app) sees work on every machine: `POST <hub_url>/v1/tracker/command` with
/// `Authorization: Bearer <ORBITAL_HUB_TOKEN>` and the same JSON the local tracker gets.
///
/// Nothing here ever blocks an agent: commands that can't be delivered wait in `hub-outbox/`
/// (oldest first, so order is kept) and go out with the next command once the hub answers.
/// After a failed attempt the hub is skipped for `cooldown` seconds, so a hub that is down costs
/// one short timeout, not one per command.
public struct HubRelay: @unchecked Sendable {
    public enum Outcome: Equatable {
        case sent, queued, dropped, skipped
    }

    public let hubURL: String
    public let token: String?
    public let outbox: URL
    public var timeout: TimeInterval = 2.5
    public var cooldown: TimeInterval = 60
    /// Older queued files are deleted; at most this many are kept.
    public var maxQueued = 500
    public var maxAge: TimeInterval = 7 * 86_400
    /// Injected for tests; defaults to SyncHTTP.
    public var transport: (URLRequest, TimeInterval) -> (status: Int, body: Data)? = { SyncHTTP.send($0, timeout: $1) }

    public init(hubURL: String, token: String?, outbox: URL) {
        self.hubURL = hubURL.trimmingCharacters(in: CharacterSet(charactersIn: "/ "))
        self.token = token
        self.outbox = outbox
    }

    public init?(config: OrbitalConfig, paths: TrackerPaths, env: [String: String] = ProcessInfo.processInfo.environment) {
        guard config.relaysToHub else { return nil }
        self.init(hubURL: config.hubURL,
                  token: Secrets.get("ORBITAL_HUB_TOKEN", file: config.secretsFile, env: env),
                  outbox: paths.hubOutboxDir)
    }

    public var commandURL: URL? { URL(string: hubURL + "/v1/tracker/command") }
    public var settingsURL: URL? { URL(string: hubURL + "/v1/tracker/settings") }
    var downMarker: URL { outbox.appendingPathComponent(".hub-down") }

    /// True while a recent attempt failed (the hub is treated as down for `cooldown` seconds).
    public func inCooldown(now: Date = Date()) -> Bool {
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: downMarker.path),
              let modified = attrs[.modificationDate] as? Date else { return false }
        return now.timeIntervalSince(modified) < cooldown
    }

    func markDown() {
        try? FileManager.default.createDirectory(at: outbox, withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: downMarker.path, contents: Data())
    }

    func markUp() { try? FileManager.default.removeItem(at: downMarker) }

    public var queued: [URL] { Inbox.pending(in: outbox) }

    /// Sends `body` (an encoded TrackerCommand) after anything already queued.
    @discardableResult
    public func relay(_ body: Data, now: Date = Date()) -> Outcome {
        trim(now: now)
        // A hub that just failed, or no token yet: queue without trying.
        if inCooldown(now: now) || token == nil {
            return enqueue(body) ? .queued : .skipped
        }
        if !flush() {
            return enqueue(body) ? .queued : .skipped
        }
        switch post(body) {
        case .delivered: return .sent
        case .rejected: return .dropped
        case .unreachable: return enqueue(body) ? .queued : .skipped
        }
    }

    /// Sends queued commands oldest first; false when the hub stopped answering.
    @discardableResult
    public func flush(limit: Int = 50) -> Bool {
        guard token != nil, !inCooldown() else { return queued.isEmpty }
        for file in queued.prefix(limit) {
            guard let data = try? Data(contentsOf: file) else {
                try? FileManager.default.removeItem(at: file)
                continue
            }
            switch post(data) {
            case .delivered, .rejected:
                // Rejected (bad id, bad token): retrying would never succeed, so it isn't kept.
                try? FileManager.default.removeItem(at: file)
            case .unreachable:
                return false
            }
        }
        return queued.isEmpty
    }

    enum PostResult { case delivered, rejected, unreachable }

    private func post(_ body: Data) -> PostResult {
        guard let url = commandURL else { return .rejected }
        let req = SyncHTTP.request("POST", url, json: body, bearer: token)
        guard let (status, _) = transport(req, timeout) else {
            markDown()
            return .unreachable
        }
        markUp()
        if (200..<300).contains(status) { return .delivered }
        if status == 408 || status == 429 || status >= 500 {
            markDown()
            return .unreachable
        }
        return .rejected
    }

    private func enqueue(_ body: Data) -> Bool {
        (try? Inbox.writeData(body, to: outbox)) != nil
    }

    /// Keeps the outbox bounded: drops files past `maxAge` and the oldest past `maxQueued`.
    public func trim(now: Date = Date()) {
        Retention.pruneFiles(in: outbox, olderThan: now.addingTimeInterval(-maxAge))
        let files = queued
        if files.count > maxQueued {
            for file in files.prefix(files.count - maxQueued) { try? FileManager.default.removeItem(at: file) }
        }
    }

    /// The hub's evidence level (nil when unreachable or not allowed).
    public func fetchSettings() -> TrackerSettings? {
        guard let url = settingsURL, let token, !inCooldown() else { return nil }
        guard let (status, body) = transport(SyncHTTP.request("GET", url, bearer: token), min(timeout, 1.5)) else {
            markDown()
            return nil
        }
        guard (200..<300).contains(status) else { return nil }
        return TrackerSettings.fromReply(body)
    }

    /// Tells the hub about a level changed on this machine. Best effort.
    @discardableResult
    public func pushSettings(_ settings: TrackerSettings, by: String) -> Bool {
        guard let url = settingsURL, let token, !inCooldown() else { return false }
        let body = try? JSONSerialization.data(withJSONObject: ["evidence": settings.evidence.rawValue, "by": by])
        guard let (status, _) = transport(SyncHTTP.request("POST", url, json: body, bearer: token), min(timeout, 1.5)) else {
            markDown()
            return false
        }
        return (200..<300).contains(status)
    }
}
