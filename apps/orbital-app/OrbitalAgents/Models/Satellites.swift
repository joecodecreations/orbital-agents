import Foundation
import SwiftUI

// Machines that joined the hub (`GET /v1/satellites`) and the agent jobs they run
// (`GET /v1/agents/jobs`). Every machine runs the satellite service, the hub included, so "run an agent
// on machine X" is the same call for all of them. Decoding is lenient: field names follow the hub's
// contract, with the obvious alternatives accepted so an older or newer hub still draws.

/// One agent CLI a machine can start (claude, codex, grok, gemini, …).
struct SatelliteAgent: Codable, Equatable, Identifiable, Hashable {
    var name: String
    var available: Bool
    var label: String?

    var id: String { name }

    init(name: String, available: Bool = true, label: String? = nil) {
        self.name = name
        self.available = available
        self.label = label
    }

    enum CodingKeys: String, CodingKey { case name, available, label }

    init(from decoder: Decoder) throws {
        if let single = try? decoder.singleValueContainer(), let name = try? single.decode(String.self) {
            self.init(name: name)
            return
        }
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let name = Insights.nonEmpty(c.lenientString(.name)) else {
            throw DecodingError.keyNotFound(CodingKeys.name, .init(codingPath: c.codingPath, debugDescription: "agent name"))
        }
        self.init(name: name, available: c.lenientBool(.available) ?? true, label: c.lenientString(.label))
    }

    var title: String { label ?? AgentStyle.displayName(name) }
}

struct SatelliteCapabilities: Codable, Equatable {
    var agents: [SatelliteAgent]
    var browser: Bool
    var browserPort: Int?
    var tracker: Bool

    enum CodingKeys: String, CodingKey {
        case agents, browser, tracker
        case browserPort = "browser_port"
    }

    init(agents: [SatelliteAgent] = [], browser: Bool = false, browserPort: Int? = nil, tracker: Bool = false) {
        self.agents = agents
        self.browser = browser
        self.browserPort = browserPort
        self.tracker = tracker
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        agents = c.lossy(SatelliteAgent.self, .agents)
        browser = c.lenientBool(.browser) ?? false
        browserPort = c.lenientInt(.browserPort)
        tracker = c.lenientBool(.tracker) ?? false
    }
}

struct SystemMetrics: Codable, Equatable {
    var cpus: Int?
    var load: [Double]?
    var memory: MemoryMetrics?
    var disks: [DiskMetrics]?
    var uptimeSeconds: Double?
    var time: Double?

    struct MemoryMetrics: Codable, Equatable {
        var total: Int64?
        var used: Int64?
        var percent: Double?
    }

    struct DiskMetrics: Codable, Equatable {
        var mount: String?
        var total: Int64?
        var used: Int64?
        var free: Int64?
        var percent: Double?
    }

    enum CodingKeys: String, CodingKey {
        case cpus, load, memory, disks, time
        case uptimeSeconds = "uptime_s"
    }

    var memoryPercent: Double? { memory?.percent }

    var loadAverage: Double? { load?.first }

    var rootDisk: DiskMetrics? { disks?.first }

    var uptimeFormatted: String? {
        guard let s = uptimeSeconds, s > 0 else { return nil }
        let days = Int(s) / 86400
        let hours = (Int(s) % 86400) / 3600
        if days > 0 { return "\(days)d \(hours)h" }
        let mins = (Int(s) % 3600) / 60
        return hours > 0 ? "\(hours)h \(mins)m" : "\(mins)m"
    }

    static func formatBytes(_ bytes: Int64?) -> String? {
        guard let bytes, bytes > 0 else { return nil }
        let gb = Double(bytes) / 1_000_000_000
        return gb >= 10 ? String(format: "%.0f GB", gb) : String(format: "%.1f GB", gb)
    }
}

