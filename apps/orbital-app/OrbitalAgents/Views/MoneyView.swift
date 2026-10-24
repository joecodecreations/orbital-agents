import Charts
import SwiftUI

/// All money, from every ledger block across sections: today, this week, this month, this year and all
/// time, a chart, and a row per business. Tap a business to see its products and sources, a product to
/// see its sources and categories, and any of them for the payments underneath. Filters narrow it by
/// business, product, source, category, date range, and income, refunds or payouts.
struct MoneyView: View {
    /// Set when pushed from a row: the business, product, source or category this page is about.
    let scope: MoneyFilter?
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @Environment(\.orbitalPushed) private var pushed
    @State private var filter: MoneyFilter
    @State private var showFilters = false
    /// Rows and entries, worked out once per change of the ledgers or the filter rather than on every draw.
    @State private var data = MoneyData()

    private struct MoneyData {
        var currency = "USD"
        var allRows: [MoneyRow] = []
        var allEntries: [MoneyEntry] = []
        var earliest: Date?
        var rows: [MoneyRow] = []
    }

    private let accent = OrbitalTheme.success

    init(scope: MoneyFilter? = nil) {
        self.scope = scope
        _filter = State(initialValue: scope ?? MoneyFilter())
    }

    // MARK: Data

    private var directory: SpaceDirectory { store.directory }
    private var currency: String { data.currency }
    private var allRows: [MoneyRow] { data.allRows }
    private var earliest: Date? { data.earliest }
    private var rows: [MoneyRow] { data.rows }
    private var range: ClosedRange<Date> { filter.range.range(earliest: earliest, from: filter.from, to: filter.to) }

    private var entries: [MoneyEntry] {
        let range = range
        return data.allEntries.filter { filter.matches($0, directory: directory) && range.contains(Calendar.current.startOfDay(for: $0.at)) }
    }

    private func reloadLedgers() {
        let currency = Insights.primaryCurrency(store.ledgers)
        data.currency = currency
        data.allRows = Insights.rows(store.ledgers, currency: currency)
        data.allEntries = Insights.entries(store.ledgers, currency: currency)
        data.earliest = data.allRows.map(\.day).min()
        refilter()
    }

    private func refilter() {
        let directory = directory
        data.rows = data.allRows.filter { filter.matches($0, directory: directory) }
    }

    private var showsActiveFilters: Bool {
        var locked = scope ?? MoneyFilter()
        locked.range = filter.range
        locked.from = filter.from
        locked.to = filter.to
        return filter != locked || filter.range != .month
    }

    /// Ledgers that hold money for this scope, connected or not.
    private var ledgersInScope: [LedgerRef] {
        store.ledgers.filter { ledger in
            if let business = filter.business {
                let own = MoneyLens.business(of: ledger.space, product: ledger.title, directory: directory) == business
                let tagged = ledger.block.days.contains { MoneyLens.business(of: $0.space ?? ledger.space, product: $0.product ?? ledger.title, directory: directory) == business }
                if !own && !tagged { return false }
            }
            if let product = filter.product {
                let named = ledger.block.days.contains { Spaces.key($0.product ?? ledger.title) == product }
                    || ledger.title.components(separatedBy: " · ").first.map(Spaces.key) == product
                if !named { return false }
            }
            if let source = filter.source, MoneyLens.sourceName(ledger.source) != source,
               !ledger.block.days.contains(where: { MoneyLens.sourceName($0.source ?? ledger.source) == source }) {
                return false
            }
            return true
        }
    }

    /// Subscriptions blocks for this scope: the product's own, or every product in the business.
    private var subscriptionRefs: [SubscriptionsRef] {
        store.subscriptions.filter { ref in
            if let product = filter.product { return ref.productID == product }
            if let business = filter.business {
                return MoneyLens.business(of: ref.space, product: ref.block.product, directory: directory) == business
            }
            return filter.source == nil && filter.category == nil
        }
    }

    private var title: String {
        if let product = filter.product {
            return directory.product(product)?.name ?? rows.first { Spaces.key($0.product) == product }?.product ?? Spaces.displayName(product)
        }
        if let business = filter.business { return MoneyLens.businessName(business, directory: directory) }
        if let source = filter.source { return source }
        if let category = filter.category { return category }
        return "All money"
    }

    private var eyebrow: String {
        if filter.product != nil, let business = filter.business ?? directory.product(filter.product)?.spaceID {
            return MoneyLens.businessName(business, directory: directory)
        }
        return "Money"
    }

    private var subtitle: String {
        let live = store.ledgers.filter(\.block.connected).count
        let total = store.ledgers.count
        if total == 0 { return "No money source has reported yet" }
        var parts = ["\(currency)", "\(live) of \(total) sources connected"]
        if scope == nil, filter.isNarrowed { parts.append("filtered") }
        return parts.joined(separator: " · ")
    }

