import Foundation
import OrbitalTrackerCore

// orbital-track: how AI agents put their work on the Agent Satellite Tracker.
//
// Delivery: HTTP to the tracker app on this Mac (127.0.0.1:<tracker.port>) → its inbox folder →
// /tmp/orbital-tracker-inbox-<uid> (for sandboxed agents). On a satellite (system.role =
// "satellite" with hub_url set) every command also goes to the hub's tracker. Tracking must never
// block an agent's real work: delivery problems only warn on stderr, the id is still printed,
// and the exit code is 0.

let help = """
orbital-track — report AI work to Orbital Agents - Agent Satellite Tracker

MAIN agents (talking to the user directly):
  orbital-track start --agent <claude|chatgpt|grok|gemini|NAME> --title "2-5 word title" \\
                      [--details "markdown"] [--progress 0-100] [--workspace DIR] [--label "Claude Code"] \\
                      [--origin ios|mac|direct|agent] [--model NAME] [--media FILE]...
      → prints the new ITEM ID. Remember it (shell variables don't survive between tool calls).
      Origin defaults to `direct` (you typed into this AI CLI). Desk/satellite jobs set ios/mac/agent.
      Model is optional; the satellite fills it from measured usage when it can.
  orbital-track update <ID> --progress 0-100 [--note "what just happened"] [--details "markdown" [--append]] [--title "..."] [--media FILE]...
      --progress is REQUIRED at every update: how far along the WHOLE job is, 0-100 (your honest
      estimate). It drives the bar on the tracker and the phone. Raise it as steps complete, e.g.
      plan 10 → first change 30 → most done 60 → tests passing 85; `done` sets 100.
  orbital-track done   <ID> [--summary "markdown: what changed, where, what's next"] [--media FILE]...
      Done items leave the floating panel immediately and stay in history.

SUBAGENTS (only when your prompt contains ORBITAL_TRACK_PARENT=<ID>):
  orbital-track sub <PARENT_ID> --agent <name> --title "..." [--details "..."]   → prints SUB ID
  orbital-track update <SUB_ID> --progress 0-100 [--note "..."]   (at each milestone)
  orbital-track done <SUB_ID> --summary "..."
  Subagents never call `start`.

SCREENSHOT EVIDENCE (set on the tracker, or in the Orbital Agents app):
  orbital-track evidence                  the current level and exactly what it asks of you
  orbital-track evidence [--json]
  orbital-track evidence set <off|min1|all>
      off   nothing required (attach media when you make it anyway)
      min1  at least 1 screenshot for any UI / website / game change (+ a video for game features in motion)
      all   every visual feature screenshotted, every game feature filmed, for review from the phone
  `start`, `sub` and `done` print the instruction on stderr whenever the level isn't off.

Other:
  orbital-track list [--all] [--json]     active items (or everything)
  orbital-track ping                      is the tracker app running? (also shows the hub relay)
  orbital-track help                      this text

Options for every command: --quiet (no warnings), --id ID (start/sub: choose the id yourself).
Text options also accept --details-file F / --summary-file F / --note-file F, or "-" for stdin.
--media takes images or videos (screenshots, recordings). The tracker keeps a small compressed
copy, so it still shows after the original temp file is gone. Markdown images
![alt](/abs/path.png) inside details/summary are archived the same way.

Settings come from $ORBITAL_HOME/config.resolved.json (default ~/.orbital-agents).
Environment: ORBITAL_TRACK_PARENT=<id> turns `start` into `sub`; ORBITAL_TRACK_DISABLE=1 makes
every command a no-op (it still prints an id).
"""

let env = ProcessInfo.processInfo.environment
let config = OrbitalConfig.load(env: env)
let paths = TrackerPaths(config: config)
let port = config.trackerPort
config.ensureHome()

func fail(_ message: String, code: Int32 = 2) -> Never {
    FileHandle.standardError.write(Data("orbital-track: \(message)\n".utf8))
    exit(code)
}

