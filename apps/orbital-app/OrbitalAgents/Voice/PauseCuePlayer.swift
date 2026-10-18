import AVFoundation
import Foundation

/// Short local cue when listening starts or stops.
///
/// The two listen-state chimes are always tones, never spoken words. A word
/// played as the mic opens is transcribed and sent as your next turn.
/// Listening-on is a bright rising chime. Listening-off is a low falling tone.
/// They are far apart in pitch so they still differ on the glasses speakers.
@MainActor
final class PauseCuePlayer: NSObject, AVSpeechSynthesizerDelegate {
    private let settings = SpeechSettings.shared
    private var player: AVAudioPlayer?
    private let synth = AVSpeechSynthesizer()

    override init() {
        super.init()
        synth.delegate = self
    }

    enum Cue {
        case micOff, micOn, ttsOff, ttsOn
    }

    func play(_ cue: Cue) {
        switch cue {
        case .micOn, .micOff:
            // Listen start / stop is always the chime, even if the mute cue is spoken or off.
            playEarcon(cue)
        case .ttsOn, .ttsOff:
            try? AudioCaptureService.applySharedSession(
                preferGlassesOutput: AudioCaptureService.preferGlassesOutputNow()
            )
            switch settings.pauseCueStyle {
            case .off:
                return
            case .tones:
                playTone(cue)
            case .spoken:
                speakWords(cue)
            }
        }
    }

    /// Rising chime or low tone. Ignores the spoken / off setting.
    func playEarcon(_ cue: Cue) {
        try? AudioCaptureService.applySharedSession(
            preferGlassesOutput: AudioCaptureService.preferGlassesOutputNow()
        )
        playTone(cue)
    }

    /// How long to keep the mic out of speech-to-text so the chime is not a user turn.
    static func earconSeconds(_ cue: Cue) -> TimeInterval {
        guard let buffer = buffer(for: cue) else { return 0.5 }
        return Double(buffer.frameLength) / buffer.format.sampleRate + 0.12
    }

    /// Float buffer for playback through the voice-processing engine.
    static func buffer(for cue: Cue, sampleRate: Double = 22_050) -> AVAudioPCMBuffer? {
        let notes = notes(for: cue)
        var samples: [Float] = []
        for (i, note) in notes.enumerated() {
            if i > 0 {
                samples.append(contentsOf: repeatElement(0, count: Int(sampleRate * 0.045)))
            }
            let n = max(1, Int(sampleRate * Double(note.ms) / 1000))
            let bright = cue == .micOn || cue == .ttsOn
            for t in 0..<n {
                let x = Double(t) / sampleRate
                let env = envelope(t: t, n: n)
                let w = 2.0 * Double.pi * note.freq * x
                let s = sin(w) * (bright ? 0.70 : 0.92) + (bright ? sin(2 * w) * 0.24 : 0)
                samples.append(Float(max(-1, min(1, s * env))))
            }
        }
        guard let format = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: sampleRate,
            channels: 1,
            interleaved: false
        ), let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(samples.count)) else {
            return nil
        }
        buffer.frameLength = AVAudioFrameCount(samples.count)
        if let dst = buffer.floatChannelData?[0] {
            for i in 0..<samples.count { dst[i] = samples[i] }
        }
        return buffer
    }

    /// Preview from Voice settings. Same two sounds the glasses play.
    func play(paused: Bool) {
        playEarcon(paused ? .micOff : .micOn)
    }

    /// Short spoken confirm for hands-free tab commands.
    func announce(_ text: String) {
        try? AudioCaptureService.applySharedSession(
            preferGlassesOutput: AudioCaptureService.preferGlassesOutputNow()
        )
        stop()
        let u = AVSpeechUtterance(string: text)
        u.voice = AVSpeechSynthesisVoice(language: settings.speechLanguage.bcp47)
        u.rate = AVSpeechUtteranceDefaultSpeechRate * 1.08
        u.volume = 1.0
        synth.speak(u)
    }

    private static func notes(for cue: Cue) -> [(freq: Double, ms: Int)] {
        switch cue {
        case .micOn: return [(988, 80), (1319, 80), (1760, 190)]
        case .micOff: return [(311, 200), (155, 320)]
        case .ttsOff: return [(700, 70), (700, 70)]
        case .ttsOn: return [(980, 140)]
        }
    }

    private func playTone(_ cue: Cue) {
        stop()
        guard let buffer = Self.buffer(for: cue), let data = Self.wav(from: buffer) else { return }
        do {
            let p = try AVAudioPlayer(data: data)
            p.volume = 1.0
            p.prepareToPlay()
            p.play()
            player = p
        } catch {
            speakWords(cue)
        }
    }

    private func speakWords(_ cue: Cue) {
        stop()
        let lang = settings.speechLanguage
        let text: String
        switch cue {
        case .micOff, .micOn:
            text = settings.pauseCueWords.phrase(paused: cue == .micOff, language: lang)
        case .ttsOff:
            text = PauseCueWords.audioOn.phrase(paused: true, language: lang)
        case .ttsOn:
            text = PauseCueWords.audioOn.phrase(paused: false, language: lang)
        }
        let u = AVSpeechUtterance(string: text)
        u.voice = AVSpeechSynthesisVoice(language: lang.bcp47)
        u.rate = AVSpeechUtteranceDefaultSpeechRate * 1.05
        u.volume = 1.0
        synth.speak(u)
    }

    func stop() {
        player?.stop()
        player = nil
        if synth.isSpeaking {
            synth.stopSpeaking(at: .immediate)
        }
    }

    private static func wav(from buffer: AVAudioPCMBuffer) -> Data? {
        guard let ch = buffer.floatChannelData?[0] else { return nil }
        let n = Int(buffer.frameLength)
        var samples: [Int16] = []
        samples.reserveCapacity(n)
        for i in 0..<n {
            let f = max(-1, min(1, ch[i]))
            samples.append(Int16(f * Float(Int16.max)))
        }
        return pcm16Wav(samples: samples, sampleRate: Int(buffer.format.sampleRate))
    }

    private static func envelope(t: Int, n: Int) -> Double {
        let attack = max(1, n / 12)
        let release = max(1, n / 5)
        if t < attack { return Double(t) / Double(attack) }
        if t > n - release { return Double(n - t) / Double(release) }
        return 1
    }

    private static func pcm16Wav(samples: [Int16], sampleRate: Int) -> Data {
        let dataSize = samples.count * 2
        var data = Data()
        data.reserveCapacity(44 + dataSize)
        func ascii(_ s: String) { data.append(contentsOf: s.utf8) }
        func u32(_ v: UInt32) {
            var le = v.littleEndian
            withUnsafeBytes(of: &le) { data.append(contentsOf: $0) }
        }
        func u16(_ v: UInt16) {
            var le = v.littleEndian
            withUnsafeBytes(of: &le) { data.append(contentsOf: $0) }
        }
        ascii("RIFF")
        u32(UInt32(36 + dataSize))
        ascii("WAVE")
        ascii("fmt ")
        u32(16)
        u16(1)
        u16(1)
        u32(UInt32(sampleRate))
        u32(UInt32(sampleRate * 2))
        u16(2)
        u16(16)
        ascii("data")
        u32(UInt32(dataSize))
        for s in samples {
            var le = s.littleEndian
            withUnsafeBytes(of: &le) { data.append(contentsOf: $0) }
        }
        return data
    }
}
