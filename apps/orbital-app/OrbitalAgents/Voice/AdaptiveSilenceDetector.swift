import Foundation

/// Environment-calibrated VAD + text-stability finalizer.
///
/// Ends an utterance when ANY of:
/// 1. Energy drops below the adaptive silence gate for `silenceSeconds` after speech,
///    and the STT partial has stopped changing (so the last words are not cut mid-token)
/// 2. STT partial text is non-empty and unchanged for `silenceSeconds` **while energy is quiet**
/// 3. Continuous speech / partial exceeds `maxUtteranceSeconds`
///
/// Text-stable alone never ends a turn while the mic still hears speech. Apple Speech often
/// holds the same partial for a second or more mid-sentence; treating that as end-of-turn
/// was cutting people off mid-word ("status of tha", "people lik").
@MainActor
final class AdaptiveSilenceDetector: ObservableObject {
    enum Phase: String {
        case calibrating = "Calibrating silence…"
        case quiet = "Quiet"
        case speech = "Hearing speech"
        case trailing = "End of speech…"
    }

    @Published private(set) var phase: Phase = .calibrating
    @Published private(set) var noiseFloorDb: Float = -50
    @Published private(set) var speechGateDb: Float = -40
    @Published private(set) var silenceGateDb: Float = -45
    @Published private(set) var currentDb: Float = -80
    @Published private(set) var isSpeech = false
    @Published private(set) var silenceCountdown: TimeInterval?

    var onUtteranceEnded: (() -> Void)?

    /// Pause after speech before the phrase is sent. Desk default is ~2.2 s.
    var silenceSeconds: TimeInterval = 2.0
    var calibrateSeconds: TimeInterval = 0.55
    var minSpeechSeconds: TimeInterval = 0.2
    var maxUtteranceSeconds: TimeInterval = 90
    var speechMarginDb: Float = 8
    /// Keep "speech" a bit longer after level drops so brief mid-sentence dips do not start the timer.
    var hangoverMarginDb: Float = 5
    var floorLearnRate: Float = 0.08
    var absoluteSpeechFloorDb: Float = -52
    var absoluteSpeechCeilDb: Float = -16
    /// Extra wait when the partial looks mid-word or ends on a continuer ("and", "tha", …).
    var incompleteHoldExtra: TimeInterval = 0.85
    /// Escape hatch when the room stays loud: only then may text-stable fire while energy is high.
    var loudStableMultiplier: Double = 3.0

    private var startedAt: Date?
    private var speechStartedAt: Date?
    private var silenceStartedAt: Date?
    private var heardSpeech = false
    private var floorSamples = 0
    private var peakSpeechDb: Float = -80
    private var calibrating = true
    private var lastFinalizeAt: Date = .distantPast
    private let minFinalizeInterval: TimeInterval = 0.45

    private var lastPartial: String = ""
    private var lastPartialChangeAt: Date?
    private var hadNonEmptyPartial = false

    func reset(silenceSeconds: TimeInterval, maxUtteranceSeconds: TimeInterval = 90) {
        self.silenceSeconds = max(0.4, min(10, silenceSeconds))
        self.maxUtteranceSeconds = max(10, min(180, maxUtteranceSeconds))
        startedAt = Date()
        speechStartedAt = nil
        silenceStartedAt = nil
        heardSpeech = false
        floorSamples = 0
        peakSpeechDb = -80
        calibrating = true
        noiseFloorDb = -50
        speechGateDb = -40
        silenceGateDb = -45
        currentDb = -80
        isSpeech = false
        silenceCountdown = nil
        phase = .calibrating
        lastPartial = ""
        lastPartialChangeAt = nil
        hadNonEmptyPartial = false
    }

    func stop() {
        phase = .quiet
        isSpeech = false
        silenceCountdown = nil
        startedAt = nil
        lastPartial = ""
        lastPartialChangeAt = nil
        hadNonEmptyPartial = false
    }

    func notePartialText(_ text: String) {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        let now = Date()
        if t != lastPartial {
            lastPartial = t
            lastPartialChangeAt = now
            if !t.isEmpty {
                hadNonEmptyPartial = true
                if !heardSpeech {
                    heardSpeech = true
                    speechStartedAt = speechStartedAt ?? now
                }
            }
        }
        evaluateFinalize(now: now, energyIsSpeech: isSpeech)
    }

