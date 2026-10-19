import Foundation
import SwiftUI

// MARK: - The Marketing tab's Automation view

/// What runs on its own for marketing, when it ran, when it runs next, what is next on each list, and what
/// is being worked on now. The `automation` object of the `marketing` block. Scheduler jobs are redrawn from
/// the live scheduler picture (`store.jobs`) when the phone has it, so their countdowns stay current.
struct MarketingAutomation: Codable, Equatable {
    var asOf: String?
    var schedulerUp: Bool?
    var groups: [AutomationGroup]
    var automations: [MarketingAutomationItem]
    var upNext: [UpNextSection]
    var working: [WorkingItem]
    var workingSource: String?
    var sources: [AutomationSource]

    enum CodingKeys: String, CodingKey {
        case groups, automations, working, sources
        case asOf = "as_of"
        case schedulerUp = "scheduler_up"
        case upNext = "up_next"
        case workingSource = "working_source"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        asOf = try? c.decodeIfPresent(String.self, forKey: .asOf)
        schedulerUp = c.lenientBool(.schedulerUp)
        groups = c.lossy(AutomationGroup.self, .groups)
        automations = c.lossy(MarketingAutomationItem.self, .automations)
        upNext = c.lossy(UpNextSection.self, .upNext)
        working = c.lossy(WorkingItem.self, .working)
        workingSource = try? c.decodeIfPresent(String.self, forKey: .workingSource)
        sources = c.lossy(AutomationSource.self, .sources)
    }

    /// Every automation with the scheduler's live state laid over the feed's copy.
    func live(_ jobs: JobsSnapshot?) -> [MarketingAutomationItem] {
        automations.map { $0.live(jobs) }
    }

    /// Group order from the feed, then any group it did not name.
    func orderedGroups(_ rows: [MarketingAutomationItem]) -> [AutomationGroup] {
        var out = groups.filter { group in rows.contains { $0.group == group.id } }
        for id in rows.map(\.group) where !out.contains(where: { $0.id == id }) {
            out.append(AutomationGroup(id: id, title: id.capitalized, symbol: nil))
        }
        return out
    }

    /// Sources the feed could not read, for the footer.
    var failedSources: [AutomationSource] { sources.filter { !$0.status.hasPrefix("ok") } }
}

struct AutomationGroup: Codable, Equatable, Identifiable {
    var id: String
    var title: String
    var symbol: String?
}

struct AutomationSource: Codable, Equatable, Identifiable {
    var name: String
    var status: String
    var id: String { name }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        name = try c.decode(String.self, forKey: .name)
        status = (try? c.decodeIfPresent(String.self, forKey: .status)) ?? "unknown"
    }
}

struct AutomationRun: Codable, Equatable, Hashable {
    var at: String?
    var status: String?
    var summary: String?
    var durationS: Double?

    enum CodingKeys: String, CodingKey {
        case at, status, summary
        case durationS = "duration_s"
    }
}

/// One thing that runs on its own: a Claude scheduled task or an Orbital scheduler job.
struct MarketingAutomationItem: Codable, Equatable, Identifiable {
    var id: String
    var name: String
    var group: String
    var kind: String
    var jobID: String?
    var brand: String?
    var purpose: String?
    var schedule: String?
    var cron: String?
    var status: String
    var paused: Bool
    var lastAt: String?
    var lastStatus: String?
    var lastSummary: String?
    var lastDetails: String?
    var lastDurationS: Double?
    var nextAt: String?
    var failingSince: String?
    var errorStreak: Int
    var issues: [String]
    var runs: [AutomationRun]
    var log: String?
    /// Set when the live scheduler picture says it is queued.
    var queued = false