/// The machine's Chrome bridge (`browser_status` in its heartbeat).
struct BrowserStatus: Codable, Equatable {
    var state: String
    var bridgeUp: Bool?
    var extensionConnected: Bool?
    var extensionVersion: String?
    var lastPollAgeSeconds: Double?
    var bind: String?
    var hubCanDrive: Bool?

    enum CodingKeys: String, CodingKey {
        case state, bind
        case bridgeUp = "bridge_up"
        case extensionConnected = "extension_connected"
        case extensionVersion = "extension_version"
        case lastPollAgeSeconds = "last_poll_age_s"
        case hubCanDrive = "hub_can_drive"
    }

    var label: String {
        switch state {
        case "connected": return "Chrome connected"
        case "no_extension": return "Extension not connected"
        case "bridge_down": return "Bridge not running"
        default: return "Browser control off"
        }
    }

    var shortLabel: String {
        switch state {
        case "connected": return "Browser"
        case "no_extension": return "No extension"
        case "bridge_down": return "Bridge down"
        default: return "Browser off"
        }
    }

    var symbol: String {
        switch state {
        case "connected": return "globe"
        case "no_extension": return "puzzlepiece.extension"
        case "bridge_down": return "exclamationmark.triangle.fill"
        default: return "globe.badge.chevron.backward"
        }
    }

    var tint: Color {
        switch state {
        case "connected": return hubCanDrive == false ? OrbitalTheme.warning : OrbitalTheme.success
        case "no_extension", "bridge_down": return OrbitalTheme.warning
        default: return OrbitalTheme.textTertiary
        }
    }

    var isProblem: Bool { state == "no_extension" || state == "bridge_down" }
}

/// One thing wrong with a machine, for its card and Home's "Needs you".
struct MachineIssue: Equatable, Identifiable {
    var id: String
    var text: String
    var symbol: String
    var danger: Bool
}

struct Satellite: Codable, Equatable, Identifiable {
    var id: String
    var name: String
    var machine: String?
    var host: String?
    var ip: String?
    var port: Int?
    var os: String?
    var version: String?
    var status: String
    var lastSeen: String?
    var joinedAt: String?
    var isHub: Bool
    var capabilities: SatelliteCapabilities
    /// Jobs running there now, from its last heartbeat.
    var runningJobs: Int?
    /// CPU, memory, disk from the satellite's last heartbeat.
    var systemMetrics: SystemMetrics?
    var browserStatus: BrowserStatus?
    var reachability: String?
    var heartbeatSeconds: Int?

    enum CodingKeys: String, CodingKey {
        case browserStatus = "browser_status"
        case reachability
        case heartbeatSeconds = "heartbeat_seconds"
        case id, name, machine, host, ip, port, os, version, status, capabilities, online, role
        case satelliteID = "satellite_id"
        case lastSeen = "last_seen"
        case joinedAt = "joined_at"
        case isHub = "is_hub"
        case runningJobs = "running_jobs"
        case systemMetrics = "system_metrics"
    }

