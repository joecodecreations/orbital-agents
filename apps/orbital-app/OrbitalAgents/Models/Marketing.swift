import Foundation
import SwiftUI

// MARK: - marketing block

/// A marketing ops ledger: the goal, each brand's scoreboard, the owner's to-dos, the experiments, and every
/// entry with its time, plus an optional `automation` picture. Brand keys are named by `brands` when sent.
struct MarketingBlock: Codable, Equatable {
    var type: String = "marketing"
    var id: String
    var title: String?
    var asOf: String?
    var timezone: String?
    var source: String?
    var goal: MarketingGoal?
    var scoreboard: [MarketingBoard]
    var todos: [MarketingTodo]
    var experiments: [MarketingExperiment]
    var entries: [MarketingEntry]
    /// The Automation view: schedules, last and next runs, what is next on each list, what is being worked on.
    var automation: MarketingAutomation?
    /// Optional names and colours for the brand keys used on entries.
    var brands: [MarketingBrandInfo]

    enum CodingKeys: String, CodingKey {
        case type, id, title, timezone, source, goal, scoreboard, todos, experiments, entries, automation, brands
        case asOf = "as_of"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = try c.decodeIfPresent(String.self, forKey: .title)
        asOf = try c.decodeIfPresent(String.self, forKey: .asOf)
        timezone = try c.decodeIfPresent(String.self, forKey: .timezone)
        source = try c.decodeIfPresent(String.self, forKey: .source)
        goal = try? c.decodeIfPresent(MarketingGoal.self, forKey: .goal)
        scoreboard = c.lossy(MarketingBoard.self, .scoreboard)
        todos = c.lossy(MarketingTodo.self, .todos)
        experiments = c.lossy(MarketingExperiment.self, .experiments)
        entries = c.lossy(MarketingEntry.self, .entries)
        automation = try? c.decodeIfPresent(MarketingAutomation.self, forKey: .automation)
        brands = c.lossy(MarketingBrandInfo.self, .brands)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(type, forKey: .type)
        try c.encode(id, forKey: .id)
        try c.encodeIfPresent(title, forKey: .title)
        try c.encodeIfPresent(asOf, forKey: .asOf)
        try c.encodeIfPresent(timezone, forKey: .timezone)
        try c.encodeIfPresent(source, forKey: .source)
        try c.encodeIfPresent(goal, forKey: .goal)
        try c.encode(scoreboard, forKey: .scoreboard)
        try c.encode(todos, forKey: .todos)
        try c.encode(experiments, forKey: .experiments)
        try c.encode(entries, forKey: .entries)
        try c.encodeIfPresent(automation, forKey: .automation)
        if !brands.isEmpty { try c.encode(brands, forKey: .brands) }
    }

    /// Every brand key in use, scoreboard order first, without "both".
    var brandKeys: [String] {
        var seen: [String] = []
        let all = brands.map(\.id) + scoreboard.map(\.brand) + todos.compactMap(\.brand) + experiments.compactMap(\.brand)
            + entries.compactMap(\.brand)
        for key in all where !key.isEmpty && key != "both" && key != "all" && !seen.contains(key) {
            seen.append(key)
        }
        return seen
    }

    var openTodos: [MarketingTodo] { todos.filter { !$0.isDone } }
    var doneTodos: [MarketingTodo] {
        todos.filter(\.isDone).sorted { ($0.doneDate ?? .distantPast) > ($1.doneDate ?? .distantPast) }
    }
}

struct MarketingGoal: Codable, Equatable {
    var label: String
    var brand: String?
    var metric: String?
    var current: Double?
    var target: Double

    var progress: Double {
        guard let current, target > 0 else { return 0 }
        return min(max(current / target, 0), 1)
    }
}

/// One brand's latest scoreboard reading. Values merge over the previous reading in the ledger.
struct MarketingBoard: Codable, Equatable, Identifiable {
    var brand: String
    var at: String?
    var local: String?
    var source: String?
    var values: [MarketingValue]

    var id: String { brand }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        brand = try c.decode(String.self, forKey: .brand)
        at = try c.decodeIfPresent(String.self, forKey: .at)
        local = try c.decodeIfPresent(String.self, forKey: .local)
        source = try c.decodeIfPresent(String.self, forKey: .source)
        values = c.lossy(MarketingValue.self, .values)
    }
}

struct MarketingValue: Codable, Equatable, Identifiable {
    var key: String
    var label: String
    var text: String
    var value: Double?

    var id: String { key }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        key = try c.decode(String.self, forKey: .key)
        label = try c.decodeIfPresent(String.self, forKey: .label) ?? key
        text = c.lenientString(.text) ?? "-"
        value = try? c.decodeIfPresent(Double.self, forKey: .value)
    }
}

