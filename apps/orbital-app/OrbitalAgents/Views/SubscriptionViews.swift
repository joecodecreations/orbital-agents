import Charts
import SwiftUI

// One product's subscriptions, from its `subscriptions` blocks: subscribers, MRR, trials and when each
// ends, new, cancelled and churned for a period, failed payments, and every subscription. Only what
// the feed sends is shown: a customer's email or id, never card details.

/// The full screen: reached from Money (a product's row) and from the product and space pages.
struct SubscriptionsView: View {
    /// SpaceDirectory product id.
    let productID: String
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @Environment(\.orbitalPushed) private var pushed
    @State private var period: MoneyRange = .month
    @State private var bucket: SubscriptionBucket = .all
    @State private var search = ""

    private var refs: [SubscriptionsRef] { store.subscriptions.filter { $0.productID == productID } }
    private var product: ProductInfo? { store.directory.product(productID) }
    private var name: String { product?.name ?? refs.first?.block.product ?? Spaces.displayName(productID) }
    private var tint: Color { product?.tint ?? OrbitalTheme.accent }
    private var currency: String { refs.first?.block.currency ?? "USD" }

    /// Every subscription across this product's blocks, once each.
    private var items: [SubscriptionItem] {
        var seen = Set<String>()
        return refs.flatMap(\.block.items).filter { seen.insert($0.id).inserted }
    }

    private var failed: [FailedPayment] {
        var seen = Set<String>()
        return refs.flatMap(\.block.failed).filter { seen.insert($0.id).inserted }
            .sorted { ($0.date ?? .distantPast) > ($1.date ?? .distantPast) }
    }

    private var range: ClosedRange<Date> {
        let earliest = items.compactMap(\.startedDate).min()
        return period.range(earliest: earliest)
    }

    private var stats: SubscriptionStats { SubscriptionStats.compute(items, failed: failed, in: range) }

    private var listed: [SubscriptionItem] {
        let now = Date()
        let query = search.trimmingCharacters(in: .whitespaces).lowercased()
        return SubscriptionStats.sorted(items).filter { item in
            (bucket == .all || item.bucket(now: now) == bucket)
                && (query.isEmpty || [item.customer, item.plan, item.statusLabel].compactMap { $0?.lowercased() }.contains { $0.contains(query) })
        }
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                header
                if refs.isEmpty {
                    Text("No subscriptions block for \(name) yet. Its feed adds one on its next run.")
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .orbitalCard(padding: 16)
                } else {
                    notConnected
                    periodBar
                    figures
                    payingChart
                    trialsCard
                    listCard
                    failedCard
                    footer
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
        .orbitalBackdrop(tint)
        .navigationTitle("Subscriptions")
        .orbitalNavigationBar(pushed: pushed)
    }

    private var header: some View {
        HStack(spacing: 14) {
            AppIcon(image: product?.iconImage, symbol: product.map { $0.symbol == "app.fill" ? "creditcard.fill" : $0.symbol } ?? "creditcard.fill", tint: tint, size: 52)
            VStack(alignment: .leading, spacing: 3) {
                OrbitalEyebrow(text: "Subscriptions", color: tint)
                Text(name)
                    .font(.title2.weight(.bold))
                    .foregroundStyle(OrbitalTheme.text)
                    .lineLimit(2)
                Text(sourceLine)
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textSecondary)
            }
            Spacer(minLength: 0)
        }
    }

    private var sourceLine: String {
        let sources = Array(Set(refs.compactMap(\.block.source))).sorted()
        var parts = [sources.isEmpty ? "From its feed" : sources.joined(separator: ", ")]
        if let relative = OrbitalDates.relative(refs.compactMap(\.block.asOf).max()) { parts.append("checked \(relative)") }
        return parts.joined(separator: " · ")
    }

    @ViewBuilder
    private var notConnected: some View {
        ForEach(refs.filter { !$0.block.connected }) { ref in
            Label(ref.block.note ?? "This source did not answer on its last run. The figures below are from the last good read.", systemImage: "exclamationmark.triangle.fill")
                .font(.footnote)
                .foregroundStyle(OrbitalTheme.warning)
                .frame(maxWidth: .infinity, alignment: .leading)
                .orbitalCard(padding: 14, tint: OrbitalTheme.warning)
        }
    }

    private static let periods: [MoneyRange] = [.week, .month, .last30, .year, .all]

