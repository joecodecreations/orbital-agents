import Foundation
import XCTest
@testable import OrbitalTrackerCore

final class StaleTests: XCTestCase {
    private let now = Date()

    private func item(_ id: String, hoursAgo: Double, source: ItemSource = .agent) -> TrackedItem {
        var item = TrackedItem(id: id, title: id, source: source, agent: source == .agent ? "claude" : nil,
                               colorIndex: 0, createdAt: now.addingTimeInterval(-hoursAgo * 3600 - 60))
        item.updatedAt = now.addingTimeInterval(-hoursAgo * 3600)
        return item
    }

    func testFindsQuietAgentItemsOnly() {
        var done = item("done-old", hoursAgo: 48)
        done.status = .done
        var dismissed = item("dismissed-old", hoursAgo: 48)
        dismissed.dismissed = true
        let items = [
            item("quiet", hoursAgo: 30),
            item("fresh", hoursAgo: 2),
            item("edge", hoursAgo: 23.9),
            item("mine", hoursAgo: 72, source: .manual),
            item("spoken", hoursAgo: 72, source: .voice),
            done,
            dismissed,
        ]
        XCTAssertEqual(Stale.find(items, hours: 24, now: now), ["quiet"])
        XCTAssertEqual(Stale.find(items, hours: 0, now: now), [], "0 turns auto-close off")
        XCTAssertEqual(Stale.find(items, hours: -5, now: now), [])
    }

    func testRecentSubagentKeepsTheItemAlive() {
        var parent = item("parent", hoursAgo: 40)
        var sub = SubTask(id: "sub", agent: "claude", title: "piece", createdAt: now.addingTimeInterval(-40 * 3600))
        sub.updatedAt = now.addingTimeInterval(-3600)
        parent.subagents = [sub]
        XCTAssertEqual(Stale.lastActivity(parent), sub.updatedAt)
        XCTAssertEqual(Stale.find([parent], hours: 24, now: now), [])
        XCTAssertEqual(Stale.find([parent], hours: 24, now: now.addingTimeInterval(24 * 3600)), ["parent"])
    }

    func testSummaryKeepsTheLastNote() {
        var quiet = item("quiet", hoursAgo: 30)
        quiet.notes = [ProgressNote(text: "first"), ProgressNote(text: "waiting on the user")]
        let text = Stale.summary(for: quiet, hours: 24)
        XCTAssertTrue(text.hasPrefix("**Closed as stale**: no update for 24 hours"))
        XCTAssertTrue(text.hasSuffix("Last note: waiting on the user"))
        XCTAssertFalse(Stale.summary(for: item("bare", hoursAgo: 30), hours: 24).contains("Last note"))
    }
}
