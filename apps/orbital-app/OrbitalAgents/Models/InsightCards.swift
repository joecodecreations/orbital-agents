import Foundation

// insight_cards: findings a feed raised, one card each, with actions you approve in the app.
// Approve, Snooze, and Dismiss post insight.* actions to the card's section; the feed that owns it writes the result back.

struct InsightCardsBlock: Codable, Equatable {
    var type: String = "insight_cards"
    var id: String
    var title: String?
    var space: String?
    var asOf: String?
    var nextRun: String?
    var note: String?
    var emptyText: String?
    var cards: [InsightCard]

    enum CodingKeys: String, CodingKey {
        case type, id, title, space, company, note, cards
        case asOf = "as_of"
        case nextRun = "next_run"
        case emptyText = "empty_text"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        id = try c.decode(String.self, forKey: .id)
        title = c.lenientString(.title)
        space = Insights.nonEmpty(c.lenientString(.space)) ?? c.lenientString(.company)
        asOf = c.lenientString(.asOf)
        nextRun = c.lenientString(.nextRun)
        note = c.lenientString(.note)
        emptyText = c.lenientString(.emptyText)
        cards = c.lossy(InsightCard.self, .cards)
    }

    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: CodingKeys.self)
        try c.encode(type, forKey: .type)
        try c.encode(id, forKey: .id)
        try c.encodeIfPresent(title, forKey: .title)
        try c.encodeIfPresent(space, forKey: .space)
        try c.encodeIfPresent(asOf, forKey: .asOf)
        try c.encodeIfPresent(nextRun, forKey: .nextRun)
        try c.encodeIfPresent(note, forKey: .note)
        try c.encodeIfPresent(emptyText, forKey: .emptyText)
        try c.encode(cards, forKey: .cards)
    }
}

struct InsightPerson: Codable, Equatable {
    var label: String?
    var email: String?
    var userID: String?
    var brand: String?

    enum CodingKeys: String, CodingKey {
        case label, email, brand
        case userID = "user_id"
    }
}

struct InsightLink: Codable, Equatable, Identifiable {
    var label: String
    var url: String
    var id: String { url }
}

struct InsightEmail: Codable, Equatable {
    var to: String
    var from: String?
    var replyTo: String?
    var subject: String
    var body: String

    enum CodingKeys: String, CodingKey {
        case to, from, subject, body
        case replyTo = "reply_to"
    }

    init(to: String, from: String? = nil, replyTo: String? = nil, subject: String, body: String) {
        self.to = to
        self.from = from
        self.replyTo = replyTo
        self.subject = subject
        self.body = body
    }

    /// `to` may be one address or a list of them.
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        if let list = try? c.decode([String].self, forKey: .to) {
            to = list.joined(separator: ", ")
        } else {
            to = c.lenientString(.to) ?? ""
        }
        from = c.lenientString(.from)
        replyTo = c.lenientString(.replyTo)
        subject = c.lenientString(.subject) ?? ""
        body = c.lenientString(.body) ?? ""
    }
}

struct InsightBrief: Codable, Equatable {
    var title: String?
    var prompt: String
    var repo: String?
    var branch: String?
    var worktree: String?
    var sessionID: String?
    var resume: String?
    var log: String?
    var trackerID: String?
    var commits: String?

    enum CodingKeys: String, CodingKey {
        case title, prompt, repo, branch, worktree, resume, log, commits
        case sessionID = "session_id"
        case trackerID = "tracker_id"
    }
}

struct InsightAction: Codable, Equatable, Identifiable {
    var id: String
    var kind: String
    var label: String
    var approveLabel: String?
    var state: String
    var statusText: String?
    var note: String?
    var blockedReason: String?
    var digest: String?
    var updatedAt: String?
    var email: InsightEmail?
    var brief: InsightBrief?

    enum CodingKeys: String, CodingKey {
        case id, kind, label, state, note, digest, email, brief
        case approveLabel = "approve_label"
        case statusText = "status_text"
        case blockedReason = "blocked_reason"
        case updatedAt = "updated_at"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let id = c.lenientString(.id), !id.isEmpty else {
            throw DecodingError.keyNotFound(CodingKeys.id, .init(codingPath: c.codingPath, debugDescription: "action id"))
        }
        self.id = id
        kind = c.lenientString(.kind) ?? "note"
        label = c.lenientString(.label) ?? "Action"
        approveLabel = c.lenientString(.approveLabel)
        state = c.lenientString(.state) ?? "ready"
        statusText = c.lenientString(.statusText)
        note = c.lenientString(.note)
        blockedReason = c.lenientString(.blockedReason)
        digest = c.lenientString(.digest)
        updatedAt = c.lenientString(.updatedAt)
        email = try? c.decodeIfPresent(InsightEmail.self, forKey: .email)
        brief = try? c.decodeIfPresent(InsightBrief.self, forKey: .brief)
    }

    /// The phone may post an approval: the action is waiting for one, or the last try failed.
    var canApprove: Bool {
        guard state == "ready" || state == "failed" else { return false }
        switch kind {
        case "email": return email != nil
        case "agent": return brief != nil
        default: return false
        }
    }

    var actionType: String? {
        switch kind {
        case "email": return "insight.email.approve"
        case "agent": return "insight.agent.approve"
        default: return nil
        }
    }

    var approveTitle: String {
        if let approveLabel, !approveLabel.isEmpty { return approveLabel }
        return kind == "email" ? "Approve & send" : "Approve & start"
    }

