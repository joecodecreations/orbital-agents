import XCTest
@testable import OrbitalTrackerCore

final class ProgressTests: XCTestCase {
    private func item(progress: Int? = nil, details: String = "", status: ItemStatus = .active) -> TrackedItem {
        var item = TrackedItem(title: "Work", details: details, source: .agent, agent: "claude", colorIndex: 0)
        item.progress = progress
        item.status = status
        return item
    }

    func testReportedFigureWins() {
        let shown = item(progress: 40, details: "- [x] a\n- [ ] b").shownProgress
        XCTAssertEqual(shown, ShownProgress(percent: 40, source: .reported))
        XCTAssertEqual(shown?.label, "40%")
    }

    func testDoneIsAlways100() {
        XCTAssertEqual(item(progress: 30, status: .done).shownProgress?.percent, 100)
        XCTAssertEqual(item(status: .done).shownProgress?.source, .done)
    }

    func testChecklistEstimate() {
        let shown = item(details: "Task\n- [x] cart\n  * [X] address\n+ [ ] payment\n- not a box").shownProgress
        XCTAssertEqual(shown?.percent, 67)
        XCTAssertEqual(shown?.source, .checklist)
        XCTAssertEqual(shown?.label, "~67%")
    }

    func testFullChecklistStopsAt99UntilDone() {
        XCTAssertEqual(item(details: "- [x] a\n- [x] b").shownProgress?.percent, 99)
    }

    func testSubagentAverage() {
        var parent = item()
        var a = SubTask(id: "a1", agent: "claude", title: "A")
        a.status = .done
        var b = SubTask(id: "b1", agent: "codex", title: "B")
        b.progress = 50
        parent.subagents = [a, b]
        XCTAssertEqual(parent.shownProgress, ShownProgress(percent: 75, source: .subagents))
    }

    func testNothingKnownYet() {
        XCTAssertNil(item(details: "Just prose.").shownProgress)
    }
}
