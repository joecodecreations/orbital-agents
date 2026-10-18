import Foundation
import SwiftUI

// The `series` block (daily numbers tagged by space, product, and kind), the day maths every chart
// shares, and how a kind's stats are named and ordered. Contract: the hub's data pipeline docs (series).

// MARK: - Block

struct SeriesPoint: Codable, Equatable {
    var x: String
    var y: Double

    enum CodingKeys: String, CodingKey { case x, y }

    init(x: String, y: Double) {
        self.x = x
        self.y = y
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let x = c.lenientString(.x), let y = c.lenientDouble(.y) else {
            throw DecodingError.keyNotFound(CodingKeys.y, .init(codingPath: c.codingPath, debugDescription: "point"))
        }
        self.x = x
        self.y = y
    }
}

struct SeriesMetric: Codable, Equatable, Identifiable {
    var id: String
    var label: String
    var platform: String?
    var stat: String?
    var format: String?
    var level: Bool
    var group: String?
    /// "#RRGGBB" for this line. Without it the line takes a colour from its platform or group.
    var color: String?
    var points: [SeriesPoint]

    enum CodingKeys: String, CodingKey {
        case id, label, platform, stat, format, level, group, color, points
    }

    init(id: String, label: String, platform: String? = nil, stat: String? = nil, format: String? = nil,
         level: Bool = false, group: String? = nil, color: String? = nil, points: [SeriesPoint]) {
        self.id = id
        self.label = label
        self.platform = platform
        self.stat = stat
        self.format = format
        self.level = level
        self.group = group
        self.color = color
        self.points = points
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let id = c.lenientString(.id), !id.isEmpty else {
            throw DecodingError.keyNotFound(CodingKeys.id, .init(codingPath: c.codingPath, debugDescription: "metric id"))
        }
        self.id = id
        label = Insights.nonEmpty(c.lenientString(.label)) ?? id
        platform = Insights.nonEmpty(c.lenientString(.platform))?.lowercased()
        stat = Insights.nonEmpty(c.lenientString(.stat))?.lowercased()
        format = Insights.nonEmpty(c.lenientString(.format))
        // A percentage or a response time is a reading, never a count to add up, unless the feed says otherwise.
        level = c.lenientBool(.level) ?? ["percent", "%", "ms", "latency"].contains(format?.lowercased() ?? "")
        group = Insights.nonEmpty(c.lenientString(.group))
        color = Insights.nonEmpty(c.lenientString(.color))
        points = c.lossy(SeriesPoint.self, .points)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(label, forKey: .label)
        try c.encodeIfPresent(platform, forKey: .platform)
        try c.encodeIfPresent(stat, forKey: .stat)
        try c.encodeIfPresent(format, forKey: .format)
        if level { try c.encode(level, forKey: .level) }
        try c.encodeIfPresent(group, forKey: .group)
        try c.encodeIfPresent(color, forKey: .color)
        try c.encode(points, forKey: .points)
    }

    /// What is counted, so the same stat adds up across products: `stat`, else the id.
    var statKey: String { stat ?? id }
}

struct SeriesBlock: Codable, Equatable {
    var type: String = "series"
    var id: String
    var kind: String
    var title: String?
    var caption: String?
    /// The space the numbers belong to (`space`, or `company` from older feeds). May be empty.
    var space: String
    /// The thing measured: an app, a site, a server. May be empty.
    var product: String
    var site: String?
    var window: Int
    var asOf: String?
    var metrics: [SeriesMetric]

    enum CodingKeys: String, CodingKey {
        case type, id, kind, title, caption, space, company, product, site, window, metrics
        case asOf = "as_of"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        kind = (c.lenientString(.kind) ?? "other").lowercased()
        title = Insights.nonEmpty(c.lenientString(.title))
        caption = Insights.nonEmpty(c.lenientString(.caption))
        space = Insights.nonEmpty(c.lenientString(.space)) ?? c.lenientString(.company) ?? ""
        product = c.lenientString(.product) ?? ""
        site = Insights.nonEmpty(c.lenientString(.site))
        window = max(1, min(90, c.lenientInt(.window) ?? 7))
        asOf = c.lenientString(.asOf)
        metrics = c.lossy(SeriesMetric.self, .metrics)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(type, forKey: .type)
        try c.encode(id, forKey: .id)
        try c.encode(kind, forKey: .kind)
        try c.encodeIfPresent(title, forKey: .title)
        try c.encodeIfPresent(caption, forKey: .caption)
        try c.encode(space, forKey: .space)
        try c.encode(product, forKey: .product)
        try c.encodeIfPresent(site, forKey: .site)
        try c.encode(window, forKey: .window)
        try c.encodeIfPresent(asOf, forKey: .asOf)
        try c.encode(metrics, forKey: .metrics)
    }
}

