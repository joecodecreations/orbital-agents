import Foundation
import XCTest
@testable import OrbitalTrackerCore

final class ConfigTests: TempDirTestCase {
    func testDefaultsWithoutAResolvedFile() {
        let home = tmp.appendingPathComponent("home")
        let c = OrbitalConfig.parse(data: nil, home: home)
        XCTAssertFalse(c.loadedFromFile)
        XCTAssertEqual(c.trackerPort, 7701)
        XCTAssertEqual(c.sttPort, 7702)
        XCTAssertFalse(c.voice)
        XCTAssertEqual(c.titles, .auto)
        XCTAssertEqual(c.historyRetentionDays, 365)
        XCTAssertEqual(c.mediaRetentionDays, 60)
        XCTAssertEqual(c.staleHours, 24)
        XCTAssertEqual(c.logMaxBytes, 5_000_000)
        XCTAssertEqual(c.logBackups, 3)
        XCTAssertEqual(c.role, "hub")
        XCTAssertFalse(c.relaysToHub)
        XCTAssertEqual(c.trackerBundleID, "com.example.orbitalagents.tracker")
        XCTAssertEqual(c.trackerData.path, home.appendingPathComponent("data/tracker").path)
        XCTAssertEqual(c.logsDir.path, home.appendingPathComponent("logs").path)
        XCTAssertEqual(c.runDir.path, home.appendingPathComponent("run").path)
        XCTAssertEqual(c.secretsFile.path, home.appendingPathComponent("secrets.env").path)
        XCTAssertFalse(c.machineName.isEmpty)
    }

    func testResolvedFileValues() throws {
        let json = """
        {"system": {"name": "Orbital Agents", "role": "satellite", "machine_name": ""},
         "machine_name": "build-box", "hub_url": "http://hub.example:7700/",
         "tracker": {"enabled": true, "port": 17711, "stt_port": "17712", "voice": true, "titles": "none",
                     "history_retention_days": 30, "media_retention_days": 7, "stale_hours": 6,
                     "grok_model": "fast-model"},
         "logging": {"level": "debug", "max_bytes": 2048, "backups": 5, "retention_days": 3},
         "apple": {"bundle_prefix": "org.sample"},
         "paths": {"home": "/opt/orbital", "tracker_data": "/opt/orbital/data/tracker", "logs": "/var/log/orbital",
                   "run": "/opt/orbital/run", "secrets": "/opt/orbital/secrets.env"}}
        """
        let c = OrbitalConfig.parse(data: Data(json.utf8), home: tmp)
        XCTAssertTrue(c.loadedFromFile)
        XCTAssertEqual(c.machineName, "build-box")
        XCTAssertEqual(c.hubURL, "http://hub.example:7700")
        XCTAssertTrue(c.isSatellite)
        XCTAssertTrue(c.relaysToHub)
        XCTAssertEqual(c.trackerPort, 17711)
        XCTAssertEqual(c.sttPort, 17712, "numbers given as strings are accepted")
        XCTAssertTrue(c.voice)
        XCTAssertEqual(c.titles, TitleMode.none)
        XCTAssertEqual(c.grokModel, "fast-model")
        XCTAssertEqual(c.historyRetentionDays, 30)
        XCTAssertEqual(c.mediaRetentionDays, 7)
        XCTAssertEqual(c.staleHours, 6)
        XCTAssertEqual(c.logLevel, "DEBUG")
        XCTAssertEqual(c.logMaxBytes, 2048)
        XCTAssertEqual(c.logBackups, 5)
        XCTAssertEqual(c.logRetentionDays, 3)
        XCTAssertEqual(c.trackerBundleID, "org.sample.orbitalagents.tracker")
        XCTAssertEqual(c.home.path, "/opt/orbital")
        XCTAssertEqual(c.trackerData.path, "/opt/orbital/data/tracker")
        XCTAssertEqual(c.logsDir.path, "/var/log/orbital")
        XCTAssertEqual(c.secretsFile.path, "/opt/orbital/secrets.env")
        XCTAssertEqual(TrackerPaths(config: c).logFile.path, "/var/log/orbital/tracker.log")
    }

    func testBadValuesFallBack() {
        let json = #"{"tracker": {"port": 99999, "voice": "maybe", "titles": "grok"}, "system": {"role": "weird"}, "hub_url": "http://h:1"}"#
        let c = OrbitalConfig.parse(data: Data(json.utf8), home: tmp)
        XCTAssertEqual(c.trackerPort, 7701)
        XCTAssertFalse(c.voice)
        XCTAssertEqual(c.titles, .grok)
        XCTAssertEqual(c.role, "hub")
        XCTAssertFalse(c.relaysToHub, "only satellites relay")
        XCTAssertFalse(OrbitalConfig.parse(data: Data("not json".utf8), home: tmp).loadedFromFile)
    }

    func testHomeFromEnvironment() {
        XCTAssertEqual(OrbitalConfig.homeDirectory(env: ["ORBITAL_HOME": "/tmp/orb-test"]).path, "/tmp/orb-test")
        XCTAssertTrue(OrbitalConfig.homeDirectory(env: [:]).path.hasSuffix("/.orbital-agents"))
        XCTAssertTrue(OrbitalConfig.homeDirectory(env: ["ORBITAL_HOME": "  "]).path.hasSuffix("/.orbital-agents"))
    }

    func testLoadReadsTheFileUnderOrbitalHome() throws {
        try write(#"{"tracker": {"port": 17799}, "machine_name": "from-file"}"#, to: tmp.appendingPathComponent("config.resolved.json"))
        let c = OrbitalConfig.load(env: ["ORBITAL_HOME": tmp.path])
        XCTAssertEqual(c.trackerPort, 17799)
        XCTAssertEqual(c.machineName, "from-file")
        XCTAssertEqual(c.trackerData.path, tmp.standardizedFileURL.appendingPathComponent("data/tracker").path)
    }

    func testSecrets() throws {
        let text = """
        # Orbital Agents secrets
        ORBITAL_HUB_TOKEN="abc123"
        EMPTY=
        QUOTED='q'
          SPACED = value with spaces
        not a line
        """
        let parsed = Secrets.parse(text)
        XCTAssertEqual(parsed["ORBITAL_HUB_TOKEN"], "abc123")
        XCTAssertEqual(parsed["QUOTED"], "q")
        XCTAssertEqual(parsed["SPACED"], "value with spaces")
        XCTAssertEqual(parsed["EMPTY"], "")
        let file = tmp.appendingPathComponent("secrets.env")
        try write(text, to: file)
        XCTAssertEqual(Secrets.get("ORBITAL_HUB_TOKEN", file: file, env: [:]), "abc123")
        XCTAssertEqual(Secrets.get("ORBITAL_HUB_TOKEN", file: file, env: ["ORBITAL_HUB_TOKEN": "from-env"]), "from-env")
        XCTAssertNil(Secrets.get("EMPTY", file: file, env: [:]))
        XCTAssertNil(Secrets.get("MISSING", file: tmp.appendingPathComponent("nope"), env: [:]))
    }

    func testTitleModes() {
        XCTAssertEqual(TitleMode(loose: "NONE"), TitleMode.none)
        XCTAssertEqual(TitleMode(loose: "grok"), .grok)
        XCTAssertEqual(TitleMode(loose: nil), .auto)
        XCTAssertEqual(TitleMode(loose: "whatever"), .auto)
    }
}
