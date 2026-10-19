import XCTest
@testable import OrbitalAgents

/// The tracker's progress rule. The Mac tracker (`ShownProgress`) uses the same one, so a bar
/// reads the same on the phone and on the Mac.
final class TrackerProgressTests: XCTestCase {
    func testDoneIsAlwaysFull() {
        let shown = TrackerProgress.of(status: "done", progress: 30, details: "- [ ] one")
        XCTAssertEqual(shown, TrackerProgress(percent: 100, source: .done))
        XCTAssertEqual(shown?.label, "100%")
    }

    func testReportedWinsAndIsClamped() {
        XCTAssertEqual(TrackerProgress.of(status: "active", progress: 40, details: "- [x] a\n- [ ] b")?.percent, 40)
        XCTAssertEqual(TrackerProgress.of(status: "active", progress: 40, details: "")?.label, "40%")
        XCTAssertEqual(TrackerProgress.of(status: "active", progress: 140, details: "")?.percent, 100)
        XCTAssertEqual(TrackerProgress.of(status: "active", progress: -5, details: "")?.percent, 0)
        XCTAssertEqual(TrackerProgress.of(status: "active", progress: 0, details: "")?.source, .reported)
    }

    func testChecklistEstimate() {
        let md = "Plan:\n- [x] one\n  * [X] two\n+ [ ] three\n-[x] not a box\n- [ ]\n1. [x] numbered is not a box\n\t- [ ] tabbed"
        XCTAssertEqual(TrackerProgress.checklist(md)?.checked, 2)
        XCTAssertEqual(TrackerProgress.checklist(md)?.total, 5)
        let shown = TrackerProgress.of(status: "active", progress: nil, details: md)
        XCTAssertEqual(shown, TrackerProgress(percent: 40, source: .checklist))
        XCTAssertEqual(shown?.label, "~40%")
        XCTAssertEqual(shown?.isEstimate, true)
        XCTAssertNil(TrackerProgress.checklist("no boxes here"))
        // Every box ticked still waits for done.
        XCTAssertEqual(TrackerProgress.of(status: "active", progress: nil, details: "- [x] a\n- [x] b")?.percent, 99)
    }

    func testSubagentEstimate() {
        let subs: [(status: String, progress: Int?)] = [("done", nil), ("active", 50), ("active", nil)]
        let shown = TrackerProgress.of(status: "active", progress: nil, details: "", subagents: subs)
        XCTAssertEqual(shown, TrackerProgress(percent: 50, source: .subagents))
        XCTAssertEqual(shown?.label, "~50%")
        XCTAssertEqual(TrackerProgress.of(status: "active", progress: nil, details: "", subagents: [("done", nil)])?.percent, 99)
        // The task list comes before the subagents.
        XCTAssertEqual(TrackerProgress.of(status: "active", progress: nil, details: "- [ ] a", subagents: subs)?.source, .checklist)
    }

    func testNothingKnownYet() {
        XCTAssertNil(TrackerProgress.of(status: "active", progress: nil, details: "Just a description."))
    }

    func testPreviewFixturesShowBars() throws {
        let page = try Fixtures.decode(TrackerPage.self, "desk/tracker.json")
        let dark = try XCTUnwrap(page.items.first { $0.id == "t-darkmode" })
        XCTAssertEqual(dark.shownProgress, TrackerProgress(percent: 62, source: .reported))
        let dns = try Fixtures.decode(TrackerDetail.self, "desk/items/t-dns.json")
        XCTAssertEqual(dns.shownProgress, TrackerProgress(percent: 40, source: .checklist))
        let darkDetail = try Fixtures.decode(TrackerDetail.self, "desk/items/t-darkmode.json")
        XCTAssertEqual(darkDetail.subagents.first?.shownProgress?.percent, 40)
        XCTAssertEqual(darkDetail.colorIndex, 2)
    }
}
