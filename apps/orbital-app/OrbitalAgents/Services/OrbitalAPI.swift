import Foundation

enum OrbitalAPIError: LocalizedError {
    case badURL
    case unauthorized
    case http(Int, String)
    /// Not an Orbital Agents hub at that address (another server answered).
    case notAHub

    var errorDescription: String? {
        switch self {
        case .badURL: return "That hub address is not a valid URL."
        case .unauthorized: return "That username or password was not accepted."
        case .notAHub: return "Something answered at that address, but it is not an Orbital Agents hub. Check the port (the hub uses 7700 unless you changed hub.port)."
        case .http(let code, _): return "The hub returned \(code): \(shortReason)."
        }
    }

    /// One line for a toast: the hub's `error` text when it sent one, else a short description.
    var shortReason: String {
        switch self {
        case .badURL: return "bad hub address"
        case .unauthorized: return "signed out"
        case .notAHub: return "not an Orbital Agents hub"
        case .http(let code, let body):
            if let message = Self.message(in: body) { return message }
            return code < 0 ? "no response" : "hub returned \(code)"
        }
    }

    /// The machine-readable `code` the hub sent, if any ("desk_not_configured", "agent_unavailable", …).
    var code: String? {
        guard case .http(_, let body) = self, let data = body.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        return obj["code"] as? String
    }

    var status: Int? {
        if case .http(let code, _) = self { return code }
        if case .unauthorized = self { return 401 }
        return nil
    }

    static func message(in body: String) -> String? {
        guard let data = body.data(using: .utf8),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
        for key in ["error", "detail", "message"] {
            if let text = obj[key] as? String, !text.isEmpty { return text }
            if let nested = obj[key] as? [String: Any], let text = nested["message"] as? String, !text.isEmpty { return text }
        }
        return nil
    }

    /// Plain words for a failed sign-in or connection, saying what to check.
    static func friendly(_ error: Error) -> String {
        if let api = error as? OrbitalAPIError {
            switch api {
            case .unauthorized: return "Wrong username or password. They are ORBITAL_HUB_USERNAME and ORBITAL_HUB_PASSWORD in ~/.orbital-agents/secrets.env on the hub."
            case .http(403, _): return "The hub refused this device's network. It only answers loopback and Tailscale addresses (hub.allow_networks)."
            default: return api.errorDescription ?? api.shortReason
            }
        }
        if let url = error as? URLError {
            switch url.code {
            case .cannotConnectToHost, .cannotFindHost, .dnsLookupFailed, .timedOut, .networkConnectionLost, .notConnectedToInternet:
                return "Can't reach the hub. Is Tailscale on, on this device and the hub, and is the hub running? (orbital status on the hub)"
            case .appTransportSecurityRequiresSecureConnection:
                return "This build only allows plain http to Tailscale and local addresses. Use the hub's Tailscale name or 100.x address."
            case .secureConnectionFailed, .serverCertificateUntrusted, .serverCertificateHasBadDate:
                return "The hub's HTTPS certificate was refused. Try http:// with port 7700."
            default:
                return url.localizedDescription
            }
        }
        return error.localizedDescription
    }
}

/// The hub's HTTP API. Contract: docs/ARCHITECTURE.md (Hub HTTP API) in the repo.
@MainActor
final class OrbitalAPI {
    var baseURL: URL
    var token: String?
    /// Sent as X-Orbital-Device so the hub can record which device posted an action.
    var deviceID: String?
    private let decoder = JSONDecoder()
    private let encoder = JSONEncoder()

    init(baseURL: URL, token: String? = nil) {
        self.baseURL = baseURL
        self.token = token
    }

    // MARK: Session

    /// `GET /v1/hub/info`: no auth. Checks the address points at an Orbital Agents hub.
    func hubInfo(timeout: TimeInterval = 6) async throws -> HubInfo {
        let data = try await send(url: url("/v1/hub/info"), authed: false, timeout: timeout)
        let info = try decoder.decode(HubInfo.self, from: data)
        guard info.isOrbitalHub else { throw OrbitalAPIError.notAHub }
        return info
    }

