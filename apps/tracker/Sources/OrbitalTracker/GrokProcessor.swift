import Foundation
import NaturalLanguage
import OrbitalTrackerCore

struct GrokDecision {
    var action: String   // add | complete | ignore
    var title: String
    var details: String
    var matchID: String
}

/// Optional: uses the `grok` CLI (headless, structured output) to turn a spoken or typed phrase into
/// a tracker action. `tracker.titles = "none"` turns it off; without grok installed the local
/// heuristics below title everything. Model: `tracker.grok_model` in config.toml (grok's default otherwise).
enum GrokProcessor {
    static func locate(env: [String: String] = ProcessInfo.processInfo.environment) -> String? {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        if let explicit = env["ORBITAL_TRACKER_GROK"], !explicit.isEmpty {
            return FileManager.default.isExecutableFile(atPath: explicit) ? explicit : nil
        }
        let pathDirs = (env["PATH"] ?? "").split(separator: ":").map(String.init)
        let candidates = pathDirs.map { "\($0)/grok" }
            + ["\(home)/.local/bin/grok", "/opt/homebrew/bin/grok", "/usr/local/bin/grok", "\(home)/.grok/bin/grok"]
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    private static let systemPrompt = """
    You title items for a personal "what am I working on" tracker. The user juggles many tasks. \
    Whenever they start something they say or type a short phrase; every such phrase, including plain \
    status lines like "gathering data on X" or "looking into Y", is something they are working on and must be tracked.
    - add (the default): title = the 2-4 word Title Case name of the THING being worked on, not the activity verb. \
    Examples: "gathering data on marketing research for youtube" -> "Marketing Research"; "fixing the login bug in \
    the billing app" -> "Login Bug Fix"; "I'm working on the onboarding emails for the store and the \
    landing page copy" -> "Onboarding Emails". details = one or two clean sentences restating only what was said, \
    keeping every specific (platforms, names, numbers). Never mention other items in details.
    - complete: ONLY when they explicitly say they finished / are done with / wrapped up one of the open items. \
    match_id = that item's id, title = that item's title.
    - ignore: ONLY pure filler or social chatter with nothing to do ("um", "hello", "what time is it").
    Leave unused fields as empty strings.
    """

    private static let schema = #"{"type":"object","properties":{"action":{"type":"string","enum":["add","complete","ignore"]},"title":{"type":"string","maxLength":40},"details":{"type":"string"},"match_id":{"type":"string"}},"required":["action","title","details","match_id"],"additionalProperties":false}"#

    static func decide(grok: String, model: String?, phrase: String, openItems: [TrackedItem], scratch: URL) async -> GrokDecision? {
        let open = openItems.map { item -> [String: String] in
            ["id": item.id, "title": item.title, "who": item.isAI ? Agents.displayName(item.agent) : "user"]
        }
        let openJSON = (try? JSONSerialization.data(withJSONObject: open)).map { String(decoding: $0, as: UTF8.self) } ?? "[]"
        let prompt = "Phrase: \"\(phrase.replacingOccurrences(of: "\"", with: "'"))\"\nOpen items: \(openJSON)"
        var args = ["-p", prompt]
        if let model, !model.isEmpty { args += ["-m", model] }
        args += ["--reasoning-effort", "low", "--json-schema", schema,
                 "--disable-web-search", "--no-subagents", "--max-turns", "1", "--tools", "",
                 "--system-prompt-override", systemPrompt, "--verbatim"]
        guard let data = await run(grok, args, cwd: scratch, timeout: 30) else { return nil }
        return parse(data)
    }

    static func parse(_ data: Data) -> GrokDecision? {
        let text = String(decoding: data, as: UTF8.self)
        guard let start = text.firstIndex(of: "{"), let end = text.lastIndex(of: "}") else { return nil }
        guard let outer = try? JSONSerialization.jsonObject(with: Data(text[start...end].utf8)) as? [String: Any] else { return nil }
        var obj = outer["structuredOutput"] as? [String: Any]
        if obj == nil, let inner = outer["text"] as? String,
           let parsed = try? JSONSerialization.jsonObject(with: Data(inner.utf8)) as? [String: Any] {
            obj = parsed
        }
        if obj == nil, outer["action"] != nil { obj = outer }
        guard let obj, let action = obj["action"] as? String else { return nil }
        return GrokDecision(action: action.lowercased(),
                            title: (obj["title"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines),
                            details: (obj["details"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines),
                            matchID: (obj["match_id"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines))
    }

    private static func run(_ exe: String, _ args: [String], cwd: URL, timeout: TimeInterval) async -> Data? {
        await withCheckedContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                let proc = Process()
                proc.executableURL = URL(fileURLWithPath: exe)
                proc.arguments = args
                try? FileManager.default.createDirectory(at: cwd, withIntermediateDirectories: true)
                proc.currentDirectoryURL = cwd
                var env = ProcessInfo.processInfo.environment
                let home = FileManager.default.homeDirectoryForCurrentUser.path
                env["PATH"] = "\(home)/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:" + (env["PATH"] ?? "")
                env["ORBITAL_TRACK_DISABLE"] = "1"  // this call must never file its own tracker item
                proc.environment = env
                let out = Pipe()
                proc.standardOutput = out
                proc.standardError = FileHandle.nullDevice
                proc.standardInput = FileHandle.nullDevice
                do { try proc.run() } catch {
                    continuation.resume(returning: nil)
                    return
                }
                let killer = DispatchWorkItem { if proc.isRunning { proc.terminate() } }
                DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: killer)
                let data = out.fileHandleForReading.readDataToEndOfFile()
                proc.waitUntilExit()
                killer.cancel()
                continuation.resume(returning: proc.terminationStatus == 0 ? data : nil)
            }
        }
    }
}