    /// Colours follow each group's total over the range, so the biggest earner is always green.
    private func colors(for dimension: MoneyLens.Dimension) -> [String: Color] {
        MoneyColors.assign(MoneyLens.groups(rows, by: dimension, in: range, directory: directory).map(\.name))
    }

    // MARK: Body

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                OrbitalScreenHeader(eyebrow: eyebrow, title: title, subtitle: subtitle, accent: accent) {
                    filterButton
                }
                rangeBar
                kindBar
                if showsActiveFilters {
                    activeFilters
                }
                if store.ledgers.isEmpty {
                    emptyCard
                } else {
                    totalCard
                    ForEach(subscriptionRefs) { ref in
                        SubscriptionsSummaryCard(ref: ref, compact: filter.product == nil)
                    }
                    chartCard
                    breakdowns
                    if !entries.isEmpty || rows.contains(where: { range.contains($0.day) }) {
                        paymentsCard
                    }
                    sourcesCard
                }
            }
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, 36)
        }
        .scrollIndicators(.hidden)
        .orbitalScrollFade()
        .refreshable { await store.refresh() }
        .orbitalBackdrop(accent)
        .navigationTitle(title)
        .orbitalNavigationBar(pushed: pushed)
        .onAppear(perform: reloadLedgers)
        .onChange(of: store.ledgers) { reloadLedgers() }
        .onChange(of: store.directory) { refilter() }
        .onChange(of: filter) { refilter() }
        .sheet(isPresented: $showFilters) {
            MoneyFilterSheet(filter: $filter, rows: allRows, locked: scope ?? MoneyFilter())
                .environmentObject(store)
        }
    }

    // MARK: Controls

    private var narrowedCount: Int {
        let locked = scope ?? MoneyFilter()
        return [filter.business != locked.business, filter.product != locked.product, filter.source != locked.source,
                filter.category != locked.category, filter.kind != nil].filter { $0 }.count
    }

    private var filterButton: some View {
        Button { showFilters = true } label: {
            HStack(spacing: 5) {
                Image(systemName: "line.3.horizontal.decrease")
                    .font(.subheadline.weight(.bold))
                Text("Filter")
                    .font(.footnote.weight(.semibold))
                if narrowedCount > 0 {
                    Text("\(narrowedCount)")
                        .font(.caption2.weight(.bold).monospacedDigit())
                        .foregroundStyle(OrbitalTheme.backgroundDeep)
                        .padding(.horizontal, 6)
                        .padding(.vertical, 1)
                        .background(accent, in: Capsule())
                }
            }
            .foregroundStyle(accent)
            .padding(.horizontal, 12)
            .frame(minHeight: 36)
            .background(accent.opacity(0.12), in: Capsule())
            .overlay(Capsule().strokeBorder(accent.opacity(0.3), lineWidth: 1))
            .contentShape(Capsule())
        }
        .buttonStyle(OrbitalPressStyle())
        .accessibilityLabel(narrowedCount > 0 ? "Filter, \(narrowedCount) on" : "Filter")
    }

    private var rangeBar: some View {
        VStack(alignment: .leading, spacing: 8) {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(MoneyRange.allCases) { option in
                        MoneyChipButton(title: option.rawValue, selected: filter.range == option, tint: accent) {
                            withAnimation(.easeInOut(duration: 0.18)) {
                                filter.range = option
                                if option == .custom, filter.from == nil {
                                    filter.from = Calendar.current.date(byAdding: .day, value: -13, to: Date())
                                    filter.to = Date()
                                }
                            }
                        }
                    }
                }
                .padding(.vertical, 1)
            }
            if filter.range == .custom {
                HStack(spacing: 10) {
                    DatePicker("From", selection: Binding(get: { filter.from ?? Date() }, set: { filter.from = $0 }), in: (earliest ?? .distantPast) ... Date(), displayedComponents: .date)
                    DatePicker("To", selection: Binding(get: { filter.to ?? Date() }, set: { filter.to = $0 }), in: (earliest ?? .distantPast) ... Date(), displayedComponents: .date)
                }
                .font(.footnote.weight(.semibold))
                .foregroundStyle(OrbitalTheme.textSecondary)
                .tint(accent)
            }
        }
    }

    private var kindBar: some View {
        Picker("Kind", selection: $filter.kind) {
            Text("All").tag(MoneyKind?.none)
            ForEach(MoneyKind.allCases) { kind in
                Text(kind.rawValue).tag(MoneyKind?.some(kind))
            }
        }
        .pickerStyle(.segmented)
        .accessibilityLabel("Show income, refunds or payouts")
    }

    private var activeFilters: some View {
        let locked = scope ?? MoneyFilter()
        return FlowLayout(spacing: 6, lineSpacing: 6) {
            if let business = filter.business, business != locked.business {
                removable("Business: \(MoneyLens.businessName(business, directory: directory))") { filter.business = nil }
            }
            if let product = filter.product, product != locked.product {
                removable("Product: \(directory.product(product)?.name ?? Spaces.displayName(product))") { filter.product = nil }
            }
            if let source = filter.source, source != locked.source {
                removable("Source: \(source)") { filter.source = nil }
            }
            if let category = filter.category, category != locked.category {
                removable("Category: \(category)") { filter.category = nil }
            }
            if let kind = filter.kind {
                removable(kind.rawValue) { filter.kind = nil }
            }
            Button {
                withAnimation(.easeInOut(duration: 0.18)) {
                    filter = locked
                    filter.range = .all
                }
            } label: {
                Label("Show all", systemImage: "arrow.counterclockwise")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(accent)
                    .padding(.horizontal, 10)
                    .frame(minHeight: 30)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityHint("Clears the filters and shows every date")
        }
    }

    private func removable(_ text: String, remove: @escaping () -> Void) -> some View {
        Button {
            withAnimation(.easeInOut(duration: 0.18)) { remove() }
        } label: {
            HStack(spacing: 4) {
                Text(text)
                    .lineLimit(1)
                Image(systemName: "xmark")
                    .font(.system(size: 9, weight: .heavy))
            }
            .font(.caption.weight(.semibold))
            .foregroundStyle(OrbitalTheme.text)
            .padding(.horizontal, 10)
            .frame(minHeight: 30)
            .background(Color.white.opacity(0.07), in: Capsule())
            .overlay(Capsule().strokeBorder(Color.white.opacity(0.1), lineWidth: 1))
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Remove filter \(text)")
    }

    // MARK: Cards

    private var totalCard: some View {
        let total = MoneyLens.total(rows, in: range)
        let previous = filter.range.previous().map { MoneyLens.total(rows, in: $0) }
        let count = MoneyLens.count(rows, in: range)
        // Rows match every filter but the dates, which each tile sets for itself.
        let scoped = rows
        let noun: (String, String) = filter.kind == .payouts ? ("payout", "payouts") : (filter.kind == .refunds ? ("refund", "refunds") : ("sale", "sales"))
        return VStack(alignment: .leading, spacing: 12) {
            OrbitalEyebrow(text: "\(filter.range.title) · \(Self.label(range))", color: accent)
            Text(OrbitalMoney.format(total, currency: currency))
                .font(.system(size: 44, weight: .bold, design: .rounded))
                .monospacedDigit()
                .foregroundStyle(total > 0 ? OrbitalTheme.text : OrbitalTheme.textSecondary)
                .lineLimit(1)
                .minimumScaleFactor(0.5)
                .contentTransition(.numericText())
            HStack(spacing: 8) {
                if let previous, let delta = OrbitalMoney.delta(total, previous, currency: currency) {
                    DeltaPill(delta: delta.text, direction: filter.kind == .refunds ? (delta.direction == "up" ? "down" : "up") : delta.direction)
                    Text(filter.range.comparison)
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
                Spacer(minLength: 0)
                if count > 0 {
                    Text("\(count) \(count == 1 ? noun.0 : noun.1)")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.textSecondary)
                }
            }
            MoneyPeriodTiles(rows: scoped, earliest: earliest, currency: currency, selected: filter.range) { picked in
                withAnimation(.easeInOut(duration: 0.18)) { filter.range = picked }
            }
        }
        .orbitalCard(padding: 18, tint: accent)
    }

    private var chartDimension: MoneyLens.Dimension {
        let next = MoneyLens.nextDimension(after: filter)
        // A single business on the root page stacks better by product.
        return next
    }

    private var chartCard: some View {
        let dimension = chartDimension
        let palette = colors(for: dimension)
        let groups = MoneyLens.groups(rows, by: dimension, in: range, directory: directory)
        return VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .firstTextBaseline) {
                Text(MoneyRangeChart.usesMonths(range) ? "By month" : "By day")
                    .font(.headline)
                    .foregroundStyle(OrbitalTheme.text)
                Spacer(minLength: 0)
                Text("by \(dimension.rawValue.lowercased())")
                    .font(.caption2)
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            MoneyRangeChart(rows: rows, range: range, currency: currency, colors: palette) { row in
                MoneyLens.key(row, dimension, directory: directory).name
            }
            if groups.count > 1 {
                FlowLayout(spacing: 10, lineSpacing: 6) {
                    ForEach(groups) { group in
                        HStack(spacing: 5) {
                            RoundedRectangle(cornerRadius: 2, style: .continuous)
                                .fill(palette[group.name] ?? accent)
                                .frame(width: 9, height: 9)
                            Text(group.name)
                                .font(.caption.weight(.medium))
                                .foregroundStyle(OrbitalTheme.textSecondary)
                                .lineLimit(1)
                        }
                    }
                }
            }
        }
        .orbitalCard(padding: 16)
    }

    /// One or two breakdowns, each a level below this page.
    @ViewBuilder
    private var breakdowns: some View {
        let next = MoneyLens.nextDimension(after: filter)
        breakdownCard(next)
        switch next {
        case .business: breakdownCard(.product)
        case .product: breakdownCard(.source)
        case .source: breakdownCard(.category)
        case .category: EmptyView()
        }
    }

    private func breakdownCard(_ dimension: MoneyLens.Dimension) -> some View {
        let inRange = MoneyLens.groups(rows, by: dimension, in: range, directory: directory)
        let everything = inRange.isEmpty ? MoneyLens.groups(rows, by: dimension, in: Date.distantPast ... Date(), directory: directory) : inRange
        let total = everything.reduce(0) { $0 + max(0, $1.total) }
        let palette = colors(for: dimension)
        return VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(Self.heading(dimension))
                    .font(.headline)
                    .foregroundStyle(OrbitalTheme.text)
                Spacer(minLength: 0)
                Text("\(everything.count)")
                    .font(.caption.weight(.bold).monospacedDigit())
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            if inRange.isEmpty && !everything.isEmpty {
                Text("Nothing in \(filter.range.title.lowercased()). All time:")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            if everything.isEmpty {
                Text("Nothing recorded for this filter.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .padding(.vertical, 4)
            }
            VStack(spacing: 2) {
                ForEach(everything) { group in
                    NavigationLink(value: BrowseRoute.money(narrowed(dimension, group))) {
                        MoneyGroupRow(
                            group: group,
                            share: total > 0 ? max(0, group.total) / total : 0,
                            currency: currency,
                            tint: palette[group.name] ?? accent,
                            icon: icon(dimension, group)
                        )
                    }
                    .buttonStyle(OrbitalPressStyle())
                }
            }
        }
        .orbitalCard(padding: 16)
    }

    private func narrowed(_ dimension: MoneyLens.Dimension, _ group: MoneyGroup) -> MoneyFilter {
        var next = filter
        switch dimension {
        case .business: next.business = group.id
        case .product: next.product = group.id
        case .source: next.source = group.id
        case .category: next.category = group.id
        }
        return next
    }

    private func icon(_ dimension: MoneyLens.Dimension, _ group: MoneyGroup) -> (image: String?, symbol: String, tint: Color)? {
        switch dimension {
        case .business:
            guard let space = directory.space(group.id) else { return nil }
            return (space.iconImage, space.symbol, space.tint)
        case .product:
            // Only a product with its own picture or symbol; the generic app glyph reads as a placeholder.
            guard let product = directory.product(group.id), product.iconImage != nil || product.symbol != "app.fill" else { return nil }
            return (product.iconImage, product.symbol, product.tint)
        case .source, .category:
            return nil
        }
    }

    private var paymentsCard: some View {
        let latest = Array(entries.prefix(6))
        return VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text("Payments")
                    .font(.headline)
                    .foregroundStyle(OrbitalTheme.text)
                Spacer(minLength: 0)
                NavigationLink(value: BrowseRoute.moneyEntries(filter)) {
                    HStack(spacing: 3) {
                        Text("Every entry")
                        Image(systemName: "chevron.right")
                            .font(.caption2.weight(.bold))
                    }
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(accent)
                    .frame(minHeight: 32)
                }
                .buttonStyle(.plain)
            }
            if latest.isEmpty {
                Text("The ledgers here send daily totals, not single payments. Every entry lists them day by day.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                VStack(spacing: 0) {
                    ForEach(latest) { entry in
                        MoneyEntryRow(entry: entry)
                    }
                }
            }
        }
        .orbitalCard(padding: 16)
    }

    private var sourcesCard: some View {
        let ledgers = ledgersInScope
        return VStack(alignment: .leading, spacing: 8) {
            Text("Where it comes from")
                .font(.headline)
                .foregroundStyle(OrbitalTheme.text)
            if ledgers.isEmpty {
                Text("No ledger reports money for this filter.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            VStack(spacing: 0) {
                ForEach(ledgers) { ledger in
                    Button {
                        router.show(ledger.location, tab: ledger.location.tabID)
                    } label: {
                        MoneySourceRow(ledger: ledger, primaryCurrency: currency)
                    }
                    .buttonStyle(OrbitalPressStyle())
                }
            }
        }
        .orbitalCard(padding: 16)
    }

    private var emptyCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            Image(systemName: "dollarsign.circle")
                .font(.title)
                .foregroundStyle(accent)
            Text("No money yet")
                .font(.headline)
                .foregroundStyle(OrbitalTheme.text)
            Text("Money shows up here when a feed pushes a ledger block: store sales, subscriptions, royalties, payouts. See docs/DATA_PIPELINE.md, \"ledger\".")
                .font(.footnote)
                .foregroundStyle(OrbitalTheme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .orbitalCard(padding: 18, tint: accent)
    }

    // MARK: Helpers

    static func heading(_ dimension: MoneyLens.Dimension) -> String {
        switch dimension {
        case .business: return "By business"
        case .product: return "By product"
        case .source: return "By source"
        case .category: return "By category"
        }
    }

    static func label(_ range: ClosedRange<Date>) -> String {
        let calendar = Calendar.current
        if calendar.isDate(range.lowerBound, inSameDayAs: range.upperBound) {
            return range.lowerBound.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated))
        }
        let sameYear = calendar.component(.year, from: range.lowerBound) == calendar.component(.year, from: range.upperBound)
        let start = sameYear ? range.lowerBound.formatted(.dateTime.day().month(.abbreviated)) : range.lowerBound.formatted(.dateTime.day().month(.abbreviated).year())
        return "\(start) – \(range.upperBound.formatted(.dateTime.day().month(.abbreviated).year()))"
    }
}

