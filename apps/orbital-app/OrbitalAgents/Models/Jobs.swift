import Foundation
import SwiftUI

/// The scheduler's picture: every feed, its schedule, its last run, and when it runs next.
/// `GET /v1/jobs`, `/v1/sync` field `jobs`, and the live socket's `jobs` event.
struct JobsSnapshot: Codable, Equatable {
    var scheduler: SchedulerInfo
    var groups: [JobGroup]
    var lanes: [JobLane]
    var jobs: [FeedJob]
    var batch: RefreshBatch?
    var disk: DiskStatus?
    var serverTime: String?

    enum CodingKeys: String, CodingKey {
        case scheduler, groups, lanes, jobs, batch, disk
        case serverTime = "server_time"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        scheduler = (try? c.decode(SchedulerInfo.self, forKey: .scheduler)) ?? SchedulerInfo()
        groups = (try? c.decode([JobGroup].self, forKey: .groups)) ?? []
        lanes = (try? c.decode([JobLane].self, forKey: .lanes)) ?? []
        jobs = (try? c.decode([FeedJob].self, forKey: .jobs)) ?? []
        batch = try? c.decode(RefreshBatch.self, forKey: .batch)
        disk = try? c.decode(DiskStatus.self, forKey: .disk)
        serverTime = try? c.decode(String.self, forKey: .serverTime)
    }

    /// The jobs that write this page. A job that names a tab counts only
    /// on that tab, and a tab with its own job shows only that job; other tabs show the node's own feed.
    func jobs(for location: PageLocation) -> [FeedJob] {
        func onPage(_ target: JobTarget) -> Bool {
            guard target.section == location.sectionID else { return false }
            guard let node = location.nodeID else { return true }
            return target.node == nil || target.node == node
        }
        let page = jobs.filter { $0.targets.contains(where: onPage) }
        if let tab = location.tabID {
            let owners = page.filter { job in job.targets.contains { onPage($0) && $0.tab == tab } }
            if !owners.isEmpty { return owners }
        }
        let whole = page.filter { job in job.targets.contains { onPage($0) && $0.tab == nil } }
        return whole.isEmpty ? page : whole
    }

    func jobs(in group: String) -> [FeedJob] {
        jobs.filter { $0.group == group }
    }

    func job(_ id: String) -> FeedJob? {
        jobs.first { $0.id == id }
    }

    /// The next scheduled run worth naming: live counters that run every minute are left out.
    var nextScheduled: (job: FeedJob, at: Date)? {
        jobs.filter { !$0.isBusy && !$0.paused && $0.everyS >= 300 }
            .compactMap { job in job.nextRunDate.map { (job: job, at: $0) } }
            .min { $0.at < $1.at }
    }

    var failing: [FeedJob] { jobs.filter { $0.health == .error } }
    var warning: [FeedJob] { jobs.filter { $0.health == .warn } }
    /// The scheduler is down, a feed is failing, or the disk is low: Home shows the feeds card at the top.
    var needsALook: Bool { scheduler.up == false || !failing.isEmpty || disk?.low == true }

    /// A refresh-all that is still going.
    var activeBatch: RefreshBatch? {
        guard let batch, batch.finishedAt == nil else { return nil }
        return batch
    }

    func doneCount(_ batch: RefreshBatch) -> Int {
        batch.jobs.filter { id in
            guard let job = job(id) else { return true }
            return job.finished(since: batch.requestedAt)
        }.count
    }

    /// 1-based stage the batch is on: the first stage with a job not finished yet.
    func currentStage(_ batch: RefreshBatch) -> Int {
        for (index, stage) in batch.stages.enumerated() where stage.contains(where: { id in !(job(id)?.finished(since: batch.requestedAt) ?? true) }) {
            return index + 1
        }
        return batch.stages.count
    }
}

/// Free space on the hub machine. At zero no page or feed state can be saved.
struct DiskStatus: Codable, Equatable {
    struct Log: Codable, Equatable {
        var path: String
        var gb: Double
    }

    var freeGb: Double?
    var totalGb: Double?
    var low: Bool?
    var bigLogs: [Log]?

    enum CodingKeys: String, CodingKey {
        case low
        case freeGb = "free_gb"
        case totalGb = "total_gb"
        case bigLogs = "big_logs"
    }
}

struct SchedulerInfo: Codable, Equatable {
    var up: Bool?
    var host: String?
    var heartbeatAt: String?
    var heartbeatAgeS: Double?
    var running: Int?
    var queued: Int?
    var maxParallel: Int?
    var everyChoices: [Int]?

    enum CodingKeys: String, CodingKey {
        case up, host, running, queued
        case heartbeatAt = "heartbeat_at"
        case heartbeatAgeS = "heartbeat_age_s"
        case maxParallel = "max_parallel"
        case everyChoices = "every_choices"
    }

    init(up: Bool? = nil) { self.up = up }
}

struct JobGroup: Codable, Equatable, Identifiable {
    var id: String
    var title: String
    var icon: String?
}