    func process(db: Float) {
        guard startedAt != nil else { return }
        let now = Date()
        let elapsed = now.timeIntervalSince(startedAt!)
        let sample = max(-90, min(0, db))
        currentDb = sample

        if calibrating {
            if floorSamples == 0 {
                noiseFloorDb = sample
            } else if sample < noiseFloorDb {
                noiseFloorDb = noiseFloorDb * 0.35 + sample * 0.65
            } else {
                noiseFloorDb = noiseFloorDb * 0.9 + sample * 0.1
            }
            floorSamples += 1
            recomputeGates()
            phase = .calibrating
            if elapsed >= calibrateSeconds && floorSamples >= 10 {
                calibrating = false
                phase = .quiet
            }
            return
        }

        if sample < noiseFloorDb + 4 {
            noiseFloorDb = noiseFloorDb * (1 - floorLearnRate) + sample * floorLearnRate
            if peakSpeechDb > -79 {
                noiseFloorDb = min(noiseFloorDb, peakSpeechDb - 10)
            }
            recomputeGates()
        }

        let currentlySpeech: Bool
        if heardSpeech {
            currentlySpeech = sample > silenceGateDb
        } else {
            currentlySpeech = sample > speechGateDb
        }

        if currentlySpeech {
            if !heardSpeech {
                heardSpeech = true
                speechStartedAt = now
            }
            peakSpeechDb = max(peakSpeechDb, sample)
            silenceStartedAt = nil
            isSpeech = true
            phase = .speech
            evaluateFinalize(now: now, energyIsSpeech: true)
            return
        }

        isSpeech = false

        if heardSpeech || hadNonEmptyPartial {
            if silenceStartedAt == nil {
                silenceStartedAt = now
            }
            let silentFor = now.timeIntervalSince(silenceStartedAt!)
            silenceCountdown = max(0, holdNeeded(for: lastPartial) - silentFor)
            phase = .trailing
            evaluateFinalize(now: now, energyIsSpeech: false)
            return
        }

        phase = .quiet
        silenceCountdown = nil
        if sample < speechGateDb {
            noiseFloorDb = noiseFloorDb * 0.97 + sample * 0.03
            recomputeGates()
        }
    }

    private func evaluateFinalize(now: Date, energyIsSpeech: Bool) {
        guard startedAt != nil, !calibrating else { return }

        let needed = holdNeeded(for: lastPartial)

        if hadNonEmptyPartial, !lastPartial.isEmpty, let changed = lastPartialChangeAt {
            let stableFor = now.timeIntervalSince(changed)
            if energyIsSpeech {
                // Mic still hears speech: do not treat a stalled STT partial as end-of-turn.
                // Only escape if the room stays loud for a long time with no new words.
                let loudNeeded = max(needed * loudStableMultiplier, 4.0)
                if stableFor >= loudNeeded {
                    silenceCountdown = 0
                    phase = .trailing
                    finalizeIfAllowed(now: now, reason: "text-stable-loud")
                    return
                }
                silenceCountdown = max(0, loudNeeded - stableFor)
            } else {
                let quietFor = silenceStartedAt.map { now.timeIntervalSince($0) } ?? 0
                if stableFor >= needed, quietFor >= needed {
                    silenceCountdown = 0
                    phase = .trailing
                    finalizeIfAllowed(now: now, reason: "text-stable")
                    return
                }
                if quietFor > 0.15 || stableFor > 0.15 {
                    silenceCountdown = max(0, needed - min(stableFor, max(quietFor, 0)))
                    if phase != .speech || stableFor > 0.3 {
                        phase = .trailing
                    }
                }
            }
        }

        if heardSpeech, !energyIsSpeech, let sil = silenceStartedAt {
            let silentFor = now.timeIntervalSince(sil)
            let speechLen = speechStartedAt.map { now.timeIntervalSince($0) } ?? 0
            if speechLen >= minSpeechSeconds, silentFor >= needed {
                // Give STT a beat to finish the last token after the room goes quiet.
                let sttSettled: Bool
                if hadNonEmptyPartial, let changed = lastPartialChangeAt {
                    sttSettled = now.timeIntervalSince(changed) >= min(0.4, needed * 0.3)
                } else {
                    sttSettled = true
                }
                if sttSettled {
                    finalizeIfAllowed(now: now, reason: "energy-silence")
                    return
                }
            }
        }

        if let start = speechStartedAt {
            let len = now.timeIntervalSince(start)
            if len >= maxUtteranceSeconds, hadNonEmptyPartial || heardSpeech {
                finalizeIfAllowed(now: now, reason: "max-length")
            }
        }
    }

