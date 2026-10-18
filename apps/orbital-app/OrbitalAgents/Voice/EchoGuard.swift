import Foundation

/// Tells your words from the speaker playing the assistant back into the mic.
///
/// Apple's voice-processing unit does not remove a reply on the loud speaker, so
/// the recognizer transcribes it. A short mismatch at the end of that transcript
/// used to count as you interrupting: playback stopped, and the accurate transcript
/// (which lands after playback has already stopped) was sent with only a few leading
/// words trimmed. The rest of its sentence became your next message.
///
/// The hub applies the same rules to what it receives (`decide`, `is_speaker_echo`,
/// `is_fragment`), so a transcript that slips through here is still caught there.
enum EchoGuard {
    private static let expansions: [String: [String]] = [
        "we've": ["we", "have"],
        "i'm": ["i", "am"],
        "i'll": ["i", "will"],
        "it's": ["it", "is"],
        "don't": ["do", "not"],
        "they're": ["they", "are"],
        "we're": ["we", "are"],
        "that's": ["that", "is"],
        "what's": ["what", "is"],
        "can't": ["can", "not"],
        "cannot": ["can", "not"],
        "won't": ["will", "not"],
        "i've": ["i", "have"],
        "you're": ["you", "are"],
        "you'll": ["you", "will"],
        "let's": ["let", "us"],
        "there's": ["there", "is"],
        "here's": ["here", "is"],
        "she's": ["she", "is"],
        "he's": ["he", "is"],
        "isn't": ["is", "not"],
        "aren't": ["are", "not"],
        "didn't": ["did", "not"],
        "doesn't": ["does", "not"],
        "wasn't": ["was", "not"],
        "weren't": ["were", "not"],
        "haven't": ["have", "not"],
        "hasn't": ["has", "not"],
        "hadn't": ["had", "not"],
        "wouldn't": ["would", "not"],
        "couldn't": ["could", "not"],
        "shouldn't": ["should", "not"],
        "i'd": ["i", "would"],
        "you'd": ["you", "would"],
        "he'd": ["he", "would"],
        "she'd": ["she", "would"],
        "we'd": ["we", "would"],
        "they'd": ["they", "would"],
        "gonna": ["going", "to"],
        "wanna": ["want", "to"],
    ]

    /// Words that do not, by themselves, prove a new sentence.
    private static let function: Set<String> = [
        "a", "an", "the", "of", "to", "and", "or", "for", "in", "on", "at", "is", "it",
        "that", "this", "so", "i", "you", "we", "they", "be", "do", "not", "have", "has",
        "had", "will", "would", "can", "could", "should", "just", "my", "your", "our",
        "me", "us", "them", "she", "he", "her", "his", "with", "from", "as", "if", "but",
        "about", "up", "out", "what", "when", "how", "who", "am", "are", "was", "were",
        "been", "being", "does", "did", "there", "here", "let", "its", "than", "then",
        "too", "very", "also", "into", "over", "again",
    ]

    static let interjections: Set<String> = [
        "stop", "wait", "hold", "hang", "hey", "no", "nope", "pause", "enough",
        "quiet", "actually", "sorry", "shush", "okay", "ok",
    ]

    struct Decision: Equatable {
        var send: String
        var barge: Bool
    }

    static func decide(heard: String, spoken: String, guarding: Bool) -> Decision {
        let parts = pieces(heard)
        let said = tokens(spoken)
        guard !parts.isEmpty else { return Decision(send: "", barge: false) }
        let flat = parts.flatMap(\.tokens)
        let saidSet = Set(said)
        let original = parts.map(\.raw).joined(separator: " ")

        if !guarding {
            if isSpeakerEcho(heard, spoken: spoken) { return Decision(send: "", barge: false) }
            return Decision(send: original, barge: false)
        }

        if let first = flat.first, interjections.contains(first) {
            let rest = Array(flat.dropFirst())
            let restEcho = rest.isEmpty || (coverage(rest, said) >= 0.65 && novelContent(rest, saidSet).count <= 1)
            if restEcho { return Decision(send: first, barge: true) }
            return Decision(send: original, barge: true)
        }

        let matched = leadingTokenMatch(flat, said)
        let kept = matched > 0 ? dropPrefix(parts, matched) : parts
        let keptFlat = kept.flatMap(\.tokens)
        if keptFlat.isEmpty { return Decision(send: "", barge: false) }

        let novel = novelContent(keptFlat, saidSet)
        let run = longestNovelRun(keptFlat, saidSet)
        let cov = coverage(keptFlat, said)
        let barge = novel.count >= 3 || run >= 3 || (novel.count >= 2 && cov < 0.45)
        let clearEcho = cov >= 0.6 && novel.count <= 1
        if barge && !clearEcho {
            return Decision(send: kept.map(\.raw).joined(separator: " "), barge: true)
        }
        return Decision(send: "", barge: false)
    }

    /// True when `text` is its own line coming back through the mic.
    /// A two-word command and a follow-up that only shares a few words are kept.
    static func isSpeakerEcho(_ text: String, spoken: String) -> Bool {
        let heard = tokens(text)
        let said = tokens(spoken)
        guard heard.count >= 4, !said.isEmpty else { return false }
        if novelContent(heard, Set(said)).count > 1 { return false }
        return coverage(heard, said) >= 0.65
    }

    /// `fragment` is a shorter contiguous piece of something just sent.
    static func isFragment(of full: String, fragment: String) -> Bool {
        let whole = tokens(full)
        let part = tokens(fragment)
        guard part.count >= 2, part.count < whole.count else { return false }
        if whole.count < part.count { return false }
        for i in 0...(whole.count - part.count) where Array(whole[i..<(i + part.count)]) == part {
            return true
        }
        return false
    }

