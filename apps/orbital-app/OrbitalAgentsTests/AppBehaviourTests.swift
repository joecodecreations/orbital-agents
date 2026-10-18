import SwiftUI
import XCTest
@testable import OrbitalAgents

/// Dates, links, hub addresses, error wording, and colours: small pieces every screen leans on.
@MainActor
final class AppBehaviourTests: XCTestCase {
    // MARK: Dates

    func testDateParsing() {
        XCTAssertNotNil(OrbitalDates.parse("2026-10-02T08:30:00Z"))
        XCTAssertNotNil(OrbitalDates.parse("2026-10-02T08:30:00.123456+00:00"), "Python microseconds")
        XCTAssertNotNil(OrbitalDates.parse("2026-10-02 08:30:00"), "space separator, no zone")
        XCTAssertNotNil(OrbitalDates.parse("2026-10-02"), "bare day")
        XCTAssertEqual(OrbitalDates.parse("1767225600")?.timeIntervalSince1970, 1_767_225_600, "Unix seconds")
        XCTAssertEqual(OrbitalDates.parse("1767225600000")?.timeIntervalSince1970, 1_767_225_600, "Unix milliseconds")
        XCTAssertNil(OrbitalDates.parse("soon"))
        XCTAssertNil(OrbitalDates.parse(nil))
        let utc = OrbitalDates.parse("2026-10-02T08:30:00")
        XCTAssertEqual(utc, OrbitalDates.parse("2026-10-02T08:30:00Z"), "no zone reads as UTC")
    }

    func testPreviewDateTokens() throws {
        let now = Date(timeIntervalSince1970: 1_767_225_600) // 2026-01-01T00:00:00Z
        let raw = Data(#"{"a": "@now", "b": "@now-90m", "c": "@now+2h", "d": "@now-3d", "e": "@day", "f": "@day-1", "g": "@nothing"}"#.utf8)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: OrbitalPreview.expand(raw, now: now)) as? [String: String])
        XCTAssertEqual(object["a"], "2026-01-01T00:00:00Z")
        XCTAssertEqual(object["b"], "2025-12-31T22:30:00Z")
        XCTAssertEqual(object["c"], "2026-01-01T02:00:00Z")
        XCTAssertEqual(object["d"], "2025-12-29T00:00:00Z")
        XCTAssertEqual(object["e"], DayMath.isoDay(now))
        XCTAssertEqual(object["f"], DayMath.isoDay(now.addingTimeInterval(-86400)))
        XCTAssertEqual(object["g"], "@nothing", "anything else is left alone")
    }

    // MARK: Hub address

    func testHubAddressParsing() {
        XCTAssertEqual(OrbitalStore.parse("my-hub.example.ts.net")?.absoluteString, "http://my-hub.example.ts.net:7700")
        XCTAssertEqual(OrbitalStore.parse("100.101.102.103")?.absoluteString, "http://100.101.102.103:7700")
        XCTAssertEqual(OrbitalStore.parse("http://hub.local:8800/")?.absoluteString, "http://hub.local:8800")
        XCTAssertEqual(OrbitalStore.parse("https://hub.example.com")?.absoluteString, "https://hub.example.com")
        XCTAssertNil(OrbitalStore.parse(""))
        XCTAssertNil(OrbitalStore.parse("ftp://hub.local"))
    }

    // MARK: Errors

    func testFriendlyErrors() {
        let unreachable = OrbitalAPIError.friendly(URLError(.cannotConnectToHost))
        XCTAssertTrue(unreachable.contains("Tailscale"), unreachable)
        XCTAssertTrue(unreachable.contains("hub running"), unreachable)
        let wrong = OrbitalAPIError.friendly(OrbitalAPIError.unauthorized)
        XCTAssertTrue(wrong.lowercased().contains("wrong username or password"), wrong)
        let refused = OrbitalAPIError.friendly(OrbitalAPIError.http(403, "{}"))
        XCTAssertTrue(refused.contains("Tailscale"), refused)
    }

    func testHubErrorBodies() {
        let error = OrbitalAPIError.http(502, #"{"error": "satellite refused the token", "detail": "join it again", "code": "satellite_auth"}"#)
        XCTAssertEqual(error.status, 502)
        XCTAssertEqual(error.code, "satellite_auth")
        XCTAssertEqual(OrbitalAPIError.message(in: #"{"detail": "only detail"}"#), "only detail")
        XCTAssertNil(OrbitalAPIError.message(in: "not json"))
    }

    // MARK: Links

    func testConnectLinkHandsOverTheHub() {
        let router = AppRouter()
        var received: String?
        router.handle(URL(string: "orbital://connect?hub=http://my-hub.example.ts.net:7700")!) { received = $0 }
        XCTAssertEqual(received, "http://my-hub.example.ts.net:7700")
    }

    func testAgentLinks() {
        let router = AppRouter()
        router.isCompact = true
        router.handle(URL(string: "orbital://agents?job=job-7c21")!)
        XCTAssertEqual(router.tab, .agents)
        XCTAssertEqual(router.agentsPath, [.agentJob("job-7c21")])

        router.handle(URL(string: "orbital://run?machine=nas-01&agent=codex&prompt=Check%20disk")!)
        XCTAssertEqual(router.runDraft?.machine, "nas-01")
        XCTAssertEqual(router.runDraft?.agent, "codex")
        XCTAssertEqual(router.runDraft?.prompt, "Check disk")
    }

    func testOtherSchemesAreIgnored() {
        let router = AppRouter()
        router.handle(URL(string: "https://example.com/agents")!)
        XCTAssertEqual(router.tab, .home)
        XCTAssertNil(router.runDraft)
    }

    // MARK: Spaces and colours

    func testSpaceKeys() {
        XCTAssertEqual(Spaces.key("Open Source"), Spaces.key("open-source"))
        XCTAssertEqual(Spaces.key("Home Lab!"), "homelab")
    }

    func testStableColours() {
        XCTAssertEqual(OrbitalTheme.color(for: "homelab"), OrbitalTheme.color(for: "homelab"))
        XCTAssertEqual(OrbitalTheme.color(for: "anything", hex: "#FF0000"), OrbitalTheme.color(for: "else", hex: "#FF0000"))
    }

    // MARK: Brand

    func testBrand() {
        XCTAssertEqual(OrbitalBrand.urlScheme, "orbital")
        XCTAssertEqual(OrbitalBrand.defaultHubPort, 7700)
        XCTAssertFalse(OrbitalBrand.appName.isEmpty)
    }
}
