import Foundation
import Observation
import OrbitalTrackerCore
import SwiftUI

enum ItemFilter: String, CaseIterable, Identifiable {
    case all, ai, mine
    var id: String { rawValue }
    var label: String {
        switch self {
        case .all: return "All"
        case .ai: return "AI"
        case .mine: return "Mine"
        }
    }

    func matches(_ item: TrackedItem) -> Bool {
        switch self {
        case .all: return true
        case .ai: return item.source == .agent
        case .mine: return item.source != .agent
        }
    }
}

enum HistoryRange: String, CaseIterable, Identifiable {
    case today, week, month, all
    var id: String { rawValue }
    var label: String {
        switch self {
        case .today: return "Today"
        case .week: return "This Week"
        case .month: return "This Month"
        case .all: return "All"
        }
    }

    func contains(_ date: Date, now: Date = Date()) -> Bool {
        let cal = Calendar.current
        switch self {
        case .today: return cal.isDate(date, inSameDayAs: now)
        case .week: return cal.dateInterval(of: .weekOfYear, for: now)?.contains(date) ?? false
        case .month: return cal.dateInterval(of: .month, for: now)?.contains(date) ?? false
        case .all: return true
        }
    }
}

struct StoreError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

/// The single writer of items.json. Everything (voice, typing, HTTP, inbox, the hub relay)
/// funnels through here on the main actor.
@MainActor @Observable
final class TrackerStore {
    private(set) var items: [TrackedItem] = []
    /// Ticks so "running 12m" style durations stay current.
    var now = Date()

    let paths: TrackerPaths
    let config: OrbitalConfig
    @ObservationIgnored let log: JSONLineLogger
    @ObservationIgnored private var saveWork: DispatchWorkItem?
    @ObservationIgnored private var nextColor = 0
    @ObservationIgnored private let archiveQueue = DispatchQueue(label: "orbital.tracker.archive", qos: .utility)

    /// Per item / subtask, so one chatty agent can't grow items.json without bound.
    static let maxNotes = 300
    static let maxMedia = 200
    static let maxSubagents = 200

    var machineName: String { config.machineName }

    init(paths: TrackerPaths, config: OrbitalConfig, log: JSONLineLogger) {
        self.paths = paths
        self.config = config
        self.log = log
        load()
    }

    // MARK: Persistence

    private func load() {
        guard let data = try? Data(contentsOf: paths.itemsFile) else { return }
        do {
            items = try TrackerJSON.decoder().decode([TrackedItem].self, from: data)
            // A crash mid-titling leaves `processing` set; nothing is still processing at launch.
            for i in items.indices where items[i].processing { items[i].processing = false }
            // Finished items never come back onto the panel: only in-progress work returns flashing.
            var autoClosed = 0
            for i in items.indices where items[i].status == .done && !items[i].dismissed {
                items[i].dismissed = true
                autoClosed += 1
            }
            nextColor = (items.map(\.colorIndex).max() ?? -1) + 1
            if autoClosed > 0 {
                event("auto_closed", ["count": autoClosed, "reason": "done items leave the live panel"])
                saveNow()
            }
        } catch {
            let backup = paths.itemsFile.deletingPathExtension().appendingPathExtension("corrupt-\(Int(Date().timeIntervalSince1970)).json")
            try? FileManager.default.copyItem(at: paths.itemsFile, to: backup)
            log.error("items.json unreadable; backed up", ["error": "\(error)", "backup": backup.path])
        }
    }

