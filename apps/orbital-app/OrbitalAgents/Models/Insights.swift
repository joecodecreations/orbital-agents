import Foundation

/// Where a block lives, so Home and the space pages can open the page it came from.
struct PageLocation: Hashable {
    var sectionID: String
    var nodeID: String?
    var tabID: String?
}

/// One product block, with the node or section it sits on.
struct ProductRef: Identifiable, Equatable {
    var id: String
    var location: PageLocation
    var title: String
    var subtitle: String?
    var iconImage: String?
    var symbol: String
    var accent: String?
    /// Space name, from the block or the section.
    var space: String
    var block: ProductBlock

    var checkedDate: Date? { OrbitalDates.parse(block.checkedAt) }

    /// Most gatherers run hourly. Three missed runs is worth a look.
    var isStale: Bool {
        guard let checked = checkedDate else { return false }
        return Date().timeIntervalSince(checked) > 3 * 3600
    }
}

/// One ledger block, with defaults filled from its section.
struct LedgerRef: Identifiable, Equatable {
    var id: String
    var location: PageLocation
    var title: String
    var iconImage: String?
    var symbol: String
    var accent: String?
    var space: String
    var source: String
    var block: LedgerBlock

    var currency: String { block.currency.uppercased() }
    var asOfDate: Date? { OrbitalDates.parse(block.asOf) }
}

/// A ledger day row with every tag resolved.
struct MoneyRow: Identifiable {
    var id: String
    var day: Date
    var amount: Double
    var net: Double?
    var count: Int
    var product: String
    var category: String
    var source: String
    var space: String
    var ledgerID: String
}

/// A single payment or proceeds line.
struct MoneyEntry: Identifiable {
    var id: String
    var at: Date
    var amount: Double
    var label: String?
    var status: String?
    var product: String
    var category: String
    var source: String
    var space: String
    var ledger: LedgerRef
}

enum MoneyPeriod: String, CaseIterable, Identifiable {
    case today = "Today"
    case week = "This week"
    case month = "This month"
    /// A rolling 30 days, so a monthly subscription paid late last month still shows.
    case last30 = "30 days"
    var id: String { rawValue }

    /// Start of the period and the end of today, in the device's calendar.
    func range(now: Date = Date(), calendar: Calendar = .current) -> ClosedRange<Date> {
        let today = calendar.startOfDay(for: now)
        let start: Date
        switch self {
        case .today:
            start = today
        case .week:
            start = calendar.dateInterval(of: .weekOfYear, for: now)?.start ?? today
        case .month:
            start = calendar.dateInterval(of: .month, for: now)?.start ?? today
        case .last30:
            start = calendar.date(byAdding: .day, value: -29, to: today) ?? today
        }
        return start ... today
    }

    /// Fits four across on a phone.
    var short: String {
        switch self {
        case .today: return "Today"
        case .week: return "Week"
        case .month: return "Month"
        case .last30: return "30 days"
        }
    }
}

enum Insights {
    /// Everything Home and the space pages read across sections.
    struct Collected: Equatable {
        var products: [ProductRef] = []
        var ledgers: [LedgerRef] = []
        var tasks: [TaskRef] = []
        var series: [SeriesRef] = []
    }

    /// Product, ledger, tasks, and series blocks across every section, in left-bar order then node order.
    static func collect(_ sections: [SectionDocument]) -> Collected {
        var found = Collected()
        var taskIDs = Set<String>()
        var seriesKeys = Set<String>()
        let ordered = sections.sorted { lhs, rhs in
            if lhs.order != rhs.order { return lhs.order < rhs.order }
            return lhs.title.localizedCaseInsensitiveCompare(rhs.title) == .orderedAscending
        }
        for section in ordered {
            for page in section.pages {
                for block in page.blocks {
                    let location = PageLocation(sectionID: section.id, nodeID: page.node?.id, tabID: page.tabID)
                    let key = [section.id, page.node?.id ?? "-", page.tabID ?? "-", block.id].joined(separator: "/")
                    let icon = page.node?.iconImage ?? section.iconImage
                    let symbol = page.node?.icon ?? section.icon
                    let sectionSpace = section.space ?? ""
                    switch block {
                    case .product(let product):
                        found.products.append(ProductRef(
                            id: key,
                            location: location,
                            title: page.node?.title ?? section.title,
                            subtitle: page.node?.subtitle ?? section.subtitle,
                            iconImage: icon,
                            symbol: symbol,
                            accent: section.accent,
                            space: nonEmpty(product.space) ?? sectionSpace,
                            block: product
                        ))
                    case .ledger(let ledger):
                        found.ledgers.append(LedgerRef(
                            id: key,
                            location: location,
                            title: nonEmpty(ledger.title) ?? page.node?.title ?? section.title,
                            iconImage: icon,
                            symbol: symbol,
                            accent: section.accent,
                            space: nonEmpty(ledger.space) ?? sectionSpace,
                            source: nonEmpty(ledger.source) ?? section.title,
                            block: ledger
                        ))
                    case .tasks(let block):
                        for item in block.items where taskIDs.insert("\(section.id)/\(item.id)").inserted {
                            found.tasks.append(TaskRef(id: "\(section.id)/\(item.id)", location: location, blockID: block.id, accent: section.accent, item: item))
                        }
                    case .series(var block):
                        if block.space.isEmpty { block.space = sectionSpace }
                        // The same product's series sent to two pages counts once.
                        let identity = [block.kind, Spaces.key(block.space), Spaces.key(block.product), block.site ?? "", block.id].joined(separator: "|")
                        if !block.metrics.isEmpty, seriesKeys.insert(identity).inserted {
                            found.series.append(SeriesRef(id: key, location: location, accent: section.accent, block: block))
                        }
                    default:
                        break
                    }
                }
            }
        }
        return found
    }

