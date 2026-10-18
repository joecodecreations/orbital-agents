import Foundation

/// Adaptive speech gate: drops non-speech energy before it hits STT.
@MainActor
final class SpeechNoiseGate: ObservableObject {
    @Published private(set) var isOpen = false
    @Published private(set) var noiseFloorDb: Float = -55
    @Published private(set) var openGateDb: Float = -42
    @Published private(set) var statusLabel: String = "Gate idle"

    var openMarginDb: Float = 9
    var holdMarginDb: Float = 5
    var openHoldFrames: Int = 2
    var closeHoldFrames: Int = 6

    private var openCount = 0
    private var closeCount = 0
    private var samples = 0
    private var enabled = true

    func reset() {
        isOpen = false
        noiseFloorDb = -55
        openGateDb = -42
        openCount = 0
        closeCount = 0
        samples = 0
        statusLabel = enabled ? "Calibrating gate…" : "Gate off"
    }

    func setEnabled(_ on: Bool) {
        enabled = on
        if !on {
            isOpen = true
            statusLabel = "Gate off · all audio"
        } else {
            reset()
        }
    }

    @discardableResult
    func shouldPass(db: Float) -> Bool {
        guard enabled else {
            isOpen = true
            return true
        }

        let sample = max(-90, min(0, db))
        samples += 1

        if sample < noiseFloorDb + 3 || samples < 20 {
            if samples < 8 {
                noiseFloorDb = sample
            } else if sample < noiseFloorDb {
                noiseFloorDb = noiseFloorDb * 0.4 + sample * 0.6
            } else {
                noiseFloorDb = noiseFloorDb * 0.95 + sample * 0.05
            }
        }
        openGateDb = min(-12, max(-55, noiseFloorDb + openMarginDb))
        let holdGate = min(openGateDb - 1, noiseFloorDb + holdMarginDb)

        let speechLike = isOpen ? (sample > holdGate) : (sample > openGateDb)

        if speechLike {
            openCount += 1
            closeCount = 0
            if openCount >= openHoldFrames {
                isOpen = true
            }
        } else {
            closeCount += 1
            openCount = 0
            if closeCount >= closeHoldFrames {
                isOpen = false
            }
        }

        if isOpen {
            statusLabel = String(format: "Speech · floor %.0f · open >%.0f dB", noiseFloorDb, openGateDb)
        } else {
            statusLabel = String(format: "Noise blocked · floor %.0f dB", noiseFloorDb)
        }
        return isOpen
    }
}