// MARK: - Pieces

/// A small selectable capsule.
struct MoneyChipButton: View {
    let title: String
    let selected: Bool
    var tint: Color = OrbitalTheme.success
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Text(title)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(selected ? OrbitalTheme.backgroundDeep : OrbitalTheme.textSecondary)
                .padding(.horizontal, 12)
                .frame(minHeight: 34)
                .background(selected ? AnyShapeStyle(tint) : AnyShapeStyle(Color.white.opacity(0.06)), in: Capsule())
                .overlay(Capsule().strokeBorder(selected ? Color.clear : Color.white.opacity(0.08), lineWidth: 1))
                .contentShape(Capsule())
        }
        .buttonStyle(OrbitalPressStyle())
        .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
    }
}

/// Today, this week, this month, this year and all time. Tapping one picks that range.
struct MoneyPeriodTiles: View {
    let rows: [MoneyRow]
    let earliest: Date?
    let currency: String
    let selected: MoneyRange
    let onPick: (MoneyRange) -> Void

    private static let ranges: [MoneyRange] = [.today, .week, .month, .year, .all]

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 96), spacing: 8)], spacing: 8) {
            ForEach(Self.ranges) { option in
                let total = MoneyLens.total(rows, in: option.range(earliest: earliest))
                Button { onPick(option) } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(option.title.uppercased())
                            .font(.system(size: 9.5, weight: .bold))
                            .tracking(0.5)
                            .foregroundStyle(selected == option ? OrbitalTheme.success : OrbitalTheme.textTertiary)
                            .lineLimit(1)
                        Text(OrbitalMoney.format(total, currency: currency, compact: true))
                            .font(.system(.headline, design: .rounded).weight(.bold))
                            .monospacedDigit()
                            .foregroundStyle(abs(total) > 0.004 ? OrbitalTheme.text : OrbitalTheme.textSecondary)
                            .lineLimit(1)
                            .minimumScaleFactor(0.6)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
                    .background(Color.white.opacity(selected == option ? 0.08 : 0.04), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .overlay(
                        RoundedRectangle(cornerRadius: 12, style: .continuous)
                            .strokeBorder(selected == option ? OrbitalTheme.success.opacity(0.45) : Color.clear, lineWidth: 1)
                    )
                    .contentShape(Rectangle())
                }
                .buttonStyle(OrbitalPressStyle())
                .accessibilityLabel("\(option.title), \(OrbitalMoney.format(total, currency: currency))")
            }
        }
    }
}