    init(id: String, name: String, machine: String? = nil, host: String? = nil, ip: String? = nil, port: Int? = nil,
         os: String? = nil, version: String? = nil, status: String = "online", lastSeen: String? = nil, joinedAt: String? = nil,
         isHub: Bool = false, capabilities: SatelliteCapabilities = SatelliteCapabilities(), runningJobs: Int? = nil,
         systemMetrics: SystemMetrics? = nil) {
        self.id = id
        self.name = name
        self.machine = machine
        self.host = host
        self.ip = ip
        self.port = port
        self.os = os
        self.version = version
        self.status = status
        self.lastSeen = lastSeen
        self.joinedAt = joinedAt
        self.isHub = isHub
        self.capabilities = capabilities
        self.runningJobs = runningJobs
        self.systemMetrics = systemMetrics
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        let machine = Insights.nonEmpty(c.lenientString(.machine))
        let name = Insights.nonEmpty(c.lenientString(.name)) ?? machine
        guard let id = Insights.nonEmpty(c.lenientString(.id)) ?? Insights.nonEmpty(c.lenientString(.satelliteID)) ?? name else {
            throw DecodingError.keyNotFound(CodingKeys.id, .init(codingPath: c.codingPath, debugDescription: "satellite id"))
        }
        self.id = id
        self.name = name ?? id
        self.machine = machine
        host = c.lenientString(.host)
        ip = c.lenientString(.ip)
        port = c.lenientInt(.port)
        os = c.lenientString(.os)
        version = c.lenientString(.version)
        if let status = Insights.nonEmpty(c.lenientString(.status)) {
            self.status = status.lowercased()
        } else if let online = c.lenientBool(.online) {
            self.status = online ? "online" : "offline"
        } else {
            self.status = "unknown"
        }
        lastSeen = c.lenientString(.lastSeen)
        joinedAt = c.lenientString(.joinedAt)
        isHub = c.lenientBool(.isHub) ?? (c.lenientString(.role) == "hub")
        capabilities = (try? c.decodeIfPresent(SatelliteCapabilities.self, forKey: .capabilities)) ?? SatelliteCapabilities()
        if let count = c.lenientInt(.runningJobs) {
            runningJobs = count
        } else if let list = try? c.decodeIfPresent([AnyCodable].self, forKey: .runningJobs) {
            runningJobs = list.count
        } else {
            runningJobs = nil
        }
        systemMetrics = try? c.decodeIfPresent(SystemMetrics.self, forKey: .systemMetrics)
        browserStatus = try? c.decodeIfPresent(BrowserStatus.self, forKey: .browserStatus)
        reachability = c.lenientString(.reachability)
        heartbeatSeconds = c.lenientInt(.heartbeatSeconds)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(name, forKey: .name)
        try c.encodeIfPresent(machine, forKey: .machine)
        try c.encodeIfPresent(host, forKey: .host)
        try c.encodeIfPresent(ip, forKey: .ip)
        try c.encodeIfPresent(port, forKey: .port)
        try c.encodeIfPresent(os, forKey: .os)
        try c.encodeIfPresent(version, forKey: .version)
        try c.encode(status, forKey: .status)
        try c.encodeIfPresent(lastSeen, forKey: .lastSeen)
        try c.encodeIfPresent(joinedAt, forKey: .joinedAt)
        try c.encode(isHub, forKey: .isHub)
        try c.encode(capabilities, forKey: .capabilities)
        try c.encodeIfPresent(runningJobs, forKey: .runningJobs)
        try c.encodeIfPresent(systemMetrics, forKey: .systemMetrics)
        try c.encodeIfPresent(browserStatus, forKey: .browserStatus)
        try c.encodeIfPresent(reachability, forKey: .reachability)
        try c.encodeIfPresent(heartbeatSeconds, forKey: .heartbeatSeconds)
    }

    var isOnline: Bool { status == "online" }

    /// "Tailscale" (the hub dials it) or "Tunnel" (it dials the hub).
    var connectionLabel: String { reachability == "pull" ? "Tunnel (machine connects out)" : "Tailscale (hub connects in)" }

    /// What needs a look, worst first. Empty = healthy.
    func issues(now: Date = Date()) -> [MachineIssue] {
        guard isOnline else {
            return [MachineIssue(id: "offline", text: "Offline · last seen \(OrbitalDates.relative(lastSeen) ?? "never")", symbol: "wifi.slash", danger: true)]
        }
        var out: [MachineIssue] = []
        if let seen = lastSeenDate, now.timeIntervalSince(seen) > Double(2 * (heartbeatSeconds ?? 60)) {
            out.append(MachineIssue(id: "late", text: "Heartbeat late (\(OrbitalDates.relative(lastSeen) ?? "?"))", symbol: "waveform.path.ecg", danger: false))
        }
        for disk in systemMetrics?.disks ?? [] {
            guard let pct = disk.percent, pct >= 90 else { continue }
            let free = SystemMetrics.formatBytes(disk.free).map { ", \($0) free" } ?? ""
            out.append(MachineIssue(id: "disk-\(disk.mount ?? "/")", text: "Disk \(disk.mount ?? "/") \(Int(pct))% full\(free)", symbol: "internaldrive", danger: pct >= 95))
        }
        if let mem = systemMetrics?.memoryPercent, mem >= 92 {
            out.append(MachineIssue(id: "memory", text: "Memory \(Int(mem))% used", symbol: "memorychip", danger: false))
        }
        if let load = systemMetrics?.loadAverage, let cpus = systemMetrics?.cpus, load > Double(cpus) * 1.5 {
            out.append(MachineIssue(id: "cpu", text: String(format: "CPU overloaded (load %.1f on %d cores)", load, cpus), symbol: "cpu", danger: false))
        }
        if capabilities.browser, let browser = browserStatus, browser.isProblem {
            out.append(MachineIssue(id: "browser", text: browser.label, symbol: browser.symbol, danger: false))
        }
        return out.sorted { $0.danger && !$1.danger }
    }

