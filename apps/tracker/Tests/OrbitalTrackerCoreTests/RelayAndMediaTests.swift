import Foundation
import XCTest
@testable import OrbitalTrackerCore

final class HubRelayTests: TempDirTestCase {
    final class FakeHub: @unchecked Sendable {
        var status: Int? = 200          // nil = unreachable
        var received: [(URLRequest, Data)] = []
    }

    private func relay(_ hub: FakeHub, token: String? = "tok") -> HubRelay {
        var r = HubRelay(hubURL: "http://hub.example:7700/", token: token, outbox: tmp.appendingPathComponent("hub-outbox"))
        r.transport = { req, _ in
            guard let status = hub.status else { return nil }
            hub.received.append((req, req.httpBody ?? Data()))
            return (status, Data("{}".utf8))
        }
        return r
    }

    private func body(_ id: String) -> Data {
        try! TrackerJSON.encoder().encode(TrackerCommand(op: "start", id: id))
    }

    func testSendsWithBearerToken() throws {
        let hub = FakeHub()
        let r = relay(hub)
        XCTAssertEqual(r.relay(body("one111")), .sent)
        let req = try XCTUnwrap(hub.received.first?.0)
        XCTAssertEqual(req.url?.absoluteString, "http://hub.example:7700/v1/tracker/command")
        XCTAssertEqual(req.httpMethod, "POST")
        XCTAssertEqual(req.value(forHTTPHeaderField: "Authorization"), "Bearer tok")
        XCTAssertEqual(req.value(forHTTPHeaderField: "Content-Type"), "application/json")
        XCTAssertTrue(r.queued.isEmpty)
    }

    func testQueuesWhileDownThenFlushesInOrder() throws {
        let hub = FakeHub()
        hub.status = nil
        var r = relay(hub)
        XCTAssertEqual(r.relay(body("first1")), .queued)
        XCTAssertTrue(r.inCooldown())
        // During the cooldown nothing is attempted: the next command queues right away.
        XCTAssertEqual(r.relay(body("second")), .queued)
        XCTAssertEqual(r.queued.count, 2)

        hub.status = 200
        r.cooldown = 0
        XCTAssertEqual(r.relay(body("third1")), .sent)
        let ids = hub.received.compactMap { try? TrackerJSON.decoder().decode(TrackerCommand.self, from: $0.1).id }
        XCTAssertEqual(ids, ["first1", "second", "third1"], "queued commands go first, in order")
        XCTAssertTrue(r.queued.isEmpty)
        XCTAssertFalse(r.inCooldown())
    }

    func testRejectedCommandsAreDropped() {
        let hub = FakeHub()
        hub.status = 400
        let r = relay(hub)
        XCTAssertEqual(r.relay(body("bad111")), .dropped)
        XCTAssertTrue(r.queued.isEmpty)
        hub.status = 503
        XCTAssertEqual(r.relay(body("retry1")), .queued, "server errors are retried later")
    }

    func testNoTokenQueuesWithoutTrying() {
        let hub = FakeHub()
        let r = relay(hub, token: nil)
        XCTAssertEqual(r.relay(body("wait11")), .queued)
        XCTAssertTrue(hub.received.isEmpty)
    }

    func testOutboxIsBounded() {
        let hub = FakeHub()
        hub.status = nil
        var r = relay(hub)
        r.maxQueued = 3
        for i in 0..<6 { r.relay(body("cmd\(i)aa")) }
        r.trim()
        XCTAssertEqual(r.queued.count, 3)
    }

