import Foundation
import Network
import OrbitalTrackerCore

struct HTTPRequest {
    var method: String
    var path: String
    var query: [String: String]
    var headers: [String: String]
    var body: Data

    /// nil until the full request (headers + Content-Length body) has arrived.
    static func parse(_ data: Data) -> HTTPRequest? {
        guard let end = data.range(of: Data("\r\n\r\n".utf8)) else { return nil }
        let head = String(decoding: data[..<end.lowerBound], as: UTF8.self)
        var lines = head.components(separatedBy: "\r\n")
        guard !lines.isEmpty else { return nil }
        let requestLine = lines.removeFirst().split(separator: " ")
        guard requestLine.count >= 2 else { return nil }
        var headers: [String: String] = [:]
        for line in lines {
            guard let colon = line.firstIndex(of: ":") else { continue }
            headers[line[..<colon].lowercased()] = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
        }
        let length = max(Int(headers["content-length"] ?? "0") ?? 0, 0)
        let bodyStart = end.upperBound
        guard data.count - bodyStart >= length else { return nil }
        let target = String(requestLine[1])
        let comps = URLComponents(string: "http://localhost\(target)")
        var query: [String: String] = [:]
        for q in comps?.queryItems ?? [] { query[q.name] = q.value ?? "" }
        return HTTPRequest(method: String(requestLine[0]).uppercased(), path: comps?.path ?? target,
                           query: query, headers: headers, body: Data(data[bodyStart..<(bodyStart + length)]))
    }
}

struct HTTPResponse {
    var status: Int
    var body: Data
    var contentType = "application/json; charset=utf-8"

    static func json(_ status: Int, _ object: Any) -> HTTPResponse {
        HTTPResponse(status: status, body: (try? JSONSerialization.data(withJSONObject: object, options: [.withoutEscapingSlashes, .sortedKeys])) ?? Data())
    }

    func serialized() -> Data {
        let reasons = [200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
                       408: "Request Timeout", 413: "Payload Too Large", 415: "Unsupported Media Type",
                       500: "Internal Server Error"]
        var head = "HTTP/1.1 \(status) \(reasons[status] ?? "OK")\r\n"
        head += "Content-Type: \(contentType)\r\n"
        head += "Cache-Control: no-store\r\n"
        head += "Content-Length: \(body.count)\r\nConnection: close\r\n\r\n"
        return Data(head.utf8) + body
    }
}

/// Loopback-only HTTP API (127.0.0.1:<tracker.port>) for the CLI, curl, the hub and local tools.
final class CommandServer: @unchecked Sendable {
    let port: UInt16
    static let maxRequestBytes = 16 << 20
    static let requestTimeout: TimeInterval = 10
    private let handler: @Sendable (HTTPRequest) async -> HTTPResponse
    private var listener: NWListener?
    private let queue = DispatchQueue(label: "orbital.tracker.http")
    var onFailure: (@Sendable (String) -> Void)?

    init(port: UInt16, handler: @escaping @Sendable (HTTPRequest) async -> HTTPResponse) {
        self.port = port
        self.handler = handler
    }

    func start() throws {
        let params = NWParameters.tcp
        params.allowLocalEndpointReuse = true
        params.requiredInterfaceType = .loopback
        params.requiredLocalEndpoint = NWEndpoint.hostPort(host: "127.0.0.1", port: NWEndpoint.Port(rawValue: port)!)
        let listener = try NWListener(using: params)
        listener.newConnectionHandler = { [weak self] conn in self?.accept(conn) }
        listener.stateUpdateHandler = { [weak self] state in
            if case .failed(let error) = state { self?.onFailure?("HTTP listener failed: \(error)") }
        }
        listener.start(queue: queue)
        self.listener = listener
    }

    func stop() { listener?.cancel() }

    private func accept(_ conn: NWConnection) {
        conn.start(queue: queue)
        // A client that never finishes its request doesn't hold the connection forever.
        queue.asyncAfter(deadline: .now() + Self.requestTimeout) { [weak conn] in
            guard let conn, conn.state != .cancelled else { return }
            conn.cancel()
        }
        receive(conn, buffer: Data())
    }