    enum CodingKeys: String, CodingKey {
        case id, name, group, kind, brand, purpose, schedule, cron, status, paused, issues, runs, log
        case jobID = "job_id"
        case lastAt = "last_at"
        case lastStatus = "last_status"
        case lastSummary = "last_summary"
        case lastDetails = "last_details"
        case lastDurationS = "last_duration_s"
        case nextAt = "next_at"
        case failingSince = "failing_since"
        case errorStreak = "error_streak"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        name = (try? c.decodeIfPresent(String.self, forKey: .name)) ?? id
        group = (try? c.decodeIfPresent(String.self, forKey: .group)) ?? "other"
        kind = (try? c.decodeIfPresent(String.self, forKey: .kind)) ?? "scheduler"
        jobID = try? c.decodeIfPresent(String.self, forKey: .jobID)
        brand = try? c.decodeIfPresent(String.self, forKey: .brand)
        purpose = try? c.decodeIfPresent(String.self, forKey: .purpose)
        schedule = try? c.decodeIfPresent(String.self, forKey: .schedule)
        cron = try? c.decodeIfPresent(String.self, forKey: .cron)
        status = (try? c.decodeIfPresent(String.self, forKey: .status)) ?? "unknown"
        paused = c.lenientBool(.paused) ?? false
        lastAt = try? c.decodeIfPresent(String.self, forKey: .lastAt)
        lastStatus = try? c.decodeIfPresent(String.self, forKey: .lastStatus)
        lastSummary = try? c.decodeIfPresent(String.self, forKey: .lastSummary)
        lastDetails = try? c.decodeIfPresent(String.self, forKey: .lastDetails)
        lastDurationS = try? c.decodeIfPresent(Double.self, forKey: .lastDurationS)
        nextAt = try? c.decodeIfPresent(String.self, forKey: .nextAt)
        failingSince = try? c.decodeIfPresent(String.self, forKey: .failingSince)
        errorStreak = c.lenientInt(.errorStreak) ?? 0
        issues = (try? c.decodeIfPresent([String].self, forKey: .issues)) ?? []
        runs = c.lossy(AutomationRun.self, .runs)
        log = try? c.decodeIfPresent(String.self, forKey: .log)
    }

    var isClaudeTask: Bool { kind == "claude_task" }
    var lastDate: Date? { OrbitalDates.parse(lastAt) }
    var nextDate: Date? { OrbitalDates.parse(nextAt) }
    var health: AutomationHealth { AutomationHealth(status: status, queued: queued) }
    /// Failing, partly working, or carrying an issue line. A paused one is counted as paused, not as a problem.
    var needsALook: Bool { !paused && (status == "error" || status == "warn" || !issues.isEmpty) }

    /// The scheduler's live job, when this is one and the phone has the picture.
    func live(_ jobs: JobsSnapshot?) -> MarketingAutomationItem {
        guard kind == "scheduler", let jobID, let job = jobs?.job(jobID) else { return self }
        var row = self
        row.paused = job.paused
        row.queued = job.isQueued
        if job.isRunning {
            row.status = "running"
        } else if job.paused {
            row.status = "paused"
        } else {
            switch job.health {
            case .ok: row.status = "ok"
            case .warn: row.status = "warn"
            case .error: row.status = "error"
            case .unknown: row.status = "unknown"
            }
        }
        if let last = job.last {
            row.lastAt = last.finishedAt ?? last.startedAt
            row.lastStatus = last.status
            row.lastSummary = last.summary
            row.lastDurationS = last.durationS
            row.issues = last.issues ?? []
        }
        row.nextAt = job.paused ? nil : job.nextRunAt
        row.failingSince = job.failingSince
        row.schedule = job.schedule.isEmpty ? schedule : job.schedule
        if !job.history.isEmpty {
            row.runs = job.history.reversed().prefix(12).map {
                AutomationRun(at: $0.at, status: $0.status, summary: $0.summary, durationS: $0.durationS)
            }
            var streak = 0
            for run in job.history.reversed() {
                guard run.status == "error" || run.status == "warn" else { break }
                streak += 1
            }
            row.errorStreak = streak
        }
        return row
    }
}

