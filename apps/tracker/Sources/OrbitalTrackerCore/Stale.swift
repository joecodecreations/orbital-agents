import Foundation

/// Agent items that stopped reporting. An agent that crashes, is closed, or never calls `done`
/// leaves its bubble on the panel forever; after `tracker.stale_hours` with no update from the
/// item or any of its subagents, the tracker closes it into history. Items the user typed or
/// spoke into the tracker are their own to-dos and are never closed this way.
/// If the agent reports again later, the item reopens.
public enum Stale {
    /// `completedBy` on an item the tracker closed for going quiet.
    public static let closedBy = "stale"
    /// How often the tracker looks for stale items.
    public static let checkInterval: TimeInterval = 600

    /// The last time anything happened on the item: its own update or any subagent's.
    public static func lastActivity(_ item: TrackedItem) -> Date {
        item.subagents.map(\.updatedAt).reduce(item.updatedAt, max)
    }

    /// Ids of agent items on the panel with no activity for `hours`. 0 or less → none.
    public static func find(_ items: [TrackedItem], hours: Int, now: Date = Date()) -> [String] {
        guard hours > 0 else { return [] }
        let cutoff = now.addingTimeInterval(-Double(hours) * 3600)
        return items
            .filter { $0.status == .active && !$0.dismissed && $0.source == .agent && lastActivity($0) < cutoff }
            .map(\.id)
    }

    /// The history summary for an item closed as stale. Keeps the agent's last note, if any.
    public static func summary(for item: TrackedItem, hours: Int) -> String {
        let when = ISO8601DateFormatter.string(from: lastActivity(item), timeZone: .current,
                                               formatOptions: [.withFullDate, .withSpaceBetweenDateAndTime,
                                                               .withTime, .withColonSeparatorInTime])
        var text = "**Closed as stale**: no update for \(hours) hours (last one \(when.prefix(16))), "
            + "and the agent never marked it done. It reopens if the agent reports again."
        if let last = item.notes.last?.text, !last.isEmpty { text += "\n\nLast note: \(last)" }
        return text
    }
}