/// One series block, with the page it sits on.
struct SeriesRef: Identifiable, Equatable {
    var id: String
    var location: PageLocation
    var accent: String? = nil
    var block: SeriesBlock

    var spaceID: String { Spaces.key(block.space) }
    var productID: String { Spaces.key(block.product) }
}

/// One to-do from any tasks block, with the block it came from so a tick can post back.
struct TaskRef: Identifiable, Equatable {
    var id: String
    var location: PageLocation
    var blockID: String
    var accent: String?
    var item: TaskItem
}

// MARK: - Days and lines

enum Trends {
    /// The last `count` calendar days, oldest first, ending today.
    static func days(_ count: Int, now: Date = Date(), calendar: Calendar = .current) -> [Date] {
        let today = calendar.startOfDay(for: now)
        return (0 ..< max(1, count)).reversed().compactMap { calendar.date(byAdding: .day, value: -$0, to: today) }
    }

    static func values(_ metric: SeriesMetric, calendar: Calendar = .current) -> [Date: Double] {
        var out: [Date: Double] = [:]
        for point in metric.points {
            guard let day = Insights.day(point.x, calendar: calendar), point.y.isFinite else { continue }
            out[day] = point.y
        }
        return out
    }

    /// One metric on each day. A level carries its last value forward; a count is nil on a day it did not measure.
    static func line(_ metric: SeriesMetric, days: [Date]) -> [Double?] {
        let map = values(metric)
        guard metric.level else { return days.map { map[$0] } }
        let known = map.keys.sorted()
        return days.map { day in
            guard let last = known.last(where: { $0 <= day }) else { return nil }
            return map[last]
        }
    }

    /// Several metrics of the same stat added up per day. Nil only where none of them has a value.
    static func combine(_ metrics: [SeriesMetric], days: [Date]) -> [Double?] {
        let lines = metrics.map { line($0, days: days) }
        return days.indices.map { index in
            let present = lines.compactMap { $0[index] }
            return present.isEmpty ? nil : present.reduce(0, +)
        }
    }

    /// A count's total over the days, or a level's last value. Nil when nothing was measured.
    static func summary(_ values: [Double?], level: Bool) -> Double? {
        let present = values.compactMap { $0 }
        guard !present.isEmpty else { return nil }
        return level ? values.last(where: { $0 != nil }) ?? nil : present.reduce(0, +)
    }

    /// Change across the window: a level's last minus first, a count's second half minus first half.
    static func change(_ values: [Double?], level: Bool) -> Double? {
        let present = values.compactMap { $0 }
        guard present.count >= 2 else { return nil }
        if level { return present.last! - present.first! }
        let half = values.count / 2
        let first = values.prefix(half).compactMap { $0 }.reduce(0, +)
        let second = values.suffix(values.count - half).compactMap { $0 }.reduce(0, +)
        return second - first
    }

    static func format(_ value: Double?, format: String?, compact: Bool = false) -> String {
        guard let value else { return "—" }
        switch format {
        case "usd", "currency":
            return OrbitalMoney.format(value, compact: compact)
        case "percent":
            return (value / (abs(value) > 1 ? 100 : 1)).formatted(.percent.precision(.fractionLength(0 ... 1)))
        case "duration":
            let seconds = Int(value.rounded())
            if seconds >= 3600 { return "\(seconds / 3600)h \((seconds % 3600) / 60)m" }
            if seconds >= 60 { return "\(seconds / 60)m \(seconds % 60)s" }
            return "\(seconds)s"
        case "ms":
            return value >= 1000 ? String(format: "%.1f s", value / 1000) : "\(Int(value.rounded())) ms"
        case "bytes":
            return ByteCountFormatter.string(fromByteCount: Int64(value), countStyle: .file)
        default:
            if compact, abs(value) >= 1_000_000 { return String(format: "%.1fM", value / 1_000_000) }
            if compact, abs(value) >= 10_000 { return String(format: "%.1fK", value / 1000) }
            let whole = abs(value - value.rounded()) < 0.005
            return whole ? Int(value.rounded()).formatted() : value.formatted(.number.precision(.fractionLength(0 ... 1)))
        }
    }
}