    private var periodBar: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                ForEach(Self.periods) { option in
                    MoneyChipButton(title: option.rawValue, selected: period == option, tint: tint) { period = option }
                }
            }
            .padding(.vertical, 1)
        }
    }

    private var figures: some View {
        let stats = stats
        return VStack(alignment: .leading, spacing: 12) {
            OrbitalEyebrow(text: "Now", color: tint)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 140), spacing: 8)], spacing: 8) {
                SubscriptionFigure(label: "Active subscribers", value: "\(stats.active)", detail: stats.canceling > 0 ? "\(stats.canceling) set to cancel" : "paying now", tone: stats.active > 0 ? OrbitalTheme.success : nil)
                SubscriptionFigure(label: "MRR", value: OrbitalMoney.format(stats.mrr, currency: currency), detail: "monthly value of active", tone: stats.mrr > 0 ? OrbitalTheme.success : nil)
                SubscriptionFigure(label: "On a trial", value: "\(stats.trials)", detail: stats.trialsEndingSoon > 0 ? "\(stats.trialsEndingSoon) end within 3 days" : "none ending soon", tone: stats.trialsEndingSoon > 0 ? OrbitalTheme.warning : nil)
                SubscriptionFigure(label: "Past due", value: "\(stats.attention)", detail: stats.attention > 0 ? "payment trouble" : "all paid up", tone: stats.attention > 0 ? OrbitalTheme.danger : nil)
            }
            OrbitalEyebrow(text: "\(period.title) · \(MoneyView.label(range))", color: tint)
                .padding(.top, 4)
            LazyVGrid(columns: [GridItem(.adaptive(minimum: 140), spacing: 8)], spacing: 8) {
                SubscriptionFigure(label: "New subscriptions", value: "\(stats.newSubscriptions)", detail: "started paying plans", tone: stats.newSubscriptions > 0 ? OrbitalTheme.success : nil)
                SubscriptionFigure(label: "New trials", value: "\(stats.newTrials)", detail: "free trials started", tone: nil)
                SubscriptionFigure(label: "Cancelled", value: "\(stats.cancelled)", detail: "subscriptions ended", tone: stats.cancelled > 0 ? OrbitalTheme.warning : nil)
                SubscriptionFigure(label: "Churned", value: "\(stats.churned)", detail: churnDetail(stats), tone: stats.churned > 0 ? OrbitalTheme.danger : nil)
                SubscriptionFigure(label: "Failed payments", value: "\(stats.failed)", detail: stats.failed > 0 ? OrbitalMoney.format(stats.failedAmount, currency: currency) : "none", tone: stats.failed > 0 ? OrbitalTheme.danger : nil)
            }
        }
        .orbitalCard(padding: 16, tint: tint)
    }

    private func churnDetail(_ stats: SubscriptionStats) -> String {
        guard let rate = stats.churnRate else { return "paying customers who left" }
        return "\(rate.formatted(.percent.precision(.fractionLength(0 ... 1)))) of \(stats.payingAtStart) paying"
    }

    @ViewBuilder
    private var payingChart: some View {
        let calendar = Calendar.current
        let today = calendar.startOfDay(for: Date())
        let days = (0 ..< 90).reversed().compactMap { calendar.date(byAdding: .day, value: -$0, to: today) }
        let points = SubscriptionStats.payingByDay(items, days: days)
        if points.contains(where: { $0.count > 0 }) {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .firstTextBaseline) {
                    Text("Paying subscribers")
                        .font(.headline)
                        .foregroundStyle(OrbitalTheme.text)
                    Spacer(minLength: 0)
                    Text("last 90 days")
                        .font(.caption2)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
                Chart(points, id: \.day) { point in
                    LineMark(x: .value("Day", point.day, unit: .day), y: .value("Paying", point.count))
                        .interpolationMethod(.stepEnd)
                        .foregroundStyle(tint)
                    AreaMark(x: .value("Day", point.day, unit: .day), y: .value("Paying", point.count))
                        .interpolationMethod(.stepEnd)
                        .foregroundStyle(LinearGradient(colors: [tint.opacity(0.28), tint.opacity(0.02)], startPoint: .top, endPoint: .bottom))
                }
                .chartYAxis {
                    AxisMarks(position: .leading, values: .automatic(desiredCount: 3)) { _ in
                        AxisGridLine(stroke: StrokeStyle(lineWidth: 1, dash: [3, 4])).foregroundStyle(OrbitalTheme.hairline.opacity(0.6))
                        AxisValueLabel().font(.caption2).foregroundStyle(OrbitalTheme.textTertiary)
                    }
                }
                .chartXAxis {
                    AxisMarks(values: .automatic(desiredCount: 4)) { _ in
                        AxisValueLabel(format: .dateTime.day().month(.abbreviated)).font(.caption2).foregroundStyle(OrbitalTheme.textTertiary)
                    }
                }
                .frame(height: 130)
                .accessibilityLabel("Paying subscribers over the last 90 days")
                Text("From each subscription's start and cancel dates.")
                    .font(.caption2)
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            .orbitalCard(padding: 16)
        }
    }

    @ViewBuilder
    private var trialsCard: some View {
        let now = Date()
        let trials = items.filter { $0.isTrial(now: now) }.sorted { ($0.trialEndDate ?? .distantFuture) < ($1.trialEndDate ?? .distantFuture) }
        if !trials.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .firstTextBaseline) {
                    Text("Trials, soonest end first")
                        .font(.headline)
                        .foregroundStyle(OrbitalTheme.text)
                    Spacer(minLength: 0)
                    Text("\(trials.count)")
                        .font(.caption.weight(.bold).monospacedDigit())
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
                VStack(spacing: 0) {
                    ForEach(trials.prefix(8)) { item in
                        SubscriptionRow(item: item, currency: currency, tint: tint)
                    }
                }
                if trials.count > 8 {
                    Button("All \(trials.count) trials") { bucket = .trial }
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(tint)
                        .frame(minHeight: 32)
                }
            }
            .orbitalCard(padding: 16)
        }
    }

    private var listCard: some View {
        let now = Date()
        return VStack(alignment: .leading, spacing: 10) {
            Text("Subscriptions")
                .font(.headline)
                .foregroundStyle(OrbitalTheme.text)
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(SubscriptionBucket.allCases) { option in
                        let count = option == .all ? items.count : items.filter { $0.bucket(now: now) == option }.count
                        if option == .all || count > 0 {
                            MoneyChipButton(title: "\(option.rawValue) \(count)", selected: bucket == option, tint: tint) { bucket = option }
                        }
                    }
                }
            }
            if items.count > 8 {
                HStack(spacing: 8) {
                    Image(systemName: "magnifyingglass")
                        .foregroundStyle(OrbitalTheme.textTertiary)
                    TextField("Customer or plan", text: $search)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                }
                .font(.subheadline)
                .padding(.horizontal, 12)
                .frame(minHeight: 40)
                .background(Color.white.opacity(0.05), in: RoundedRectangle(cornerRadius: OrbitalTheme.controlRadius, style: .continuous))
            }
            if listed.isEmpty {
                Text("None here.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .padding(.vertical, 4)
            }
            VStack(spacing: 0) {
                ForEach(listed) { item in
                    SubscriptionRow(item: item, currency: currency, tint: tint)
                }
            }
        }
        .orbitalCard(padding: 16)
    }

    @ViewBuilder
    private var failedCard: some View {
        if !failed.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                Text("Failed payments")
                    .font(.headline)
                    .foregroundStyle(OrbitalTheme.text)
                VStack(spacing: 0) {
                    ForEach(failed.prefix(20)) { payment in
                        HStack(spacing: 10) {
                            Image(systemName: "xmark.octagon.fill")
                                .foregroundStyle(OrbitalTheme.danger)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(payment.customer ?? "Unknown customer")
                                    .font(.subheadline.weight(.semibold))
                                    .foregroundStyle(OrbitalTheme.text)
                                    .lineLimit(1)
                                Text([payment.reasonLabel, payment.date.map { $0.formatted(.dateTime.day().month(.abbreviated).hour().minute()) }].compactMap { $0 }.joined(separator: " · "))
                                    .font(.caption)
                                    .foregroundStyle(OrbitalTheme.textTertiary)
                                    .lineLimit(1)
                            }
                            Spacer(minLength: 8)
                            if let amount = payment.amount {
                                Text(OrbitalMoney.format(amount, currency: currency))
                                    .font(.subheadline.weight(.bold).monospacedDigit())
                                    .foregroundStyle(OrbitalTheme.danger)
                            }
                        }
                        .padding(.vertical, 6)
                        .accessibilityElement(children: .combine)
                    }
                }
            }
            .orbitalCard(padding: 16, tint: OrbitalTheme.danger)
        }
    }

    private var footer: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(refs) { ref in
                if let note = ref.block.note, ref.block.connected, !note.isEmpty {
                    Text(note)
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Text("Active counts subscriptions that are paid up or past due, including ones set to cancel at the end of their period. Churned counts cancelled subscriptions that had paid at least once. New trials include free trials inside the app.")
                .font(.caption)
                .foregroundStyle(OrbitalTheme.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 10) {
                if let first = refs.first {
                    Button {
                        router.show(first.location, tab: first.location.tabID)
                    } label: {
                        Label("Feed page", systemImage: "doc.text")
                    }
                }
                Button {
                    var filter = MoneyFilter()
                    filter.product = productID
                    router.push(.money(filter))
                } label: {
                    Label("\(name) money", systemImage: "dollarsign.circle")
                }
            }
            .font(.footnote.weight(.semibold))
            .foregroundStyle(tint)
            .buttonStyle(.plain)
            .frame(minHeight: 32)
        }
    }
}

