#if DEBUG
import Foundation

/// Simulator and screenshot runs: draw a folder of sample payloads without a hub or a sign-in.
///
///     SIMCTL_CHILD_ORBITAL_PREVIEW_DIR=/path/to/Fixtures/preview \
///       xcrun simctl launch booted <bundle_prefix>.orbitalagents
///
/// The folder holds the same shapes the hub serves (see apps/orbital-app/Fixtures/preview):
/// `hub.json` (`/v1/hub/info`), `catalog.json` (spaces), `prefs.json` (pinned sections), `sections/*.json`, `alerts.json`, `pulse.json`,
/// `jobs.json` (`/v1/jobs`), `satellites.json`, `agent-jobs.json`, `agent-logs/<job>.txt`, `job-logs/<id>.txt`,
/// `desk/desk.json`, `desk/tracker.json`, `desk/items/<id>.json`, `desk/media/<media id>.jpg`, and `media/<id>`.
/// `ORBITAL_PREVIEW_ROUTE` opens an orbital:// link at launch. Release builds do not contain this code.
///
/// So the sample data always looks current, a JSON string may be a relative date token, swapped for a real
/// date as the file loads: `"@now"`, `"@now-15m"`, `"@now+2h"`, `"@now-3d"` (an ISO timestamp) and
/// `"@day"`, `"@day-6"`, `"@day+3"` (a yyyy-MM-dd day).
enum OrbitalPreview {
    static var directory: URL? {
        guard let raw = ProcessInfo.processInfo.environment["ORBITAL_PREVIEW_DIR"], !raw.isEmpty else { return nil }
        return URL(fileURLWithPath: raw, isDirectory: true)
    }

    static var isActive: Bool { directory != nil }

    /// An orbital:// link to open at launch, e.g. "orbital://agents", for screenshots.
    static var launchRoute: URL? {
        guard isActive, let raw = ProcessInfo.processInfo.environment["ORBITAL_PREVIEW_ROUTE"], !raw.isEmpty else { return nil }
        return URL(string: raw)
    }

    private static func data(_ name: String) -> Data? {
        guard let url = directory?.appendingPathComponent(name) else { return nil }
        return (try? Data(contentsOf: url)).map { expand($0) }
    }

    private static let token = try! NSRegularExpression(pattern: #""@(now|day)(?:([+-])(\d+)([smhd]?))?""#)

    /// Replaces every `"@now…"` / `"@day…"` string with a date relative to `now`.
    static func expand(_ data: Data, now: Date = Date()) -> Data {
        guard let text = String(data: data, encoding: .utf8), text.contains("\"@") else { return data }
        let ns = text as NSString
        var out = ""
        var last = 0
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime]
        let day = DateFormatter()
        day.calendar = Calendar(identifier: .gregorian)
        day.locale = Locale(identifier: "en_US_POSIX")
        day.dateFormat = "yyyy-MM-dd"
        for match in token.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            out += ns.substring(with: NSRange(location: last, length: match.range.location - last))
            last = match.range.location + match.range.length
            func group(_ i: Int) -> String? {
                let r = match.range(at: i)
                return r.location == NSNotFound ? nil : ns.substring(with: r)
            }
            let sign: Double = group(2) == "-" ? -1 : 1
            let amount = Double(group(3) ?? "0") ?? 0
            if group(1) == "day" {
                let date = Calendar.current.date(byAdding: .day, value: Int(sign * amount), to: now) ?? now
                out += "\"\(day.string(from: date))\""
            } else {
                let unit: Double
                switch group(4) {
                case "s": unit = 1
                case "h": unit = 3600
                case "d": unit = 86400
                default: unit = 60
                }
                out += "\"\(iso.string(from: now.addingTimeInterval(sign * amount * unit)))\""
            }
        }
        out += ns.substring(from: last)
        return Data(out.utf8)
    }

    private static func decode<T: Decodable>(_ type: T.Type, _ name: String) -> T? {
        guard let data = data(name) else { return nil }
        return try? JSONDecoder().decode(T.self, from: data)
    }

    static func sections() -> [SectionDocument] {
        guard let folder = directory?.appendingPathComponent("sections", isDirectory: true),
              let urls = try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)
        else { return [] }
        return urls.filter { $0.pathExtension == "json" }.sorted { $0.lastPathComponent < $1.lastPathComponent }.compactMap { url in
            guard let data = try? Data(contentsOf: url) else { return nil }
            return try? JSONDecoder().decode(SectionDocument.self, from: expand(data))
        }
    }

    static func spaces() -> [SpaceInfo] {
        decode(CatalogResponse.self, "catalog.json")?.spaces ?? []
    }

    /// A copy of `GET /v1/prefs` (`{"prefs": {"pinned_sections": [...]}}`): the sections pinned to Home.
    static func prefs() -> SharedPrefs? {
        struct Box: Decodable { var prefs: SharedPrefs }
        return decode(Box.self, "prefs.json")?.prefs
    }

    static func hubInfo() -> HubInfo? {
        decode(HubInfo.self, "hub.json")
    }

    static func alerts() -> [OrbitalAlert] {
        (decode([OrbitalAlert].self, "alerts.json") ?? []).sorted { ($0.createdAt ?? "") > ($1.createdAt ?? "") }
    }

    static func pulses() -> [PulseItem] {
        decode([PulseItem].self, "pulse.json") ?? []
    }

    /// A copy of `GET /v1/jobs`.
    static func jobs() -> JobsSnapshot? {
        decode(JobsSnapshot.self, "jobs.json")
    }

    static func jobLog(_ id: String) -> [String]? {
        data("job-logs/\(id).txt").flatMap { String(data: $0, encoding: .utf8) }?.components(separatedBy: "\n")
    }

    /// A copy of `GET /v1/satellites`.
    static func satellites() -> [Satellite] {
        decode(SatelliteList.self, "satellites.json")?.satellites ?? []
    }

    /// A copy of `GET /v1/agents/jobs`.
    static func agentJobs() -> [AgentJob] {
        decode(AgentJobList.self, "agent-jobs.json")?.jobs ?? []
    }

    static func agentJobLog(_ id: String) -> [String]? {
        data("agent-logs/\(id).txt").flatMap { String(data: $0, encoding: .utf8) }?.components(separatedBy: "\n")
    }

    /// desk/<name>: copies of `GET /v1/desk`, `/v1/tracker/items` (tracker.json), and `/v1/tracker/items/<id>` (items/<id>.json).
    static func deskData(_ name: String) -> Data? {
        data("desk/\(name)")
    }

    static func mediaFile(for src: String) -> URL? {
        guard let directory else { return nil }
        if src.hasPrefix("/v1/tracker/media/") {
            // desk/media/<media id>.jpg stands in for every size of that picture.
            let parts = src.split(separator: "?")[0].split(separator: "/")
            guard let media = parts.last else { return nil }
            let file = directory.appendingPathComponent("desk/media/\(media).jpg")
            return FileManager.default.fileExists(atPath: file.path) ? file : nil
        }
        guard src.hasPrefix("/v1/media/") else { return nil }
        let id = String(src.dropFirst("/v1/media/".count))
        let file = directory.appendingPathComponent("media", isDirectory: true).appendingPathComponent(id)
        return FileManager.default.fileExists(atPath: file.path) ? file : nil
    }
}
#endif