let p: CLIArguments
do {
    p = try CLIArguments.parse(Array(CommandLine.arguments.dropFirst()))
} catch {
    fail(error.localizedDescription)
}
let quiet = p.flags.contains("quiet")

func warn(_ message: String) {
    guard !quiet else { return }
    FileHandle.standardError.write(Data("orbital-track: \(message)\n".utf8))
}

if p.flags.contains("help") || p.positional.isEmpty || ["help"].contains(p.verb ?? "") {
    print(help)
    exit(p.positional.isEmpty && !p.flags.contains("help") ? 2 : 0)
}

let verb = p.verb ?? ""

// Set by the tracker for its own grok title calls, and by anyone who wants tracking off.
if let off = env["ORBITAL_TRACK_DISABLE"], !off.isEmpty, off != "0",
   !["list", "ls", "ping", "status", "evidence", "screenshots", "settings"].contains(verb) {
    print(["start", "sub", "add"].contains(verb) ? IDGen.make() : "ok")
    exit(0)
}
let envParent = env["ORBITAL_TRACK_PARENT"].flatMap { $0.trimmingCharacters(in: .whitespaces).isEmpty ? nil : $0.trimmingCharacters(in: .whitespaces) }

func readStdin() -> String {
    String(decoding: FileHandle.standardInput.readDataToEndOfFile(), as: UTF8.self)
}

/// --details "text" | --details - (stdin) | --details-file path
func text(_ key: String) -> String? {
    if let file = p.value("\(key)-file") {
        if file == "-" { return readStdin() }
        guard let s = try? String(contentsOfFile: MediaArchiver.normalizePath(file), encoding: .utf8) else {
            fail("can't read \(file)")
        }
        return s
    }
    guard let v = p.value(key) else { return nil }
    return v == "-" ? readStdin() : v
}

// MARK: Talking to the local tracker

func local(_ method: String, _ path: String, body: Data? = nil, timeout: TimeInterval = 2) -> (status: Int, body: Data)? {
    guard config.trackerEnabled, let url = URL(string: "http://127.0.0.1:\(port)\(path)") else { return nil }
    return SyncHTTP.send(SyncHTTP.request(method, url, json: body), timeout: timeout)
}

let relay = HubRelay(config: config, paths: paths, env: env)

/// This Mac's tracker first; on a satellite, also the hub (in the background of the agent's work:
/// failures are queued silently).
func deliver(_ command: TrackerCommand) {
    guard let body = try? TrackerJSON.encoder().encode(command) else {
        warn("couldn't encode the command; continuing without tracking")
        return
    }
    if config.trackerEnabled {
        deliverLocal(body)
    }
    if let relay {
        var forHub = command
        forHub.media = command.media?.map { input in
            var m = input
            if let ref = input.archived { m.inlineArchive = MediaArchiver.inlineData(for: ref, paths: paths) }
            return m
        }
        let hubBody = (try? TrackerJSON.encoder().encode(forHub)) ?? body
        relay.relay(hubBody)
    }
}

func deliverLocal(_ body: Data) {
    if let (status, data) = local("POST", "/v1/command", body: body) {
        if (200..<300).contains(status) { return }
        warn("tracker rejected the command (\(status)): \(String(decoding: data.prefix(300), as: UTF8.self))")
        return
    }
    for dir in [paths.inboxDir, TrackerPaths.tmpInbox] {
        if (try? Inbox.writeData(body, to: dir)) != nil {
            warn("tracker app isn't running; queued in \(dir.path)")
            return
        }
    }
    warn("couldn't reach the tracker app or write to its inbox; continuing without tracking")
}