/// One business, product, source or category with its share of the total.
struct MoneyGroupRow: View {
    let group: MoneyGroup
    let share: Double
    let currency: String
    let tint: Color
    var icon: (image: String?, symbol: String, tint: Color)?

    var body: some View {
        HStack(spacing: 12) {
            if let icon {
                AppIcon(image: icon.image, symbol: icon.symbol, tint: icon.tint, size: 34)
            } else {
                RoundedRectangle(cornerRadius: 3, style: .continuous)
                    .fill(tint)
                    .frame(width: 6, height: 30)
            }
            VStack(alignment: .leading, spacing: 5) {
                HStack(alignment: .firstTextBaseline) {
                    Text(group.name)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                        .lineLimit(1)
                    Spacer(minLength: 8)
                    Text(OrbitalMoney.format(group.total, currency: currency))
                        .font(.subheadline.weight(.bold).monospacedDigit())
                        .foregroundStyle(group.total < 0 ? OrbitalTheme.danger : OrbitalTheme.text)
                }
                GeometryReader { geo in
                    ZStack(alignment: .leading) {
                        Capsule().fill(Color.white.opacity(0.06))
                        Capsule().fill(tint).frame(width: max(share > 0 ? 4 : 0, geo.size.width * share))
                    }
                }
                .frame(height: 5)
                HStack {
                    Text(detail)
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(OrbitalTheme.textTertiary)
                    Spacer(minLength: 0)
                }
            }
            Image(systemName: "chevron.right")
                .font(.caption2.weight(.bold))
                .foregroundStyle(OrbitalTheme.textTertiary)
        }
        .padding(.vertical, 8)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityHint("Shows this \(group.name)'s money")
    }