    /// The agents this machine can start right now.
    var availableAgents: [SatelliteAgent] { capabilities.agents.filter(\.available) }

    var lastSeenDate: Date? { OrbitalDates.parse(lastSeen) }

    var displayName: String { machine.map { $0 == name ? name : "\(name)" } ?? name }

    /// SF Symbol for the machine, from its OS string.
    var symbol: String {
        let text = (os ?? "").lowercased()
        if text.contains("mac") || text.contains("darwin") { return "laptopcomputer" }
        if text.contains("windows") { return "pc" }
        if text.contains("linux") { return "server.rack" }
        return "desktopcomputer"
    }

    var osLabel: String? {
        guard let os, !os.isEmpty else { return nil }
        let lower = os.lowercased()
        if lower == "darwin" || lower.hasPrefix("darwin ") { return "macOS" }
        return os
    }
}

/// `{satellites: [...]}` or a bare list.
struct SatelliteList: Decodable {
    var satellites: [Satellite]

    enum CodingKeys: String, CodingKey { case satellites, items }

    init(from decoder: Decoder) throws {
        if var list = try? decoder.unkeyedContainer() {
            var rows: [Satellite] = []
            while !list.isAtEnd {
                if let row = try? list.decode(Satellite.self) { rows.append(row) } else { _ = try? list.decode(AnyCodable.self) }
            }
            satellites = rows
            return
        }
        let c = try decoder.container(keyedBy: CodingKeys.self)
        satellites = c.contains(.satellites) ? c.lossy(Satellite.self, .satellites) : c.lossy(Satellite.self, .items)
    }
}

/// One agent run on one machine.
struct AgentJob: Codable, Equatable, Identifiable {
    var id: String
    var satelliteID: String?
    var machine: String?
    var agent: String
    var title: String?
    var prompt: String?
    var cwd: String?
    var status: String
    var exitCode: Int?
    var createdAt: String?
    var startedAt: String?
    var finishedAt: String?
    var outputTail: String?
    var error: String?
    var trackerID: String?
    var usage: AIUsage?

    enum CodingKeys: String, CodingKey {
        case id, machine, agent, title, prompt, cwd, status, error, satellite, started, finished, created, output, usage
        case jobID = "job_id"
        case satelliteID = "satellite_id"
        case exitCode = "exit_code"
        case createdAt = "created_at"
        case startedAt = "started_at"
        case finishedAt = "finished_at"
        case outputTail = "output_tail"
        case trackerID = "tracker_id"
    }