/// One browser profile on one machine, or one API account. Jobs in a lane run one at a time.
struct JobLane: Codable, Equatable, Identifiable {
    var id: String
    var title: String
    var kind: String
    var ok: Bool?
    var detail: String?
    var machine: String?
    var profile: String?
    var bridge: String?
    var lastPollAgeS: Double?
    var checkedAt: String?
    var busyWith: String?

    enum CodingKeys: String, CodingKey {
        case id, title, kind, ok, detail, machine, profile, bridge
        case lastPollAgeS = "last_poll_age_s"
        case checkedAt = "checked_at"
        case busyWith = "busy_with"
    }
}

struct JobTarget: Codable, Equatable, Hashable {
    var section: String
    var node: String?
    var tab: String?
}

struct JobRun: Codable, Equatable {
    var startedAt: String?
    var finishedAt: String?
    var durationS: Double?
    var exitCode: Int?
    var status: String?
    var summary: String?
    var issues: [String]?
    var reason: String?
    var usage: AIUsage?

    enum CodingKeys: String, CodingKey {
        case status, summary, issues, reason, usage
        case startedAt = "started_at"
        case finishedAt = "finished_at"
        case durationS = "duration_s"
        case exitCode = "exit_code"
    }
}

struct JobHistoryEntry: Codable, Equatable, Hashable {
    var at: String?
    var status: String?
    var durationS: Double?
    var summary: String?
    var reason: String?
    var tokens: Int?
    var planPercent: Double?

    enum CodingKeys: String, CodingKey {
        case at, status, summary, reason, tokens
        case durationS = "duration_s"
        case planPercent = "plan_percent"
    }
}

struct RefreshBatch: Codable, Equatable {
    var id: String
    var group: String?
    var requestedAt: String?
    var by: String?
    var jobs: [String]
    var stages: [[String]]
    var finishedAt: String?

    enum CodingKeys: String, CodingKey {
        case id, group, by, jobs, stages
        case requestedAt = "requested_at"
        case finishedAt = "finished_at"
    }
}

struct FeedJob: Codable, Equatable, Identifiable {
    enum Health { case ok, warn, error, unknown }

    var id: String
    var name: String
    var group: String
    var reads: String?
    var targets: [JobTarget]
    var lane: String
    var everyS: Int
    var defaultEveryS: Int?
    var offsetS: Int?
    var schedule: String
    var watches: Bool?
    var paused: Bool
    var timeoutS: Int?
    var state: String
    var queuedReason: String?
    var queuePosition: Int?
    var startedAt: String?
    var nextRunAt: String?
    var last: JobRun?
    var lastOkAt: String?
    var failingSince: String?
    var history: [JobHistoryEntry]
    var logs: [String]?
    /// "agent" when the job spins up an AI agent, else "automation" (a plain script).
    var kind: String
    /// Agent jobs: tokens and plan share over every measured run, and the run in progress.
    var usageTotal: AIUsage?
    var usageLive: AIUsage?

    enum CodingKeys: String, CodingKey {
        case id, name, group, reads, targets, lane, schedule, watches, paused, state, last, history, logs, kind
        case usageTotal = "usage_total"
        case usageLive = "usage_live"
        case everyS = "every_s"
        case defaultEveryS = "default_every_s"
        case offsetS = "offset_s"
        case timeoutS = "timeout_s"
        case queuedReason = "queued_reason"
        case queuePosition = "queue_position"
        case startedAt = "started_at"
        case nextRunAt = "next_run_at"
        case lastOkAt = "last_ok_at"
        case failingSince = "failing_since"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = (try? c.decode(String.self, forKey: .name)) ?? id
        group = (try? c.decode(String.self, forKey: .group)) ?? ""
        reads = try? c.decode(String.self, forKey: .reads)
        targets = (try? c.decode([JobTarget].self, forKey: .targets)) ?? []
        lane = (try? c.decode(String.self, forKey: .lane)) ?? id
        everyS = (try? c.decode(Int.self, forKey: .everyS)) ?? 3600
        defaultEveryS = try? c.decode(Int.self, forKey: .defaultEveryS)
        offsetS = try? c.decode(Int.self, forKey: .offsetS)
        schedule = (try? c.decode(String.self, forKey: .schedule)) ?? ""
        watches = try? c.decode(Bool.self, forKey: .watches)
        paused = (try? c.decode(Bool.self, forKey: .paused)) ?? false
        timeoutS = try? c.decode(Int.self, forKey: .timeoutS)
        state = (try? c.decode(String.self, forKey: .state)) ?? "idle"
        queuedReason = try? c.decode(String.self, forKey: .queuedReason)
        queuePosition = try? c.decode(Int.self, forKey: .queuePosition)
        startedAt = try? c.decode(String.self, forKey: .startedAt)
        nextRunAt = try? c.decode(String.self, forKey: .nextRunAt)
        last = try? c.decode(JobRun.self, forKey: .last)
        lastOkAt = try? c.decode(String.self, forKey: .lastOkAt)
        failingSince = try? c.decode(String.self, forKey: .failingSince)
        history = (try? c.decode([JobHistoryEntry].self, forKey: .history)) ?? []
        logs = try? c.decode([String].self, forKey: .logs)
        kind = (try? c.decode(String.self, forKey: .kind)) ?? "automation"
        usageTotal = try? c.decode(AIUsage.self, forKey: .usageTotal)
        usageLive = try? c.decode(AIUsage.self, forKey: .usageLive)
    }

