import Foundation

// The Money tab's filters and the sums behind it. Every figure is added up from the ledger blocks
// the feeds send (see docs/DATA_PIPELINE.md, "ledger"); nothing is typed in by hand.

/// Income, refunds and payouts are told apart by a row's category and sign.
enum MoneyKind: String, CaseIterable, Identifiable, Hashable {
    case income = "Income"
    case refunds = "Refunds"
    case payouts = "Payouts"
    var id: String { rawValue }

    /// A category naming a payout or transfer is a payout; a refund category or a negative amount is a refund.
    static func of(category: String, amount: Double) -> MoneyKind {
        let lowered = category.lowercased()
        if lowered.contains("payout") || lowered.contains("transfer to bank") { return .payouts }
        if lowered.contains("refund") || lowered.contains("chargeback") || amount < 0 { return .refunds }
        return .income
    }
}

extension MoneyRow {
    var kind: MoneyKind { MoneyKind.of(category: category, amount: amount) }
}

extension MoneyEntry {
    var kind: MoneyKind { MoneyKind.of(category: category, amount: amount) }
}

/// The date range the Money tab is showing.
enum MoneyRange: String, CaseIterable, Identifiable, Hashable {
    case today = "Today"
    case week = "Week"
    case month = "Month"
    case last30 = "30 days"
    case year = "Year"
    case all = "All time"
    case custom = "Custom"
    var id: String { rawValue }

    var title: String {
        switch self {
        case .today: return "Today"
        case .week: return "This week"
        case .month: return "This month"
        case .last30: return "Last 30 days"
        case .year: return "This year"
        case .all: return "All time"
        case .custom: return "Custom range"
        }
    }

    /// Start and end days. `earliest` is the first day any ledger has, for All time.
    func range(now: Date = Date(), earliest: Date?, from: Date? = nil, to: Date? = nil, calendar: Calendar = .current) -> ClosedRange<Date> {
        let today = calendar.startOfDay(for: now)
        switch self {
        case .today: return MoneyPeriod.today.range(now: now, calendar: calendar)
        case .week: return MoneyPeriod.week.range(now: now, calendar: calendar)
        case .month: return MoneyPeriod.month.range(now: now, calendar: calendar)
        case .last30: return MoneyPeriod.last30.range(now: now, calendar: calendar)
        case .year:
            let start = calendar.dateInterval(of: .year, for: now)?.start ?? today
            return start ... today
        case .all:
            let start = min(earliest.map { calendar.startOfDay(for: $0) } ?? today, today)
            return start ... today
        case .custom:
            let lower = calendar.startOfDay(for: from ?? today)
            let upper = calendar.startOfDay(for: to ?? today)
            return min(lower, upper) ... max(lower, upper)
        }
    }

    /// The same span one period earlier, for the change pill. None for All time and Custom.
    func previous(now: Date = Date(), calendar: Calendar = .current) -> ClosedRange<Date>? {
        switch self {
        case .today: return MoneyPeriod.today.previousRange(now: now, calendar: calendar)
        case .week: return MoneyPeriod.week.previousRange(now: now, calendar: calendar)
        case .month: return MoneyPeriod.month.previousRange(now: now, calendar: calendar)
        case .last30: return MoneyPeriod.last30.previousRange(now: now, calendar: calendar)
        case .year:
            let current = range(now: now, earliest: nil, calendar: calendar)
            guard let start = calendar.date(byAdding: .year, value: -1, to: current.lowerBound),
                  let end = calendar.date(byAdding: .year, value: -1, to: current.upperBound) else { return nil }
            return start ... end
        case .all, .custom: return nil
        }
    }

    var comparison: String {
        switch self {
        case .today: return "vs yesterday"
        case .week: return "vs same days last week"
        case .month: return "vs same days last month"
        case .last30: return "vs the 30 days before"
        case .year: return "vs same days last year"
        case .all, .custom: return ""
        }
    }
}

/// What the Money tab is narrowed to. Pushed as a route when you tap into a business or product.
struct MoneyFilter: Hashable {
    /// SpaceDirectory space id.
    var business: String?
    /// Spaces.key of the product name.
    var product: String?
    var source: String?
    var category: String?
    /// nil: income and refunds together. Payouts only show when picked, since they move money already counted.
    var kind: MoneyKind?
    var range: MoneyRange = .month
    var from: Date?
    var to: Date?

    /// Narrowed by anything other than the date range.
    var isNarrowed: Bool { business != nil || product != nil || source != nil || category != nil || kind != nil }