    private var detail: String {
        var parts: [String] = []
        if share >= 0.005 { parts.append(share.formatted(.percent.precision(.fractionLength(0)))) } else if share > 0 { parts.append("<1%") }
        if group.count > 0 { parts.append("\(group.count) \(group.count == 1 ? "sale" : "sales")") }
        if let last = group.lastDay {
            parts.append("last \(Calendar.current.isDateInToday(last) ? "today" : last.formatted(.dateTime.day().month(.abbreviated)))")
        }
        return parts.joined(separator: " · ")
    }
}

/// One ledger: where it comes from, whether it is connected, and when it last reported.
struct MoneySourceRow: View {
    let ledger: LedgerRef
    let primaryCurrency: String

    var body: some View {
        HStack(spacing: 12) {
            AppIcon(image: ledger.iconImage, symbol: ledger.symbol, tint: OrbitalTheme.accentColor(ledger.accent), size: 32)
            VStack(alignment: .leading, spacing: 2) {
                Text(ledger.title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
                    .lineLimit(1)
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .lineLimit(2)
            }
            Spacer(minLength: 8)
            if ledger.block.connected {
                StatusPill(text: "Connected", status: "live")
            } else {
                StatusPill(text: "Not connected", status: "pending")
            }
        }
        .padding(.vertical, 7)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens the page this money comes from")
    }

    private var detail: String {
        var parts = [ledger.source]
        if ledger.currency != primaryCurrency { parts.append("\(ledger.currency), not in the \(primaryCurrency) totals") }
        if let relative = OrbitalDates.relative(ledger.block.asOf) { parts.append("checked \(relative)") }
        return parts.joined(separator: " · ")
    }
}

/// Bars over any date range: one per day up to about three months, one per month after that.
struct MoneyRangeChart: View {
    let rows: [MoneyRow]
    let range: ClosedRange<Date>
    var currency = "USD"
    var colors: [String: Color] = [:]
    var height: CGFloat = 180
    let group: (MoneyRow) -> String