/// Status word, symbol, and colour for an automation. The word always goes with the colour.
struct AutomationHealth {
    let word: String
    let symbol: String
    let tint: Color

    init(status: String, queued: Bool = false) {
        if queued {
            (word, symbol, tint) = ("Queued", "hourglass", OrbitalTheme.accent)
            return
        }
        switch status {
        case "ok": (word, symbol, tint) = ("OK", "checkmark.circle.fill", OrbitalTheme.success)
        case "warn": (word, symbol, tint) = ("Needs a look", "exclamationmark.triangle.fill", OrbitalTheme.warning)
        case "error": (word, symbol, tint) = ("Failing", "xmark.octagon.fill", OrbitalTheme.danger)
        case "running": (word, symbol, tint) = ("Running", "arrow.triangle.2.circlepath", OrbitalTheme.accent)
        case "paused": (word, symbol, tint) = ("Paused", "pause.circle.fill", OrbitalTheme.textTertiary)
        default: (word, symbol, tint) = ("Not run yet", "circle.dashed", OrbitalTheme.textTertiary)
        }
    }

    /// A run in a list: ok, warn, error, running.
    static func run(_ status: String?) -> AutomationHealth { AutomationHealth(status: status ?? "unknown") }
}

// MARK: - Up next

struct UpNextSection: Codable, Equatable, Identifiable {
    var id: String
    var title: String
    var brand: String?
    var symbol: String?
    var note: String?
    var items: [UpNextItem]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? id
        brand = try? c.decodeIfPresent(String.self, forKey: .brand)
        symbol = try? c.decodeIfPresent(String.self, forKey: .symbol)
        note = try? c.decodeIfPresent(String.self, forKey: .note)
        items = c.lossy(UpNextItem.self, .items)
    }
}

struct ChecklistRow: Codable, Equatable, Hashable {
    var label: String
    var ok: Bool
    var note: String?

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        label = try c.decode(String.self, forKey: .label)
        ok = c.lenientBool(.ok) ?? false
        note = try? c.decodeIfPresent(String.self, forKey: .note)
    }
}

/// One thing next on a list: a post, a production run, an outreach wave, a release, an experiment read.
struct UpNextItem: Codable, Equatable, Identifiable {
    var id: String
    var title: String
    var detail: String?
    var at: String?
    var when: String?
    var status: String
    var reason: String?
    var stage: String?
    var brand: String?
    var command: String?
    var then: String?
    var link: String?
    var checklist: [ChecklistRow]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? id
        detail = try? c.decodeIfPresent(String.self, forKey: .detail)
        at = try? c.decodeIfPresent(String.self, forKey: .at)
        when = try? c.decodeIfPresent(String.self, forKey: .when)
        status = (try? c.decodeIfPresent(String.self, forKey: .status)) ?? "info"
        reason = try? c.decodeIfPresent(String.self, forKey: .reason)
        stage = try? c.decodeIfPresent(String.self, forKey: .stage)
        brand = try? c.decodeIfPresent(String.self, forKey: .brand)
        command = try? c.decodeIfPresent(String.self, forKey: .command)
        then = try? c.decodeIfPresent(String.self, forKey: .then)
        link = try? c.decodeIfPresent(String.self, forKey: .link)
        checklist = c.lossy(ChecklistRow.self, .checklist)
    }

    var date: Date? { OrbitalDates.parse(at) }
    var style: UpNextStatus { UpNextStatus(status) }
    var hasMore: Bool { reason != nil || command != nil || then != nil || link != nil || !checklist.isEmpty || (detail?.count ?? 0) > 90 }
}

struct UpNextStatus {
    let word: String
    let symbol: String
    let tint: Color