    /// Seconds of quiet (and stable text) required before this partial may be sent.
    func holdNeeded(for text: String) -> TimeInterval {
        looksIncomplete(text) ? silenceSeconds + incompleteHoldExtra : silenceSeconds
    }

    /// True when the partial likely still has more speech coming (mid-word or continuer).
    static func looksIncomplete(_ text: String) -> Bool {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return false }
        if let last = t.last, ".!?…".contains(last) { return false }
        let lastWord = t.split(whereSeparator: \.isWhitespace).last.map(String.init) ?? ""
        let trail = lastWord.lowercased().trimmingCharacters(in: CharacterSet.alphanumerics.inverted)
        guard !trail.isEmpty else { return false }
        let continuers: Set<String> = [
            "and", "or", "but", "so", "because", "that", "the", "a", "an", "to", "for",
            "with", "if", "when", "while", "about", "like", "also", "then", "than",
            "of", "in", "on", "at", "from", "into", "just", "really", "very", "my",
            "your", "our", "its", "as", "by", "we", "i", "i'm", "it's", "there's",
        ]
        if continuers.contains(trail) { return true }
        // Apple often emits a stem ("tha", "lik", "talki") while the rest of the word
        // is still being said. Extra hold only; false positives just wait ~0.85 s more.
        if trail.count <= 5 { return true }
        return false
    }

    private func looksIncomplete(_ text: String) -> Bool {
        Self.looksIncomplete(text)
    }

    private func recomputeGates() {
        speechGateDb = min(absoluteSpeechCeilDb, max(absoluteSpeechFloorDb, noiseFloorDb + speechMarginDb))
        silenceGateDb = min(speechGateDb - 1, noiseFloorDb + hangoverMarginDb)
    }

    private func finalizeIfAllowed(now: Date, reason: String) {
        guard now.timeIntervalSince(lastFinalizeAt) >= minFinalizeInterval else { return }
        guard hadNonEmptyPartial || heardSpeech else { return }

        lastFinalizeAt = now
        heardSpeech = false
        speechStartedAt = nil
        silenceStartedAt = nil
        peakSpeechDb = -80
        silenceCountdown = nil
        isSpeech = false
        phase = .quiet
        lastPartial = ""
        lastPartialChangeAt = nil
        hadNonEmptyPartial = false
        print("[vad] finalize reason=\(reason)")
        onUtteranceEnded?()
    }

    var statusLine: String {
        switch phase {
        case .calibrating:
            return String(format: "Calibrating… floor %.0f dB", noiseFloorDb)
        case .quiet:
            return String(format: "Quiet · floor %.0f · gate %.0f dB", noiseFloorDb, speechGateDb)
        case .speech:
            return String(format: "Speech · %.0f dB", currentDb)
        case .trailing:
            let left = silenceCountdown ?? 0
            return String(format: "Closing in %.1fs…", left)
        }
    }
}

#if VAD_SELFTEST
@main
enum VadSelfTest {
    static func main() {
        var failures: [String] = []
        func expect(_ ok: Bool, _ name: String) {
            if !ok { failures.append(name) }
        }
        expect(AdaptiveSilenceDetector.looksIncomplete("tell me the status of tha"), "mid-word tha")
        expect(AdaptiveSilenceDetector.looksIncomplete("some people lik"), "mid-word lik")
        expect(AdaptiveSilenceDetector.looksIncomplete("before we go into talki"), "mid-word talki")
        expect(AdaptiveSilenceDetector.looksIncomplete("check marketing and"), "continuer and")
        expect(AdaptiveSilenceDetector.looksIncomplete("open the"), "continuer the")
        expect(!AdaptiveSilenceDetector.looksIncomplete("Can you hear me?"), "complete question")
        expect(!AdaptiveSilenceDetector.looksIncomplete("Open the calculator."), "complete sentence")
        expect(!AdaptiveSilenceDetector.looksIncomplete("Hello"), "single full word")
        if failures.isEmpty {
            print("ok")
        } else {
            for f in failures { print("FAIL \(f)") }
            exit(1)
        }
    }
}
#endif