    static func usesMonths(_ range: ClosedRange<Date>) -> Bool {
        (Calendar.current.dateComponents([.day], from: range.lowerBound, to: range.upperBound).day ?? 0) > 92
    }

    var body: some View {
        let months = Self.usesMonths(range)
        let bars = bars(months: months)
        if bars.allSatisfy({ abs($0.amount) < 0.005 }) {
            VStack(spacing: 6) {
                Image(systemName: "chart.bar.xaxis")
                    .font(.title3)
                Text("No money in this range.")
                    .font(.footnote)
            }
            .foregroundStyle(OrbitalTheme.textTertiary)
            .frame(maxWidth: .infinity, minHeight: height * 0.6)
        } else {
            let unit: Calendar.Component = months ? .month : .day
            let groups = orderedGroups(bars)
            Chart(bars) { bar in
                BarMark(x: .value("Date", bar.day, unit: unit), y: .value("Amount", bar.amount))
                    .foregroundStyle(by: .value("Group", bar.group))
                    .cornerRadius(3)
            }
            .chartForegroundStyleScale(domain: groups, range: groups.map { colors[$0] ?? OrbitalTheme.accent })
            .chartLegend(.hidden)
            .chartXScale(domain: xDomain(months: months))
            .chartXAxis {
                AxisMarks(values: .automatic(desiredCount: 5)) { _ in
                    AxisValueLabel(format: months ? .dateTime.month(.abbreviated) : .dateTime.day().month(.abbreviated), centered: true)
                        .font(.caption2)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
            }
            .chartYAxis {
                AxisMarks(position: .leading, values: .automatic(desiredCount: 4)) { value in
                    AxisGridLine(stroke: StrokeStyle(lineWidth: 1, dash: [3, 4]))
                        .foregroundStyle(OrbitalTheme.hairline.opacity(0.6))
                    AxisValueLabel {
                        if let amount = value.as(Double.self) {
                            Text(OrbitalMoney.format(amount, currency: currency, compact: true))
                                .font(.caption2)
                                .foregroundStyle(OrbitalTheme.textTertiary)
                        }
                    }
                }
            }
            .frame(height: height)
            .accessibilityLabel(months ? "Money by month" : "Money by day")
        }
    }

    private func xDomain(months: Bool) -> ClosedRange<Date> {
        let calendar = Calendar.current
        if months {
            let start = calendar.dateInterval(of: .month, for: range.lowerBound)?.start ?? range.lowerBound
            let end = calendar.dateInterval(of: .month, for: range.upperBound)?.end ?? range.upperBound
            return start ... end
        }
        let end = calendar.date(byAdding: .day, value: 1, to: range.upperBound) ?? range.upperBound
        return range.lowerBound ... end
    }

    private func bars(months: Bool) -> [MoneyBar] {
        let calendar = Calendar.current
        var sums: [String: MoneyBar] = [:]
        for row in rows where range.contains(row.day) {
            let bucket = months ? (calendar.dateInterval(of: .month, for: row.day)?.start ?? row.day) : row.day
            let name = group(row)
            let key = "\(bucket.timeIntervalSince1970)|\(name)"
            var bar = sums[key] ?? MoneyBar(day: bucket, group: name, amount: 0, inPeriod: true)
            bar.amount += row.amount
            sums[key] = bar
        }
        return sums.values.sorted { $0.day < $1.day }
    }

    private func orderedGroups(_ bars: [MoneyBar]) -> [String] {
        var totals: [String: Double] = [:]
        for bar in bars { totals[bar.group, default: 0] += bar.amount }
        return totals.sorted { $0.value > $1.value }.map(\.key)
    }
}

// MARK: - Filters sheet

/// Business, product, source, category, income or refunds or payouts, and the dates. "Show all" clears them.
struct MoneyFilterSheet: View {
    @Binding var filter: MoneyFilter
    let rows: [MoneyRow]
    /// What the page itself is about; these stay set.
    let locked: MoneyFilter
    @EnvironmentObject private var store: OrbitalStore
    @Environment(\.dismiss) private var dismiss

