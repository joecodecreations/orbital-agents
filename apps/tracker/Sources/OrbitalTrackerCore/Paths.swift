import Foundation

/// Where the tracker keeps things. Everything comes from config.resolved.json:
///
///     <paths.tracker_data>/items.json      every item (the app is the only writer)
///                          settings.json   screenshot evidence level, shared with the phone app and the CLI
///                          inbox/          commands queued while the app wasn't reachable
///                          hub-outbox/     satellites: commands waiting for the hub
///                          media/YYYY-MM/  small archived copies of screenshots and video frame sheets
///                          icons/          optional provider logos (<agent>.png)
///     <paths.logs>/tracker.log             JSON lines, rolled by size
///     <paths.run>/tracker.json             {port, pid, startedAt, version} while the app runs
public struct TrackerPaths: Sendable, Equatable {
    public let root: URL
    public let logsDir: URL
    public let runDir: URL

    public init(root: URL, logsDir: URL, runDir: URL) {
        self.root = root
        self.logsDir = logsDir
        self.runDir = runDir
    }

    public init(config: OrbitalConfig) {
        self.init(root: config.trackerData, logsDir: config.logsDir, runDir: config.runDir)
    }

    public static func resolve() -> TrackerPaths { TrackerPaths(config: .load()) }

    /// Fallback inbox for sandboxed agents that can't write into the data folder (one per user).
    public static var tmpInbox: URL {
        URL(fileURLWithPath: "/tmp/orbital-tracker-inbox-\(getuid())", isDirectory: true)
    }

    public var itemsFile: URL { root.appendingPathComponent("items.json") }
    /// Shared with the phone app and the CLI: TrackerSettings.
    public var settingsFile: URL { root.appendingPathComponent("settings.json") }
    public var inboxDir: URL { root.appendingPathComponent("inbox", isDirectory: true) }
    public var failedInboxDir: URL { inboxDir.appendingPathComponent("failed", isDirectory: true) }
    public var hubOutboxDir: URL { root.appendingPathComponent("hub-outbox", isDirectory: true) }
    public var mediaDir: URL { root.appendingPathComponent("media", isDirectory: true) }
    public var iconsDir: URL { root.appendingPathComponent("icons", isDirectory: true) }
    public var scratchDir: URL { root.appendingPathComponent(".grok-scratch", isDirectory: true) }

    public var logFile: URL { logsDir.appendingPathComponent("tracker.log") }
    /// Raw stdout/stderr of the optional speech sidecar.
    public var voiceLogFile: URL { logsDir.appendingPathComponent("tracker-voice.stderr.log") }
    public var runFile: URL { runDir.appendingPathComponent("tracker.json") }
    public var lockFile: URL { runDir.appendingPathComponent("tracker.lock") }

    public func ensureDirectories() throws {
        for dir in [root, inboxDir, mediaDir, iconsDir, scratchDir, logsDir, runDir] {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        }
    }

    public func archiveURL(_ relative: String) -> URL { root.appendingPathComponent(relative) }
}
