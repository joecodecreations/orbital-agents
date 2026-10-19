import XCTest
@testable import OrbitalAgents

/// Talk-button ready cue: rising chime only after the mic is actually listening.
@MainActor
final class VoiceReadyCueTests: XCTestCase {
    func testMicOnEarconIsRisingBrightChime() {
        let on = PauseCuePlayer.buffer(for: .micOn, sampleRate: 22_050)
        let off = PauseCuePlayer.buffer(for: .micOff, sampleRate: 22_050)
        XCTAssertNotNil(on)
        XCTAssertNotNil(off)
        guard let on, let off else { return }
        // Ready cue is a short rising chime; stop cue is longer and lower.
        XCTAssertGreaterThan(on.frameLength, 0)
        XCTAssertGreaterThan(off.frameLength, on.frameLength)
        let onSecs = PauseCuePlayer.earconSeconds(.micOn)
        let offSecs = PauseCuePlayer.earconSeconds(.micOff)
        XCTAssertGreaterThan(onSecs, 0.2)
        XCTAssertLessThan(onSecs, 0.8)
        XCTAssertGreaterThan(offSecs, onSecs)
    }

    func testWaitForFirstBufferFailsWhenCaptureNotRunning() async {
        let capture = AudioCaptureService()
        XCTAssertFalse(capture.isRunning)
        XCTAssertFalse(capture.hasMicAudio)
        let start = Date()
        let ok = await capture.waitForFirstBuffer(timeout: 0.25)
        XCTAssertFalse(ok)
        XCTAssertLessThan(Date().timeIntervalSince(start), 1.0)
    }

    func testReadyEarconShorterThanStopEarcon() {
        // Chime must finish before STT unmute; keep it short so talk feels snappy.
        let onSecs = PauseCuePlayer.earconSeconds(.micOn)
        let offSecs = PauseCuePlayer.earconSeconds(.micOff)
        XCTAssertLessThanOrEqual(onSecs, 0.75)
        XCTAssertGreaterThan(offSecs, 0.4)
    }
}