    init(id: String, satelliteID: String? = nil, machine: String? = nil, agent: String, title: String? = nil,
         prompt: String? = nil, cwd: String? = nil, status: String = "queued", exitCode: Int? = nil, createdAt: String? = nil,
         startedAt: String? = nil, finishedAt: String? = nil, outputTail: String? = nil, error: String? = nil, trackerID: String? = nil) {
        self.id = id
        self.satelliteID = satelliteID
        self.machine = machine
        self.agent = agent
        self.title = title
        self.prompt = prompt
        self.cwd = cwd
        self.status = status
        self.exitCode = exitCode
        self.createdAt = createdAt
        self.startedAt = startedAt
        self.finishedAt = finishedAt
        self.outputTail = outputTail
        self.error = error
        self.trackerID = trackerID
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let id = Insights.nonEmpty(c.lenientString(.jobID)) ?? Insights.nonEmpty(c.lenientString(.id)) else {
            throw DecodingError.keyNotFound(CodingKeys.id, .init(codingPath: c.codingPath, debugDescription: "job id"))
        }
        self.id = id
        satelliteID = Insights.nonEmpty(c.lenientString(.satelliteID)) ?? Insights.nonEmpty(c.lenientString(.satellite))
        machine = c.lenientString(.machine)
        agent = Insights.nonEmpty(c.lenientString(.agent)) ?? "agent"
        title = Insights.nonEmpty(c.lenientString(.title))
        prompt = c.lenientString(.prompt)
        cwd = Insights.nonEmpty(c.lenientString(.cwd))
        status = (Insights.nonEmpty(c.lenientString(.status)) ?? "queued").lowercased()
        exitCode = c.lenientInt(.exitCode)
        createdAt = c.lenientString(.createdAt) ?? c.lenientString(.created)
        startedAt = c.lenientString(.startedAt) ?? c.lenientString(.started)
        finishedAt = c.lenientString(.finishedAt) ?? c.lenientString(.finished)
        outputTail = c.lenientString(.outputTail) ?? c.lenientString(.output)
        error = c.lenientString(.error)
        trackerID = c.lenientString(.trackerID)
        usage = try? c.decodeIfPresent(AIUsage.self, forKey: .usage)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .jobID)
        try c.encodeIfPresent(satelliteID, forKey: .satelliteID)
        try c.encodeIfPresent(machine, forKey: .machine)
        try c.encode(agent, forKey: .agent)
        try c.encodeIfPresent(title, forKey: .title)
        try c.encodeIfPresent(prompt, forKey: .prompt)
        try c.encodeIfPresent(cwd, forKey: .cwd)
        try c.encode(status, forKey: .status)
        try c.encodeIfPresent(exitCode, forKey: .exitCode)
        try c.encodeIfPresent(createdAt, forKey: .createdAt)
        try c.encodeIfPresent(startedAt, forKey: .startedAt)
        try c.encodeIfPresent(finishedAt, forKey: .finishedAt)
        try c.encodeIfPresent(outputTail, forKey: .outputTail)
        try c.encodeIfPresent(error, forKey: .error)
        try c.encodeIfPresent(trackerID, forKey: .trackerID)
        try c.encodeIfPresent(usage, forKey: .usage)
    }

    var isActive: Bool { status == "queued" || status == "running" || status == "starting" }
    var isRunning: Bool { status == "running" }
    var isFailed: Bool { status == "failed" || status == "error" || (status == "done" && (exitCode ?? 0) != 0) }

    var shownTitle: String {
        if let title { return title }
        guard let prompt = Insights.nonEmpty(prompt) else { return "\(AgentStyle.displayName(agent)) job" }
        let line = prompt.split(whereSeparator: \.isNewline).first.map(String.init) ?? prompt
        return line.count > 80 ? String(line.prefix(78)) + "…" : line
    }

    var startedDate: Date? { OrbitalDates.parse(startedAt ?? createdAt) }
    var finishedDate: Date? { OrbitalDates.parse(finishedAt) }

    /// Seconds from start to finish, or to `now` while it runs.
    func duration(now: Date = Date()) -> TimeInterval? {
        guard let start = startedDate else { return nil }
        let end = finishedDate ?? (isActive ? now : nil)
        guard let end else { return nil }
        return max(0, end.timeIntervalSince(start))
    }

    var statusWord: String {
        switch status {
        case "queued": return "Queued"
        case "starting": return "Starting"
        case "running": return "Running"
        case "done", "succeeded", "success": return (exitCode ?? 0) == 0 ? "Done" : "Exited \(exitCode ?? 0)"
        case "failed", "error": return "Failed"
        case "cancelled", "canceled": return "Cancelled"
        case "timeout", "timed_out": return "Timed out"
        default: return status.capitalized
        }
    }

    var statusSymbol: String {
        switch status {
        case "queued": return "hourglass"
        case "starting", "running": return "arrow.triangle.2.circlepath"
        case "done", "succeeded", "success": return (exitCode ?? 0) == 0 ? "checkmark.circle.fill" : "exclamationmark.triangle.fill"
        case "cancelled", "canceled": return "stop.circle.fill"
        default: return "xmark.octagon.fill"
        }
    }

    var tint: Color {
        switch status {
        case "queued", "starting", "running": return OrbitalTheme.accent
        case "done", "succeeded", "success": return (exitCode ?? 0) == 0 ? OrbitalTheme.success : OrbitalTheme.warning
        case "cancelled", "canceled": return OrbitalTheme.textSecondary
        default: return OrbitalTheme.danger
        }
    }
}