func mediaInputs(itemID: String, markdown: [String?]) -> [MediaInput]? {
    var seen = Set<String>()
    var inputs: [MediaInput] = []
    let explicit = p.values("media").map { MediaArchiver.normalizePath($0) }
    let inline = markdown.flatMap { MarkdownMedia.localPaths(in: $0) }
    for path in explicit + inline where seen.insert(path).inserted {
        guard FileManager.default.fileExists(atPath: path) else {
            warn("media not found, skipped: \(path)")
            continue
        }
        // Archive right now: agents' screenshots often live in temp dirs.
        // If the data folder isn't writable (a sandbox), the app archives on receipt.
        var ref = MediaArchiver.archive(path: path, caption: nil, itemID: itemID, paths: paths)
        ref.machine = config.machineName
        inputs.append(MediaInput(path: path, archived: ref.archivePath == nil ? nil : ref))
    }
    return inputs.isEmpty ? nil : inputs
}

/// The level in force: the running app first (it may have just changed), then settings.json;
/// on a satellite, the hub's copy when it was changed more recently.
func currentSettings() -> TrackerSettings {
    var settings: TrackerSettings
    if let (status, body) = local("GET", "/v1/settings", timeout: 1), status == 200, let s = TrackerSettings.fromReply(body) {
        settings = s
    } else {
        settings = TrackerSettings.load(paths)
    }
    if let relay { settings = TrackerSettings.newest(settings, relay.fetchSettings()) }
    return settings
}

/// The evidence instruction for one item, on stderr so `ID=$(orbital-track start ...)` still gets just the id.
func remindEvidence(_ level: EvidenceLevel, id: String, extra: String? = nil) {
    guard level != .off, !quiet else { return }
    var text = level.agentInstruction.replacingOccurrences(of: "<ID>", with: id)
    if let extra { text += "\n" + extra }
    FileHandle.standardError.write(Data(("\n" + text + "\n").utf8))
}

func loadItems(all: Bool) -> [TrackedItem]? {
    if let (status, body) = local("GET", all ? "/v1/items?all=1" : "/v1/items"), status == 200,
       let items = try? TrackerJSON.decoder().decode([TrackedItem].self, from: body) {
        return items
    }
    guard let raw = try? Data(contentsOf: paths.itemsFile),
          let items = try? TrackerJSON.decoder().decode([TrackedItem].self, from: raw) else { return nil }
    warn("tracker app not running; reading \(paths.itemsFile.path)")
    return all ? items : items.filter { $0.status == .active && !$0.dismissed }
}

/// Media already on an item or subtask (so `done` only nags when there is none).
func mediaCount(_ id: String) -> Int? {
    guard let items = loadItems(all: true) else { return nil }
    if let item = items.first(where: { $0.id == id }) {
        return item.media.count + item.subagents.reduce(0) { $0 + $1.media.count }
    }
    for item in items {
        if let sub = item.subagents.first(where: { $0.id == id }) { return sub.media.count }
    }
    return nil
}

func resolveAgent() -> String {
    Agents.normalize(p.value("agent") ?? Agents.detectFromEnvironment(env) ?? "other")
}

/// Where this item came from. Explicit flag/env win; a subagent is always `agent`; otherwise
/// a CLI start from the user AI chat is `direct`.
func resolveOrigin(isSub: Bool) -> String? {
    if let raw = p.value("origin") ?? env["ORBITAL_TRACK_ORIGIN"], let parsed = ItemOrigin.parse(raw) {
        return parsed.rawValue
    }
    if isSub { return ItemOrigin.agent.rawValue }
    return ItemOrigin.direct.rawValue
}

func resolveModel() -> String? {
    let raw = (p.value("model") ?? env["ORBITAL_TRACK_MODEL"] ?? "")
        .trimmingCharacters(in: .whitespacesAndNewlines)
    guard !raw.isEmpty else { return nil }
    return String(raw.prefix(120))
}

/// `--progress 40`, `--progress 40%`: how far along the whole job is.
func progressOption() -> Int? {
    guard let raw = p.value("progress") else { return nil }
    let digits = raw.trimmingCharacters(in: CharacterSet(charactersIn: "% "))
    guard let value = Double(digits), (0...100).contains(value) else { fail("--progress takes a percent from 0 to 100") }
    return Int(value.rounded())
}

// MARK: Commands