/// Offline titles and completion detection, used before grok answers or when it is off/unavailable.
enum VoiceHeuristics {
    private static let completionPatterns = [
        #"^(ok(ay)?,?\s+)?(i'?m|i am|we'?re)?\s*(all\s+)?(done|finished|through)\s+(with|on)\b"#,
        #"^(ok(ay)?,?\s+)?(i\s+)?(just\s+)?(completed?|finished|wrapped up|closed?|shipped)\b"#,
        #"^(mark|check)\b.*\b(done|complete|finished)\b"#,
        #"\b(is|are)\s+(done|finished|complete)[.!]?$"#,
    ]

    static func looksLikeCompletion(_ phrase: String) -> Bool {
        let s = phrase.lowercased()
        return completionPatterns.contains { s.range(of: $0, options: .regularExpression) != nil }
    }

    private static let weakNouns: Set<String> = ["stuff", "thing", "things", "data", "work", "bit", "lot", "today", "now", "something"]

    /// "gathering data on marketing research for youtube" → "Marketing Research":
    /// the longest run of nouns/adjectives, up to 4 words.
    static func title(_ phrase: String) -> String {
        let tagger = NLTagger(tagSchemes: [.lexicalClass])
        tagger.string = phrase
        var runs: [[String]] = []
        var current: [String] = []
        tagger.enumerateTags(in: phrase.startIndex..<phrase.endIndex, unit: .word, scheme: .lexicalClass,
                             options: [.omitWhitespace, .omitPunctuation]) { tag, range in
            let word = String(phrase[range])
            if (tag == .noun || tag == .adjective || tag == .otherWord), !weakNouns.contains(word.lowercased()) {
                current.append(word)
            } else if !current.isEmpty {
                runs.append(current)
                current = []
            }
            return true
        }
        if !current.isEmpty { runs.append(current) }
        let best = runs.max { $0.count < $1.count } ?? []
        let words = best.isEmpty ? phrase.split(separator: " ").prefix(4).map(String.init) : Array(best.prefix(4))
        return words.map(titleCase).joined(separator: " ")
    }

    /// Enforces the bubble style on any title: Title Case, at most 5 words.
    static func tidyTitle(_ title: String, phrase: String) -> String {
        let words = title.split(whereSeparator: \.isWhitespace).map(String.init)
        if words.isEmpty || words.count > 5 { return Self.title(phrase) }
        let small: Set<String> = ["a", "an", "and", "for", "of", "on", "the", "to", "in", "with", "vs"]
        return words.enumerated().map { i, w in
            i > 0 && small.contains(w.lowercased()) ? w.lowercased() : (w.first?.isUppercase == true ? w : titleCase(w))
        }.joined(separator: " ")
    }

    static func saysDone(_ phrase: String) -> Bool {
        let s = phrase.lowercased()
        return looksLikeCompletion(phrase)
            || ["done", "finished", "complete", "completed", "wrapped up", "closed", "shipped"].contains { s.contains($0) }
    }

    private static func titleCase(_ word: String) -> String {
        if word.count > 1, word == word.uppercased() { return word }  // keep acronyms like "API"
        let known = ["youtube": "YouTube", "tiktok": "TikTok", "ios": "iOS", "ai": "AI", "seo": "SEO", "api": "API"]
        if let k = known[word.lowercased()] { return k }
        return word.prefix(1).uppercased() + word.dropFirst()
    }

    /// Picks the open item whose title shares the most words with the phrase.
    static func bestMatch(_ phrase: String, in items: [TrackedItem]) -> TrackedItem? {
        let words = Set(phrase.lowercased().split { !$0.isLetter && !$0.isNumber }.map(String.init).filter { $0.count > 2 })
        let scored = items.map { item -> (TrackedItem, Int) in
            let t = Set((item.title + " " + item.details).lowercased().split { !$0.isLetter && !$0.isNumber }.map(String.init))
            return (item, words.intersection(t).count)
        }
        guard let best = scored.max(by: { $0.1 < $1.1 }), best.1 > 0 else { return nil }
        return best.0
    }
}
