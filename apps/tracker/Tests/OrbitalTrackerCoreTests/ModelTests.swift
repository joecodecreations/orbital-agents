import Foundation
import XCTest
@testable import OrbitalTrackerCore

final class ModelTests: XCTestCase {
    func testItemRoundTripKeepsMachine() throws {
        var item = TrackedItem(id: "abc12345", title: "Fix Login", details: "**md**", source: .agent, agent: "claude",
                               agentLabel: "Claude Code", machine: "studio-mac", workspace: "/tmp/w", colorIndex: 3)
        var sub = SubTask(id: "sub1", agent: "gemini", machine: "build-box", title: "Audit")
        sub.notes.append(ProgressNote(text: "half way"))
        item.subagents.append(sub)
        item.media.append(MediaRef(id: "m1", kind: .image, originalPath: "/tmp/a.png", archivePath: "media/2026-01/x-m1.jpg",
                                   machine: "studio-mac"))

        let data = try TrackerJSON.encoder().encode([item])
        let json = String(decoding: data, as: UTF8.self)
        XCTAssertTrue(json.contains("\"machine\":\"studio-mac\""))
        XCTAssertTrue(json.contains("\"machine\":\"build-box\""))

        let back = try TrackerJSON.decoder().decode([TrackedItem].self, from: data)
        XCTAssertEqual(back.count, 1)
        XCTAssertEqual(back[0].machine, "studio-mac")
        XCTAssertEqual(back[0].subagents.first?.machine, "build-box")
        XCTAssertEqual(back[0].media.first?.machine, "studio-mac")
        XCTAssertEqual(back[0].subagents.first?.notes.first?.text, "half way")
        XCTAssertEqual(back[0].agentLabel, "Claude Code")
    }

    func testMachineIsOmittedWhenNil() throws {
        let item = TrackedItem(id: "n1", title: "T", source: .manual, colorIndex: 0)
        let json = String(decoding: try TrackerJSON.encoder().encode(item), as: UTF8.self)
        XCTAssertFalse(json.contains("machine"))
    }

    func testOlderItemsWithoutNewFieldsStillDecode() throws {
        let legacy = """
        [{"id":"old1","title":"Old","source":"agent","agent":"claude","colorIndex":1,"status":"done",
          "createdAt":"2026-01-02T03:04:05Z","completedAt":"2026-01-02T04:04:05.250Z",
          "subagents":[{"id":"s","title":"Sub"}]},
         {"id":"old2","createdAt":1767323045000}]
        """
        let items = try TrackerJSON.decoder().decode([TrackedItem].self, from: Data(legacy.utf8))
        XCTAssertEqual(items.count, 2)
        XCTAssertNil(items[0].machine)
        XCTAssertEqual(items[0].status, .done)
        XCTAssertEqual(items[0].subagents.first?.agent, "other")
        XCTAssertNotNil(items[0].completedAt)
        XCTAssertEqual(items[1].title, "Untitled")
        XCTAssertEqual(items[1].source, .manual)
        // Milliseconds since 1970 are accepted too.
        XCTAssertEqual(items[1].createdAt.timeIntervalSince1970, 1_767_323_045, accuracy: 0.001)
    }

    func testFinishedAtFallsBackToUpdatedAt() {
        var item = TrackedItem(title: "T", source: .manual, colorIndex: 0, createdAt: Date(timeIntervalSince1970: 100))
        XCTAssertEqual(item.finishedAt, item.updatedAt)
        item.completedAt = Date(timeIntervalSince1970: 500)
        XCTAssertEqual(item.finishedAt.timeIntervalSince1970, 500)
    }

    func testIDs() {
        let id = IDGen.make()
        XCTAssertEqual(id.count, 8)
        XCTAssertTrue(IDGen.isValid(id))
        XCTAssertTrue(IDGen.isValid("job_42-a"))
        XCTAssertFalse(IDGen.isValid("../../etc"))
        XCTAssertFalse(IDGen.isValid("a"))
        XCTAssertFalse(IDGen.isValid("with space"))
        XCTAssertFalse(IDGen.isValid(String(repeating: "x", count: 65)))
        XCTAssertFalse(IDGen.isValid("ünï"))
    }

    func testAgentNames() {
        XCTAssertEqual(Agents.normalize("Claude Code"), "claude")
        XCTAssertEqual(Agents.normalize("codex"), "chatgpt")
        XCTAssertEqual(Agents.normalize("Antigravity"), "gemini")
        XCTAssertEqual(Agents.normalize("xai"), "grok")
        XCTAssertEqual(Agents.normalize("echo"), "echo")
        XCTAssertEqual(Agents.normalize(nil), "other")
        XCTAssertEqual(Agents.displayName("echo"), "Echo")
        XCTAssertEqual(Agents.detectFromEnvironment(["CLAUDECODE": "1"]), "claude")
        XCTAssertEqual(Agents.detectFromEnvironment(["GROK_API_KEY": "x"]), nil)
    }
}
