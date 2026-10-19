import XCTest
@testable import OrbitalAgents

/// End-of-turn rules for hands-free desk voice: do not cut people off mid-word.
@MainActor
final class VoiceSilenceTests: XCTestCase {
    func testLooksIncompleteMidWord() {
        XCTAssertTrue(AdaptiveSilenceDetector.looksIncomplete("tell me the status of tha"))
        XCTAssertTrue(AdaptiveSilenceDetector.looksIncomplete("some people lik"))
        XCTAssertTrue(AdaptiveSilenceDetector.looksIncomplete("before we go into talki"))
    }

    func testLooksIncompleteContinuers() {
        XCTAssertTrue(AdaptiveSilenceDetector.looksIncomplete("check marketing and"))
        XCTAssertTrue(AdaptiveSilenceDetector.looksIncomplete("open the"))
        XCTAssertTrue(AdaptiveSilenceDetector.looksIncomplete("because it seems like"))
    }

    func testLooksCompleteFinishedPhrases() {
        XCTAssertFalse(AdaptiveSilenceDetector.looksIncomplete("Can you hear me?"))
        XCTAssertFalse(AdaptiveSilenceDetector.looksIncomplete("Open the calculator."))
        XCTAssertFalse(AdaptiveSilenceDetector.looksIncomplete("Open up the calculator on the Mac Mini."))
        XCTAssertFalse(AdaptiveSilenceDetector.looksIncomplete("Please restart the satellite service."))
        // Short unpunctuated words still take the extra hold; that only delays send a little.
        XCTAssertTrue(AdaptiveSilenceDetector.looksIncomplete("Hello"))
    }

    func testHoldNeededAddsExtraForIncomplete() {
        let vad = AdaptiveSilenceDetector()
        vad.silenceSeconds = 2.0
        vad.incompleteHoldExtra = 0.85
        XCTAssertEqual(vad.holdNeeded(for: "status of tha"), 2.85, accuracy: 0.001)
        XCTAssertEqual(vad.holdNeeded(for: "Can you hear me?"), 2.0, accuracy: 0.001)
    }

    func testTextStableDoesNotFinalizeWhileSpeechEnergyPresent() async throws {
        let vad = AdaptiveSilenceDetector()
        var ended = 0
        vad.onUtteranceEnded = { ended += 1 }
        vad.reset(silenceSeconds: 0.5, maxUtteranceSeconds: 90)
        // Finish calibration quickly with quiet samples.
        for _ in 0..<12 {
            vad.process(db: -55)
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        // Speech + a stalled partial (Apple holding the same words mid-sentence).
        vad.process(db: -25)
        vad.notePartialText("tell me the status of tha")
        let start = Date()
        while Date().timeIntervalSince(start) < 0.7 {
            vad.process(db: -25)
            vad.notePartialText("tell me the status of tha")
            try await Task.sleep(nanoseconds: 40_000_000)
        }
        XCTAssertEqual(ended, 0, "must not finalize on text-stable while mic still hears speech")
        // Quiet long enough with the same partial → may finalize.
        let quietStart = Date()
        while Date().timeIntervalSince(quietStart) < 1.6 {
            vad.process(db: -60)
            vad.notePartialText("tell me the status of tha")
            try await Task.sleep(nanoseconds: 40_000_000)
        }
        XCTAssertEqual(ended, 1, "finalize after real silence and stable text")
    }
}
