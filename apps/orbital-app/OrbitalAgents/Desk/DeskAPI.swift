import Foundation

/// Hub calls for the Agent desk, its voices, and the tracker. Built per call from the store's
/// address and token, so a hub change or a new sign-in applies at once.
struct DeskAPI {
    let baseURL: URL
    let token: String?
    var deviceID: String?

    private static let streamSession: URLSession = {
        let config = URLSessionConfiguration.default
        // Starting a worker can take a while; a fast reply is under a second.
        config.timeoutIntervalForRequest = 120
        config.timeoutIntervalForResource = 300
        return URLSession(configuration: config)
    }()

    func url(_ path: String) -> URL? {
        if path.hasPrefix("http://") || path.hasPrefix("https://") { return URL(string: path) }
        let trimmed = path.hasPrefix("/") ? path : "/" + path
        return URL(string: baseURL.absoluteString + trimmed)
    }

    private func request(_ path: String, method: String = "GET", json: [String: Any]? = nil) throws -> URLRequest {
        guard let url = url(path) else { throw OrbitalAPIError.badURL }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let deviceID { request.setValue(deviceID, forHTTPHeaderField: "X-Orbital-Device") }
        if let json {
            request.httpBody = try JSONSerialization.data(withJSONObject: json)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return request
    }

    private func data(_ request: URLRequest) async throws -> Data {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw OrbitalAPIError.http(-1, "No response") }
        if http.statusCode == 401 { throw OrbitalAPIError.unauthorized }
        guard (200 ... 299).contains(http.statusCode) else {
            throw OrbitalAPIError.http(http.statusCode, String(data: data, encoding: .utf8) ?? "")
        }
        return data
    }

    private func decode<T: Decodable>(_ type: T.Type, _ request: URLRequest) async throws -> T {
        try JSONDecoder().decode(T.self, from: try await data(request))
    }

    // MARK: Desk

    func snapshot(limit: Int = 80) async throws -> DeskSnapshot {
        #if DEBUG
        if OrbitalPreview.isActive { return try preview(DeskSnapshot.self, "desk.json") }
        #endif
        return try await decode(DeskSnapshot.self, request("/v1/desk?limit=\(limit)"))
    }

    func older(before id: String, limit: Int = 60) async throws -> [DeskMessage] {
        struct Box: Decodable { var messages: [DeskMessage] }
        let encoded = id.addingPercentEncoding(withAllowedCharacters: .urlQueryValueAllowed) ?? id
        return try await decode(Box.self, request("/v1/desk/messages?before=\(encoded)&limit=\(limit)")).messages
    }

    func warm() async {
        #if DEBUG
        if OrbitalPreview.isActive { return }
        #endif
        _ = try? await data(request("/v1/desk/warm", method: "POST", json: [:]))
    }

    /// Start the desk model's conversation over. Running work and the thread stay.
    func clearContext() async throws -> DeskMessage {
        struct Box: Decodable { var message: DeskMessage }
        return try await decode(Box.self, request("/v1/desk/clear", method: "POST", json: [:])).message
    }

    func cancel(task id: String) async throws -> DeskTask {
        try await decode(DeskTask.self, request("/v1/desk/tasks/\(OrbitalAPI.escape(id))/cancel", method: "POST", json: [:]))
    }