/// Lines split by `platform` (a social network, a region, a host). Names are tidied for display;
/// colours come from the metric's `color`, else a stable hash of the platform name.
enum PlatformStyle {
    static func color(_ platform: String?, hex: String? = nil) -> Color {
        OrbitalTheme.color(for: platform ?? "", hex: hex)
    }

    /// "youtube" reads "YouTube", "ios" reads "iOS". Anything else is capitalized.
    static func name(_ platform: String?) -> String {
        guard let platform, !platform.isEmpty else { return "Other" }
        let known = ["youtube": "YouTube", "tiktok": "TikTok", "ios": "iOS", "ipados": "iPadOS", "macos": "macOS", "x": "X"]
        return known[platform.lowercased()] ?? platform.replacingOccurrences(of: "_", with: " ").capitalized
    }

    /// The first letter, for the round badge beside a line.
    static func glyph(_ platform: String?) -> String {
        guard let first = name(platform).first else { return "•" }
        return String(first).uppercased()
    }

    static func sortIndex(_ platform: String?) -> String { (platform ?? "").lowercased() }
}

/// Names and order for the stats each kind of series carries. Kinds and stats are whatever feeds send;
/// these are only nicer labels and a sensible order for the common ones.
enum StatStyle {
    static let social = ["activity", "likes", "comments", "follows", "followers", "views"]
    static let site = ["visitors", "new_visitors", "sessions", "views", "uptime", "latency"]
    static let health = ["uptime", "latency", "cpu", "memory", "disk", "errors", "requests"]
    static let fitness = ["steps", "active_minutes", "calories", "distance", "sleep", "resting_hr"]
    static let sales = ["revenue", "orders", "customers", "refunds"]
    static let repo = ["stars", "forks", "issues", "pull_requests", "commits", "downloads"]

    static func label(_ stat: String) -> String {
        switch stat {
        case "activity": return "All activity"
        case "follows": return "New follows"
        case "new_visitors": return "New visitors"
        case "pull_requests": return "Pull requests"
        case "active_minutes": return "Active minutes"
        case "resting_hr": return "Resting heart rate"
        case "cpu": return "CPU"
        default: return stat.replacingOccurrences(of: "_", with: " ").replacingOccurrences(of: "-", with: " ").capitalized
        }
    }

    static func order(for kind: String) -> [String] {
        switch kind {
        case "social": return social
        case "site", "web", "uptime": return site
        case "health", "server", "infra": return health
        case "fitness": return fitness
        case "sales", "money": return sales
        case "repo", "github": return repo
        default: return []
        }
    }

