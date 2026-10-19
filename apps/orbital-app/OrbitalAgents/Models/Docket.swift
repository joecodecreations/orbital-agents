import Foundation

// MARK: - Docket (hearings and deadlines)

/// Where a row opens. Same keys as a button's `open` action.
struct BlockOpenTarget: Codable, Equatable {
    var sectionId: String
    var nodeId: String?
    var tabId: String?

    enum CodingKeys: String, CodingKey {
        case sectionId = "section_id"
        case nodeId = "node_id"
        case tabId = "tab_id"
    }

    var location: PageLocation { PageLocation(sectionID: sectionId, nodeID: nodeId, tabID: tabId) }
}

/// One hearing or deadline. The day count is computed on the phone from `date`.
struct DocketItem: Codable, Equatable, Identifiable {
    var id: String
    var kind: String
    var date: String
    var time: String?
    var title: String
    var caseName: String?
    var caseNumber: String?
    var place: String?
    var detail: String?
    var confirmed: Bool
    var appearance: Bool
    var canReady: Bool
    var ready: Bool
    var readyAt: String?
    var prepOpen: Int?
    var prepTotal: Int?
    var pending: Bool
    var open: BlockOpenTarget?

    enum CodingKeys: String, CodingKey {
        case id, kind, date, time, title, detail, confirmed, appearance, ready, pending, open
        case caseName = "case"
        case caseNumber = "case_number"
        case place = "where"
        case canReady = "can_ready"
        case readyAt = "ready_at"
        case prepOpen = "prep_open"
        case prepTotal = "prep_total"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let id = c.lenientString(.id), !id.isEmpty else {
            throw DecodingError.keyNotFound(CodingKeys.id, .init(codingPath: c.codingPath, debugDescription: "docket item id"))
        }
        self.id = id
        date = try c.decode(String.self, forKey: .date)
        kind = Insights.nonEmpty(c.lenientString(.kind)) ?? "hearing"
        time = c.lenientString(.time)
        title = c.lenientString(.title) ?? ""
        caseName = c.lenientString(.caseName)
        caseNumber = c.lenientString(.caseNumber)
        place = c.lenientString(.place)
        detail = c.lenientString(.detail)
        confirmed = c.lenientBool(.confirmed) ?? true
        appearance = c.lenientBool(.appearance) ?? true
        canReady = c.lenientBool(.canReady) ?? false
        ready = c.lenientBool(.ready) ?? false
        readyAt = c.lenientString(.readyAt)
        prepOpen = c.lenientInt(.prepOpen)
        prepTotal = c.lenientInt(.prepTotal)
        pending = c.lenientBool(.pending) ?? false
        open = try? c.decodeIfPresent(BlockOpenTarget.self, forKey: .open)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(id, forKey: .id)
        try c.encode(kind, forKey: .kind)
        try c.encode(date, forKey: .date)
        try c.encodeIfPresent(time, forKey: .time)
        try c.encode(title, forKey: .title)
        try c.encodeIfPresent(caseName, forKey: .caseName)
        try c.encodeIfPresent(caseNumber, forKey: .caseNumber)
        try c.encodeIfPresent(place, forKey: .place)
        try c.encodeIfPresent(detail, forKey: .detail)
        try c.encode(confirmed, forKey: .confirmed)
        try c.encode(appearance, forKey: .appearance)
        try c.encode(canReady, forKey: .canReady)
        try c.encode(ready, forKey: .ready)
        try c.encodeIfPresent(readyAt, forKey: .readyAt)
        try c.encodeIfPresent(prepOpen, forKey: .prepOpen)
        try c.encodeIfPresent(prepTotal, forKey: .prepTotal)
        try c.encode(pending, forKey: .pending)
        try c.encodeIfPresent(open, forKey: .open)
    }

    var isDeadline: Bool { kind == "deadline" }
    var isHearing: Bool { !isDeadline }
    /// Only a hearing carries the Ready toggle.
    var showsReady: Bool { isHearing && canReady }
}

struct DocketBlock: Codable, Equatable {
    var type: String = "docket"
    var id: String
    var title: String?
    var asOf: String?
    var emptyText: String?
    var items: [DocketItem]

    enum CodingKeys: String, CodingKey {
        case type, id, title, items
        case asOf = "as_of"
        case emptyText = "empty_text"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = c.lenientString(.title)
        asOf = c.lenientString(.asOf)
        emptyText = c.lenientString(.emptyText)
        items = c.lossy(DocketItem.self, .items)
    }

}