    func login(username: String, password: String) async throws -> String {
        let body = try JSONSerialization.data(withJSONObject: ["username": username, "password": password])
        let data = try await send("/v1/login", method: "POST", body: body, authed: false)
        let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard let token = (obj?["token"] ?? obj?["session_token"] ?? obj?["access_token"]) as? String, !token.isEmpty else {
            throw OrbitalAPIError.unauthorized
        }
        self.token = token
        return token
    }

    /// Extra bottom-bar items and the sidebar tree (`orbital.nav.v1`).
    func nav() async throws -> NavDoc {
        let data = try await send("/v1/nav")
        return NavDoc(json: (try JSONSerialization.jsonObject(with: data) as? [String: Any]) ?? [:])
    }

    /// Signs in with a one-time code from an orbital://connect link (`orbital pair` on the hub).
    func pair(code: String) async throws -> (token: String, username: String?) {
        let body = try JSONSerialization.data(withJSONObject: ["code": code])
        let data = try await send("/v1/pair", method: "POST", body: body, authed: false)
        let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard let token = obj?["token"] as? String, !token.isEmpty else { throw OrbitalAPIError.unauthorized }
        self.token = token
        return (token, obj?["username"] as? String)
    }

    func logout() async {
        _ = try? await send("/v1/logout", method: "POST", body: Data("{}".utf8))
    }

    // MARK: Sections

    func catalog() async throws -> CatalogResponse {
        try decoder.decode(CatalogResponse.self, from: try await send("/v1/catalog"))
    }

    func section(_ id: String) async throws -> SectionDocument {
        let data = try await send("/v1/sections/\(Self.escape(id))")
        return try decoder.decode(SectionDocument.self, from: data)
    }

    func sync(known: [String: Int]) async throws -> SyncResponse {
        let knownString = known.map { "\($0.key):\($0.value)" }.sorted().joined(separator: ",")
        var components = URLComponents(url: try url("/v1/sync"), resolvingAgainstBaseURL: false)
        components?.queryItems = [URLQueryItem(name: "known", value: knownString)]
        guard let url = components?.url else { throw OrbitalAPIError.badURL }
        let data = try await send(url: url)
        return try decoder.decode(SyncResponse.self, from: data)
    }

    func putSection(_ document: SectionDocument) async throws -> SectionDocument {
        let body = try encoder.encode(document)
        let data = try await send("/v1/sections/\(Self.escape(document.id))", method: "PUT", body: body)
        if let saved = try? decoder.decode(SectionDocument.self, from: data) { return saved }
        // A hub that answers {ok, section} instead of the document.
        let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard let section = obj?["section"] else { return document }
        return try decoder.decode(SectionDocument.self, from: JSONSerialization.data(withJSONObject: section))
    }

    func deleteSection(_ id: String) async throws {
        _ = try await send("/v1/sections/\(Self.escape(id))", method: "DELETE")
    }

    func ops(sectionID: String, body: [String: Any]) async throws -> SectionDocument? {
        let payload = try JSONSerialization.data(withJSONObject: body)
        let data = try await send("/v1/sections/\(Self.escape(sectionID))/ops", method: "POST", body: payload)
        let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard let section = obj?["section"], !(section is NSNull) else { return nil }
        let sectionData = try JSONSerialization.data(withJSONObject: section)
        return try decoder.decode(SectionDocument.self, from: sectionData)
    }

    /// Posts one write-back action. Returns the patched section when the hub sent one, and the whole response.
    func postAction(sectionID: String, body: [String: Any]) async throws -> (SectionDocument?, [String: Any]) {
        var payload = body
        payload["schema"] = OrbitalSchema.action
        let data = try JSONSerialization.data(withJSONObject: payload)
        let response = try await send("/v1/sections/\(Self.escape(sectionID))/actions", method: "POST", body: data)
        let obj = (try JSONSerialization.jsonObject(with: response) as? [String: Any]) ?? [:]
        guard let section = obj["section"], !(section is NSNull),
              let sectionData = try? JSONSerialization.data(withJSONObject: section)
        else { return (nil, obj) }
        return (try? decoder.decode(SectionDocument.self, from: sectionData), obj)
    }

