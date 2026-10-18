import AppKit
import Carbon
import OrbitalTrackerCore
import SwiftUI

/// Borderless, transparent, always-on-top panel that never steals focus
/// (except when you click + to type).
final class FloatingPanel: NSPanel {
    init(rect: NSRect) {
        super.init(contentRect: rect, styleMask: [.borderless, .nonactivatingPanel, .fullSizeContentView],
                   backing: .buffered, defer: false)
        isFloatingPanel = true
        level = .floating
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .ignoresCycle]
        backgroundColor = .clear
        isOpaque = false
        hasShadow = false
        hidesOnDeactivate = false
        isMovable = true
        isMovableByWindowBackground = false
        becomesKeyOnlyIfNeeded = true
        appearance = NSAppearance(named: .darkAqua)
        animationBehavior = .utilityWindow
    }

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}

/// One tracker per user: a second copy (another login item, a double click, launchd) quits
/// with exit code 0 so launchd doesn't treat it as a crash.
enum SingleInstance {
    nonisolated(unsafe) private static var lockFD: Int32 = -1

    /// Another running app with our bundle id, or the run/tracker.lock flock held by another process.
    static func acquire(lockFile: URL) -> Bool {
        if let bundleID = Bundle.main.bundleIdentifier,
           let other = NSRunningApplication.runningApplications(withBundleIdentifier: bundleID)
            .first(where: { $0 != NSRunningApplication.current }) {
            other.activate()
            return false
        }
        try? FileManager.default.createDirectory(at: lockFile.deletingLastPathComponent(), withIntermediateDirectories: true)
        let fd = open(lockFile.path, O_CREAT | O_RDWR, 0o600)
        guard fd >= 0 else { return true }   // can't lock: don't refuse to run
        if flock(fd, LOCK_EX | LOCK_NB) != 0 {
            close(fd)
            return false
        }
        lockFD = fd   // held until the process exits
        return true
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSWindowDelegate {
    private var config: OrbitalConfig!
    private var paths: TrackerPaths!
    private var log: JSONLineLogger!
    private var panel: FloatingPanel!
    private var model: AppModel!
    private var server: CommandServer?
    private var inbox: InboxWatcher?
    private var statusItem: NSStatusItem?
    private var hotKeys: [HotKey] = []
    private var detailWindows: DetailWindows!
    private var retentionTimer: Timer?
    private var signalSources: [DispatchSourceSignal] = []
    private var started = false

    func applicationDidFinishLaunching(_ notification: Notification) {
        config = OrbitalConfig.load()
        paths = TrackerPaths(config: config)
        config.ensureHome()
        try? paths.ensureDirectories()
        log = JSONLineLogger(config: config, component: "tracker")

        guard SingleInstance.acquire(lockFile: paths.lockFile) else {
            log.info("another tracker is already running; exiting")
            exit(0)
        }
        started = true
        installSignalHandlers()

        AgentIcons.iconsDir = paths.iconsDir
        model = AppModel(config: config, paths: paths, log: log)
        log.info("tracker started", ["version": TrackerInfo.version, "pid": Int(getpid()), "port": Int(config.trackerPort),
                                     "machine": config.machineName, "role": config.role, "voice": config.voice,
                                     "titles": config.titles.rawValue, "config_file": config.loadedFromFile,
                                     "relay_to_hub": config.relaysToHub, "hotkeys": config.hotkeys,
                                     "hotkey_modifiers": config.hotkeyModifiers.configString])

        model.store.applyRetention()
        retentionTimer = Timer.scheduledTimer(withTimeInterval: 86_400, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.model.store.applyRetention() }
        }

        buildPanel()
        startServer()
        inbox = InboxWatcher(dirs: [paths.inboxDir, TrackerPaths.tmpInbox], failedDir: paths.failedInboxDir,
                             onCommand: { [weak self] command in self?.applyFromInbox(command) },
                             onBadFile: { [weak self] name, error in
                                 self?.log.warning("bad inbox file moved to inbox/failed", ["file": name, "error": "\(error)"])
                             })
        inbox?.start()
        buildStatusItem()

        registerHotKeys()
    }

    /// `tracker.hotkeys` / `tracker.hotkey_modifiers`; ORBITAL_TRACKER_NO_HOTKEYS=1 turns them off too
    /// (tests, or a second tracker build next to the installed one).
    private func registerHotKeys() {
        if !config.hotkeyModifiersValid {
            log.warning("tracker.hotkey_modifiers not understood; using ctrl+option",
                        ["value": config.hotkeyModifiersRaw, "accepted": "'+'-joined ctrl, option/alt, shift, cmd/command"])
        }
        guard model.hotkeysEnabled else {
            log.info("global shortcuts off", ["config": config.hotkeys,
                                              "env_override": ProcessInfo.processInfo.environment["ORBITAL_TRACKER_NO_HOTKEYS"] != nil])
            return
        }
        let flags = Int(config.hotkeyModifiers.carbonFlags)
        var keys: [(String, HotKey)] = [
            ("T", HotKey(keyCode: kVK_ANSI_T, modifiers: flags, id: 2) { [weak self] in self?.togglePanel() }),
            ("N", HotKey(keyCode: kVK_ANSI_N, modifiers: flags, id: 3) { [weak self] in
                self?.showPanel()
                self?.model.startTyping()
            }),
        ]
        if model.voiceEnabled {
            keys.append(("M", HotKey(keyCode: kVK_ANSI_M, modifiers: flags, id: 1) { [weak self] in self?.model.toggleMic() }))
        }
        hotKeys = keys.map(\.1)
        let taken = keys.filter { !$0.1.registered }.map { config.hotkeyModifiers.shortcut($0.0) }
        if !taken.isEmpty {
            log.warning("shortcuts already taken by another app; set tracker.hotkey_modifiers to another combination",
                        ["shortcuts": taken])
        }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationWillTerminate(_ notification: Notification) {
        guard started else { return }
        model?.store.saveNow()
        model?.stt.stop()
        server?.stop()
        inbox?.stop()
        try? FileManager.default.removeItem(at: paths.runFile)
        log.info("tracker stopped")
    }

    /// launchd stops jobs with SIGTERM: save and quit cleanly (exit code 0).
    private func installSignalHandlers() {
        for sig in [SIGTERM, SIGINT, SIGHUP] {
            signal(sig, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            source.setEventHandler {
                MainActor.assumeIsolated { NSApp.terminate(nil) }
            }
            source.resume()
            signalSources.append(source)
        }
    }

    // MARK: Panel

    private func buildPanel() {
        let size = NSSize(width: Metrics.barWidth + Metrics.margin * 2, height: 80)
        panel = FloatingPanel(rect: NSRect(origin: .zero, size: size))
        panel.title = TrackerInfo.productName
        panel.delegate = self
        // Hook the size callback up before SwiftUI's first layout pass reports a size.
        model.onContentSize = { [weak self] size in self?.resize(to: size) }
        model.onWantsKeyFocus = { [weak self] in
            NSApp.activate(ignoringOtherApps: true)
            self?.panel.makeKeyAndOrderFront(nil)
        }
        detailWindows = DetailWindows(model: model) { [weak self] in self?.panel.frame ?? .zero }
        model.onOpenDetailWindow = { [weak self] id in self?.detailWindows.open(id) }
        let host = NSHostingView(rootView: RootView(model: model))
        host.sizingOptions = []
        panel.contentView = host
        panel.setFrameTopLeftPoint(initialTopLeft(size: size))
        resize(to: host.fittingSize)
        panel.orderFrontRegardless()
    }

    private func initialTopLeft(size: NSSize) -> NSPoint {
        let d = UserDefaults.standard
        if d.object(forKey: "panelX") != nil {
            let p = NSPoint(x: d.double(forKey: "panelX"), y: d.double(forKey: "panelY"))
            if NSScreen.screens.contains(where: { $0.visibleFrame.insetBy(dx: -20, dy: -20).contains(p) }) { return p }
        }
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        return NSPoint(x: screen.maxX - size.width - 12, y: screen.maxY - 8)
    }

    /// Grows/shrinks downward from a fixed top-left corner.
    private func resize(to size: CGSize) {
        guard size.width > 0, size.height > 0 else { return }
        let frame = panel.frame
        let height = ceil(size.height)
        guard abs(frame.height - height) > 0.5 || abs(frame.width - size.width) > 0.5 else { return }
        panel.setFrame(NSRect(x: frame.minX, y: frame.maxY - height, width: ceil(size.width), height: height), display: true)
    }

    func windowDidMove(_ notification: Notification) {
        UserDefaults.standard.set(panel.frame.minX, forKey: "panelX")
        UserDefaults.standard.set(panel.frame.maxY, forKey: "panelY")
    }

    private func togglePanel() {
        if panel.isVisible { panel.orderOut(nil) } else { showPanel() }
    }

    private func showPanel() { panel.orderFrontRegardless() }

    // MARK: Agent API

    private func startServer() {
        let port = config.trackerPort
        let server = CommandServer(port: port) { [weak self] request in
            await self?.handle(request) ?? .json(500, ["ok": false, "error": "the tracker is shutting down"])
        }
        let log = self.log!
        server.onFailure = { message in log.error(message, ["port": Int(port)]) }
        do {
            try server.start()
            self.server = server
            let info: [String: Any] = ["service": TrackerInfo.service, "version": TrackerInfo.version,
                                       "port": Int(port), "pid": Int(getpid()),
                                       "startedAt": TrackerJSON.string(Date())]
            if let data = try? JSONSerialization.data(withJSONObject: info, options: [.prettyPrinted, .sortedKeys]) {
                try? data.write(to: paths.runFile, options: .atomic)
            }
        } catch {
            log.error("HTTP server failed to start; agents' commands will queue in the inbox",
                      ["port": Int(port), "error": "\(error)"])
        }
    }

    private func applyFromInbox(_ command: TrackerCommand) {
        do { try model.store.apply(command) } catch {
            log.warning("inbox command failed", ["op": command.op, "id": command.id, "error": error.localizedDescription])
        }
    }

    private func handle(_ req: HTTPRequest) -> HTTPResponse {
        let store = model.store
        let parts = req.path.split(separator: "/").map(String.init)  // ["v1", "command"]
        guard parts.first == "v1" else { return .json(404, ["ok": false, "error": "try /v1/health"]) }
        let route = parts.count > 1 ? parts[1] : ""

        switch (req.method, route) {
        case ("GET", "health"):
            return .json(200, ["ok": true, "service": TrackerInfo.service, "version": TrackerInfo.version,
                               "app": TrackerInfo.productName, "machine": config.machineName, "role": config.role,
                               "port": Int(config.trackerPort), "pid": Int(getpid()), "active": store.activeCount,
                               "items": store.items.count, "evidence": model.evidence.rawValue,
                               "voice": ["enabled": model.voiceEnabled, "status": model.stt.statusText],
                               "titles": ["mode": config.titles.rawValue, "grok": model.grokAvailable],
                               "relay_to_hub": config.relaysToHub,
                               "hotkeys": ["enabled": model.hotkeysEnabled, "modifiers": config.hotkeyModifiers.configString,
                                           "symbols": config.hotkeyModifiers.symbols]])

        case ("GET", "settings"):
            model.reloadSettingsIfChanged()
            return .json(200, ["ok": true, "settings": model.shared.json])

        case ("POST", "settings"):
            // {"evidence": "off|min1|all", "by": "tracker|app|cli"}. The hub sends the phone's toggle here.
            let obj = (try? JSONSerialization.jsonObject(with: req.body) as? [String: Any]) ?? [:]
            guard let raw = obj["evidence"] as? String, let level = EvidenceLevel(loose: raw) else {
                return .json(400, ["ok": false, "error": "evidence is off, min1 or all"])
            }
            model.setEvidence(level, by: TrackerSettings.normalizeWriter((obj["by"] as? String) ?? "cli"))
            return .json(200, ["ok": true, "settings": model.shared.json])

        case ("GET", "items"):
            if parts.count > 2 {
                guard let item = store.item(parts[2]),
                      let data = try? TrackerJSON.encoder().encode(item) else {
                    return .json(404, ["ok": false, "error": "unknown id"])
                }
                return HTTPResponse(status: 200, body: data)
            }
            let items = req.query["all"] != nil ? store.items : store.items.filter { $0.status == .active && !$0.dismissed }
            let data = (try? TrackerJSON.encoder().encode(items)) ?? Data("[]".utf8)
            return HTTPResponse(status: 200, body: data)

        case ("GET", "media") where parts.count > 3:
            // The original file while it exists on this machine, else the archived JPEG.
            guard let ref = store.mediaRef(item: parts[2], media: parts[3]),
                  let media = ResolvedMedia.resolve(ref, paths: paths, machine: config.machineName),
                  let data = try? Data(contentsOf: media.url, options: .mappedIfSafe) else {
                return .json(404, ["ok": false, "error": "no such media"])
            }
            return HTTPResponse(status: 200, body: data, contentType: ResolvedMedia.contentType(media.url))

        case ("POST", "command"), ("POST", "start"), ("POST", "sub"), ("POST", "update"), ("POST", "done"):
            do {
                let command: TrackerCommand
                if route == "command" {
                    command = try TrackerJSON.decoder().decode(TrackerCommand.self, from: req.body)
                } else {
                    command = try TrackerCommand.fromRoute(route, pathID: parts.count > 2 ? parts[2] : nil, body: req.body)
                }
                let id = try store.apply(command)
                return .json(200, ["ok": true, "id": id])
            } catch let error as StoreError {
                return .json(400, ["ok": false, "error": error.message])
            } catch let error as CommandError {
                return .json(400, ["ok": false, "error": error.message])
            } catch {
                return .json(400, ["ok": false, "error": "bad JSON: \(error.localizedDescription)"])
            }

        case ("POST", "debug") where parts.count > 2 && parts[2] == "ui":
            // Testing hook: {"view": "active|history", "select": "<id>", "expand": "<id>", "range": "week",
            //   "filter": "all|ai|mine", "pageSize": 15, "page": 1, "editPageSize": true,
            //   "detailStyle": "inline|window", "openWindow": "<id>", "closeWindows": true, "dismiss": "<id>",
            //   "editWake": true, "toast": "text", "showPanel": true}
            let obj = (try? JSONSerialization.jsonObject(with: req.body) as? [String: Any]) ?? [:]
            if let style = obj["detailStyle"] as? String, let d = DetailStyle(rawValue: style) { model.detailStyle = d }
            if let view = obj["view"] as? String, let mode = ViewMode(rawValue: view) { model.viewMode = mode }
            if let range = obj["range"] as? String, let r = HistoryRange(rawValue: range) { model.historyRange = r }
            if let filter = obj["filter"] as? String, let f = ItemFilter(rawValue: filter) { model.filter = f }
            if let id = obj["select"] as? String { model.selectedID = id.isEmpty ? nil : id }
            if let id = obj["expand"] as? String { model.expandedHistoryID = id.isEmpty ? nil : id }
            if let id = obj["dismiss"] as? String { model.close(id) }
            if let edit = obj["editWake"] as? Bool { model.isEditingWakeWord = edit }
            if let size = obj["pageSize"] as? Int { model.setHistoryPageSize(size) }
            if let page = obj["page"] as? Int { model.historyPage = max(0, page) }
            if let edit = obj["editPageSize"] as? Bool { model.isEditingPageSize = edit }
            if let id = obj["openWindow"] as? String { detailWindows.open(id) }
            if obj["closeWindows"] as? Bool == true { detailWindows.closeAll() }
            if let text = obj["toast"] as? String { model.showToast(text) }
            if obj["showPanel"] as? Bool == true { showPanel() }
            return .json(200, ["ok": true, "pageSize": model.historyPageSize, "detailStyle": model.detailStyle.rawValue,
                               "windows": detailWindows.openIDs, "windowNumbers": detailWindows.windowNumbers, "panel": ["x": panel.frame.minX, "y": panel.frame.minY,
                                                                          "width": panel.frame.width, "height": panel.frame.height,
                                                                          "windowNumber": panel.windowNumber]])

        case ("POST", "debug") where parts.count > 2 && parts[2] == "wake":
            // Testing hook: {"phrase": "...", "wake": "hey tracker, track"} → what wake-word mode keeps.
            let obj = (try? JSONSerialization.jsonObject(with: req.body) as? [String: Any]) ?? [:]
            let phrase = obj["phrase"] as? String ?? ""
            let wake = (obj["wake"] as? String ?? model.wakeWordsText).split(separator: ",").map { AppModel.words(String($0)) }
            let rest = AppModel.stripWakeWord(phrase, wakeWords: wake)
            return .json(200, ["ok": true, "matched": rest != nil, "rest": (rest as Any?) ?? NSNull()])

        case ("POST", "debug") where parts.count > 2 && parts[2] == "audio":
            guard let obj = try? JSONSerialization.jsonObject(with: req.body) as? [String: Any],
                  let path = obj["path"] as? String else { return .json(400, ["ok": false, "error": "{\"path\": ...} required"]) }
            do {
                try model.injectAudio(URL(fileURLWithPath: path))
                return .json(200, ["ok": true, "queued": path])
            } catch {
                return .json(400, ["ok": false, "error": error.localizedDescription])
            }

        case ("POST", "debug") where parts.count > 2 && parts[2] == "retention":
            // Testing hook: runs the retention pass now.
            store.applyRetention()
            return .json(200, ["ok": true, "items": store.items.count])

        default:
            return .json(404, ["ok": false, "error": "unknown route \(req.method) \(req.path)"])
        }
    }

    // MARK: Menu bar

    private func buildStatusItem() {
        let item = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        item.button?.image = NSImage(systemSymbolName: "antenna.radiowaves.left.and.right",
                                     accessibilityDescription: TrackerInfo.productName)
        let menu = NSMenu()
        menu.addItem(withTitle: TrackerInfo.productName, action: nil, keyEquivalent: "").isEnabled = false
        menu.addItem(.separator())
        menu.addItem(withTitle: "Show / Hide Tracker" + model.shortcutSuffix("T"), action: #selector(menuToggle), keyEquivalent: "").target = self
        if model.voiceEnabled {
            menu.addItem(withTitle: "Mic On / Off" + model.shortcutSuffix("M"), action: #selector(menuMic), keyEquivalent: "").target = self
        }
        menu.addItem(withTitle: "Type an Item" + model.shortcutSuffix("N"), action: #selector(menuType), keyEquivalent: "").target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: "Open Data Folder", action: #selector(menuData), keyEquivalent: "").target = self
        menu.addItem(withTitle: "Open Log", action: #selector(menuLog), keyEquivalent: "").target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: "Quit \(TrackerInfo.shortName)", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        item.menu = menu
        statusItem = item
    }

    @objc private func menuToggle() { togglePanel() }
    @objc private func menuMic() { model.toggleMic() }
    @objc private func menuType() { showPanel(); model.startTyping() }
    @objc private func menuData() { NSWorkspace.shared.open(paths.root) }
    @objc private func menuLog() {
        if FileManager.default.fileExists(atPath: paths.logFile.path) { NSWorkspace.shared.open(paths.logFile) }
        else { NSWorkspace.shared.open(paths.logsDir) }
    }
}
