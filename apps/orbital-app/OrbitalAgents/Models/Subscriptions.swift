import Foundation

// Subscriptions for one product: who pays, who is on a trial and when it ends, who cancelled, and
// which payments failed. A feed sends a `subscriptions` block; the app works out the figures for
// whatever period you pick, so they always agree with the list underneath.

/// A `subscriptions` block.
struct SubscriptionsBlock: Codable, Equatable {
    var type: String = "subscriptions"
    var id: String
    var title: String?
    var product: String
    var space: String?
    var source: String?
    var currency: String
    var connected: Bool
    var asOf: String?
    var note: String?
    var items: [SubscriptionItem]
    var failed: [FailedPayment]
    var metrics: [MetricTile]

    enum CodingKeys: String, CodingKey {
        case type, id, title, product, space, company, source, currency, connected, note, items, failed, metrics
        case asOf = "as_of"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = c.lenientString(.title)
        product = Insights.nonEmpty(c.lenientString(.product)) ?? "Subscriptions"
        space = Insights.nonEmpty(c.lenientString(.space)) ?? Insights.nonEmpty(c.lenientString(.company))
        source = c.lenientString(.source)
        currency = (Insights.nonEmpty(c.lenientString(.currency)) ?? "USD").uppercased()
        connected = c.lenientBool(.connected) ?? true
        asOf = c.lenientString(.asOf)
        note = c.lenientString(.note)
        items = c.lossy(SubscriptionItem.self, .items)
        failed = c.lossy(FailedPayment.self, .failed)
        metrics = c.lossy(MetricTile.self, .metrics)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(type, forKey: .type)
        try c.encode(id, forKey: .id)
        try c.encodeIfPresent(title, forKey: .title)
        try c.encode(product, forKey: .product)
        try c.encodeIfPresent(space, forKey: .space)
        try c.encodeIfPresent(source, forKey: .source)
        try c.encode(currency, forKey: .currency)
        try c.encode(connected, forKey: .connected)
        try c.encodeIfPresent(asOf, forKey: .asOf)
        try c.encodeIfPresent(note, forKey: .note)
        try c.encode(items, forKey: .items)
        try c.encode(failed, forKey: .failed)
        try c.encode(metrics, forKey: .metrics)
    }
}

/// One subscription, or one in-app free trial (`billing: "app"`).
struct SubscriptionItem: Codable, Equatable, Identifiable {
    var id: String
    var customer: String?
    var plan: String?
    var interval: String?
    var status: String
    var amount: Double?
    var mrr: Double?
    var started: String
    var trialEnd: String?
    var periodEnd: String?
    var cancelAtPeriodEnd: Bool
    var canceledAt: String?
    var paid: Bool?
    var billing: String?

    enum CodingKeys: String, CodingKey {
        case id, customer, plan, interval, status, amount, mrr, started, paid, billing
        case trialEnd = "trial_end"
        case periodEnd = "period_end"
        case cancelAtPeriodEnd = "cancel_at_period_end"
        case canceledAt = "canceled_at"
    }

    init(id: String, customer: String? = nil, plan: String? = nil, interval: String? = nil, status: String, amount: Double? = nil,
         mrr: Double? = nil, started: String, trialEnd: String? = nil, periodEnd: String? = nil, cancelAtPeriodEnd: Bool = false,
         canceledAt: String? = nil, paid: Bool? = nil, billing: String? = nil) {
        self.id = id
        self.customer = customer
        self.plan = plan
        self.interval = interval
        self.status = status
        self.amount = amount
        self.mrr = mrr
        self.started = started
        self.trialEnd = trialEnd
        self.periodEnd = periodEnd
        self.cancelAtPeriodEnd = cancelAtPeriodEnd
        self.canceledAt = canceledAt
        self.paid = paid
        self.billing = billing
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        customer = Insights.nonEmpty(c.lenientString(.customer))
        plan = Insights.nonEmpty(c.lenientString(.plan))
        interval = Insights.nonEmpty(c.lenientString(.interval))
        status = (Insights.nonEmpty(c.lenientString(.status)) ?? "active").lowercased()
        amount = c.lenientDouble(.amount)
        mrr = c.lenientDouble(.mrr)
        started = try c.decode(String.self, forKey: .started)
        trialEnd = Insights.nonEmpty(c.lenientString(.trialEnd))
        periodEnd = Insights.nonEmpty(c.lenientString(.periodEnd))
        cancelAtPeriodEnd = c.lenientBool(.cancelAtPeriodEnd) ?? false
        canceledAt = Insights.nonEmpty(c.lenientString(.canceledAt))
        paid = c.lenientBool(.paid)
        billing = Insights.nonEmpty(c.lenientString(.billing))
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encodeIfPresent(customer, forKey: .customer)
        try c.encodeIfPresent(plan, forKey: .plan)
        try c.encodeIfPresent(interval, forKey: .interval)
        try c.encode(status, forKey: .status)
        try c.encodeIfPresent(amount, forKey: .amount)
        try c.encodeIfPresent(mrr, forKey: .mrr)
        try c.encode(started, forKey: .started)
        try c.encodeIfPresent(trialEnd, forKey: .trialEnd)
        try c.encodeIfPresent(periodEnd, forKey: .periodEnd)
        try c.encode(cancelAtPeriodEnd, forKey: .cancelAtPeriodEnd)
        try c.encodeIfPresent(canceledAt, forKey: .canceledAt)
        try c.encodeIfPresent(paid, forKey: .paid)
        try c.encodeIfPresent(billing, forKey: .billing)
    }