    private func receive(_ conn: NWConnection, buffer: Data) {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 1 << 20) { [weak self] data, _, isComplete, error in
            guard let self else { return }
            var buffer = buffer
            if let data { buffer.append(data) }
            if buffer.count > Self.maxRequestBytes {
                self.send(.json(413, ["ok": false, "error": "request too large"]), on: conn)
            } else if let request = HTTPRequest.parse(buffer) {
                if let rejection = Self.guardRequest(request) {
                    self.send(rejection, on: conn)
                } else {
                    Task {
                        let response = await self.handler(request)
                        self.send(response, on: conn)
                    }
                }
            } else if isComplete || error != nil {
                conn.cancel()
            } else {
                self.receive(conn, buffer: buffer)
            }
        }
    }

    /// Blocks browsers (any Origin header) and DNS-rebinding hosts; only local tools get in.
    static func guardRequest(_ req: HTTPRequest) -> HTTPResponse? {
        if req.headers["origin"] != nil {
            return .json(403, ["ok": false, "error": "browser requests are not accepted"])
        }
        if let host = req.headers["host"]?.lowercased() {
            let name: String
            if host.hasPrefix("[") { name = String(host.prefix { $0 != "]" }) + "]" }
            else { name = host.split(separator: ":").first.map(String.init) ?? host }
            if !["127.0.0.1", "localhost", "[::1]", "::1"].contains(name) {
                return .json(403, ["ok": false, "error": "bad host"])
            }
        }
        if req.method == "POST", !(req.headers["content-type"] ?? "").lowercased().contains("application/json") {
            return .json(415, ["ok": false, "error": "send Content-Type: application/json"])
        }
        return nil
    }

    private func send(_ response: HTTPResponse, on conn: NWConnection) {
        conn.send(content: response.serialized(), completion: .contentProcessed { _ in conn.cancel() })
    }
}

/// Watches the inbox folders for command files the CLI (or the hub, when this app wasn't running)
/// dropped there, and applies them in order.
@MainActor
final class InboxWatcher {
    private let dirs: [URL]
    private let failedDir: URL
    private let onCommand: (TrackerCommand) -> Void
    private let onBadFile: (String, Error) -> Void
    private var sources: [DispatchSourceFileSystemObject] = []
    private var timer: Timer?

    init(dirs: [URL], failedDir: URL, onCommand: @escaping (TrackerCommand) -> Void,
         onBadFile: @escaping (String, Error) -> Void) {
        self.dirs = dirs
        self.failedDir = failedDir
        self.onCommand = onCommand
        self.onBadFile = onBadFile
    }

    func start() {
        for dir in dirs {
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let fd = open(dir.path, O_EVTONLY)
            guard fd >= 0 else { continue }
            let source = DispatchSource.makeFileSystemObjectSource(fileDescriptor: fd, eventMask: .write, queue: .main)
            source.setEventHandler { [weak self] in
                MainActor.assumeIsolated { self?.scan(dir) }
            }
            source.setCancelHandler { close(fd) }
            source.resume()
            sources.append(source)
        }
        // Belt and braces: events can be missed while the Mac sleeps.
        timer = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.scanAll() }
        }
        scanAll()
    }

    func stop() {
        sources.forEach { $0.cancel() }
        sources.removeAll()
        timer?.invalidate()
    }

    func scanAll() { dirs.forEach(scan) }

    private func scan(_ dir: URL) {
        // Only folders this user owns: another account could create the shared /tmp fallback first.
        guard let owner = (try? FileManager.default.attributesOfItem(atPath: dir.path))?[.ownerAccountID] as? NSNumber,
              owner.uint32Value == getuid() else { return }
        for url in Inbox.pending(in: dir) {
            guard let data = try? Data(contentsOf: url) else { continue }
            do {
                let command = try TrackerJSON.decoder().decode(TrackerCommand.self, from: data)
                try? FileManager.default.removeItem(at: url)
                onCommand(command)
            } catch {
                try? FileManager.default.createDirectory(at: failedDir, withIntermediateDirectories: true)
                let dest = failedDir.appendingPathComponent(url.lastPathComponent)
                try? FileManager.default.removeItem(at: dest)
                if (try? FileManager.default.moveItem(at: url, to: dest)) == nil { try? FileManager.default.removeItem(at: url) }
                onBadFile(url.lastPathComponent, error)
            }
        }
    }
}