    func testSettingsFromHub() {
        let hub = FakeHub()
        var r = relay(hub)
        r.transport = { req, _ in
            XCTAssertEqual(req.url?.path, "/v1/tracker/settings")
            return (200, Data(#"{"ok":true,"settings":{"evidence":"all","updatedAt":"2026-05-01T00:00:00Z"}}"#.utf8))
        }
        XCTAssertEqual(r.fetchSettings()?.evidence, .all)
    }

    func testConfigDecidesWhetherToRelay() throws {
        let hubCfg = OrbitalConfig.parse(data: Data(#"{"system":{"role":"hub"}}"#.utf8), home: tmp)
        XCTAssertNil(HubRelay(config: hubCfg, paths: paths, env: [:]))
        let secrets = tmp.appendingPathComponent("secrets.env")
        try write("ORBITAL_HUB_TOKEN=s3cret\n", to: secrets)
        let json = #"{"system":{"role":"satellite"},"hub_url":"http://hub:7700","paths":{"secrets":"\#(secrets.path)"}}"#
        let sat = OrbitalConfig.parse(data: Data(json.utf8), home: tmp)
        let r = try XCTUnwrap(HubRelay(config: sat, paths: paths, env: [:]))
        XCTAssertEqual(r.token, "s3cret")
        XCTAssertEqual(r.outbox, paths.hubOutboxDir)
    }
}

final class MediaArchiverTests: TempDirTestCase {
    func testArchiveNamesAreSanitized() {
        let rel = MediaArchiver.archiveRelativePath(itemID: "../../evil", mediaID: "m/1", date: Date(timeIntervalSince1970: 1_000_000_000))
        XCTAssertTrue(rel.hasPrefix("media/2001-09/"), rel)
        XCTAssertFalse(rel.dropFirst("media/".count).contains(".."))
        XCTAssertTrue(rel.hasSuffix("______evil-m_1.jpg"), rel)
    }

    func testArchiveInlineRoundTrip() throws {
        let p = paths
        let png = tmp.appendingPathComponent("shot.png")
        try makePNG(at: png, size: 900)
        let ref = MediaArchiver.archive(path: png.path, caption: nil, itemID: "item1", paths: p)
        XCTAssertEqual(ref.kind, .image)
        let rel = try XCTUnwrap(ref.archivePath)
        let archived = p.archiveURL(rel)
        XCTAssertTrue(FileManager.default.fileExists(atPath: archived.path))
        let thumb = try XCTUnwrap(MediaArchiver.thumbnail(archived, maxPixel: 2000))
        XCTAssertLessThanOrEqual(max(thumb.width, thumb.height), MediaArchiver.maxPixel)

        // Relay to another tracker: base64 out, stored again on the other side.
        let inline = try XCTUnwrap(MediaArchiver.inlineData(for: ref, paths: p))
        let other = TrackerPaths(root: tmp.appendingPathComponent("hub/tracker"), logsDir: tmp, runDir: tmp)
        let stored = MediaArchiver.storeInline(inline, ref: ref, itemID: "item1", paths: other)
        let storedRel = try XCTUnwrap(stored.archivePath)
        XCTAssertEqual(try Data(contentsOf: other.archiveURL(storedRel)), try Data(contentsOf: archived))
    }

    func testStoreInlineRejectsNonJPEG() {
        let ref = MediaRef(id: "m1", kind: .image, originalPath: "/x.png", archivePath: "media/x.jpg")
        let stored = MediaArchiver.storeInline(Data("hello".utf8).base64EncodedString(), ref: ref, itemID: "i1", paths: paths)
        XCTAssertNil(stored.archivePath)
        XCTAssertNil(MediaArchiver.storeInline("%%%not base64", ref: ref, itemID: "i1", paths: paths).archivePath)
    }

    func testKindsAndPaths() {
        XCTAssertEqual(MediaArchiver.kind(for: URL(fileURLWithPath: "/a.PNG")), .image)
        XCTAssertEqual(MediaArchiver.kind(for: URL(fileURLWithPath: "/a.mov")), .video)
        XCTAssertEqual(MediaArchiver.kind(for: URL(fileURLWithPath: "/a.txt")), .file)
        XCTAssertEqual(MediaArchiver.normalizePath("shots/a.png", relativeTo: "/work"), "/work/shots/a.png")
        XCTAssertEqual(MediaArchiver.normalizePath("file:///tmp/b.png"), "/tmp/b.png")
    }
}
