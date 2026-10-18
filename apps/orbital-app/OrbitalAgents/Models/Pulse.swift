import Foundation

/// One realtime value from `POST /v1/pulse`. The newest item per key replaces the last one.
/// A pulse never changes a section.
struct PulseItem: Codable, Equatable, Identifiable {
    var key: String
    var sectionId: String
    var nodeId: String?
    var label: String
    var value: Double
    var display: String?
    var detail: String?
    var unit: String?
    var platform: String?
    var source: String?
    var at: String?
    var ttlSeconds: Int?
    var order: Int?
    var series: [PulsePoint]?

    var id: String { key }

    enum CodingKeys: String, CodingKey {
        case key, label, value, display, detail, unit, platform, source, at, order, series
        case sectionId = "section_id"
        case nodeId = "node_id"
        case ttlSeconds = "ttl_seconds"
    }

    var observedAt: Date? { OrbitalDates.parse(at) }

    /// Inside its time-to-live. A stale value still shows, greyed, with its age.
    func isLive(now: Date = Date()) -> Bool {
        guard let observedAt else { return false }
        return now.timeIntervalSince(observedAt) <= Double(ttlSeconds ?? 180)
    }

    var shownValue: String {
        if let display, !display.isEmpty { return display }
        if value.rounded() == value, abs(value) < 1e12 { return Int(value).formatted() }
        return value.formatted(.number.precision(.fractionLength(0 ... 2)))
    }

    func belongs(to location: PageLocation) -> Bool {
        sectionId == location.sectionID && (nodeId == nil || nodeId == location.nodeID)
    }
}

struct PulsePoint: Codable, Equatable {
    var x: String
    var y: Double
}