    private var directory: SpaceDirectory { store.directory }

    /// Rows that match everything but the dimension being picked, so each list only offers what exists.
    private func options(_ dimension: MoneyLens.Dimension) -> [MoneyGroup] {
        var probe = filter
        probe.range = .all
        switch dimension {
        case .business: probe.business = locked.business
        case .product: probe.product = locked.product
        case .source: probe.source = locked.source
        case .category: probe.category = locked.category
        }
        let matching = rows.filter { probe.matches($0, directory: directory) }
        return MoneyLens.groups(matching, by: dimension, in: Date.distantPast ... Date.distantFuture, directory: directory)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section("Show") {
                    Picker("Money", selection: $filter.kind) {
                        Text("Income and refunds").tag(MoneyKind?.none)
                        ForEach(MoneyKind.allCases) { Text($0.rawValue).tag(MoneyKind?.some($0)) }
                    }
                    Picker("Dates", selection: $filter.range) {
                        ForEach(MoneyRange.allCases) { Text($0.title).tag($0) }
                    }
                    if filter.range == .custom {
                        DatePicker("From", selection: Binding(get: { filter.from ?? Date() }, set: { filter.from = $0 }), in: ...Date(), displayedComponents: .date)
                        DatePicker("To", selection: Binding(get: { filter.to ?? Date() }, set: { filter.to = $0 }), in: ...Date(), displayedComponents: .date)
                    }
                }
                Section("Narrow to") {
                    if locked.business == nil {
                        picker("Business", .business, selection: $filter.business)
                    }
                    if locked.product == nil {
                        picker("Product", .product, selection: $filter.product)
                    }
                    if locked.source == nil {
                        picker("Source", .source, selection: $filter.source)
                    }
                    if locked.category == nil {
                        picker("Category", .category, selection: $filter.category)
                    }
                }
                Section {
                    Button {
                        filter = locked
                        filter.range = .all
                        dismiss()
                    } label: {
                        Label("Show all", systemImage: "arrow.counterclockwise")
                    }
                } footer: {
                    Text("Payouts are money moving to your bank, already counted when it came in, so they show only when you pick Payouts. Amounts in other currencies are listed under Where it comes from and left out of the totals.")
                }
            }
            .scrollContentBackground(.hidden)
            .background(OrbitalTheme.background)
            .navigationTitle("Filter money")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
        }
        .tint(OrbitalTheme.success)
        .preferredColorScheme(.dark)
        .presentationDetents([.medium, .large])
    }