/// Something only you can do. Done ones stay listed with who closed them and when.
struct MarketingTodo: Codable, Equatable, Identifiable {
    var id: String
    var brand: String?
    var title: String
    var why: String?
    var details: String?
    var at: String?
    var actor: String?
    var status: String
    var doneAt: String?
    var doneDetails: String?
    var doneBy: String?
    var pending: Bool

    enum CodingKeys: String, CodingKey {
        case id, brand, title, why, details, at, actor, status, pending
        case doneAt = "done_at"
        case doneDetails = "done_details"
        case doneBy = "done_by"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        brand = try c.decodeIfPresent(String.self, forKey: .brand)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? id
        why = try c.decodeIfPresent(String.self, forKey: .why)
        details = try c.decodeIfPresent(String.self, forKey: .details)
        at = try c.decodeIfPresent(String.self, forKey: .at)
        actor = try c.decodeIfPresent(String.self, forKey: .actor)
        status = (try c.decodeIfPresent(String.self, forKey: .status)) ?? "open"
        doneAt = try c.decodeIfPresent(String.self, forKey: .doneAt)
        doneDetails = try c.decodeIfPresent(String.self, forKey: .doneDetails)
        doneBy = try c.decodeIfPresent(String.self, forKey: .doneBy)
        pending = c.lenientBool(.pending) ?? false
    }

    var isDone: Bool { status == "done" }
    var date: Date? { OrbitalDates.parse(at) }
    var doneDate: Date? { OrbitalDates.parse(doneAt) }
}

struct MarketingResult: Codable, Equatable {
    var at: String?
    var status: String?
    var details: String?
}

struct MarketingExperiment: Codable, Equatable, Identifiable {
    var id: String
    var brand: String?
    var title: String
    var status: String
    var due: Bool
    var at: String?
    var hypothesis: String?
    var metric: String?
    var baseline: String?
    var target: String?
    var check: String?
    var why: String?
    var details: String?
    var links: [String]
    var results: [MarketingResult]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        brand = try c.decodeIfPresent(String.self, forKey: .brand)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? id
        status = (try c.decodeIfPresent(String.self, forKey: .status)) ?? "running"
        due = c.lenientBool(.due) ?? false
        at = try c.decodeIfPresent(String.self, forKey: .at)
        hypothesis = c.lenientString(.hypothesis)
        metric = c.lenientString(.metric)
        baseline = c.lenientString(.baseline)
        target = c.lenientString(.target)
        check = c.lenientString(.check)
        why = try c.decodeIfPresent(String.self, forKey: .why)
        details = try c.decodeIfPresent(String.self, forKey: .details)
        links = (try? c.decodeIfPresent([String].self, forKey: .links)) ?? []
        results = c.lossy(MarketingResult.self, .results)
    }

    var isRunning: Bool { status == "running" }
    var latestResult: MarketingResult? { results.last }
}

/// One ledger line: a decision, action, experiment, result, to-do, to-do done, alert, note, or scoreboard reading.
struct MarketingEntry: Codable, Equatable, Identifiable {
    var id: String
    var kind: String
    var brand: String?
    var at: String
    var local: String?
    var actor: String?
    var title: String
    var why: String?
    var details: String?
    var links: [String]
    var status: String?
    var ref: String?
    var hypothesis: String?
    var metric: String?
    var baseline: String?
    var target: String?
    var check: String?
    var tags: [String]
    var values: [MarketingValue]

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        kind = (try c.decodeIfPresent(String.self, forKey: .kind)) ?? "note"
        brand = try c.decodeIfPresent(String.self, forKey: .brand)
        at = try c.decode(String.self, forKey: .at)
        local = try c.decodeIfPresent(String.self, forKey: .local)
        actor = try c.decodeIfPresent(String.self, forKey: .actor)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? id
        why = try c.decodeIfPresent(String.self, forKey: .why)
        details = try c.decodeIfPresent(String.self, forKey: .details)
        links = (try? c.decodeIfPresent([String].self, forKey: .links)) ?? []
        status = try c.decodeIfPresent(String.self, forKey: .status)
        ref = try c.decodeIfPresent(String.self, forKey: .ref)
        hypothesis = c.lenientString(.hypothesis)
        metric = c.lenientString(.metric)
        baseline = c.lenientString(.baseline)
        target = c.lenientString(.target)
        check = c.lenientString(.check)
        tags = (try? c.decodeIfPresent([String].self, forKey: .tags)) ?? []
        values = c.lossy(MarketingValue.self, .values)
    }

    var date: Date? { OrbitalDates.parse(at) }
}