    init(_ status: String) {
        switch status {
        case "ready": (word, symbol, tint) = ("Ready", "play.circle.fill", OrbitalTheme.success)
        case "scheduled": (word, symbol, tint) = ("Scheduled", "clock.fill", OrbitalTheme.accent)
        case "blocked": (word, symbol, tint) = ("Blocked", "hand.raised.fill", OrbitalTheme.danger)
        case "waiting": (word, symbol, tint) = ("Waiting on you", "person.fill.questionmark", OrbitalTheme.warning)
        case "due": (word, symbol, tint) = ("Due", "clock.badge.exclamationmark", OrbitalTheme.warning)
        case "done": (word, symbol, tint) = ("Done", "checkmark.circle.fill", OrbitalTheme.success)
        case "paused": (word, symbol, tint) = ("Paused", "pause.circle.fill", OrbitalTheme.textTertiary)
        case "info": (word, symbol, tint) = ("Info", "info.circle.fill", OrbitalTheme.textSecondary)
        default: (word, symbol, tint) = ("Unknown", "questionmark.circle", OrbitalTheme.textTertiary)
        }
    }
}

// MARK: - Working now

/// Something an agent is working on right now (the agent tracker), or a ledger action from the last 2 hours.
struct WorkingItem: Codable, Equatable, Identifiable {
    var id: String
    var title: String
    var agent: String?
    var startedAt: String?
    var updatedAt: String?
    var note: String?
    var details: String?
    var subagents: Int
    var subagentTitles: [String]
    var quiet: Bool
    var source: String?

    enum CodingKeys: String, CodingKey {
        case id, title, agent, note, details, subagents, quiet, source
        case startedAt = "started_at"
        case updatedAt = "updated_at"
        case subagentTitles = "subagent_titles"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = (try? c.decodeIfPresent(String.self, forKey: .title)) ?? id
        agent = try? c.decodeIfPresent(String.self, forKey: .agent)
        startedAt = try? c.decodeIfPresent(String.self, forKey: .startedAt)
        updatedAt = try? c.decodeIfPresent(String.self, forKey: .updatedAt)
        note = try? c.decodeIfPresent(String.self, forKey: .note)
        details = try? c.decodeIfPresent(String.self, forKey: .details)
        subagents = c.lenientInt(.subagents) ?? 0
        subagentTitles = (try? c.decodeIfPresent([String].self, forKey: .subagentTitles)) ?? []
        quiet = c.lenientBool(.quiet) ?? false
        source = try? c.decodeIfPresent(String.self, forKey: .source)
    }
}

// MARK: - Times

enum AutomationTime {
    /// "3:25 PM", "Tomorrow 12:25 AM", "Thu 4:12 PM", "Fri 16 Oct".
    static func clock(_ date: Date?, now: Date = Date()) -> String? {
        guard let date else { return nil }
        let calendar = Calendar.current
        let time = date.formatted(date: .omitted, time: .shortened)
        if calendar.isDateInToday(date) { return time }
        if calendar.isDateInTomorrow(date) { return "Tomorrow \(time)" }
        if calendar.isDateInYesterday(date) { return "Yesterday \(time)" }
        let days = abs(calendar.dateComponents([.day], from: calendar.startOfDay(for: now), to: calendar.startOfDay(for: date)).day ?? 99)
        if days < 7 { return "\(date.formatted(.dateTime.weekday(.abbreviated))) \(time)" }
        return date.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated))
    }

    /// "in 12 min", "in 3 h", "in 2 days", "now", "12 min ago".
    static func until(_ date: Date?, now: Date = Date()) -> String? {
        guard let date else { return nil }
        let seconds = date.timeIntervalSince(now)
        let minutes = Int((abs(seconds) / 60).rounded())
        let text: String
        if minutes < 1 { return seconds >= 0 ? "in under a minute" : "just now" }
        if minutes < 60 { text = "\(minutes) min" }
        else if minutes < 48 * 60 { text = "\(Int((Double(minutes) / 60).rounded())) h" }
        else { text = "\(minutes / 1440) days" }
        return seconds >= 0 ? "in \(text)" : "\(text) ago"
    }
}
