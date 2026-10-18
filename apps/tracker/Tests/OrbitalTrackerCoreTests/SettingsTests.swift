import Foundation
import XCTest
@testable import OrbitalTrackerCore

final class SettingsTests: TempDirTestCase {
    func testMissingFileIsOff() {
        let s = TrackerSettings.load(paths)
        XCTAssertEqual(s.evidence, .off)
        XCTAssertNil(s.updatedBy)
    }

    func testSaveAndLoad() throws {
        let date = Date(timeIntervalSince1970: 1_800_000_000)
        try TrackerSettings(evidence: .all, updatedAt: date, updatedBy: "cli").save(paths)
        let back = TrackerSettings.load(paths)
        XCTAssertEqual(back.evidence, .all)
        XCTAssertEqual(back.updatedBy, "cli")
        XCTAssertEqual(back.updatedAt?.timeIntervalSince1970 ?? 0, 1_800_000_000, accuracy: 1)
        let raw = try String(contentsOf: paths.settingsFile, encoding: .utf8)
        XCTAssertTrue(raw.contains("\"evidence\" : \"all\""))
    }

    func testWritersAreNormalized() throws {
        // The phone app's former name, and anything else unknown, reads as "app".
        let legacy = "secre" + "tary"
        try write(#"{"evidence":"min1","updatedBy":"\#(legacy)"}"#, to: paths.settingsFile)
        XCTAssertEqual(TrackerSettings.load(paths).updatedBy, "app")
        XCTAssertEqual(TrackerSettings.normalizeWriter("Tracker"), "tracker")
        XCTAssertEqual(TrackerSettings.normalizeWriter("cli"), "cli")
        XCTAssertEqual(TrackerSettings.normalizeWriter("app"), "app")
        XCTAssertEqual(TrackerSettings(evidence: .off, updatedBy: legacy).updatedBy, "app")
    }

    func testUnknownLevelReadsAsOff() throws {
        try write(#"{"evidence":"maximum-overdrive"}"#, to: paths.settingsFile)
        XCTAssertEqual(TrackerSettings.load(paths).evidence, .off)
        try write("not json", to: paths.settingsFile)
        XCTAssertEqual(TrackerSettings.load(paths).evidence, .off)
    }

    func testLooseLevels() {
        XCTAssertEqual(EvidenceLevel(loose: "Min 1"), .min1)
        XCTAssertEqual(EvidenceLevel(loose: "1"), .min1)
        XCTAssertEqual(EvidenceLevel(loose: "EVERY"), .all)
        XCTAssertEqual(EvidenceLevel(loose: "none"), .off)
        XCTAssertNil(EvidenceLevel(loose: "sometimes"))
        XCTAssertTrue(EvidenceLevel.min1.agentInstruction.contains("orbital-track update <ID>"))
    }

    func testReplyParsingAndNewest() {
        let reply = Data(#"{"ok":true,"settings":{"evidence":"all","updatedAt":"2026-05-01T10:00:00Z","updatedBy":"app"}}"#.utf8)
        let hub = TrackerSettings.fromReply(reply)
        XCTAssertEqual(hub?.evidence, .all)
        XCTAssertEqual(hub?.updatedBy, "app")
        XCTAssertEqual(TrackerSettings.fromReply(Data(#"{"evidence":"min1"}"#.utf8))?.evidence, .min1)
        XCTAssertNil(TrackerSettings.fromReply(Data("[]".utf8)))

        let older = TrackerSettings(evidence: .off, updatedAt: TrackerJSON.parseDate("2026-04-01T00:00:00Z"), updatedBy: "tracker")
        XCTAssertEqual(TrackerSettings.newest(older, hub).evidence, .all)
        XCTAssertEqual(TrackerSettings.newest(hub!, older).evidence, .all)
        XCTAssertEqual(TrackerSettings.newest(older, nil), older)
        let undated = TrackerSettings(evidence: .min1)
        XCTAssertEqual(TrackerSettings.newest(undated, older), older)
    }

    func testJSONForTheAPI() {
        let json = TrackerSettings(evidence: .min1, updatedAt: Date(timeIntervalSince1970: 0), updatedBy: "tracker").json
        XCTAssertEqual(json["evidence"] as? String, "min1")
        XCTAssertEqual(json["label"] as? String, "Min 1")
        XCTAssertEqual(json["updatedAt"] as? String, "1970-01-01T00:00:00Z")
    }
}