    func scheduleSave() {
        saveWork?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.saveNow() }
        saveWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.4, execute: work)
    }

    func saveNow() {
        saveWork?.cancel()
        do {
            let data = try TrackerJSON.encoder(pretty: true).encode(items)
            try data.write(to: paths.itemsFile, options: .atomic)
        } catch {
            log.error("save failed", ["error": error.localizedDescription])
        }
    }

    /// Item history goes to the tracker log (logs/tracker.log) as `{"msg": "item <event>", "event": ...}`.
    private func event(_ name: String, _ fields: [String: Any?]) {
        var f = fields
        f["event"] = name
        log.info("item \(name.replacingOccurrences(of: "_", with: " "))", f)
    }

    // MARK: Retention

    /// Drops finished items past `tracker.history_retention_days` and media archives past
    /// `tracker.media_retention_days`. Runs at launch and daily.
    func applyRetention(now: Date = Date()) {
        let history = Retention.pruneHistory(items, days: config.historyRetentionDays, now: now)
        var changed = false
        if !history.removed.isEmpty {
            items = history.kept
            changed = true
        }
        let deleted = Retention.pruneMedia(mediaDir: paths.mediaDir, relativeTo: paths.root,
                                           days: config.mediaRetentionDays, now: now)
        if Retention.forgetArchives(Set(deleted), in: &items) > 0 { changed = true }
        let stale = now.addingTimeInterval(-Double(max(config.logRetentionDays, 1)) * 86_400)
        let failed = Retention.pruneFiles(in: paths.failedInboxDir, olderThan: stale)
        if changed { saveNow() }
        if !history.removed.isEmpty || !deleted.isEmpty || !failed.isEmpty {
            log.info("retention", ["items_removed": history.removed.count, "media_removed": deleted.count,
                                   "failed_inbox_removed": failed.count,
                                   "history_days": config.historyRetentionDays, "media_days": config.mediaRetentionDays])
        }
    }

    // MARK: Stale items

    /// Closes agent items with no update for `tracker.stale_hours` into history (see `Stale`).
    /// Runs every 10 minutes. Returns the closed ids.
    @discardableResult
    func closeStale(now: Date = Date()) -> [String] {
        let hours = config.staleHours
        let ids = Stale.find(items, hours: hours, now: now)
        guard !ids.isEmpty else { return [] }
        withAnimation(.easeOut(duration: 0.3)) {
            for id in ids {
                guard let i = items.firstIndex(where: { $0.id == id }) else { continue }
                let quietSince = Stale.lastActivity(items[i])
                let summary = Stale.summary(for: items[i], hours: hours)
                complete(index: i, by: Stale.closedBy, summary: summary)
                event("closed_stale", ["id": id, "title": items[i].title, "agent": items[i].agent,
                                       "machine": items[i].machine, "hours": hours,
                                       "quiet_since": ISO8601DateFormatter().string(from: quietSince)])
            }
        }
        saveNow()
        return ids
    }

    /// An agent reported on an item the tracker closed as stale: it is live again.
    private func reopenIfStale(_ i: Int) {
        guard items[i].status == .done, items[i].completedBy == Stale.closedBy else { return }
        withAnimation(.spring(response: 0.35, dampingFraction: 0.8)) {
            items[i].status = .active
            items[i].dismissed = false
            items[i].completedAt = nil
            items[i].completedBy = nil
            items[i].summary = nil
        }
        event("reopened", ["id": items[i].id, "title": items[i].title, "reason": "agent reported after a stale close"])
    }

    // MARK: Queries

    func item(_ id: String) -> TrackedItem? { items.first { $0.id == id } }

    /// Live panel is only work still in progress. Done items stay in history.
    func isOnScreen(_ item: TrackedItem) -> Bool { item.status == .active && !item.dismissed }

    /// Oldest first, so the newest bubble is always last (bottom).
    func activeItems(_ filter: ItemFilter) -> [TrackedItem] {
        items.filter { isOnScreen($0) && filter.matches($0) }.sorted { $0.createdAt < $1.createdAt }
    }

    func history(_ filter: ItemFilter, range: HistoryRange) -> [TrackedItem] {
        items
            .filter { $0.status == .done && filter.matches($0) && range.contains($0.finishedAt, now: now) }
            .sorted { $0.finishedAt > $1.finishedAt }
    }

    var activeCount: Int { items.filter { $0.status == .active && !$0.dismissed }.count }

    // MARK: Your own items (voice and typed)

    private func takeColor() -> Int {
        defer { nextColor += 1 }
        return nextColor
    }

    @discardableResult
    func addPersonal(title: String, details: String, transcript: String?, source: ItemSource, processing: Bool) -> String {
        // Typed or spoken into the Mac tracker panel → Mac/OS app origin.
        var item = TrackedItem(title: title, details: details, transcript: transcript, source: source,
                               origin: .mac, machine: machineName, colorIndex: takeColor())
        item.processing = processing
        withAnimation(.spring(response: 0.35, dampingFraction: 0.8)) { items.append(item) }
        event("added", ["id": item.id, "title": title, "source": source.rawValue, "origin": ItemOrigin.mac.rawValue])
        scheduleSave()
        return item.id
    }

    func finishProcessing(_ id: String, title: String?, details: String?) {
        guard let i = items.firstIndex(where: { $0.id == id }) else { return }
        if let title, !title.isEmpty { items[i].title = title }
        if let details, !details.isEmpty { items[i].details = details }
        items[i].processing = false
        items[i].updatedAt = Date()
        event("titled", ["id": id, "title": items[i].title])
        scheduleSave()
    }

    /// Drops a provisional voice bubble that turned out not to be a task.
    func discard(_ id: String, reason: String) {
        guard let i = items.firstIndex(where: { $0.id == id }) else { return }
        let removed = items[i]
        withAnimation(.easeOut(duration: 0.2)) { _ = items.remove(at: i) }
        event("discarded", ["id": id, "title": removed.title, "reason": reason])
        scheduleSave()
    }

    /// The user clicked X: active items become done and leave the panel. History keeps them.
    func userClose(_ id: String) {
        guard let i = items.firstIndex(where: { $0.id == id }) else { return }
        withAnimation(.easeOut(duration: 0.25)) {
            if items[i].status == .active {
                complete(index: i, by: "user", summary: nil)
            }
            items[i].dismissed = true
        }
        event("dismissed", ["id": id, "title": items[i].title])
        scheduleSave()
    }

    /// Marks done and takes it off the live panel. History keeps it.
    func markDone(_ id: String, summary: String? = nil) {
        guard let i = items.firstIndex(where: { $0.id == id }), items[i].status == .active else { return }
        withAnimation(.easeOut(duration: 0.25)) { complete(index: i, by: "user", summary: summary) }
        scheduleSave()
    }

    func markDoneByVoice(_ id: String, phrase: String) {
        markDone(id, summary: "Closed by voice: “\(phrase)”")
    }

    private func complete(index i: Int, by: String, summary: String?) {
        let now = Date()
        items[i].status = .done
        items[i].completedAt = now
        items[i].completedBy = by
        items[i].updatedAt = now
        items[i].dismissed = true
        if let summary, !summary.isEmpty { items[i].summary = summary }
        for s in items[i].subagents.indices where items[i].subagents[s].status == .active {
            items[i].subagents[s].status = .done
            items[i].subagents[s].completedAt = now
        }
        event("completed", ["id": items[i].id, "title": items[i].title, "by": by, "agent": items[i].agent,
                            "machine": items[i].machine,
                            "minutes": Int(now.timeIntervalSince(items[i].createdAt) / 60)])
    }

    // MARK: Agent commands

    /// Applies a command from HTTP, an inbox file or the hub relay. Returns the affected id.
    @discardableResult
    func apply(_ cmd: TrackerCommand) throws -> String {
        do {
            try cmd.validate()
        } catch let error as CommandError {
            throw StoreError(message: error.message)
        }
        let op = TrackerCommand.canonicalOp(cmd.op) ?? cmd.op
        let agent = Agents.normalize(cmd.agent)
        let machine = clean(cmd.machine).map { String($0.prefix(64)) } ?? machineName
        switch op {
        case "start":
            if let parent = cmd.parent, !parent.isEmpty {
                var sub = cmd
                sub.op = "sub"
                return try apply(sub)
            }
            let id = cmd.id ?? IDGen.make()
            if item(id) != nil { return try update(id: id, cmd: cmd) }
            guard let title = clean(cmd.title) else { throw StoreError(message: "title is required") }
            let origin = ItemOrigin.parse(cmd.origin)
            let model = clean(cmd.model).map { String($0.prefix(120)) }
            var item = TrackedItem(id: id, title: String(title.prefix(200)), details: cmd.details ?? "", source: .agent,
                                   agent: agent, agentLabel: cmd.agentLabel, origin: origin, model: model,
                                   machine: machine, workspace: cmd.workspace, colorIndex: takeColor(),
                                   createdAt: min(cmd.sentAt ?? Date(), Date()))
            item.media = acceptMedia(cmd.media, itemID: id, subID: nil, machine: machine)
            item.progress = cmd.progress.map { min(100, max(0, $0)) }
            if let usage = cmd.usage {
                item.usage = usage
                if item.model == nil, let m = usage.model, !m.isEmpty { item.model = m }
            }
            withAnimation(.spring(response: 0.35, dampingFraction: 0.8)) { items.append(item) }
            event("added", ["id": id, "title": title, "source": "agent", "agent": agent, "machine": machine,
                            "workspace": cmd.workspace, "origin": origin?.rawValue, "model": model])
            archiveInlineMarkdown(itemID: id, subID: nil, texts: [cmd.details], machine: machine)
            scheduleSave()
            return id

        case "sub":
            guard let parent = cmd.parent, let p = items.firstIndex(where: { $0.id == parent }) else {
                throw StoreError(message: "unknown parent id '\(cmd.parent ?? "")' — subagents need the main agent's item id")
            }
            let id = cmd.id ?? IDGen.make()
            if items[p].subagents.contains(where: { $0.id == id }) { return try update(id: id, cmd: cmd) }
            reopenIfStale(p)
            guard let title = clean(cmd.title) else { throw StoreError(message: "title is required") }
            // A subagent is work an agent sent in.
            var sub = SubTask(id: id, agent: agent, agentLabel: cmd.agentLabel, machine: machine,
                              title: String(title.prefix(200)), details: cmd.details ?? "",
                              model: clean(cmd.model).map { String($0.prefix(120)) })
            sub.media = acceptMedia(cmd.media, itemID: parent, subID: id, machine: machine)
            sub.progress = cmd.progress.map { min(100, max(0, $0)) }
            if let usage = cmd.usage {
                sub.usage = usage
                if sub.model == nil, let m = usage.model, !m.isEmpty { sub.model = m }
            }
            withAnimation {
                items[p].subagents.append(sub)
                if items[p].subagents.count > Self.maxSubagents {
                    items[p].subagents.removeFirst(items[p].subagents.count - Self.maxSubagents)
                }
            }
            items[p].updatedAt = Date()
            event("sub_added", ["id": id, "parent": parent, "title": title, "agent": agent, "machine": machine])
            archiveInlineMarkdown(itemID: parent, subID: id, texts: [cmd.details], machine: machine)
            scheduleSave()
            return id

        case "update":
            guard let id = cmd.id else { throw StoreError(message: "id is required") }
            return try update(id: id, cmd: cmd)

        case "done":
            guard let id = cmd.id else { throw StoreError(message: "id is required") }
            _ = try update(id: id, cmd: cmd, quiet: true)
            if let i = items.firstIndex(where: { $0.id == id }) {
                items[i].progress = 100
                if items[i].status == .active {
                    let by = cmd.agent.map(Agents.normalize) ?? items[i].agent ?? "agent"
                    withAnimation(.easeOut(duration: 0.3)) { complete(index: i, by: by, summary: cmd.summary) }
                } else if let summary = cmd.summary {
                    items[i].summary = summary
                }
            } else if let (p, s) = subIndex(id) {
                if items[p].subagents[s].status == .active {
                    withAnimation {
                        items[p].subagents[s].status = .done
                        items[p].subagents[s].completedAt = Date()
                    }
                }
                items[p].subagents[s].progress = 100
                if let summary = cmd.summary { items[p].subagents[s].summary = summary }
                event("sub_completed", ["id": id, "parent": items[p].id, "title": items[p].subagents[s].title])
            }
            scheduleSave()
            return id

        default:
            throw StoreError(message: "unknown op '\(cmd.op)' (use start, sub, update, done)")
        }
    }

    private func subIndex(_ id: String) -> (Int, Int)? {
        for (p, item) in items.enumerated() {
            if let s = item.subagents.firstIndex(where: { $0.id == id }) { return (p, s) }
        }
        return nil
    }

    private func update(id: String, cmd: TrackerCommand, quiet: Bool = false) throws -> String {
        let machine = clean(cmd.machine) ?? machineName
        if let i = items.firstIndex(where: { $0.id == id }) {
            reopenIfStale(i)
            if let title = clean(cmd.title) { items[i].title = String(title.prefix(200)) }
            if let details = cmd.details {
                items[i].details = cmd.appendDetails == true && !items[i].details.isEmpty
                    ? items[i].details + "\n\n" + details : details
            }
            if let note = clean(cmd.note) { Self.appendNote(&items[i].notes, note) }
            if let progress = cmd.progress { items[i].progress = min(100, max(0, progress)) }
            if let origin = ItemOrigin.parse(cmd.origin), items[i].origin == nil { items[i].origin = origin }
            if let model = clean(cmd.model) { items[i].model = String(model.prefix(120)) }
            if let usage = cmd.usage {
                items[i].usage = Self.stamped(usage)
                if items[i].model == nil, let m = usage.model, !m.isEmpty { items[i].model = m }
            }
            Self.appendMedia(&items[i].media, acceptMedia(cmd.media, itemID: id, subID: nil, machine: machine))
            items[i].updatedAt = Date()
            archiveInlineMarkdown(itemID: id, subID: nil, texts: [cmd.details, cmd.note, cmd.summary], machine: machine)
            if !quiet { event("updated", ["id": id, "note": cmd.note.map { String($0.prefix(300)) }]) }
            scheduleSave()
            return id
        }
        if let (p, s) = subIndex(id) {
            reopenIfStale(p)
            if let title = clean(cmd.title) { items[p].subagents[s].title = String(title.prefix(200)) }
            if let details = cmd.details {
                let old = items[p].subagents[s].details
                items[p].subagents[s].details = cmd.appendDetails == true && !old.isEmpty ? old + "\n\n" + details : details
            }
            if let note = clean(cmd.note) { Self.appendNote(&items[p].subagents[s].notes, note) }
            if let progress = cmd.progress { items[p].subagents[s].progress = min(100, max(0, progress)) }
            if let model = clean(cmd.model) { items[p].subagents[s].model = String(model.prefix(120)) }
            if let usage = cmd.usage {
                items[p].subagents[s].usage = Self.stamped(usage)
                if items[p].subagents[s].model == nil, let m = usage.model, !m.isEmpty {
                    items[p].subagents[s].model = m
                }
            }
            Self.appendMedia(&items[p].subagents[s].media,
                             acceptMedia(cmd.media, itemID: items[p].id, subID: id, machine: machine))
            items[p].subagents[s].updatedAt = Date()
            archiveInlineMarkdown(itemID: items[p].id, subID: id, texts: [cmd.details, cmd.note, cmd.summary], machine: machine)
            scheduleSave()
            return id
        }
        throw StoreError(message: "unknown id '\(id)'")
    }

    private static func stamped(_ usage: TokenUsage) -> TokenUsage {
        var out = usage
        out.updatedAt = out.updatedAt ?? Date()
        return out
    }

    private static func appendNote(_ notes: inout [ProgressNote], _ text: String) {
        notes.append(ProgressNote(text: text))
        if notes.count > maxNotes { notes.removeFirst(notes.count - maxNotes) }
    }

    private static func appendMedia(_ media: inout [MediaRef], _ new: [MediaRef]) {
        media += new
        if media.count > maxMedia { media.removeFirst(media.count - maxMedia) }
    }

    private func clean(_ s: String?) -> String? {
        guard let t = s?.trimmingCharacters(in: .whitespacesAndNewlines), !t.isEmpty else { return nil }
        return t
    }

    // MARK: Media

    /// Keeps refs the CLI already archived (storing inline copies relayed from other machines);
    /// archives the rest in the background.
    private func acceptMedia(_ inputs: [MediaInput]?, itemID: String, subID: String?, machine: String) -> [MediaRef] {
        var ready: [MediaRef] = []
        let remote = machine != machineName
        for input in (inputs ?? []).prefix(Self.maxMedia) {
            if var ref = input.archived {
                if ref.caption == nil { ref.caption = input.caption }
                if !IDGen.isValid(ref.id) { ref.id = IDGen.make() }
                if remote {
                    ref.machine = machine
                    // The satellite's archive path means nothing here: keep only what came inline.
                    ref = input.inlineArchive.map { MediaArchiver.storeInline($0, ref: ref, itemID: itemID, paths: paths) }
                        ?? { var r = ref; r.archivePath = nil; return r }()
                } else if let rel = ref.archivePath, !Self.isSafeArchivePath(rel) {
                    ref.archivePath = nil
                }
                ready.append(ref)
            } else if remote {
                // A file on another machine that wasn't archived there: keep the record only.
                ready.append(MediaRef(kind: MediaArchiver.kind(for: URL(fileURLWithPath: input.path)),
                                      originalPath: input.path, caption: input.caption, machine: machine))
            } else {
                archiveLater(path: MediaArchiver.normalizePath(input.path, relativeTo: "/"), caption: input.caption,
                             itemID: itemID, subID: subID)
            }
        }
        return ready
    }

    /// Archive refs from the CLI must stay inside media/.
    static func isSafeArchivePath(_ rel: String) -> Bool {
        rel.hasPrefix("media/") && !rel.contains("..") && !rel.hasPrefix("/")
    }

    private func archiveInlineMarkdown(itemID: String, subID: String?, texts: [String?], machine: String) {
        guard machine == machineName else { return }   // another machine's paths aren't on this disk
        let known = Set(allMedia(itemID: itemID, subID: subID).map(\.originalPath))
        for path in texts.flatMap({ MarkdownMedia.localPaths(in: $0) }) where !known.contains(path) {
            archiveLater(path: path, caption: nil, itemID: itemID, subID: subID)
        }
    }

    private func allMedia(itemID: String, subID: String?) -> [MediaRef] {
        guard let item = item(itemID) else { return [] }
        if let subID { return item.subagents.first { $0.id == subID }?.media ?? [] }
        return item.media
    }

    private func archiveLater(path: String, caption: String?, itemID: String, subID: String?) {
        let paths = self.paths
        archiveQueue.async { [weak self] in
            guard FileManager.default.fileExists(atPath: path) else { return }
            let ref = MediaArchiver.archive(path: path, caption: caption, itemID: itemID, paths: paths)
            DispatchQueue.main.async {
                guard let self, let p = self.items.firstIndex(where: { $0.id == itemID }) else { return }
                if let subID, let s = self.items[p].subagents.firstIndex(where: { $0.id == subID }) {
                    guard !self.items[p].subagents[s].media.contains(where: { $0.originalPath == path }) else { return }
                    Self.appendMedia(&self.items[p].subagents[s].media, [ref])
                } else {
                    guard !self.items[p].media.contains(where: { $0.originalPath == path }) else { return }
                    Self.appendMedia(&self.items[p].media, [ref])
                }
                self.scheduleSave()
            }
        }
    }

    /// Finds the archived copy for a path referenced in markdown.
    func mediaRef(forPath path: String) -> MediaRef? {
        for item in items {
            if let m = item.media.first(where: { $0.originalPath == path }) { return m }
            for sub in item.subagents {
                if let m = sub.media.first(where: { $0.originalPath == path }) { return m }
            }
        }
        return nil
    }

    /// A media ref by item (or subtask) id and media id, for `GET /v1/media/<item>/<media>`.
    func mediaRef(item itemID: String, media mediaID: String) -> MediaRef? {
        for item in items {
            if item.id == itemID, let m = item.media.first(where: { $0.id == mediaID }) { return m }
            for sub in item.subagents where sub.id == itemID || item.id == itemID {
                if let m = sub.media.first(where: { $0.id == mediaID }) { return m }
            }
        }
        return nil
    }
}
