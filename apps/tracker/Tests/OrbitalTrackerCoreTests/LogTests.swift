import Foundation
import XCTest
@testable import OrbitalTrackerCore

final class LogTests: TempDirTestCase {
    func testLineFormatMatchesThePythonLogs() throws {
        let date = Date(timeIntervalSince1970: 1_767_323_045.678)
        let line = JSONLineLogger.formatLine(date: date, level: .warning, component: "tracker", msg: "item added",
                                             fields: ["id": "abc", "count": 3, "missing": nil, "ok": true, "msg": "ignored"])
        XCTAssertTrue(line.hasPrefix(#"{"ts":"2026-01-02T03:04:05.678Z","level":"WARNING","component":"tracker","msg":"item added""#), line)
        let obj = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any])
        XCTAssertEqual(obj["id"] as? String, "abc")
        XCTAssertEqual(obj["count"] as? Int, 3)
        XCTAssertEqual(obj["ok"] as? Bool, true)
        XCTAssertNil(obj["missing"])
        XCTAssertEqual(obj["msg"] as? String, "item added", "fields can't overwrite the reserved keys")
        XCTAssertFalse(line.contains("\n"))
    }

    func testLevelsFilter() throws {
        let file = tmp.appendingPathComponent("logs/tracker.log")
        let log = JSONLineLogger(file: file, component: "tracker", maxBytes: 10_000, backups: 2, level: .warning)
        log.info("hidden")
        log.warning("shown")
        log.error("also shown", ["path": URL(fileURLWithPath: "/tmp/x"), "list": ["a", 1]])
        let lines = try String(contentsOf: file, encoding: .utf8).split(separator: "\n")
        XCTAssertEqual(lines.count, 2)
        XCTAssertTrue(lines[1].contains(#""path":"/tmp/x""#))
        XCTAssertEqual(LogLevel(name: "warn"), .warning)
        XCTAssertEqual(LogLevel(name: "nonsense"), .info)
    }

    func testRollsBySizeAndKeepsBackups() throws {
        let file = tmp.appendingPathComponent("logs/tracker.log")
        let log = JSONLineLogger(file: file, component: "tracker", maxBytes: 300, backups: 2)
        for i in 0..<40 { log.info("message number \(i) with some padding to fill the file") }
        let fm = FileManager.default
        XCTAssertTrue(fm.fileExists(atPath: file.path))
        XCTAssertTrue(fm.fileExists(atPath: file.path + ".1"))
        XCTAssertTrue(fm.fileExists(atPath: file.path + ".2"))
        XCTAssertFalse(fm.fileExists(atPath: file.path + ".3"), "only `backups` rolled files are kept")
        for name in [file.path, file.path + ".1", file.path + ".2"] {
            let size = (try fm.attributesOfItem(atPath: name)[.size] as? NSNumber)?.intValue ?? 0
            XCTAssertLessThanOrEqual(size, 300)
        }
        // The newest line is in the live file, older ones in .1.
        let live = try String(contentsOfFile: file.path, encoding: .utf8)
        XCTAssertTrue(live.contains("message number 39"))
        let rolled = try String(contentsOfFile: file.path + ".1", encoding: .utf8)
        XCTAssertFalse(rolled.contains("message number 39"))
    }

    func testZeroBackupsTruncates() throws {
        let file = tmp.appendingPathComponent("logs/x.log")
        let log = JSONLineLogger(file: file, component: "x", maxBytes: 256, backups: 0)
        for i in 0..<20 { log.info("line \(i) padding padding padding padding") }
        XCTAssertFalse(FileManager.default.fileExists(atPath: file.path + ".1"))
        let size = (try FileManager.default.attributesOfItem(atPath: file.path)[.size] as? NSNumber)?.intValue ?? 0
        XCTAssertLessThanOrEqual(size, 256)
    }

    func testRollIfLarge() throws {
        let file = tmp.appendingPathComponent("logs/tracker-voice.stderr.log")
        try write(String(repeating: "x", count: 2000), to: file)
        JSONLineLogger.rollIfLarge(file, maxBytes: 5000, backups: 1)
        XCTAssertTrue(FileManager.default.fileExists(atPath: file.path))
        JSONLineLogger.rollIfLarge(file, maxBytes: 1000, backups: 1)
        XCTAssertFalse(FileManager.default.fileExists(atPath: file.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: file.path + ".1"))
    }
}