// MARK: - Collected across sections

/// A marketing block and the page it was found on, so a Done tap posts to the right section.
struct MarketingRef: Identifiable, Equatable {
    var id: String
    var location: PageLocation
    var block: MarketingBlock
}

/// One calendar day of ledger entries, newest first.
struct MarketingDay: Identifiable, Equatable {
    var id: Date { day }
    var day: Date
    var entries: [MarketingEntry]
}

enum MarketingFeed {
    static func days(_ entries: [MarketingEntry], calendar: Calendar = .current) -> [MarketingDay] {
        let dated = entries.compactMap { entry in entry.date.map { (entry, $0) } }
        let byDay = Dictionary(grouping: dated) { calendar.startOfDay(for: $0.1) }
        return byDay.keys.sorted(by: >).map { day in
            MarketingDay(day: day, entries: (byDay[day] ?? []).sorted { $0.1 > $1.1 }.map(\.0))
        }
    }

    /// "3 h old", "2 days old": how long a to-do has waited on you.
    static func age(_ raw: String?, now: Date = Date()) -> String? {
        guard let date = OrbitalDates.parse(raw) else { return nil }
        let seconds = max(0, now.timeIntervalSince(date))
        if seconds < 3600 { return "\(max(1, Int(seconds / 60))) min old" }
        if seconds < 48 * 3600 { return "\(Int(seconds / 3600)) h old" }
        return "\(Int(seconds / 86400)) days old"
    }

    /// "Tue 21 Oct" for an experiment's YYYY-MM-DD check date; the raw text otherwise.
    static func checkDay(_ raw: String?) -> String? {
        guard let raw, !raw.isEmpty else { return nil }
        guard let date = Insights.day(String(raw.prefix(10))) else { return raw }
        return date.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated))
    }
}

// MARK: - Styles

/// Colour, symbol, and name for each kind of ledger entry. The name always goes with the colour.
struct MarketingKind {
    let name: String
    let symbol: String
    let tint: Color

    init(_ kind: String) {
        switch kind {
        case "decision": (name, symbol, tint) = ("Decision", "arrow.triangle.branch", OrbitalTheme.violet)
        case "action": (name, symbol, tint) = ("Action", "bolt.fill", OrbitalTheme.accent)
        case "experiment": (name, symbol, tint) = ("Experiment", "flask.fill", Color(hex: "#5AC8FA") ?? .cyan)
        case "result": (name, symbol, tint) = ("Result", "chart.bar.xaxis", OrbitalTheme.success)
        case "todo": (name, symbol, tint) = ("To-do for you", "person.fill.questionmark", OrbitalTheme.warning)
        case "todo_done": (name, symbol, tint) = ("To-do done", "checkmark.circle.fill", OrbitalTheme.success)
        case "alert": (name, symbol, tint) = ("Alert", "exclamationmark.triangle.fill", OrbitalTheme.danger)
        case "score": (name, symbol, tint) = ("Scoreboard", "gauge.with.dots.needle.67percent", Color(hex: "#FF9F6B") ?? .orange)
        default: (name, symbol, tint) = ("Note", "note.text", OrbitalTheme.textSecondary)
        }
    }

    /// Filter chips, in reading order.
    static let filters: [(id: String, label: String)] = [
        ("", "Everything"), ("decision", "Decisions"), ("action", "Actions"), ("experiment", "Experiments"),
        ("result", "Results"), ("todo", "To-dos"), ("alert", "Alerts"), ("note", "Notes"), ("score", "Scoreboard"),
    ]

    /// The to-do chip also shows when a to-do was done.
    static func matches(_ kind: String, filter: String) -> Bool {
        filter.isEmpty || kind == filter || (filter == "todo" && kind == "todo_done") || (filter == "experiment" && kind == "result")
    }
}

/// How a brand key ("acme", "both") is named and coloured. The block may name its brands in `brands`;
/// otherwise a key is matched to a product or space the app knows (by id, id prefix, or initials).
struct MarketingBrand: Equatable {
    var name: String
    var short: String
    var tint: Color
    var symbol: String
    var image: String?

    init(name: String, short: String, tint: Color, symbol: String = "megaphone.fill", image: String? = nil) {
        self.name = name
        self.short = short
        self.tint = tint
        self.symbol = symbol
        self.image = image
    }

    /// The style for a key, from the page's brand list when it has one.
    init(_ brand: String?, in brands: [String: MarketingBrand] = [:]) {
        if let brand, let known = brands[brand] {
            self = known
            return
        }
        self = Self.fallback(brand)
    }

