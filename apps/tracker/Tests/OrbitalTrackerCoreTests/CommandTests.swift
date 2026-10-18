import Foundation
import XCTest
@testable import OrbitalTrackerCore

final class CommandTests: TempDirTestCase {
    func testCLIArgumentParsing() throws {
        let p = try CLIArguments.parse(["start", "--agent", "claude", "--title=Fix Login", "--media", "/a.png",
                                        "--media", "/b.png", "--append", "-h", "extra"])
        XCTAssertEqual(p.verb, "start")
        XCTAssertEqual(p.positional, ["start", "extra"])
        XCTAssertEqual(p.value("agent"), "claude")
        XCTAssertEqual(p.value("title"), "Fix Login")
        XCTAssertEqual(p.values("media"), ["/a.png", "/b.png"])
        XCTAssertTrue(p.flags.contains("append"))
        XCTAssertTrue(p.flags.contains("help"))
    }

    func testCLIDoubleDashAndMissingValue() throws {
        let p = try CLIArguments.parse(["note", "abc", "--", "--not-a-flag", "text"])
        XCTAssertEqual(p.positional, ["note", "abc", "--not-a-flag", "text"])
        XCTAssertThrowsError(try CLIArguments.parse(["start", "--title"])) { error in
            XCTAssertEqual((error as? CommandError)?.message, "--title needs a value")
        }
        // A later value wins for single-value options.
        XCTAssertEqual(try CLIArguments.parse(["x", "--title", "a", "--title", "b"]).value("title"), "b")
    }

    func testCanonicalOps() {
        XCTAssertEqual(TrackerCommand.canonicalOp("ADD"), "start")
        XCTAssertEqual(TrackerCommand.canonicalOp("note"), "update")
        XCTAssertEqual(TrackerCommand.canonicalOp("finish"), "done")
        XCTAssertEqual(TrackerCommand.canonicalOp("sub"), "sub")
        XCTAssertNil(TrackerCommand.canonicalOp("delete"))
    }

    func testFriendlyRoutes() throws {
        let start = try TrackerCommand.fromRoute("start", pathID: nil, body: Data(#"{"title":"T","agent":"grok"}"#.utf8))
        XCTAssertEqual(start.op, "start")
        XCTAssertEqual(start.title, "T")
        XCTAssertTrue(IDGen.isValid(start.id ?? ""))

        let sub = try TrackerCommand.fromRoute("sub", pathID: "parent1", body: Data(#"{"title":"S"}"#.utf8))
        XCTAssertEqual(sub.parent, "parent1")
        XCTAssertNotNil(sub.id)

        let done = try TrackerCommand.fromRoute("done", pathID: "item1", body: Data())
        XCTAssertEqual(done.id, "item1")
        XCTAssertEqual(done.op, "done")

        XCTAssertThrowsError(try TrackerCommand.fromRoute("update", pathID: "x1", body: Data("[1]".utf8)))
    }

    func testValidation() throws {
        var cmd = TrackerCommand(op: "start", id: "good_id")
        XCTAssertNoThrow(try cmd.validate())
        cmd.parent = "../../escape"
        XCTAssertThrowsError(try cmd.validate())
        XCTAssertThrowsError(try TrackerCommand(op: "explode", id: "ok12").validate())
    }

    func testCommandJSONRoundTrip() throws {
        var cmd = TrackerCommand(op: "update", id: "k7m2x9qa")
        cmd.machine = "laptop"
        cmd.note = "progress"
        cmd.appendDetails = true
        cmd.media = [MediaInput(path: "/tmp/x.png", caption: "x",
                                archived: MediaRef(id: "m1", kind: .image, originalPath: "/tmp/x.png"),
                                inlineArchive: "AAAA")]
        let data = try TrackerJSON.encoder().encode(cmd)
        let back = try TrackerJSON.decoder().decode(TrackerCommand.self, from: data)
        XCTAssertEqual(back.machine, "laptop")
        XCTAssertEqual(back.media?.first?.inlineArchive, "AAAA")
        XCTAssertEqual(back.media?.first?.archived?.id, "m1")
        XCTAssertEqual(back.appendDetails, true)
        // The minimal form agents and the hub send.
        let minimal = try TrackerJSON.decoder().decode(TrackerCommand.self, from: Data(#"{"op":"done","id":"abc"}"#.utf8))
        XCTAssertEqual(minimal.op, "done")
        XCTAssertNil(minimal.machine)
    }

    func testInboxWritesInOrder() throws {
        let dir = tmp.appendingPathComponent("inbox")
        let a = try Inbox.write(TrackerCommand(op: "start", id: "first1"), to: dir)
        Thread.sleep(forTimeInterval: 0.005)
        _ = try Inbox.write(TrackerCommand(op: "done", id: "first1"), to: dir)
        let pending = Inbox.pending(in: dir)
        XCTAssertEqual(pending.count, 2)
        XCTAssertEqual(pending.first?.lastPathComponent, a.lastPathComponent)
        // No temp files are left behind.
        XCTAssertTrue(try FileManager.default.contentsOfDirectory(atPath: dir.path).allSatisfy { !$0.hasPrefix(".") })
    }

    func testInboxOrderAcrossNameFormats() throws {
        let dir = tmp.appendingPathComponent("inbox2")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        // A 13-digit (milliseconds) name written by another tool, and newer 16-digit (microseconds) ones.
        try write("{}", to: dir.appendingPathComponent("1792258749842-zzzz.json"))
        try write("{}", to: dir.appendingPathComponent("1792258749841999-0001-aaaa.json"))
        try write("{}", to: dir.appendingPathComponent("1792258749843000-0002-aaaa.json"))
        try write("{}", to: dir.appendingPathComponent(".hidden.json.tmp"))
        XCTAssertEqual(Inbox.pending(in: dir).map(\.lastPathComponent),
                       ["1792258749841999-0001-aaaa.json", "1792258749842-zzzz.json", "1792258749843000-0002-aaaa.json"])
        // Many writes in a row keep their order.
        let fast = tmp.appendingPathComponent("fast")
        let written = try (0..<50).map { try Inbox.write(TrackerCommand(op: "update", id: "item\($0)"), to: fast).lastPathComponent }
        XCTAssertEqual(Inbox.pending(in: fast).map(\.lastPathComponent), written)
    }

    func testMarkdownImagePaths() {
        let md = "See ![a](/tmp/a.png) and ![b](~/b.png \"title\") and ![c](https://x/c.png) ![d](file:///tmp/d.png)"
        let paths = MarkdownMedia.localPaths(in: md)
        XCTAssertEqual(paths.count, 3)
        XCTAssertEqual(paths.first, "/tmp/a.png")
        XCTAssertTrue(paths.contains("/tmp/d.png"))
        XCTAssertFalse(paths.contains { $0.contains("https") })
    }
}