    var isAgent: Bool { kind == "agent" }

    /// Average tokens and plan share per measured run.
    var averageTokens: Int? {
        guard let total = usageTotal, let runs = total.runs, runs > 0 else { return nil }
        return total.totalTokens / runs
    }

    var averagePlanPercent: Double? {
        guard let total = usageTotal, let runs = total.runs, runs > 0, let plan = total.planPercent else { return nil }
        return plan / Double(runs)
    }

    var isRunning: Bool { state == "running" }
    var isQueued: Bool { state == "queued" }
    var isBusy: Bool { isRunning || isQueued }
    var nextRunDate: Date? { OrbitalDates.parse(nextRunAt) }
    var startedDate: Date? { OrbitalDates.parse(startedAt) }
    var lastFinishedDate: Date? { OrbitalDates.parse(last?.finishedAt) }

    var health: Health {
        switch last?.status {
        case "ok": return .ok
        case "warn": return .warn
        case "error": return .error
        default: return .unknown
        }
    }

    var tint: Color {
        switch health {
        case .ok: return OrbitalTheme.success
        case .warn: return OrbitalTheme.warning
        case .error: return OrbitalTheme.danger
        case .unknown: return OrbitalTheme.textTertiary
        }
    }

    var statusSymbol: String {
        if isRunning { return "arrow.triangle.2.circlepath" }
        if isQueued { return "hourglass" }
        if paused { return "pause.circle.fill" }
        switch health {
        case .ok: return "checkmark.circle.fill"
        case .warn: return "exclamationmark.triangle.fill"
        case .error: return "xmark.octagon.fill"
        case .unknown: return "circle.dashed"
        }
    }

    var statusWord: String {
        if isRunning { return "Updating" }
        if isQueued { return "Queued" }
        switch health {
        case .ok: return "OK"
        case .warn: return "Partly working"
        case .error: return "Failing"
        case .unknown: return "Not run yet"
        }
    }

    /// Finished a run that started at or after `since` (ISO), so a refresh-all counts it done.
    func finished(since: String?) -> Bool {
        guard !isBusy, let started = last?.startedAt else { return false }
        guard let since else { return true }
        return started >= since
    }

    var reasonWord: String? {
        switch last?.reason {
        case "manual": return "run from the app"
        case "refresh-all": return "refresh everything"
        case "watch": return "a file changed"
        case "startup": return "at start"
        case "catch-up": return "catching up"
        case "restart": return "after a restart"
        case "schedule": return "on schedule"
        default: return last?.reason
        }
    }
}

enum JobFormat {
    /// "12:04", "1 h 05 m", "now".
    static func countdown(to date: Date?, now: Date) -> String {
        guard let date else { return "—" }
        let seconds = Int(date.timeIntervalSince(now).rounded(.up))
        if seconds <= 0 { return "now" }
        if seconds < 3600 { return String(format: "%d:%02d", seconds / 60, seconds % 60) }
        let hours = seconds / 3600
        let minutes = (seconds % 3600) / 60
        return hours >= 24 ? "\(hours / 24) d \(hours % 24) h" : String(format: "%d h %02d m", hours, minutes)
    }

    /// "0:23" for a run in progress.
    static func elapsed(since date: Date?, now: Date) -> String {
        guard let date else { return "" }
        let seconds = max(0, Int(now.timeIntervalSince(date)))
        return seconds < 3600 ? String(format: "%d:%02d", seconds / 60, seconds % 60) : "\(seconds / 3600) h \((seconds % 3600) / 60) m"
    }

    static func every(_ seconds: Int) -> String {
        switch seconds {
        case ..<60: return "Every \(seconds) s"
        case 60: return "Every minute"
        case ..<3600: return "Every \(seconds / 60) min"
        case 3600: return "Hourly"
        case ..<86400: return seconds % 3600 == 0 ? "Every \(seconds / 3600) h" : "Every \(seconds / 60) min"
        case 86400: return "Daily"
        default: return "Every \(seconds / 86400) days"
        }
    }

    static func duration(_ seconds: Double?) -> String? {
        guard let seconds else { return nil }
        if seconds < 60 { return String(format: seconds < 10 ? "%.1f s" : "%.0f s", seconds) }
        return "\(Int(seconds) / 60) m \(Int(seconds) % 60) s"
    }

    static func clock(_ date: Date?) -> String? {
        date?.formatted(date: .omitted, time: .shortened)
    }

    /// "11:40 PM" today, "Yesterday 9:07 PM", then "9 Oct, 9:07 PM".
    static func runTime(_ raw: String?) -> String? {
        guard let date = OrbitalDates.parse(raw) else { return nil }
        let time = date.formatted(date: .omitted, time: .shortened)
        let calendar = Calendar.current
        if calendar.isDateInToday(date) { return time }
        if calendar.isDateInYesterday(date) { return "Yesterday \(time)" }
        return "\(date.formatted(.dateTime.day().month(.abbreviated))), \(time)"
    }
}