    /// One turn. Yields each NDJSON event as it arrives: user, start, delta, said, reply, event, done.
    /// `interrupt` is set when you talked over (or before) the last reply: which reply, and the words
    /// of it you actually heard, so the desk can pick its point back up.
    func say(text: String, source: String, clientID: String, interrupt: DeskInterrupt? = nil) -> AsyncThrowingStream<[String: Any], Error> {
        AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var body: [String: Any] = ["text": text, "source": source, "client_id": clientID]
                    // So the hub can stamp desk-started tracker items with iOS vs Mac origin.
                    #if targetEnvironment(macCatalyst) || os(macOS)
                    body["platform"] = "mac"
                    #else
                    body["platform"] = "ios"
                    #endif
                    if let interrupt {
                        var cut: [String: Any] = ["heard": interrupt.heard]
                        if let replyID = interrupt.replyID { cut["reply_id"] = replyID }
                        body["interrupt"] = cut
                    }
                    let req = try request("/v1/desk/say", method: "POST", json: body)
                    let (bytes, response) = try await Self.streamSession.bytes(for: req)
                    guard let http = response as? HTTPURLResponse else { throw OrbitalAPIError.http(-1, "No response") }
                    if http.statusCode == 401 { throw OrbitalAPIError.unauthorized }
                    guard (200 ... 299).contains(http.statusCode) else {
                        var body = ""
                        for try await line in bytes.lines { body += line }
                        throw OrbitalAPIError.http(http.statusCode, body)
                    }
                    for try await line in bytes.lines {
                        guard let data = line.data(using: .utf8),
                              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
                        continuation.yield(obj)
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// Voice diagnostics lines for the hub's logs. Best effort.
    func voiceLog(_ lines: [String], device: String) async {
        #if DEBUG
        if OrbitalPreview.isActive { return }
        #endif
        guard let req = try? request("/v1/desk/voice-log", method: "POST", json: ["lines": lines, "device": device]) else { return }
        _ = try? await data(req)
    }

    // MARK: Voices and on-device speech

    /// What the hub can do for speech: Fish voices (its key, never the device's) and its voice list.
    func speechStatus() async throws -> SpeechStatus {
        #if DEBUG
        if OrbitalPreview.isActive { return (try? preview(SpeechStatus.self, "speech.json")) ?? SpeechStatus() }
        #endif
        return try await decode(SpeechStatus.self, request("/v1/speech/status"))
    }

    func speechModels() async throws -> [OnDeviceModelInfo] {
        struct Box: Decodable { var models: [OnDeviceModelInfo] }
        #if DEBUG
        if OrbitalPreview.isActive { return [] }
        #endif
        return try await decode(Box.self, request("/v1/stt/models")).models
    }

    /// One file of a model, for a download task (the hub answers Range requests).
    func speechModelFileRequest(model: String, path: String) throws -> URLRequest {
        let encoded = path.split(separator: "/").map {
            String($0).addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? String($0)
        }.joined(separator: "/")
        var req = try request("/v1/stt/models/\(OrbitalAPI.escape(model))/files/\(encoded)")
        req.setValue("application/octet-stream", forHTTPHeaderField: "Accept")
        return req
    }

    /// A Fish voice clip (MP3) made by the hub with its key: the device needs none.
    func fishSpeech(text: String, voiceID: String, speed: Double, model: String) async throws -> Data {
        var json: [String: Any] = ["text": text, "speed": speed]
        if !voiceID.isEmpty { json["reference_id"] = voiceID }
        if !model.isEmpty { json["model"] = model }
        var req = try request("/v1/speech/tts", method: "POST", json: json)
        req.setValue("audio/mpeg", forHTTPHeaderField: "Accept")
        req.timeoutInterval = 45
        let audio = try await data(req)
        guard !audio.isEmpty else { throw OrbitalAPIError.http(502, "no audio") }
        return audio
    }

    /// The same clip as 16-bit mono PCM, yielded while Fish is still making it (about 0.1 s of
    /// audio per piece). Throws before any audio if the hub or Fish refuses.
    func fishSpeechStream(text: String, voiceID: String, speed: Double, model: String) async throws -> (sampleRate: Double, chunks: AsyncThrowingStream<Data, Error>) {
        var json: [String: Any] = ["text": text, "speed": speed]
        if !voiceID.isEmpty { json["reference_id"] = voiceID }
        if !model.isEmpty { json["model"] = model }
        var req = try request("/v1/speech/tts/stream", method: "POST", json: json)
        req.setValue("audio/pcm", forHTTPHeaderField: "Accept")
        req.timeoutInterval = 20
        let (bytes, response) = try await Self.streamSession.bytes(for: req)
        guard let http = response as? HTTPURLResponse else { throw OrbitalAPIError.http(-1, "No response") }
        if http.statusCode == 401 { throw OrbitalAPIError.unauthorized }
        guard (200 ... 299).contains(http.statusCode) else {
            var body = ""
            for try await line in bytes.lines { body += line }
            throw OrbitalAPIError.http(http.statusCode, String(body.prefix(300)))
        }
        let rate = Double(http.value(forHTTPHeaderField: "X-Sample-Rate") ?? "") ?? 24000
        let piece = 4800
        let chunks = AsyncThrowingStream<Data, Error> { continuation in
            let task = Task {
                var pending = Data(capacity: piece)
                do {
                    for try await byte in bytes {
                        pending.append(byte)
                        if pending.count >= piece {
                            continuation.yield(pending)
                            pending = Data(capacity: piece)
                        }
                    }
                    if !pending.isEmpty { continuation.yield(pending) }
                    continuation.finish()
                } catch {
                    if !pending.isEmpty { continuation.yield(pending) }
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
        return (rate, chunks)
    }

    // MARK: Tracker

    /// One page of tracker items. `finishedOnly` is the history pane (`status=done`): running work stays out.
    /// Without it, items still being worked on come first and show in every range. `query` matches every word.
    func trackerPage(scope: TrackerScope, who: TrackerWho, query: String = "", limit: Int = 40, offset: Int = 0, finishedOnly: Bool = false) async throws -> TrackerPage {
        #if DEBUG
        if OrbitalPreview.isActive {
            var page = try preview(TrackerPage.self, "tracker.json")
            let words = query.lowercased().split(separator: " ")
            page.items = page.items.filter { item in
                let kept = finishedOnly ? !item.isActive : (scope != .active || item.isActive)
                let whoOK = who == .all || (who == .ai) == item.isAI
                return kept && whoOK
                    && words.allSatisfy { "\(item.title) \(item.project ?? "") \(item.lastNote ?? "") \(item.summary ?? "")".lowercased().contains($0) }
            }
            if finishedOnly {
                page.items.sort { ($0.completedAt ?? $0.updatedAt ?? "") > ($1.completedAt ?? $1.updatedAt ?? "") }
            }
            page.total = page.items.count
            page.done = page.items.filter { !$0.isActive }.count
            page.active = page.items.filter(\.isActive).count
            return page
        }
        #endif
        let status = finishedOnly ? "done" : "any"
        var path = "/v1/tracker/items?scope=\(scope.rawValue)&who=\(who.rawValue)&status=\(status)&limit=\(limit)&offset=\(offset)"
        let trimmed = query.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmed.isEmpty, let encoded = trimmed.addingPercentEncoding(withAllowedCharacters: .urlQueryValueAllowed) {
            path += "&q=\(encoded)"
        }
        return try await decode(TrackerPage.self, request(path))
    }

    func trackerItem(_ id: String) async throws -> TrackerDetail {
        #if DEBUG
        if OrbitalPreview.isActive { return try preview(TrackerDetail.self, "items/\(id).json") }
        #endif
        return try await decode(TrackerDetail.self, request("/v1/tracker/items/\(OrbitalAPI.escape(id))"))
    }

    /// The screenshot evidence level agents follow (shared with every Mac's Agent Satellite Tracker).
    func trackerSettings() async throws -> TrackerSettings {
        #if DEBUG
        if OrbitalPreview.isActive {
            return TrackerSettings(evidence: TrackerEvidence(rawValue: UserDefaults.standard.string(forKey: DeskState.Keys.evidence) ?? "") ?? .off)
        }
        #endif
        return try await decode(TrackerSettings.self, request("/v1/tracker/settings"))
    }

    func setTrackerEvidence(_ level: TrackerEvidence) async throws -> TrackerSettings {
        #if DEBUG
        if OrbitalPreview.isActive { return TrackerSettings(evidence: level, updatedBy: "app") }
        #endif
        return try await decode(TrackerSettings.self, request("/v1/tracker/settings", method: "POST", json: ["evidence": level.rawValue]))
    }

    // MARK: Agent settings (System → Settings)

    func agentSettings() async throws -> AgentSettingsResponse {
        #if DEBUG
        if OrbitalPreview.isActive { return try preview(AgentSettingsResponse.self, "settings.json") }
        #endif
        return try await decode(AgentSettingsResponse.self, request("/v1/desk/settings"))
    }

    /// Sends only the keys that changed; the hub answers with the whole settings.
    func saveAgentSettings(_ changes: [String: Any]) async throws -> AgentSettingsResponse {
        #if DEBUG
        if OrbitalPreview.isActive { return try preview(AgentSettingsResponse.self, "settings.json") }
        #endif
        return try await decode(AgentSettingsResponse.self, request("/v1/desk/settings", method: "PUT", json: changes))
    }

    #if DEBUG
    private func preview<T: Decodable>(_ type: T.Type, _ name: String) throws -> T {
        guard let data = OrbitalPreview.deskData(name) else { throw OrbitalAPIError.http(404, "{\"error\": \"no preview \(name)\"}") }
        return try JSONDecoder().decode(T.self, from: data)
    }
    #endif
}

/// Decodes one live-socket or stream payload into a model.
enum DeskDecode {
    static func value<T: Decodable>(_ type: T.Type, from raw: Any?) -> T? {
        guard let raw, !(raw is NSNull), JSONSerialization.isValidJSONObject(raw) || raw is [String: Any],
              let data = try? JSONSerialization.data(withJSONObject: raw) else { return nil }
        return try? JSONDecoder().decode(T.self, from: data)
    }
}

extension CharacterSet {
    /// A query value: `urlQueryAllowed` without the characters that split or end a query.
    static let urlQueryValueAllowed = CharacterSet.urlQueryAllowed.subtracting(CharacterSet(charactersIn: "&+=?#"))
}