    var symbol: String {
        switch kind {
        case "email": return "envelope.fill"
        case "agent": return "wrench.and.screwdriver.fill"
        default: return "note.text"
        }
    }
}

struct InsightCard: Codable, Equatable, Identifiable {
    var id: String
    var kind: String
    var severity: String
    var state: String
    var title: String
    var summary: String?
    var product: String?
    var person: InsightPerson?
    var deadline: String?
    var deadlineLabel: String?
    var count: Int?
    var countLabel: String?
    var statusText: String?
    var firstSeen: String?
    var updatedAt: String?
    var snoozedUntil: String?
    var pending: Bool
    var evidence: [String]
    var metrics: [MetricTile]
    var links: [InsightLink]
    var actions: [InsightAction]

    enum CodingKeys: String, CodingKey {
        case id, kind, severity, state, title, summary, product, person, deadline, count, pending, evidence, metrics, links, actions
        case deadlineLabel = "deadline_label"
        case countLabel = "count_label"
        case statusText = "status_text"
        case firstSeen = "first_seen"
        case updatedAt = "updated_at"
        case snoozedUntil = "snoozed_until"
    }

    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        guard let id = c.lenientString(.id), !id.isEmpty else {
            throw DecodingError.keyNotFound(CodingKeys.id, .init(codingPath: c.codingPath, debugDescription: "card id"))
        }
        self.id = id
        kind = c.lenientString(.kind) ?? "insight"
        severity = c.lenientString(.severity) ?? "medium"
        state = c.lenientString(.state) ?? "new"
        title = c.lenientString(.title) ?? ""
        summary = c.lenientString(.summary)
        product = c.lenientString(.product)
        person = try? c.decodeIfPresent(InsightPerson.self, forKey: .person)
        deadline = c.lenientString(.deadline)
        deadlineLabel = c.lenientString(.deadlineLabel)
        count = c.lenientInt(.count)
        countLabel = c.lenientString(.countLabel)
        statusText = c.lenientString(.statusText)
        firstSeen = c.lenientString(.firstSeen)
        updatedAt = c.lenientString(.updatedAt)
        snoozedUntil = c.lenientString(.snoozedUntil)
        pending = c.lenientBool(.pending) ?? false
        evidence = (try? c.decodeIfPresent([String].self, forKey: .evidence)) ?? []
        metrics = c.lossy(MetricTile.self, .metrics)
        links = c.lossy(InsightLink.self, .links)
        actions = c.lossy(InsightAction.self, .actions)
    }

    /// Still asks something of you: shown on Home.
    var isOpen: Bool {
        switch state {
        case "dismissed", "done", "resolved": return false
        case "snoozed":
            guard let until = OrbitalDates.parse(snoozedUntil) else { return false }
            return until <= Date()
        default: return true
        }
    }

    var severityRank: Int {
        switch severity {
        case "critical": return 0
        case "high": return 1
        case "medium": return 2
        case "low": return 3
        default: return 4
        }
    }

    /// Whole days from today to the deadline in the phone's calendar. Negative when past.
    var daysToDeadline: Int? {
        guard let deadline, let day = Insights.day(deadline) else { return nil }
        let today = Calendar.current.startOfDay(for: Date())
        return Calendar.current.dateComponents([.day], from: today, to: day).day
    }

    var activeAction: InsightAction? {
        actions.first { ["pending", "running", "waiting"].contains($0.state) }
    }
}

/// One card with the page it lives on, so a tap posts to the right section.
struct InsightRef: Identifiable, Equatable {
    var id: String
    var sectionID: String
    var sectionTitle: String
    var blockID: String
    var nodeID: String?
    var tabID: String?
    var accent: String?
    var card: InsightCard
}

enum InsightFeed {
    /// Every insight card across every section, deduped by section and card id.
    static func collect(_ sections: [SectionDocument]) -> [InsightRef] {
        var out: [InsightRef] = []
        var seen = Set<String>()
        for section in sections {
            var pages: [(node: String?, tab: String?, blocks: [ContentBlock])] = [(nil, nil, section.blocks)]
            pages += section.tabs.map { (nil, $0.id, $0.blocks) }
            for node in section.nodes {
                pages.append((node.id, nil, node.blocks))
                pages += node.tabs.map { (node.id, $0.id, $0.blocks) }
            }
            for page in pages {
                for block in page.blocks {
                    guard case .insightCards(let insights) = block else { continue }
                    for card in insights.cards {
                        let key = "\(section.id)/\(card.id)"
                        guard seen.insert(key).inserted else { continue }
                        out.append(InsightRef(
                            id: key, sectionID: section.id, sectionTitle: section.title, blockID: insights.id,
                            nodeID: page.node, tabID: page.tab, accent: section.accent, card: card
                        ))
                    }
                }
            }
        }
        return out
    }

    /// Open cards, most severe first, then the nearest deadline, then the newest.
    static func open(_ refs: [InsightRef]) -> [InsightRef] {
        refs.filter(\.card.isOpen).sorted { lhs, rhs in
            if lhs.card.severityRank != rhs.card.severityRank { return lhs.card.severityRank < rhs.card.severityRank }
            let ld = lhs.card.daysToDeadline ?? Int.max
            let rd = rhs.card.daysToDeadline ?? Int.max
            if ld != rd { return ld < rd }
            return (lhs.card.updatedAt ?? "") > (rhs.card.updatedAt ?? "")
        }
    }
}
