import XCTest
@testable import OrbitalAgents

/// The Money tab's filters and sums, and the subscriptions block and its figures.
@MainActor
final class MoneyTests: XCTestCase {
    private func directory(_ sections: [SectionDocument]) throws -> SpaceDirectory {
        let catalog = try Fixtures.decode(CatalogResponse.self, "catalog.json")
        return SpaceDirectory.build(
            catalog: sections.map(OrbitalStore.catalogEntry(for:)),
            declared: catalog.spaces,
            sections: Dictionary(uniqueKeysWithValues: sections.map { ($0.id, $0) }),
            collected: Insights.collect(sections)
        )
    }

    func testBusinessesProductsAndDrillDown() throws {
        let sections = try Fixtures.sections()
        let directory = try directory(sections)
        let rows = Insights.rows(Insights.collect(sections).ledgers)
        let all = MoneyRange.all.range(earliest: rows.map(\.day).min())
        let everything = rows.filter { MoneyFilter().matches($0, directory: directory) }

        let businesses = MoneyLens.groups(everything, by: .business, in: all, directory: directory)
        XCTAssertEqual(Set(businesses.map(\.id)), ["shop", "studio", "examplebooks"])
        XCTAssertEqual(businesses.first?.id, "shop", "largest first")

        var studio = MoneyFilter()
        studio.business = "studio"
        let studioRows = rows.filter { studio.matches($0, directory: directory) }
        let products = MoneyLens.groups(studioRows, by: .product, in: all, directory: directory)
        XCTAssertEqual(Set(products.map(\.name)), ["Planner Pro", "Puzzle Game"])

        // The business total is the sum of its products, and every row lands in exactly one business.
        let studioTotal = MoneyLens.total(studioRows, in: all)
        XCTAssertEqual(products.reduce(0) { $0 + $1.total }, studioTotal, accuracy: 0.001)
        XCTAssertEqual(businesses.reduce(0) { $0 + $1.total }, MoneyLens.total(everything, in: all), accuracy: 0.001)

        XCTAssertEqual(MoneyLens.nextDimension(after: MoneyFilter()), .business)
        XCTAssertEqual(MoneyLens.nextDimension(after: studio), .product)
    }

    func testKindsKeepPayoutsOutOfTotals() throws {
        let sections = try Fixtures.sections()
        let directory = try directory(sections)
        let rows = Insights.rows(Insights.collect(sections).ledgers)
        var planner = MoneyFilter()
        planner.product = "plannerpro"
        let all = MoneyRange.all.range(earliest: rows.map(\.day).min())

        let shown = rows.filter { planner.matches($0, directory: directory) }
        XCTAssertFalse(shown.contains { $0.kind == .payouts }, "payouts only when asked for")
        XCTAssertTrue(shown.contains { $0.kind == .refunds })

        planner.kind = .payouts
        let payouts = rows.filter { planner.matches($0, directory: directory) }
        XCTAssertEqual(payouts.count, 5)
        XCTAssertTrue(payouts.allSatisfy { $0.amount > 0 })

        planner.kind = .refunds
        XCTAssertLessThan(MoneyLens.total(rows.filter { planner.matches($0, directory: directory) }, in: all), 0)

        XCTAssertEqual(MoneyKind.of(category: "Stripe payout", amount: 100), .payouts)
        XCTAssertEqual(MoneyKind.of(category: "Subscription", amount: -5), .refunds)
        XCTAssertEqual(MoneyKind.of(category: "Royalties", amount: 5), .income)
    }

    func testRanges() {
        let calendar = Calendar.current
        let now = calendar.date(from: DateComponents(year: 2026, month: 3, day: 18, hour: 15))!
        let year = MoneyRange.year.range(now: now, earliest: nil)
        XCTAssertEqual(calendar.component(.month, from: year.lowerBound), 1)
        XCTAssertEqual(calendar.component(.day, from: year.lowerBound), 1)
        let earliest = calendar.date(from: DateComponents(year: 2025, month: 6, day: 2))!
        XCTAssertEqual(MoneyRange.all.range(now: now, earliest: earliest).lowerBound, earliest)
        let custom = MoneyRange.custom.range(now: now, earliest: nil, from: now, to: earliest)
        XCTAssertEqual(custom.lowerBound, earliest, "a backwards custom range is flipped")
        XCTAssertNil(MoneyRange.all.previous(now: now))
        XCTAssertNotNil(MoneyRange.year.previous(now: now))
    }

    func testSubscriptionsBlockDecodesAndCounts() throws {
        let doc = try Fixtures.section("studio")
        let refs = Insights.collect([doc]).subscriptions
        let ref = try XCTUnwrap(refs.first)
        XCTAssertEqual(ref.productID, "plannerpro")
        XCTAssertEqual(ref.block.items.count, 11)
        XCTAssertEqual(ref.block.failed.count, 2)

        let stats = SubscriptionStats.compute(ref.block.items, failed: ref.block.failed, in: MoneyRange.last30.range(earliest: nil))
        XCTAssertEqual(stats.active, 6, "active and past due, including the one set to cancel")
        XCTAssertEqual(stats.mrr, 70, accuracy: 0.001)
        XCTAssertEqual(stats.canceling, 1)
        XCTAssertEqual(stats.attention, 1)
        XCTAssertEqual(stats.trials, 3)
        XCTAssertEqual(stats.trialsEndingSoon, 1)
        XCTAssertEqual(stats.cancelled, 2)
        XCTAssertEqual(stats.churned, 1, "the trial that never paid is not churn")
        XCTAssertEqual(stats.failed, 1)

        let sorted = SubscriptionStats.sorted(ref.block.items)
        XCTAssertEqual(sorted.first?.status, "past_due", "payment trouble first")
        let appTrial = try XCTUnwrap(ref.block.items.first { $0.billing == "app" })
        XCTAssertEqual(appTrial.statusLabel, "Free trial")
    }

    func testSubscriptionItemDefaults() throws {
        let item = try Fixtures.json(SubscriptionItem.self, """
        {"id": "s1", "status": "ACTIVE", "started": "2026-01-05", "amount": 120, "interval": "year"}
        """)
        XCTAssertEqual(item.status, "active")
        XCTAssertEqual(item.monthly, 10, accuracy: 0.001)
        XCTAssertNotNil(item.startedDate)
        XCTAssertFalse(item.cancelAtPeriodEnd)
    }

    func testNavBarDropsDuplicateMoney() {
        let nav = NavDoc(tabs: [
            NavItem(id: "cash", title: "Cash", target: .view("money")),
            NavItem(id: "m", title: "Money", target: .section("sales", node: nil, tab: nil)),
            NavItem(id: "lab", title: "Lab", target: .space("homelab")),
        ])
        XCTAssertEqual(nav.barTabs.map(\.id), ["lab"])
    }
}