    // MARK: Alerts and prefs

    func ack(ids: [String]) async throws {
        let body = try JSONSerialization.data(withJSONObject: ["ids": ids])
        _ = try await send("/v1/alerts/ack", method: "POST", body: body)
    }

    /// Saves shared settings for every device. NSNull removes a key. Returns every pref after the change.
    func setPrefs(_ changes: [String: Any]) async throws -> SharedPrefs {
        let body = try JSONSerialization.data(withJSONObject: ["prefs": changes])
        let data = try await send("/v1/prefs", method: "POST", body: body)
        struct Box: Decodable { var prefs: SharedPrefs }
        if let boxed = try? decoder.decode(Box.self, from: data) { return boxed.prefs }
        return try decoder.decode(SharedPrefs.self, from: data)
    }

    /// Registers this device (and its Apple push token, when it has one). Returns whether the hub sends pushes.
    func registerDevice(id: String, name: String, platform: String, apnsToken: String?) async throws -> Bool {
        var body: [String: Any] = ["device_id": id, "name": name, "platform": platform]
        if let apnsToken { body["apns_token"] = apnsToken }
        let data = try await send("/v1/devices", method: "POST", body: try JSONSerialization.data(withJSONObject: body))
        let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any]
        if let flag = obj?["apns"] as? Bool { return flag }
        return (obj?["apns"] as? [String: Any])?["enabled"] as? Bool ?? false
    }

    /// `GET /v1/health`: no auth.
    func health() async throws -> HubHealth {
        try decoder.decode(HubHealth.self, from: try await send(url: url("/v1/health"), authed: false, timeout: 6))
    }

    // MARK: Scheduler

    func jobs() async throws -> JobsSnapshot {
        try decoder.decode(JobsSnapshot.self, from: try await send("/v1/jobs"))
    }

    /// Plans, the work that used the most, and agent automations' totals.
    func usage() async throws -> UsageReport {
        try decoder.decode(UsageReport.self, from: try await send("/v1/usage"))
    }

    func runJob(_ id: String) async throws {
        _ = try await send("/v1/jobs/\(Self.escape(id))/run", method: "POST", body: Data("{}".utf8))
    }

    /// Every job, one group's jobs, or every job that writes one page. Lanes run side by side, in stages.
    func runAll(group: String? = nil, sectionID: String? = nil, nodeID: String? = nil) async throws -> [String] {
        var body: [String: Any] = [:]
        if let group { body["group"] = group }
        if let sectionID { body["section_id"] = sectionID }
        if let nodeID { body["node_id"] = nodeID }
        let data = try await send("/v1/jobs/run-all", method: "POST", body: try JSONSerialization.data(withJSONObject: body))
        let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        return obj?["queued"] as? [String] ?? []
    }

    func jobSettings(_ id: String, every: Int? = nil, resetEvery: Bool = false, paused: Bool? = nil) async throws {
        var body: [String: Any] = [:]
        if let every { body["every_s"] = every }
        if resetEvery { body["every_s"] = NSNull() }
        if let paused { body["paused"] = paused }
        _ = try await send("/v1/jobs/\(Self.escape(id))/settings", method: "POST", body: try JSONSerialization.data(withJSONObject: body))
    }

    func cancelJob(_ id: String) async throws {
        _ = try await send("/v1/jobs/\(Self.escape(id))/cancel", method: "POST", body: Data("{}".utf8))
    }

    func jobLog(_ id: String) async throws -> [String] {
        Self.logLines(try await send("/v1/jobs/\(Self.escape(id))/log"))
    }

    // MARK: Satellites and agents

    func joinWindow() async throws -> JoinWindowStatus {
        try decoder.decode(JoinWindowStatus.self, from: try await send("/v1/satellites/invite"))
    }

    /// Opens joining: for `minutes`, machines on your Tailscale account connect without a token.
    func openJoinWindow(minutes: Int = 15) async throws -> JoinWindowStatus {
        let body = try JSONSerialization.data(withJSONObject: ["minutes": minutes, "by": "app"])
        return try decoder.decode(JoinWindowStatus.self, from: try await send("/v1/satellites/invite", method: "POST", body: body))
    }

    func closeJoinWindow() async throws -> JoinWindowStatus {
        try decoder.decode(JoinWindowStatus.self, from: try await send("/v1/satellites/invite", method: "DELETE"))
    }

    func satellites() async throws -> [Satellite] {
        try decoder.decode(SatelliteList.self, from: try await send("/v1/satellites")).satellites
    }

    func removeSatellite(_ id: String) async throws {
        _ = try await send("/v1/satellites/\(Self.escape(id))", method: "DELETE")
    }

    func agentJobs() async throws -> [AgentJob] {
        try decoder.decode(AgentJobList.self, from: try await send("/v1/agents/jobs")).jobs
    }

    /// Starts an agent on one machine. Returns the job id the satellite gave it.
    func runAgent(_ request: AgentRunRequest) async throws -> String {
        let body = try JSONSerialization.data(withJSONObject: request.body)
        let data = try await send("/v1/satellites/\(Self.escape(request.satelliteID))/agents/run", method: "POST", body: body)
        let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        guard let id = (obj?["job_id"] ?? obj?["id"]) as? String ?? (obj?["job"] as? [String: Any])?["job_id"] as? String else {
            throw OrbitalAPIError.http(502, "{\"error\": \"the satellite did not return a job id\"}")
        }
        return id
    }

    func agentJob(satellite: String, job: String) async throws -> AgentJob {
        let data = try await send("/v1/satellites/\(Self.escape(satellite))/agents/jobs/\(Self.escape(job))")
        if let job = try? decoder.decode(AgentJob.self, from: data) { return job }
        struct Box: Decodable { var job: AgentJob }
        return try decoder.decode(Box.self, from: data).job
    }

    func agentJobLog(satellite: String, job: String) async throws -> [String] {
        Self.logLines(try await send("/v1/satellites/\(Self.escape(satellite))/agents/jobs/\(Self.escape(job))/log"))
    }

    func cancelAgentJob(satellite: String, job: String) async throws {
        _ = try await send("/v1/satellites/\(Self.escape(satellite))/agents/jobs/\(Self.escape(job))/cancel", method: "POST", body: Data("{}".utf8))
    }

    // MARK: Transport

    /// A log as lines, whether the hub sent `{lines: [...]}`, `{log: "..."}`, `{output: "..."}`, or plain text.
    static func logLines(_ data: Data) -> [String] {
        if let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            if let lines = obj["lines"] as? [String] { return lines }
            for key in ["log", "output", "text", "output_tail"] {
                if let text = obj[key] as? String { return text.components(separatedBy: "\n") }
            }
            return []
        }
        return (String(data: data, encoding: .utf8) ?? "").components(separatedBy: "\n")
    }

    nonisolated static func escape(_ id: String) -> String {
        id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed.subtracting(CharacterSet(charactersIn: "/"))) ?? id
    }

    private func send(_ path: String, method: String = "GET", body: Data? = nil, authed: Bool = true) async throws -> Data {
        try await send(url: url(path), method: method, body: body, authed: authed)
    }

    private func send(url: URL, method: String = "GET", body: Data? = nil, authed: Bool = true, timeout: TimeInterval = 30) async throws -> Data {
        var request = URLRequest(url: url, timeoutInterval: timeout)
        request.httpMethod = method
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        if let body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        if authed, let token, !token.isEmpty {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let deviceID, !deviceID.isEmpty {
            request.setValue(deviceID, forHTTPHeaderField: "X-Orbital-Device")
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else {
            throw OrbitalAPIError.http(-1, "No response")
        }
        if http.statusCode == 401 { throw OrbitalAPIError.unauthorized }
        guard (200 ... 299).contains(http.statusCode) else {
            let text = String(data: data, encoding: .utf8) ?? ""
            throw OrbitalAPIError.http(http.statusCode, text)
        }
        return data
    }

    private func url(_ path: String) throws -> URL {
        let trimmed = path.hasPrefix("/") ? path : "/" + path
        guard let url = URL(string: baseURL.absoluteString + trimmed) else {
            throw OrbitalAPIError.badURL
        }
        return url
    }
}