    // MARK: Tokens

    private struct Piece {
        var raw: String
        var tokens: [String]
    }

    private static func normalize(_ word: String) -> String {
        let lowered = word.lowercased()
            .replacingOccurrences(of: "\u{2019}", with: "'")
            .replacingOccurrences(of: "\u{2018}", with: "'")
        var cleaned = ""
        for ch in lowered where ch == "'" || ch.isLetter || ch.isNumber {
            cleaned.append(ch)
        }
        return cleaned.trimmingCharacters(in: CharacterSet(charactersIn: "'"))
    }

    private static func pieces(_ text: String) -> [Piece] {
        text.split(whereSeparator: \.isWhitespace).compactMap { raw in
            let norm = normalize(String(raw))
            guard !norm.isEmpty else { return nil }
            return Piece(raw: String(raw), tokens: expansions[norm] ?? [norm])
        }
    }

    private static func tokens(_ text: String) -> [String] {
        pieces(text).flatMap(\.tokens)
    }

    private static func matchRun(_ heard: [String], _ spoken: [String], _ h0: Int, _ s0: Int) -> Int {
        guard !heard.isEmpty, !spoken.isEmpty else { return 0 }
        var matched = 0
        var j = s0
        let limit = min(spoken.count, s0 + (heard.count - h0) + 4)
        for h in heard[h0...] {
            while j < limit && spoken[j] != h { j += 1 }
            if j >= limit { break }
            matched += 1
            j += 1
        }
        return matched
    }

    private static func coverage(_ heard: [String], _ spoken: [String]) -> Double {
        if heard.isEmpty { return 1 }
        if spoken.isEmpty { return 0 }
        var best = 0
        for h0 in heard.indices {
            for s0 in spoken.indices where spoken[s0] == heard[h0] {
                best = max(best, matchRun(heard, spoken, h0, s0))
                if best == heard.count { return 1 }
            }
        }
        return Double(best) / Double(heard.count)
    }

    private static func novelContent(_ heard: [String], _ spoken: Set<String>) -> [String] {
        heard.filter { !spoken.contains($0) && !function.contains($0) }
    }

    private static func longestNovelRun(_ heard: [String], _ spoken: Set<String>) -> Int {
        var best = 0
        var run = 0
        for w in heard {
            if !spoken.contains(w) && !function.contains(w) {
                run += 1
                best = max(best, run)
            } else {
                run = 0
            }
        }
        return best
    }

    private static func leadingTokenMatch(_ heard: [String], _ spoken: [String]) -> Int {
        guard heard.count >= 2, !spoken.isEmpty else { return 0 }
        var best = 0
        for s0 in spoken.indices where spoken[s0] == heard[0] {
            best = max(best, matchRun(heard, spoken, 0, s0))
            if best == heard.count { break }
        }
        return best >= 2 ? best : 0
    }

    private static func dropPrefix(_ parts: [Piece], _ count: Int) -> [Piece] {
        var left = count
        var kept: [Piece] = []
        var started = false
        for part in parts {
            if !started {
                if left >= part.tokens.count {
                    left -= part.tokens.count
                    continue
                }
                started = true
            }
            kept.append(part)
        }
        return kept
    }
}

#if ECHO_SELFTEST
@main
enum EchoSelfTest {
    static func main() {
        let spoken = "Already checking whether we've emailed people about the games. I'll tell you as soon as that comes back."
        let looking = "It's still looking, so I don't have the email yet. As soon as it lands I'll read you one real one, subject and all."
        let finishes = "When that check finishes, I'll read you one real email we sent, subject and all."
        var failures: [String] = []
        func expect(_ ok: Bool, _ name: String) {
            if !ok { failures.append(name) }
        }
        expect(EchoGuard.isSpeakerEcho("we've emailed people about those.", spoken: spoken), "echo those")
        expect(EchoGuard.isSpeakerEcho("When that check finishes already.", spoken: finishes), "echo finishes")
        expect(EchoGuard.isSpeakerEcho("They're still looking so I.", spoken: looking), "echo looking")
        expect(!EchoGuard.isSpeakerEcho("Check email", spoken: "On it. I'll check whether we've started emailing people about the games yet."), "keep check email")
        expect(!EchoGuard.isSpeakerEcho("You heard yourself.", spoken: "Got it. I'll have the agent look for the emails."), "keep heard yourself")
        expect(EchoGuard.isFragment(of: "You keep on hearing yourself in the app, you need to do something to fix that.", fragment: "You keep on."), "fragment")
        let cut = EchoGuard.decide(heard: "wait, show me the email", spoken: finishes, guarding: true)
        expect(cut.barge && cut.send.contains("show me the email"), "barge wait")
        let stop = EchoGuard.decide(heard: "stop", spoken: finishes, guarding: true)
        expect(stop.barge && stop.send == "stop", "barge stop")
        let echo = EchoGuard.decide(heard: "we've emailed people about those.", spoken: spoken, guarding: true)
        expect(echo.send.isEmpty && !echo.barge, "no barge on echo")
        let later = EchoGuard.decide(heard: "Check email", spoken: "On it. I'll check whether we've started emailing people about the games yet.", guarding: false)
        expect(later.send == "Check email" && !later.barge, "after, check email")
        let mixed = EchoGuard.decide(
            heard: "Already checking whether we've emailed people about those. Wait, show me one different report please.",
            spoken: spoken,
            guarding: true
        )
        expect(mixed.barge && !mixed.send.contains("Already checking") && mixed.send.contains("different"), "strip her lead")
        if failures.isEmpty {
            print("ok")
        } else {
            for f in failures { print("FAIL \(f)") }
            exit(1)
        }
    }
}
#endif