    var startedDate: Date? { SubscriptionDates.parse(started) }
    var trialEndDate: Date? { SubscriptionDates.parse(trialEnd) }
    var periodEndDate: Date? { SubscriptionDates.parse(periodEnd) }
    var canceledDate: Date? { SubscriptionDates.parse(canceledAt) }

    /// Paying now: active or past due. A subscription set to cancel still pays until its period ends.
    var isPaying: Bool { status == "active" || status == "past_due" }
    var isCanceling: Bool { isPaying && cancelAtPeriodEnd }
    var isInAppTrial: Bool { billing == "app" }

    func isTrial(now: Date = Date()) -> Bool {
        guard status == "trialing" else { return false }
        guard let end = trialEndDate else { return true }
        return end > now
    }

    /// Needs a look: payment trouble or a start that never completed.
    var needsAttention: Bool { ["past_due", "unpaid", "incomplete"].contains(status) }

    /// Monthly value, from `mrr` or the price and interval.
    var monthly: Double {
        if let mrr { return mrr }
        guard let amount else { return 0 }
        switch interval {
        case "year": return amount / 12
        case "week": return amount * 4.348125
        case "day": return amount * 30.4375
        default: return amount
        }
    }

    /// When it started paying: the end of its trial, else when it was created.
    var payingSince: Date? { trialEndDate.flatMap { end in startedDate.map { max($0, end) } ?? end } ?? startedDate }

    /// The word shown on its pill.
    var statusLabel: String {
        if isCanceling { return "Canceling" }
        if isInAppTrial, status == "trialing" { return "Free trial" }
        switch status {
        case "trialing": return "Trial"
        case "active": return "Active"
        case "past_due": return "Past due"
        case "canceled": return "Canceled"
        case "unpaid": return "Unpaid"
        case "incomplete": return "Incomplete"
        case "incomplete_expired": return "Expired"
        case "paused": return "Paused"
        default: return status.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }

    /// The filter bucket this subscription belongs to.
    func bucket(now: Date = Date()) -> SubscriptionBucket {
        if isCanceling { return .canceling }
        if needsAttention { return .attention }
        if isPaying { return .active }
        if isTrial(now: now) { return .trial }
        return .ended
    }
}

struct FailedPayment: Codable, Equatable, Identifiable {
    var id: String
    var at: String
    var amount: Double?
    var customer: String?
    var reason: String?

    enum CodingKeys: String, CodingKey { case id, at, amount, customer, reason }

    init(id: String, at: String, amount: Double? = nil, customer: String? = nil, reason: String? = nil) {
        self.id = id
        self.at = at
        self.amount = amount
        self.customer = customer
        self.reason = reason
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        at = try c.decode(String.self, forKey: .at)
        id = Insights.nonEmpty(c.lenientString(.id)) ?? at
        amount = c.lenientDouble(.amount)
        customer = Insights.nonEmpty(c.lenientString(.customer))
        reason = Insights.nonEmpty(c.lenientString(.reason))
    }

    var date: Date? { SubscriptionDates.parse(at) }

    /// "card_declined" → "Card declined".
    var reasonLabel: String? {
        guard let reason else { return nil }
        let words = reason.replacingOccurrences(of: "_", with: " ")
        return words.prefix(1).uppercased() + words.dropFirst()
    }
}

enum SubscriptionBucket: String, CaseIterable, Identifiable {
    case all = "All"
    case active = "Active"
    case trial = "Trials"
    case canceling = "Canceling"
    case attention = "Past due"
    case ended = "Ended"
    var id: String { rawValue }
}

/// One subscriptions block and the page it sits on.
struct SubscriptionsRef: Identifiable, Equatable {
    var id: String
    var location: PageLocation
    var space: String
    var block: SubscriptionsBlock