    /// Ledger days in one currency (the most common one when none is given).
    static func rows(_ ledgers: [LedgerRef], currency: String? = nil, calendar: Calendar = .current) -> [MoneyRow] {
        let wanted = currency ?? primaryCurrency(ledgers)
        var out: [MoneyRow] = []
        for ledger in ledgers where ledger.currency == wanted {
            for (index, day) in ledger.block.days.enumerated() {
                guard let date = Self.day(day.date, calendar: calendar) else { continue }
                out.append(MoneyRow(
                    id: "\(ledger.id)#\(index)",
                    day: date,
                    amount: day.amount,
                    net: day.net,
                    count: day.count ?? 0,
                    product: nonEmpty(day.product) ?? ledger.title,
                    category: nonEmpty(day.category) ?? "Other",
                    source: nonEmpty(day.source) ?? ledger.source,
                    space: nonEmpty(day.space) ?? ledger.space,
                    ledgerID: ledger.id
                ))
            }
        }
        return out
    }

    static func entries(_ ledgers: [LedgerRef], currency: String? = nil) -> [MoneyEntry] {
        let wanted = currency ?? primaryCurrency(ledgers)
        var out: [MoneyEntry] = []
        for ledger in ledgers where ledger.currency == wanted {
            for (index, entry) in ledger.block.entries.enumerated() {
                guard let at = OrbitalDates.parse(entry.at) ?? Self.day(entry.at) else { continue }
                out.append(MoneyEntry(
                    id: "\(ledger.id)#\(entry.id ?? String(index))",
                    at: at,
                    amount: entry.amount,
                    label: nonEmpty(entry.label),
                    status: nonEmpty(entry.status),
                    product: nonEmpty(entry.product) ?? ledger.title,
                    category: nonEmpty(entry.category) ?? "Other",
                    source: nonEmpty(entry.source) ?? ledger.source,
                    space: nonEmpty(entry.space) ?? ledger.space,
                    ledger: ledger
                ))
            }
        }
        return out.sorted { $0.at > $1.at }
    }

    /// The currency most ledgers report in, so totals never add dollars to euros.
    static func primaryCurrency(_ ledgers: [LedgerRef]) -> String {
        let counts = Dictionary(grouping: ledgers.filter(\.block.connected), by: \.currency).mapValues(\.count)
        return counts.max { lhs, rhs in lhs.value == rhs.value ? lhs.key > rhs.key : lhs.value < rhs.value }?.key ?? "USD"
    }

    static func total(_ rows: [MoneyRow], in range: ClosedRange<Date>) -> Double {
        rows.filter { range.contains($0.day) }.reduce(0) { $0 + $1.amount }
    }

    /// "2026-10-01" as midnight in the device's calendar. Ledger dates are calendar days, not instants.
    static func day(_ raw: String, calendar: Calendar = .current) -> Date? {
        let parts = raw.prefix(10).split(separator: "-").compactMap { Int($0) }
        guard parts.count == 3 else { return nil }
        return calendar.date(from: DateComponents(year: parts[0], month: parts[1], day: parts[2]))
    }

    static func nonEmpty(_ value: String?) -> String? {
        guard let value, !value.trimmingCharacters(in: .whitespaces).isEmpty else { return nil }
        return value
    }
}

enum OrbitalMoney {
    /// "$1,234" for whole amounts, "$24.99" otherwise, in the ledger's currency.
    static func format(_ amount: Double, currency: String = "USD", compact: Bool = false) -> String {
        let formatter = NumberFormatter()
        formatter.numberStyle = .currency
        formatter.currencyCode = currency
        if compact, abs(amount) >= 10_000 {
            formatter.maximumFractionDigits = 1
            formatter.minimumFractionDigits = 0
            let symbol = formatter.currencySymbol ?? "$"
            return amount >= 1_000_000
                ? String(format: "%@%.1fM", symbol, amount / 1_000_000)
                : String(format: "%@%.1fK", symbol, amount / 1000)
        }
        let whole = abs(amount - amount.rounded()) < 0.005
        formatter.minimumFractionDigits = whole ? 0 : 2
        formatter.maximumFractionDigits = whole ? 0 : 2
        return formatter.string(from: NSNumber(value: amount)) ?? "\(currency) \(amount)"
    }
}
