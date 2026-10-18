import AVFoundation
import AudioToolbox
import Foundation

/// Extra loudness for spoken replies on the built-in speaker.
///
/// The phone-speaker route used to play on the call curve (`.voiceChat`), which
/// stays quiet in a car even when the side buttons are up. That route is now the
/// louder speakerphone curve. This peak limiter adds the decibels from the
/// speaker-volume control on top, then catches the peaks so the boost does not
/// turn into crackle.
///
/// Headphones, glasses, CarPlay and any other external route get no boost, so a
/// high setting cannot blast them.
@MainActor
final class SpeakerBoost {
    static let shared = SpeakerBoost()

    static let minDb: Double = 0
    static let maxDb: Double = 18
    /// Loud enough to hear while walking or in a car, with room left on the slider.
    static let defaultDb: Double = 10

    private let units = NSHashTable<AVAudioUnitEffect>.weakObjects()
    private var lastLogged = ""

    private init() {}

    /// Every current output is the iPhone speaker. An empty route is not.
    /// The Mac app is left alone: this boost is for the phone in a car or a pocket.
    static func routeIsBuiltInSpeaker(_ session: AVAudioSession = .sharedInstance()) -> Bool {
        #if targetEnvironment(macCatalyst)
        return false
        #else
        let outs = session.currentRoute.outputs
        guard !outs.isEmpty else { return false }
        return outs.allSatisfy { $0.portType == .builtInSpeaker }
        #endif
    }

    /// Decibels to add right now. Zero whenever the sound is leaving the device.
    static func effectiveDb() -> Float {
        guard routeIsBuiltInSpeaker() else { return 0 }
        return Float(SpeechSettings.shared.speakerBoostDb)
    }

    /// Player → peak limiter → mixer. The limiter is bypassed when `effectiveDb()` is 0.
    func install(on engine: AVAudioEngine, after player: AVAudioPlayerNode, format: AVAudioFormat) {
        let description = AudioComponentDescription(
            componentType: kAudioUnitType_Effect,
            componentSubType: kAudioUnitSubType_PeakLimiter,
            componentManufacturer: kAudioUnitManufacturer_Apple,
            componentFlags: 0,
            componentFlagsMask: 0
        )
        guard !AVAudioUnitComponentManager.shared().components(matching: description).isEmpty else {
            engine.connect(player, to: engine.mainMixerNode, format: format)
            VoiceDebug.shared.log("speaker boost: peak limiter missing, playing at unity")
            return
        }
        let limiter = AVAudioUnitEffect(audioComponentDescription: description)
        engine.attach(limiter)
        engine.connect(player, to: limiter, format: format)
        engine.connect(limiter, to: engine.mainMixerNode, format: format)
        units.add(limiter)
        apply(limiter, db: Self.effectiveDb())
    }

    /// Call after a route change, a slider move, or an engine start.
    func refresh() {
        let db = Self.effectiveDb()
        let outs = AVAudioSession.sharedInstance().currentRoute.outputs
            .map { "\($0.portType.rawValue)" }
            .joined(separator: "+")
        let key = "\(db)|\(outs)"
        if key != lastLogged {
            lastLogged = key
            let gain = db < 0.5 ? "off" : "+\(Int(db.rounded()))dB"
            VoiceDebug.shared.log("speaker boost \(gain) outs=\(outs.isEmpty ? "none" : outs)")
        }
        for limiter in units.allObjects {
            apply(limiter, db: db)
        }
    }

    private func apply(_ limiter: AVAudioUnitEffect, db: Float) {
        let clamped = min(Float(Self.maxDb), max(0, db))
        if let pre = limiter.auAudioUnit.parameterTree?
            .parameter(withAddress: AUParameterAddress(kLimiterParam_PreGain)) {
            pre.value = clamped
        } else {
            AudioUnitSetParameter(
                limiter.audioUnit,
                kLimiterParam_PreGain,
                kAudioUnitScope_Global,
                0,
                clamped,
                0
            )
        }
        // Unity on headphones and when you slide all the way down.
        limiter.auAudioUnit.shouldBypassEffect = clamped < 0.5
    }
}