/// `{jobs: [...]}` or a bare list.
struct AgentJobList: Decodable {
    var jobs: [AgentJob]

    enum CodingKeys: String, CodingKey { case jobs, items }

    init(from decoder: Decoder) throws {
        if var list = try? decoder.unkeyedContainer() {
            var rows: [AgentJob] = []
            while !list.isAtEnd {
                if let row = try? list.decode(AgentJob.self) { rows.append(row) } else { _ = try? list.decode(AnyCodable.self) }
            }
            jobs = rows
            return
        }
        let c = try decoder.container(keyedBy: CodingKeys.self)
        jobs = c.contains(.jobs) ? c.lossy(AgentJob.self, .jobs) : c.lossy(AgentJob.self, .items)
    }
}

/// What Run agent sends: `POST /v1/satellites/{id}/agents/run`.
struct AgentRunRequest: Equatable {
    var satelliteID: String
    var agent: String
    var prompt: String
    var cwd: String?
    var title: String?

    var body: [String: Any] {
        var out: [String: Any] = ["agent": agent, "prompt": prompt]
        if let cwd = Insights.nonEmpty(cwd) { out["cwd"] = cwd }
        if let title = Insights.nonEmpty(title) { out["title"] = title }
        return out
    }
}

/// Run agent opened from a button or a deep link, already filled in.
struct AgentRunDraft: Equatable, Identifiable {
    var id = UUID()
    var machine: String?
    var agent: String?
    var prompt: String = ""
    var cwd: String = ""
    var title: String = ""
}

enum AgentJobsSort {
    /// Running first (newest start first), then finished, newest first.
    static func ordered(_ jobs: [AgentJob]) -> [AgentJob] {
        jobs.sorted { lhs, rhs in
            if lhs.isActive != rhs.isActive { return lhs.isActive }
            let left = lhs.startedAt ?? lhs.createdAt ?? lhs.finishedAt ?? ""
            let right = rhs.startedAt ?? rhs.createdAt ?? rhs.finishedAt ?? ""
            let l = OrbitalDates.parse(left) ?? .distantPast
            let r = OrbitalDates.parse(right) ?? .distantPast
            return l > r
        }
    }
}


/// The hub's join window (`/v1/satellites/invite`): while open, machines on your Tailscale account join by themselves.
struct JoinWindowStatus: Codable, Equatable {
    var open: Bool
    var until: String?
    var secondsLeft: Int
    var joined: [String]

    enum CodingKeys: String, CodingKey {
        case open, until, joined
        case secondsLeft = "seconds_left"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        open = (try? c.decode(Bool.self, forKey: .open)) ?? false
        until = try? c.decode(String.self, forKey: .until)
        secondsLeft = (try? c.decode(Int.self, forKey: .secondsLeft)) ?? 0
        joined = (try? c.decode([String].self, forKey: .joined)) ?? []
    }

    var untilDate: Date? { OrbitalDates.parse(until) }
}