    /// Same scope, every date and kind: "show all".
    var cleared: MoneyFilter { MoneyFilter() }

    func matches(_ row: MoneyRow, directory: SpaceDirectory) -> Bool {
        matches(space: row.space, product: row.product, source: row.source, category: row.category, kind: row.kind, directory: directory)
    }

    func matches(_ entry: MoneyEntry, directory: SpaceDirectory) -> Bool {
        matches(space: entry.space, product: entry.product, source: entry.source, category: entry.category, kind: entry.kind, directory: directory)
    }

    private func matches(space: String, product: String, source: String, category: String, kind: MoneyKind, directory: SpaceDirectory) -> Bool {
        if let business, MoneyLens.business(of: space, product: product, directory: directory) != business { return false }
        if let wanted = self.product, Spaces.key(product) != wanted { return false }
        if let wanted = self.source, MoneyLens.sourceName(source) != wanted { return false }
        if let wanted = self.category, category != wanted { return false }
        if let wanted = self.kind {
            if kind != wanted { return false }
        } else if kind == .payouts {
            return false
        }
        return true
    }
}

/// One line in a breakdown: a business, product, source or category.
struct MoneyGroup: Identifiable, Equatable {
    var id: String
    var name: String
    var total: Double
    var count: Int
    var lastDay: Date?
}

/// Adds rows up by business, product, source and category.
enum MoneyLens {
    enum Dimension: String, CaseIterable, Identifiable {
        case business = "Business"
        case product = "Product"
        case source = "Source"
        case category = "Category"
        var id: String { rawValue }
    }

    /// The space id a row belongs to: its space tag, else the space of the product it names.
    static func business(of space: String, product: String, directory: SpaceDirectory) -> String {
        let resolved = directory.resolve(space)
        if !resolved.isEmpty { return resolved }
        return directory.product(Spaces.key(product))?.spaceID ?? "other"
    }

    static func businessName(_ id: String, directory: SpaceDirectory) -> String {
        if let space = directory.space(id) { return space.name }
        return id == "other" ? "Other" : Spaces.displayName(id)
    }

    /// Source text as shown: blank sources read "Other".
    static func sourceName(_ raw: String) -> String {
        let trimmed = raw.trimmingCharacters(in: .whitespaces)
        return trimmed.isEmpty ? "Other" : trimmed
    }

    static func key(_ row: MoneyRow, _ dimension: Dimension, directory: SpaceDirectory) -> (id: String, name: String) {
        switch dimension {
        case .business:
            let id = business(of: row.space, product: row.product, directory: directory)
            return (id, businessName(id, directory: directory))
        case .product:
            return (Spaces.key(row.product), row.product)
        case .source:
            let name = sourceName(row.source)
            return (name, name)
        case .category:
            return (row.category, row.category)
        }
    }

    /// Totals per group inside `range`, largest first. Groups with nothing in range are left out.
    static func groups(_ rows: [MoneyRow], by dimension: Dimension, in range: ClosedRange<Date>, directory: SpaceDirectory) -> [MoneyGroup] {
        var out: [String: MoneyGroup] = [:]
        for row in rows where range.contains(row.day) {
            let key = key(row, dimension, directory: directory)
            var group = out[key.id] ?? MoneyGroup(id: key.id, name: key.name, total: 0, count: 0, lastDay: nil)
            group.total += row.amount
            group.count += row.count
            if abs(row.amount) > 0.004 { group.lastDay = max(group.lastDay ?? row.day, row.day) }
            out[key.id] = group
        }
        return out.values
            .filter { abs($0.total) > 0.004 || $0.count > 0 }
            .sorted { abs($0.total - $1.total) > 0.004 ? $0.total > $1.total : $0.name < $1.name }
    }

    static func total(_ rows: [MoneyRow], in range: ClosedRange<Date>) -> Double {
        rows.filter { range.contains($0.day) }.reduce(0) { $0 + $1.amount }
    }

    static func count(_ rows: [MoneyRow], in range: ClosedRange<Date>) -> Int {
        rows.filter { range.contains($0.day) }.reduce(0) { $0 + $1.count }
    }

    /// The breakdown one level below a filter: businesses, then products, then sources, then categories.
    static func nextDimension(after filter: MoneyFilter) -> Dimension {
        if filter.business == nil && filter.product == nil { return .business }
        if filter.product == nil { return .product }
        if filter.source == nil { return .source }
        return .category
    }
}