/// A labelled figure in the subscriptions grid.
struct SubscriptionFigure: View {
    let label: String
    let value: String
    var detail: String?
    var tone: Color?

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(label.uppercased())
                .font(.system(size: 9.5, weight: .bold))
                .tracking(0.5)
                .foregroundStyle(OrbitalTheme.textTertiary)
                .lineLimit(1)
            Text(value)
                .font(.system(.title3, design: .rounded).weight(.bold))
                .monospacedDigit()
                .foregroundStyle(tone ?? OrbitalTheme.text)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            if let detail {
                Text(detail)
                    .font(.caption2)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .lineLimit(2)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(Color.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

/// One subscription: who, which plan, its status, when it started, and when it renews or ends.
struct SubscriptionRow: View {
    let item: SubscriptionItem
    let currency: String
    var tint: Color = OrbitalTheme.accent

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            VStack(alignment: .leading, spacing: 3) {
                Text(item.customer ?? "Customer")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Text(planLine)
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .lineLimit(1)
                Text(dateLine)
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .lineLimit(2)
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 4) {
                OrbitalChip(text: item.statusLabel, tint: statusTint)
                if item.monthly > 0.004 {
                    Text("\(OrbitalMoney.format(item.monthly, currency: currency))/mo")
                        .font(.caption.weight(.bold).monospacedDigit())
                        .foregroundStyle(item.isPaying ? OrbitalTheme.text : OrbitalTheme.textTertiary)
                }
            }
        }
        .padding(.vertical, 8)
        .accessibilityElement(children: .combine)
    }

    private var statusTint: Color {
        switch item.bucket() {
        case .active: return OrbitalTheme.success
        case .trial: return OrbitalTheme.accent
        case .canceling: return OrbitalTheme.warning
        case .attention: return OrbitalTheme.danger
        default: return OrbitalTheme.textTertiary
        }
    }

    private var planLine: String {
        var parts: [String] = []
        parts.append(item.plan ?? (item.isInAppTrial ? "Free trial" : "Plan"))
        if let amount = item.amount, amount > 0.004 {
            let per = item.interval.map { "/\($0 == "month" ? "mo" : ($0 == "year" ? "yr" : $0))" } ?? ""
            parts.append(OrbitalMoney.format(amount, currency: currency) + per)
        }
        if item.isInAppTrial { parts.append("in the app") }
        return parts.joined(separator: " · ")
    }

    private var dateLine: String {
        let format: Date.FormatStyle = .dateTime.day().month(.abbreviated).year()
        var parts: [String] = []
        if let started = item.startedDate { parts.append("Started \(started.formatted(format))") }
        let now = Date()
        if item.isTrial(now: now), let end = item.trialEndDate {
            parts.append("trial ends \(Self.relativeDay(end, now: now))")
        } else if let cancelled = item.canceledDate, !item.isPaying {
            parts.append("ended \(cancelled.formatted(format))")
        } else if let end = item.periodEndDate {
            if item.needsAttention, end < now {
                parts.append("was due \(end.formatted(format))")
            } else {
                parts.append(item.cancelAtPeriodEnd ? "ends \(end.formatted(format))" : "renews \(end.formatted(format))")
            }
        }
        return parts.joined(separator: " · ")
    }

    /// "today", "tomorrow", "in 3 days (28 Oct)".
    static func relativeDay(_ date: Date, now: Date = Date()) -> String {
        let calendar = Calendar.current
        let days = calendar.dateComponents([.day], from: calendar.startOfDay(for: now), to: calendar.startOfDay(for: date)).day ?? 0
        let day = date.formatted(.dateTime.day().month(.abbreviated))
        switch days {
        case ..<0: return day
        case 0: return "today"
        case 1: return "tomorrow"
        default: return "in \(days) days (\(day))"
        }
    }
}

/// A short card for Money, the product page and the space page. Tap for the full screen.
struct SubscriptionsSummaryCard: View {
    let ref: SubscriptionsRef
    /// One line of figures (Money's business and root pages); otherwise figures and the next trials.
    var compact = false
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter

    private var tint: Color { store.directory.product(ref.productID)?.tint ?? OrbitalTheme.accent }

    var body: some View {
        let now = Date()
        let block = ref.block
        let stats = SubscriptionStats.compute(block.items, failed: block.failed, in: MoneyPeriod.month.range())
        let trials = block.items.filter { $0.isTrial(now: now) }.sorted { ($0.trialEndDate ?? .distantFuture) < ($1.trialEndDate ?? .distantFuture) }
        Button {
            router.push(.subscriptions(ref.productID))
        } label: {
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    OrbitalEyebrow(text: "\(block.product) subscriptions", color: tint)
                    Spacer(minLength: 0)
                    if !block.connected {
                        StatusPill(text: "Not connected", status: "pending")
                    }
                    Image(systemName: "chevron.right")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(tint)
                }
                HStack(spacing: 8) {
                    figure("Active", "\(stats.active)", tone: stats.active > 0 ? OrbitalTheme.success : nil)
                    figure("MRR", OrbitalMoney.format(stats.mrr, currency: block.currency), tone: stats.mrr > 0 ? OrbitalTheme.success : nil)
                    figure("Trials", "\(stats.trials)", tone: stats.trialsEndingSoon > 0 ? OrbitalTheme.warning : nil)
                    figure("Past due", "\(stats.attention)", tone: stats.attention > 0 ? OrbitalTheme.danger : nil)
                }
                if !compact {
                    let month = [
                        stats.newSubscriptions > 0 ? "\(stats.newSubscriptions) new" : nil,
                        stats.cancelled > 0 ? "\(stats.cancelled) cancelled" : nil,
                        stats.failed > 0 ? "\(stats.failed) failed \(stats.failed == 1 ? "payment" : "payments")" : nil,
                    ].compactMap { $0 }
                    Text(month.isEmpty ? "This month: no new subscriptions, cancellations or failed payments." : "This month: " + month.joined(separator: " · "))
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                    ForEach(trials.prefix(3)) { item in
                        HStack(spacing: 8) {
                            Image(systemName: "hourglass")
                                .font(.caption.weight(.bold))
                                .foregroundStyle(OrbitalTheme.accent)
                            Text(item.customer ?? "Trial")
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(OrbitalTheme.text)
                                .lineLimit(1)
                                .truncationMode(.middle)
                            Spacer(minLength: 6)
                            if let end = item.trialEndDate {
                                Text("ends \(SubscriptionRow.relativeDay(end, now: now))")
                                    .font(.caption2.weight(.semibold))
                                    .foregroundStyle(end.timeIntervalSince(now) < 3 * 86_400 ? OrbitalTheme.warning : OrbitalTheme.textTertiary)
                            }
                        }
                    }
                } else if let next = trials.first, let end = next.trialEndDate {
                    Text("Next trial ends \(SubscriptionRow.relativeDay(end, now: now))")
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                }
            }
            .orbitalCard(padding: 16, tint: tint)
        }
        .buttonStyle(OrbitalPressStyle())
        .accessibilityHint("Opens \(block.product) subscriptions")
    }

    private func figure(_ label: String, _ value: String, tone: Color?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label.uppercased())
                .font(.system(size: 9, weight: .bold))
                .tracking(0.4)
                .foregroundStyle(OrbitalTheme.textTertiary)
                .lineLimit(1)
            Text(value)
                .font(.system(.headline, design: .rounded).weight(.bold))
                .monospacedDigit()
                .foregroundStyle(tone ?? OrbitalTheme.text)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// A subscriptions block drawn on its own page: the summary, then the first subscriptions.
struct SubscriptionsBlockCard: View {
    let block: SubscriptionsBlock
    let accent: Color
    var location: PageLocation?
    @EnvironmentObject private var store: OrbitalStore

    var body: some View {
        let ref = store.subscriptions.first { $0.location == location && $0.block.id == block.id }
            ?? SubscriptionsRef(id: block.id, location: location ?? PageLocation(sectionID: ""), space: block.space ?? "", block: block)
        VStack(alignment: .leading, spacing: 12) {
            SubscriptionsSummaryCard(ref: ref)
            let items = SubscriptionStats.sorted(block.items)
            if !items.isEmpty {
                VStack(spacing: 0) {
                    ForEach(items.prefix(6)) { item in
                        SubscriptionRow(item: item, currency: block.currency, tint: accent)
                    }
                }
                .orbitalCard(padding: 14)
            }
            CheckedLine(raw: block.asOf)
        }
    }
}
