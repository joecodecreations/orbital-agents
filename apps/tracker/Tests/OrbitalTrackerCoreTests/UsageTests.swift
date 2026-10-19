import XCTest
@testable import OrbitalTrackerCore

final class UsageTests: XCTestCase {
    func testUpdateRouteCarriesProgressAndUsage() throws {
        let body = #"{"progress":27,"usage":{"engine":"grok","inputTokens":18811,"cachedTokens":17664,"outputTokens":36,"reasoningTokens":30,"planPercent":0.4,"planUsedPercent":13,"planPeriod":"weekly","measuredBy":"satellite"}}"#
        let cmd = try TrackerCommand.fromRoute("update", pathID: "abc123", body: Data(body.utf8))
        XCTAssertEqual(cmd.progress, 27)
        XCTAssertEqual(cmd.usage?.engine, "grok")
        XCTAssertEqual(cmd.usage?.totalTokens, 18811 + 36 + 30)
        XCTAssertEqual(cmd.usage?.planPeriod, "weekly")
    }

    func testItemKeepsProgressAndUsageThroughJSON() throws {
        var item = TrackedItem(title: "Fix login", source: .agent, agent: "claude", colorIndex: 0)
        item.progress = 40
        item.usage = TokenUsage(engine: "claude", inputTokens: 1000, outputTokens: 200)
        let data = try TrackerJSON.encoder().encode(item)
        let back = try TrackerJSON.decoder().decode(TrackedItem.self, from: data)
        XCTAssertEqual(back.progress, 40)
        XCTAssertEqual(back.usage?.totalTokens, 1200)
    }

    func testOldItemsWithoutTheFieldsStillLoad() throws {
        let old = #"{"id":"old1","title":"x","source":"agent","colorIndex":0,"status":"active"}"#
        let item = try TrackerJSON.decoder().decode(TrackedItem.self, from: Data(old.utf8))
        XCTAssertNil(item.progress)
        XCTAssertNil(item.usage)
    }
}
