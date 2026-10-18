import Foundation

/// Keeps the tracker's data bounded. Runs at launch and once a day.
///   - finished items older than `tracker.history_retention_days` leave items.json
///   - archived media older than `tracker.media_retention_days` is deleted
///   - stale inbox failures and hub-outbox files older than `logging.retention_days` are deleted
/// A value of 0 or less keeps everything.
public enum Retention {
    public struct HistoryResult: Equatable {
        public var kept: [TrackedItem]
        public var removed: [TrackedItem]
    }

    /// Active items are always kept, whatever their age.
    public static func pruneHistory(_ items: [TrackedItem], days: Int, now: Date = Date()) -> HistoryResult {
        guard days > 0 else { return HistoryResult(kept: items, removed: []) }
        let cutoff = now.addingTimeInterval(-Double(days) * 86_400)
        var kept: [TrackedItem] = []
        var removed: [TrackedItem] = []
        for item in items {
            if item.status == .done && item.finishedAt < cutoff { removed.append(item) } else { kept.append(item) }
        }
        return HistoryResult(kept: kept, removed: removed)
    }

    /// Deletes files under `mediaDir` last modified before the cutoff, then empty month folders.
    /// Returns the deleted paths relative to `relativeTo` (the tracker data folder), e.g. "media/2026-01/x-y.jpg".
    @discardableResult
    public static func pruneMedia(mediaDir: URL, relativeTo root: URL, days: Int, now: Date = Date()) -> [String] {
        guard days > 0 else { return [] }
        let removed = pruneFiles(in: mediaDir, olderThan: now.addingTimeInterval(-Double(days) * 86_400), recursive: true)
        let base = plainPath(root)
        return removed.map { url in
            let p = plainPath(url)
            return p.hasPrefix(base + "/") ? String(p.dropFirst(base.count + 1)) : p
        }
    }

    /// Standardized path without macOS's /private prefix (/var and /tmp are links into it).
    static func plainPath(_ url: URL) -> String {
        let p = url.standardizedFileURL.path
        return p.hasPrefix("/private/") ? String(p.dropFirst("/private".count)) : p
    }

    /// Deletes plain files in `dir` (optionally one level of sub-folders, removing them when empty)
    /// whose modification date is before `cutoff`.
    @discardableResult
    public static func pruneFiles(in dir: URL, olderThan cutoff: Date, recursive: Bool = false) -> [URL] {
        let fm = FileManager.default
        guard let entries = try? fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: [.contentModificationDateKey, .isDirectoryKey]) else {
            return []
        }
        var removed: [URL] = []
        for url in entries {
            let values = try? url.resourceValues(forKeys: [.contentModificationDateKey, .isDirectoryKey])
            if values?.isDirectory == true {
                guard recursive else { continue }
                removed += pruneFiles(in: url, olderThan: cutoff, recursive: false)
                if (try? fm.contentsOfDirectory(atPath: url.path))?.isEmpty == true { try? fm.removeItem(at: url) }
            } else if let modified = values?.contentModificationDate, modified < cutoff {
                if (try? fm.removeItem(at: url)) != nil { removed.append(url) }
            }
        }
        return removed
    }

    /// Clears `archivePath` on refs whose archive file was deleted, so the UI shows them as gone.
    public static func forgetArchives(_ deleted: Set<String>, in items: inout [TrackedItem]) -> Int {
        guard !deleted.isEmpty else { return 0 }
        var changed = 0
        func scrub(_ refs: inout [MediaRef]) {
            for i in refs.indices where refs[i].archivePath.map(deleted.contains) == true {
                refs[i].archivePath = nil
                changed += 1
            }
        }
        for i in items.indices {
            scrub(&items[i].media)
            for s in items[i].subagents.indices { scrub(&items[i].subagents[s].media) }
        }
        return changed
    }
}