    static func fallback(_ brand: String?) -> MarketingBrand {
        guard let brand = Insights.nonEmpty(brand) else {
            return MarketingBrand(name: "Marketing", short: "-", tint: OrbitalTheme.textSecondary)
        }
        if brand == "both" || brand == "all" {
            return MarketingBrand(name: "Every brand", short: "All", tint: OrbitalTheme.accent, symbol: "square.grid.2x2.fill")
        }
        let name = Spaces.displayName(brand)
        let short = brand.count <= 3 ? brand.uppercased() : name.prefix(1).uppercased() + name.dropFirst()
        return MarketingBrand(name: brand.count <= 3 ? short : name, short: short, tint: tint(for: brand))
    }

    /// A steady colour per key, from the swatches people pick for spaces.
    static func tint(for key: String) -> Color {
        let swatches = OrbitalTheme.accentSwatches.dropFirst()
        let index = key.unicodeScalars.reduce(0) { ($0 &* 31 &+ Int($1.value)) & 0xFFFF } % swatches.count
        return Color(hex: swatches[swatches.startIndex + index]) ?? OrbitalTheme.accent
    }

    /// Every brand key on the block, named from `brands`, then the directory, then the key itself.
    static func registry(for block: MarketingBlock, directory: SpaceDirectory) -> [String: MarketingBrand] {
        var out: [String: MarketingBrand] = [:]
        for key in block.brandKeys {
            var style = fallback(key)
            if let match = match(key, in: directory) {
                style.name = match.name
                style.short = key.count <= 3 ? key.uppercased() : match.name
                style.tint = match.tint
                style.symbol = match.symbol
                style.image = match.image
            }
            if let declared = block.brands.first(where: { $0.id == key }) {
                if let name = Insights.nonEmpty(declared.name) { style.name = name }
                if let short = Insights.nonEmpty(declared.short) { style.short = short }
                if let color = declared.color.flatMap({ Color(hex: $0) }) { style.tint = color }
                if let symbol = Insights.nonEmpty(declared.symbol) { style.symbol = symbol }
            }
            out[key] = style
        }
        return out
    }

    private static func match(_ key: String, in directory: SpaceDirectory) -> (name: String, tint: Color, symbol: String, image: String?)? {
        let wanted = key.lowercased()
        func initials(_ name: String) -> String {
            name.split(whereSeparator: { !$0.isLetter && !$0.isNumber }).compactMap(\.first).map(String.init).joined().lowercased()
        }
        func fits(id: String, name: String) -> Bool {
            let id = id.lowercased()
            return id == wanted || id.hasPrefix(wanted + "-") || initials(name) == wanted
                || name.lowercased().hasPrefix(wanted + " ")
        }
        if let product = directory.products.first(where: { fits(id: $0.id, name: $0.name) }) {
            return (product.name, product.tint, product.symbol, product.iconImage)
        }
        if let space = directory.spaces.first(where: { fits(id: $0.id, name: $0.name) }) {
            return (space.name, space.tint, space.symbol, space.iconImage)
        }
        return nil
    }

    /// A brand filter keeps entries for that brand and the ones marked both.
    static func matches(_ brand: String?, filter: String) -> Bool {
        filter.isEmpty || brand == filter || brand == "both" || brand == "all"
    }
}

/// A brand the block names itself: `{"id": "acme", "name": "Acme", "short": "Acme", "color": "#5AC8FA"}`.
struct MarketingBrandInfo: Codable, Equatable {
    var id: String
    var name: String?
    var short: String?
    var color: String?
    var symbol: String?
}

private struct MarketingBrandsKey: EnvironmentKey {
    static let defaultValue: [String: MarketingBrand] = [:]
}

extension EnvironmentValues {
    /// Brand names and colours for the marketing block on screen.
    var marketingBrands: [String: MarketingBrand] {
        get { self[MarketingBrandsKey.self] }
        set { self[MarketingBrandsKey.self] = newValue }
    }
}

struct MarketingStatus {
    let name: String
    let symbol: String
    let tint: Color

    init(_ status: String?) {
        switch status {
        case "won": (name, symbol, tint) = ("Won", "trophy.fill", OrbitalTheme.success)
        case "lost": (name, symbol, tint) = ("Lost", "xmark.octagon.fill", OrbitalTheme.danger)
        case "inconclusive": (name, symbol, tint) = ("Inconclusive", "questionmark.circle.fill", OrbitalTheme.textSecondary)
        case "stopped": (name, symbol, tint) = ("Stopped", "stop.circle.fill", OrbitalTheme.textTertiary)
        default: (name, symbol, tint) = ("Running", "flask.fill", Color(hex: "#5AC8FA") ?? .cyan)
        }
    }
}