switch verb {
case "start", "add", "sub":
    var parent = p.value("parent")
    if verb == "sub" { parent = parent ?? (p.positional.count > 1 ? p.positional[1] : nil) ?? envParent }
    // Safety net: a subagent that inherited ORBITAL_TRACK_PARENT can't create a top-level bubble.
    if verb != "sub", parent == nil, let envParent { parent = envParent }
    if verb == "sub", parent == nil { fail("sub needs a parent id: orbital-track sub <PARENT_ID> --title ...") }
    let titleArgs = verb != "sub" && p.positional.count > 1 ? p.positional[1...].joined(separator: " ") : nil
    guard let title = p.value("title") ?? titleArgs, !title.trimmingCharacters(in: .whitespaces).isEmpty else {
        fail("--title is required")
    }
    let id = p.value("id") ?? IDGen.make()
    guard IDGen.isValid(id) else { fail("--id must be 2-64 letters, digits, '-' or '_'") }
    if let parent, !IDGen.isValid(parent) { fail("parent id must be 2-64 letters, digits, '-' or '_'") }
    var cmd = TrackerCommand(op: parent == nil ? "start" : "sub", id: id)
    cmd.parent = parent
    cmd.agent = resolveAgent()
    cmd.agentLabel = p.value("label")
    cmd.machine = config.machineName
    cmd.title = title
    cmd.details = text("details")
    cmd.workspace = p.value("workspace").map { MediaArchiver.normalizePath($0) } ?? FileManager.default.currentDirectoryPath
    cmd.progress = progressOption()
    cmd.origin = resolveOrigin(isSub: parent != nil)
    cmd.model = resolveModel()
    cmd.media = mediaInputs(itemID: parent ?? id, markdown: [cmd.details])
    deliver(cmd)
    print(id)
    remindEvidence(currentSettings().evidence, id: id)

case "update", "note", "done", "complete", "finish":
    guard p.positional.count > 1 else { fail("\(verb) needs an item id") }
    let id = p.positional[1]
    guard IDGen.isValid(id) else { fail("item ids are 2-64 letters, digits, '-' or '_'") }
    let isDone = ["done", "complete", "finish"].contains(verb)
    var cmd = TrackerCommand(op: isDone ? "done" : "update", id: id)
    // Only an explicit --agent; otherwise the item keeps its own agent (env guesses can mislabel).
    cmd.agent = p.value("agent").map(Agents.normalize)
    cmd.machine = config.machineName
    cmd.title = p.value("title")
    cmd.details = text("details")
    cmd.appendDetails = p.flags.contains("append") ? true : nil
    cmd.note = text("note") ?? (verb == "note" && p.positional.count > 2 ? p.positional[2...].joined(separator: " ") : nil)
    cmd.summary = text("summary")
    cmd.progress = progressOption()
    if let origin = p.value("origin") ?? env["ORBITAL_TRACK_ORIGIN"] {
        cmd.origin = ItemOrigin.parse(origin)?.rawValue
    }
    if let model = resolveModel() { cmd.model = model }
    if let raw = p.value("usage-json") {
        guard let usage = try? TrackerJSON.decoder().decode(TokenUsage.self, from: Data(raw.utf8)) else {
            fail("--usage-json must be a JSON object like {\"engine\":\"grok\",\"inputTokens\":1200,\"outputTokens\":300}")
        }
        cmd.usage = usage
        if cmd.model == nil, let m = usage.model, !m.isEmpty { cmd.model = m }
    }
    cmd.media = mediaInputs(itemID: id, markdown: [cmd.details, cmd.note, cmd.summary])
    deliver(cmd)
    print("ok")
    if !isDone, cmd.progress == nil, !quiet {
        FileHandle.standardError.write(Data(
            "orbital-track: add --progress <0-100> to every update so the tracker's bar moves (e.g. --progress 40).\n".utf8))
    }
    if isDone {
        let level = currentSettings().evidence
        if level != .off {
            let count = mediaCount(id) ?? (cmd.media?.count ?? 0)
            if count == 0 {
                remindEvidence(level, id: id, extra: """
                ⚠︎ This item has NO screenshots or videos. If the work was visual, attach them now \
                (orbital-track update \(id) --media /abs/shot.png); the item stays done.
                """)
            } else if level == .all, !quiet {
                FileHandle.standardError.write(Data("""
                Screenshot evidence: ALL. \(count) picture(s)/video(s) on this item. If any visual feature you \
                changed has none yet, add it: orbital-track update \(id) --media /abs/shot.png

                """.utf8))
            }
        }
    }

case "list", "ls":
    guard var items = loadItems(all: p.flags.contains("all")) else { fail("no items available", code: 1) }
    if !p.flags.contains("all") { items = items.filter { $0.status == .active } }
    if p.flags.contains("json") {
        print(String(decoding: (try? TrackerJSON.encoder(pretty: true).encode(items)) ?? Data("[]".utf8), as: UTF8.self))
    } else if items.isEmpty {
        print("(nothing being tracked)")
    } else {
        for item in items {
            let who = item.isAI ? Agents.displayName(item.agent) : "You"
            let running = item.subagents.filter { $0.status == .active }.count
            let subs = item.subagents.isEmpty ? "" : "  [\(running)/\(item.subagents.count) subagents running]"
            let machine = item.machine.map { "  @\($0)" } ?? ""
            print("\(item.id)  \(item.status == .done ? "✓" : "•") \(who): \(item.title)\(subs)\(machine)")
        }
    }

case "evidence", "screenshots", "settings":
    // orbital-track evidence [--json] | orbital-track evidence set <off|min1|all> | orbital-track evidence all
    var args = Array(p.positional.dropFirst())
    if args.first?.lowercased() == "set" { args.removeFirst() }
    if let raw = args.first {
        guard let level = EvidenceLevel(loose: args.joined(separator: " ")) else {
            fail("unknown level '\(raw)'. Use off, min1 or all")
        }
        let body = try? JSONSerialization.data(withJSONObject: ["evidence": level.rawValue, "by": "cli"])
        let saved = TrackerSettings(evidence: level, updatedAt: Date(), updatedBy: "cli")
        if let (status, _) = local("POST", "/v1/settings", body: body), (200..<300).contains(status) {
            // The app saved it; the hub picks it up from settings.json.
        } else {
            do {
                try saved.save(paths)
            } catch {
                fail("couldn't save \(paths.settingsFile.path): \(error.localizedDescription)", code: 1)
            }
        }
        relay?.pushSettings(saved, by: "cli")
    }
    let settings = currentSettings()
    if p.flags.contains("json") {
        let data = (try? JSONSerialization.data(withJSONObject: settings.json, options: [.prettyPrinted, .sortedKeys])) ?? Data()
        print(String(decoding: data, as: UTF8.self))
    } else {
        print(settings.evidence.agentInstruction)
    }

case "ping", "status":
    var out: [String: Any] = ["machine": config.machineName, "role": config.role, "port": Int(port),
                              "cli_version": TrackerInfo.version]
    var ok = false
    if let (status, body) = local("GET", "/v1/health", timeout: 1.5), status == 200,
       let health = try? JSONSerialization.jsonObject(with: body) as? [String: Any] {
        out["tracker"] = health
        ok = true
    } else {
        out["tracker"] = ["ok": false, "reason": config.trackerEnabled
            ? "tracker app not running on port \(port); commands queue in \(paths.inboxDir.path)"
            : "tracker.enabled is false on this machine"]
    }
    if let relay {
        out["hub"] = ["url": config.hubURL, "token": relay.token == nil ? "missing (ORBITAL_HUB_TOKEN)" : "set",
                      "queued": relay.queued.count, "cooling_down": relay.inCooldown()]
        if !config.trackerEnabled { ok = relay.token != nil }
    }
    out["ok"] = ok
    let data = (try? JSONSerialization.data(withJSONObject: out, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])) ?? Data()
    print(String(decoding: data, as: UTF8.self))
    exit(ok ? 0 : 1)

default:
    fail("unknown command '\(verb)'. Try: orbital-track help")
}