    var productID: String { Spaces.key(block.product) }
}

enum SubscriptionDates {
    static func parse(_ raw: String?) -> Date? {
        guard let raw, !raw.isEmpty else { return nil }
        return OrbitalDates.parse(raw) ?? Insights.day(raw)
    }
}

/// The figures for one period.
struct SubscriptionStats: Equatable {
    var active = 0
    var mrr: Double = 0
    var trials = 0
    var trialsEndingSoon = 0
    var canceling = 0
    var attention = 0
    var newSubscriptions = 0
    var newTrials = 0
    var cancelled = 0
    var churned = 0
    /// Paying at the start of the period, for the churn rate.
    var payingAtStart = 0
    var failed = 0
    var failedAmount: Double = 0

    var churnRate: Double? { payingAtStart > 0 ? Double(churned) / Double(payingAtStart) : nil }

    /// "Now" figures (active, MRR, trials) are as of today; the rest count events inside `range`.
    static func compute(_ items: [SubscriptionItem], failed: [FailedPayment], in range: ClosedRange<Date>, now: Date = Date(),
                        soon: TimeInterval = 3 * 86_400) -> SubscriptionStats {
        var stats = SubscriptionStats()
        let calendar = Calendar.current
        // `range` holds calendar days; an event on the last day counts until midnight.
        let end = calendar.date(byAdding: .day, value: 1, to: calendar.startOfDay(for: range.upperBound)) ?? range.upperBound
        let start = range.lowerBound
        func inside(_ date: Date?) -> Bool { date.map { $0 >= start && $0 < end } ?? false }
        for item in items {
            if item.isPaying {
                stats.active += 1
                stats.mrr += item.monthly
            }
            if item.isCanceling { stats.canceling += 1 }
            if item.needsAttention { stats.attention += 1 }
            if item.isTrial(now: now) {
                stats.trials += 1
                if let ends = item.trialEndDate, ends.timeIntervalSince(now) <= soon { stats.trialsEndingSoon += 1 }
            }
            let startedInside = inside(item.startedDate)
            if startedInside {
                if item.status == "trialing" || item.isInAppTrial || item.trialEndDate != nil {
                    stats.newTrials += 1
                }
                if !item.isInAppTrial, !["incomplete", "incomplete_expired"].contains(item.status), item.status != "trialing" {
                    stats.newSubscriptions += 1
                }
            }
            if inside(item.canceledDate) {
                stats.cancelled += 1
                if item.paid == true { stats.churned += 1 }
            }
            // Paying when the period began: paid, started paying before it, not cancelled before it.
            if item.paid == true || item.isPaying, let since = item.payingSince, since < start,
               item.canceledDate.map({ $0 >= start }) ?? true, !item.isInAppTrial {
                stats.payingAtStart += 1
            }
        }
        for payment in failed where inside(payment.date) {
            stats.failed += 1
            stats.failedAmount += payment.amount ?? 0
        }
        return stats
    }

    /// Paying subscriptions on each day of `days`, for the chart. Built from start and cancel dates.
    static func payingByDay(_ items: [SubscriptionItem], days: [Date]) -> [(day: Date, count: Int)] {
        let calendar = Calendar.current
        let paying = items.filter { !$0.isInAppTrial && ($0.paid == true || $0.isPaying) }
        return days.map { day in
            let dayEnd = calendar.date(byAdding: .day, value: 1, to: day) ?? day
            let count = paying.filter { item in
                guard let since = item.payingSince, since < dayEnd else { return false }
                if let cancelled = item.canceledDate { return cancelled >= dayEnd }
                return true
            }.count
            return (day, count)
        }
    }

    /// Paying first (largest first), then trials by the soonest end, then the rest newest first.
    static func sorted(_ items: [SubscriptionItem], now: Date = Date()) -> [SubscriptionItem] {
        func rank(_ item: SubscriptionItem) -> Int {
            switch item.bucket(now: now) {
            case .attention: return 0
            case .canceling: return 1
            case .active: return 2
            case .trial: return 3
            default: return 4
            }
        }
        return items.sorted { lhs, rhs in
            let (l, r) = (rank(lhs), rank(rhs))
            if l != r { return l < r }
            switch l {
            case 3:
                return (lhs.trialEndDate ?? .distantFuture) < (rhs.trialEndDate ?? .distantFuture)
            case 0, 1, 2:
                if abs(lhs.monthly - rhs.monthly) > 0.004 { return lhs.monthly > rhs.monthly }
                return (lhs.startedDate ?? .distantPast) < (rhs.startedDate ?? .distantPast)
            default:
                return (lhs.canceledDate ?? lhs.startedDate ?? .distantPast) > (rhs.canceledDate ?? rhs.startedDate ?? .distantPast)
            }
        }
    }
}