    private func picker(_ title: String, _ dimension: MoneyLens.Dimension, selection: Binding<String?>) -> some View {
        Picker(title, selection: selection) {
            Text("All").tag(String?.none)
            ForEach(options(dimension)) { group in
                Text(group.name).tag(String?.some(group.id))
            }
        }
    }
}

// MARK: - Every entry

/// Single payments, then every ledger day, for a filter. Newest first.
struct MoneyEntriesView: View {
    let scope: MoneyFilter
    @EnvironmentObject private var store: OrbitalStore
    @Environment(\.orbitalPushed) private var pushed
    @State private var range: MoneyRange

    init(scope: MoneyFilter) {
        self.scope = scope
        _range = State(initialValue: scope.range)
    }

    private var directory: SpaceDirectory { store.directory }
    private var currency: String { Insights.primaryCurrency(store.ledgers) }

    private var filter: MoneyFilter {
        var out = scope
        out.range = range
        return out
    }

    private var rows: [MoneyRow] { Insights.rows(store.ledgers, currency: currency).filter { filter.matches($0, directory: directory) } }
    private var window: ClosedRange<Date> {
        filter.range.range(earliest: rows.map(\.day).min(), from: filter.from, to: filter.to)
    }

    private var entries: [MoneyEntry] {
        Insights.entries(store.ledgers, currency: currency)
            .filter { filter.matches($0, directory: directory) && window.contains(Calendar.current.startOfDay(for: $0.at)) }
    }

    private var days: [(day: Date, rows: [MoneyRow])] {
        let inside = rows.filter { window.contains($0.day) && (abs($0.amount) > 0.004 || $0.count > 0) }
        return Dictionary(grouping: inside, by: \.day)
            .map { (day: $0.key, rows: $0.value.sorted { $0.amount > $1.amount }) }
            .sorted { $0.day > $1.day }
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                OrbitalScreenHeader(eyebrow: "Money", title: "Every entry", subtitle: "\(range.title) · \(MoneyView.label(window))", accent: OrbitalTheme.success)
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 6) {
                        ForEach(MoneyRange.allCases.filter { $0 != .custom || scope.range == .custom }) { option in
                            MoneyChipButton(title: option.rawValue, selected: range == option) { range = option }
                        }
                    }
                }
                if !entries.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Payments")
                            .font(.headline)
                            .foregroundStyle(OrbitalTheme.text)
                        VStack(spacing: 0) {
                            ForEach(entries) { MoneyEntryRow(entry: $0) }
                        }
                    }
                    .orbitalCard(padding: 16)
                }
                VStack(alignment: .leading, spacing: 6) {
                    Text("Day by day")
                        .font(.headline)
                        .foregroundStyle(OrbitalTheme.text)
                    if days.isEmpty {
                        Text("Nothing in this range.")
                            .font(.footnote)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                    }
                    ForEach(days, id: \.day) { day in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack {
                                Text(day.day.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated).year()))
                                    .font(.caption.weight(.bold))
                                    .foregroundStyle(OrbitalTheme.textSecondary)
                                Spacer(minLength: 0)
                                Text(OrbitalMoney.format(day.rows.reduce(0) { $0 + $1.amount }, currency: currency))
                                    .font(.caption.weight(.bold).monospacedDigit())
                                    .foregroundStyle(OrbitalTheme.textSecondary)
                            }
                            .padding(.top, 8)
                            ForEach(day.rows) { row in
                                MoneyDayRow(row: row, currency: currency, business: MoneyLens.businessName(MoneyLens.business(of: row.space, product: row.product, directory: directory), directory: directory))
                            }
                        }
                    }
                }
                .orbitalCard(padding: 16)
            }
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, 36)
        }
        .scrollIndicators(.hidden)
        .refreshable { await store.refresh() }
        .orbitalBackdrop(OrbitalTheme.success)
        .navigationTitle("Every entry")
        .orbitalNavigationBar(pushed: pushed)
    }
}

/// One ledger day: product, category and source, how many sales, and the amount.
struct MoneyDayRow: View {
    let row: MoneyRow
    let currency: String
    let business: String

    var body: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 2) {
                Text(row.product)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
                    .lineLimit(1)
                Text([business, row.category, MoneyLens.sourceName(row.source)].joined(separator: " · "))
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .lineLimit(1)
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 2) {
                Text(OrbitalMoney.format(row.amount, currency: currency))
                    .font(.subheadline.weight(.bold).monospacedDigit())
                    .foregroundStyle(row.amount < 0 ? OrbitalTheme.danger : (row.amount > 0 ? OrbitalTheme.success : OrbitalTheme.textSecondary))
                if row.count > 0 {
                    Text("\(row.count) \(row.count == 1 ? "sale" : "sales")")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
            }
        }
        .padding(.vertical, 5)
        .accessibilityElement(children: .combine)
    }
}
