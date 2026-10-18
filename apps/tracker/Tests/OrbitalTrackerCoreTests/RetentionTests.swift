import Foundation
import XCTest
@testable import OrbitalTrackerCore

final class RetentionTests: TempDirTestCase {
    private func item(_ id: String, status: ItemStatus, daysAgo: Double, now: Date) -> TrackedItem {
        var item = TrackedItem(id: id, title: id, source: .agent, colorIndex: 0,
                               createdAt: now.addingTimeInterval(-daysAgo * 86_400 - 3600))
        item.status = status
        item.updatedAt = now.addingTimeInterval(-daysAgo * 86_400)
        if status == .done { item.completedAt = item.updatedAt }
        return item
    }

    func testHistoryPruning() {
        let now = Date()
        let items = [
            item("old-done", status: .done, daysAgo: 400, now: now),
            item("old-active", status: .active, daysAgo: 400, now: now),
            item("recent-done", status: .done, daysAgo: 10, now: now),
            item("edge-done", status: .done, daysAgo: 364, now: now),
        ]
        let result = Retention.pruneHistory(items, days: 365, now: now)
        XCTAssertEqual(result.removed.map(\.id), ["old-done"])
        XCTAssertEqual(Set(result.kept.map(\.id)), ["old-active", "recent-done", "edge-done"])

        // 0 keeps everything.
        XCTAssertEqual(Retention.pruneHistory(items, days: 0, now: now).kept.count, 4)
    }

    func testMediaPruningDeletesOldFilesAndEmptyMonths() throws {
        let p = paths
        let oldFile = p.mediaDir.appendingPathComponent("2025-01/item1-m1.jpg")
        let newFile = p.mediaDir.appendingPathComponent("2026-10/item2-m2.jpg")
        try write("old", to: oldFile)
        try write("new", to: newFile)
        try setModified(oldFile, daysAgo: 90)
        try setModified(newFile, daysAgo: 5)

        let deleted = Retention.pruneMedia(mediaDir: p.mediaDir, relativeTo: p.root, days: 60)
        XCTAssertEqual(deleted, ["media/2025-01/item1-m1.jpg"])
        XCTAssertFalse(FileManager.default.fileExists(atPath: oldFile.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: oldFile.deletingLastPathComponent().path), "empty month folder removed")
        XCTAssertTrue(FileManager.default.fileExists(atPath: newFile.path))

        XCTAssertEqual(Retention.pruneMedia(mediaDir: p.mediaDir, relativeTo: p.root, days: 0), [])
    }

    func testForgetArchives() {
        var item = TrackedItem(id: "i1", title: "t", source: .agent, colorIndex: 0)
        item.media = [MediaRef(id: "a", kind: .image, originalPath: "/x.png", archivePath: "media/2025-01/i1-a.jpg"),
                      MediaRef(id: "b", kind: .image, originalPath: "/y.png", archivePath: "media/2026-10/i1-b.jpg")]
        var sub = SubTask(id: "s1", agent: "claude", title: "s")
        sub.media = [MediaRef(id: "c", kind: .image, originalPath: "/z.png", archivePath: "media/2025-01/i1-c.jpg")]
        item.subagents = [sub]
        var items = [item]
        let changed = Retention.forgetArchives(["media/2025-01/i1-a.jpg", "media/2025-01/i1-c.jpg"], in: &items)
        XCTAssertEqual(changed, 2)
        XCTAssertNil(items[0].media[0].archivePath)
        XCTAssertNotNil(items[0].media[1].archivePath)
        XCTAssertNil(items[0].subagents[0].media[0].archivePath)
    }

    func testPruneFilesIgnoresSubfoldersUnlessRecursive() throws {
        let dir = tmp.appendingPathComponent("outbox")
        let old = dir.appendingPathComponent("1-a.json")
        let nested = dir.appendingPathComponent("failed/2-b.json")
        try write("{}", to: old)
        try write("{}", to: nested)
        try setModified(old, daysAgo: 30)
        try setModified(nested, daysAgo: 30)
        let removed = Retention.pruneFiles(in: dir, olderThan: Date().addingTimeInterval(-7 * 86_400))
        XCTAssertEqual(removed.map(\.lastPathComponent), ["1-a.json"])
        XCTAssertTrue(FileManager.default.fileExists(atPath: nested.path))
    }
}