    /// How a kind is named on a card when the block has no title.
    static func kindLabel(_ kind: String) -> String {
        switch kind {
        case "site", "web": return "Websites"
        case "usage": return "Usage"
        case "social": return "Social"
        case "health", "server", "infra": return "Health"
        case "repo", "github": return "Repositories"
        case "other": return "Daily"
        default: return kind.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    /// Stats present in these blocks, in the kind's usual order, unknown ones after.
    static func present(_ refs: [SeriesRef], kind: String) -> [String] {
        var seen: [String] = []
        for ref in refs {
            for metric in ref.block.metrics where !seen.contains(metric.statKey) {
                seen.append(metric.statKey)
            }
        }
        let known = order(for: kind)
        return seen.sorted { lhs, rhs in
            let left = known.firstIndex(of: lhs) ?? Int.max
            let right = known.firstIndex(of: rhs) ?? Int.max
            if left != right { return left < right }
            return seen.firstIndex(of: lhs) ?? 0 < seen.firstIndex(of: rhs) ?? 0
        }
    }
}

// MARK: - To-dos coming due

/// How pressing a to-do is. Lower is more urgent.
enum TaskTier: Int, Comparable {
    case overdue = 0, today, soon, week, dated, open, waiting, later

    static func < (lhs: TaskTier, rhs: TaskTier) -> Bool { lhs.rawValue < rhs.rawValue }

    var label: String {
        switch self {
        case .overdue: return "Overdue"
        case .today: return "Today"
        case .soon: return "Next 3 days"
        case .week: return "This week"
        case .dated: return "Coming up"
        case .open: return "Open"
        case .waiting: return "Waiting"
        case .later: return "Later"
        }
    }

    var color: Color {
        switch self {
        case .overdue, .today: return OrbitalTheme.danger
        case .soon: return Color(hex: "#FF9F6B") ?? OrbitalTheme.warning
        case .week: return OrbitalTheme.warning
        default: return OrbitalTheme.textSecondary
        }
    }

    /// Overdue, today, and the next three days count as needing attention.
    var isUrgent: Bool { self <= .soon }
}

enum TaskPriority {
    static func tier(_ item: TaskItem, now: Date = Date()) -> TaskTier {
        let status = item.status?.lowercased() ?? ""
        if let days = DayMath.daysUntil(item.due, now: now) {
            if status == "later" && days > 7 { return .later }
            if days < 0 { return .overdue }
            if days == 0 { return .today }
            if days <= 3 { return .soon }
            if days <= 7 { return .week }
            return .dated
        }
        switch status {
        case "waiting", "blocked": return .waiting
        case "later": return .later
        default: return .open
        }
    }

    /// Open to-dos, most urgent first: tier, then the most overdue or soonest, then title.
    static func ordered(_ tasks: [TaskRef], now: Date = Date()) -> [(ref: TaskRef, tier: TaskTier)] {
        tasks
            .filter { !$0.item.done && ($0.item.status?.lowercased() ?? "") != "done" }
            .map { (ref: $0, tier: tier($0.item, now: now)) }
            .sorted { lhs, rhs in
                if lhs.tier != rhs.tier { return lhs.tier < rhs.tier }
                let left = DayMath.daysUntil(lhs.ref.item.due, now: now) ?? Int.max
                let right = DayMath.daysUntil(rhs.ref.item.due, now: now) ?? Int.max
                if left != right { return left < right }
                return lhs.ref.item.title < rhs.ref.item.title
            }
    }
}

/// Calendar-day maths for `YYYY-MM-DD` fields.
enum DayMath {
    /// Whole days from today to a `YYYY-MM-DD` day, in the device's calendar. Negative when it is past.
    static func daysUntil(_ raw: String?, now: Date = Date(), calendar: Calendar = .current) -> Int? {
        guard let raw, let day = Insights.day(raw, calendar: calendar) else { return nil }
        return calendar.dateComponents([.day], from: calendar.startOfDay(for: now), to: calendar.startOfDay(for: day)).day
    }

    /// `YYYY-MM-DD` for a date in the device's calendar.
    static func isoDay(_ date: Date, calendar: Calendar = .current) -> String {
        let parts = calendar.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", parts.year ?? 0, parts.month ?? 0, parts.day ?? 0)
    }

    /// "due today", "in 3 days", "2 days overdue", with the free-text label after it.
    static func due(_ item: TaskItem) -> (text: String, tint: Color)? {
        var parts: [String] = []
        var tint = OrbitalTheme.textSecondary
        if let days = daysUntil(item.due) {
            switch days {
            case 0: parts.append("due today")
            case 1: parts.append("due tomorrow")
            case 2...: parts.append("in \(days) days")
            case -1: parts.append("1 day overdue")
            default: parts.append("\(-days) days overdue")
            }
            if !item.done {
                if days < 0 {
                    tint = OrbitalTheme.danger
                } else if days <= 3 {
                    tint = OrbitalTheme.warning
                }
            }
        } else if let raw = Insights.nonEmpty(item.due) {
            parts.append("due \(raw)")
        }
        if let label = Insights.nonEmpty(item.dueLabel) { parts.append(label) }
        guard !parts.isEmpty else { return nil }
        return (parts.joined(separator: " · "), tint)
    }

    static func statusColor(_ status: String) -> Color {
        switch status.lowercased() {
        case "ready", "done": return OrbitalTheme.success
        case "waiting": return OrbitalTheme.warning
        case "blocked": return OrbitalTheme.danger
        case "later": return OrbitalTheme.textTertiary
        default: return OrbitalTheme.textSecondary
        }
    }

    static func statusSymbol(_ status: String) -> String? {
        switch status.lowercased() {
        case "ready": return "play.circle.fill"
        case "done": return "checkmark.circle.fill"
        case "waiting": return "hourglass"
        case "blocked": return "nosign"
        case "later": return "moon.zzz.fill"
        default: return nil
        }
    }
}
