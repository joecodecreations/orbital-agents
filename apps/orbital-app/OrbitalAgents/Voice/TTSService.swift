import AVFoundation
import Foundation

/// Text-to-speech with **parallel synthesis** + **serial playback**.
@MainActor
final class TTSService: NSObject, ObservableObject {
    @Published var isSpeaking = false

    private let settings = SpeechSettings.shared
    private var player: AVAudioPlayer?
    private let synth = AVSpeechSynthesizer()
    private var appleSpeakContinuation: CheckedContinuation<Void, Never>?
    private var filePlayContinuation: CheckedContinuation<Void, Never>?

    /// Fish through the hub (its FISH_API_KEY). Args: text, voiceId, speed, model.
    var hubSynthesize: ((String, String, Double, String) async throws -> Data)?
    /// The same Fish voice, played while the hub is still making it. Args as above.
    var hubStream: (@MainActor (String, String, Double, String) async throws -> SpeechStream)?
    /// The hub couldn't stream (an older hub, Fish refused): use whole clips until then.
    private var streamPausedUntil = Date.distantPast
    /// How the last Fish clip was made ("Fish stream via hub · Narrator").
    @Published private(set) var lastRoute = ""
    /// Why the last reply fell back to Apple's voice; nil once Fish works again.
    @Published private(set) var lastIssue: String?
    /// Told when replies start falling back to Apple's voice (once per distinct reason).
    var onFallback: ((String) -> Void)?
    /// The mic engine. While it runs, clips play through it so its echo canceller removes them
    /// from the mic (you can talk over a reply). Otherwise the plain players below are used.
    weak var duplex: AudioCaptureService?
    /// Writes Apple speech to buffers for the engine.
    private let bufferSynth = AVSpeechSynthesizer()
    /// Plays a streamed clip when the mic engine is off (typed chat with the speaker on).
    private var streamEngine: AVAudioEngine?
    private var streamToken = 0
    private var streamContinuation: CheckedContinuation<Void, Never>?

    /// How much of the current clip has played, 0...1.
    var playbackFraction: Double {
        if let duplex, duplex.isPlaying { return duplex.playbackFraction }
        if let player, player.duration > 0 { return min(1, player.currentTime / player.duration) }
        return 0
    }

    struct PreparedClip: Sendable {
        enum Kind: Sendable {
            case file(URL)
            case appleUtterance(String)
            /// Fish audio still arriving from the hub.
            case stream(SpeechStream)
        }
        let kind: Kind
        let language: SpeechLanguage
        let text: String
    }

    override init() {
        super.init()
        synth.delegate = self
    }

    func synthesize(_ text: String, language: SpeechLanguage, force: Bool = false) async throws -> PreparedClip {
        let cleaned = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleaned.isEmpty else { throw TTSError.http("empty text") }
        guard force || settings.ttsEnabled else { throw TTSError.http("TTS disabled") }

        switch settings.ttsProvider {
        case .fishAudio:
            let spoken = settings.fishEmotionTagsEnabled ? cleaned : Self.stripFishEmotionTags(cleaned)
            if let stream = await openFishStream(spoken, language: language) {
                return PreparedClip(kind: .stream(stream), language: language, text: spoken)
            }
            let url = try await fetchFish(spoken, language: language)
            return PreparedClip(kind: .file(url), language: language, text: spoken)
        case .azureSpeech:
            let plain = Self.stripFishEmotionTags(cleaned)
            let url = try await fetchAzureAudio(plain, language: language)
            return PreparedClip(kind: .file(url), language: language, text: plain)
        case .appleOnDevice:
            let plain = Self.stripFishEmotionTags(cleaned)
            return PreparedClip(kind: .appleUtterance(plain), language: language, text: plain)
        }
    }

    func playPrepared(_ clip: PreparedClip) async {
        stopPlaybackOnly()
        try? configurePlaybackSession()
        // Silent keep-alive holds Now Playing; pause it so TTS isn't cut short.
        NotificationCenter.default.post(name: .agentTTSWillPlay, object: nil)
        defer {
            // Played or cut: either way nothing more of it is needed.
            if case .stream(let stream) = clip.kind { stream.cancel() }
        }
        if let duplex, duplex.canPlay, await playThroughEngine(clip, duplex) {
            NotificationCenter.default.post(name: .agentTTSDidFinish, object: nil)
            return
        }
        switch clip.kind {
        case .file(let url):
            await playFile(url)
        case .appleUtterance(let text):
            await playApple(text, language: clip.language)
        case .stream(let stream):
            if !(await playStream(stream)) { await playApple(clip.text, language: clip.language) }
        }
        NotificationCenter.default.post(name: .agentTTSDidFinish, object: nil)
    }

    func speak(_ text: String, language: SpeechLanguage, force: Bool = false) async {
        do {
            let clip = try await synthesize(text, language: language, force: force)
            await playPrepared(clip)
        } catch {
            await playApple(text, language: language)
        }
    }

    func playSample(language: SpeechLanguage) async {
        let voiceName = settings.voiceName(for: settings.voiceId(for: language))
        let text: String
        switch language {
        case .english:
            text = "Hi, this is the \(voiceName) voice. This is how loud I am on this speaker."
        case .portuguese:
            text = "Oi, eu sou a voz \(voiceName). Este é o volume neste alto-falante."
        }
        await speak(text, language: language, force: true)
    }

    func stop() {
        stopPlaybackOnly()
    }

    private func playThroughEngine(_ clip: PreparedClip, _ duplex: AudioCaptureService) async -> Bool {
        isSpeaking = true
        defer { isSpeaking = false }
        switch clip.kind {
        case .file(let url):
            return await duplex.play(fileURL: url)
        case .appleUtterance(let text):
            let buffers = await appleBuffers(text, language: clip.language)
            return await duplex.play(buffers: buffers)
        case .stream(let stream):
            return await duplex.play(stream: stream)
        }
    }

    /// A streamed clip on its own engine, for when the mic engine is off. False if it can't start.
    private func playStream(_ stream: SpeechStream) async -> Bool {
        let engine = AVAudioEngine()
        let node = AVAudioPlayerNode()
        engine.attach(node)
        SpeakerBoost.shared.install(on: engine, after: node, format: stream.format)
        do {
            try engine.start()
        } catch {
            VoiceDebug.shared.log("tts stream player failed to start: \(error.localizedDescription)")
            return false
        }
        SpeakerBoost.shared.refresh()
        streamEngine = engine
        streamToken += 1
        let token = streamToken
        isSpeaking = true
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            streamContinuation = cont
            Task { @MainActor [weak self] in
                await stream.schedule(
                    on: node,
                    format: stream.format,
                    lead: SpeechStream.lead,
                    isCurrent: { [weak self] in self?.streamToken == token },
                    added: { _ in },
                    ended: { [weak self] in self?.finishStream(token) }
                )
            }
        }
        node.stop()
        engine.stop()
        if streamEngine === engine { streamEngine = nil }
        isSpeaking = false
        return true
    }

    /// Whole clip on the speaker engine, so the peak limiter can raise it.
    /// False if the file or the engine cannot start; the caller uses AVAudioPlayer.
    private func playFileOnEngine(_ url: URL) async -> Bool {
        guard let file = try? AVAudioFile(forReading: url), file.length > 0 else { return false }
        let engine = AVAudioEngine()
        let node = AVAudioPlayerNode()
        engine.attach(node)
        SpeakerBoost.shared.install(on: engine, after: node, format: file.processingFormat)
        do {
            try engine.start()
        } catch {
            VoiceDebug.shared.log("speaker file player failed: \(error.localizedDescription)")
            return false
        }
        SpeakerBoost.shared.refresh()
        streamEngine = engine
        streamToken += 1
        let token = streamToken
        if !node.isPlaying { node.play() }
        isSpeaking = true
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            streamContinuation = cont
            node.scheduleFile(file, at: nil, completionCallbackType: .dataPlayedBack) { _ in
                Task { @MainActor [weak self] in self?.finishStream(token) }
            }
        }
        node.stop()
        engine.stop()
        if streamEngine === engine { streamEngine = nil }
        isSpeaking = false
        return true
    }

    /// Apple speech buffers through the same limiter, for the built-in speaker.
    private func playBuffersOnEngine(_ buffers: [AVAudioPCMBuffer]) async -> Bool {
        guard let first = buffers.first, first.frameLength > 0,
              let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: first.format.sampleRate, channels: 1, interleaved: false)
        else { return false }
        let converted = buffers.compactMap { AudioCaptureService.convert($0, to: format) }
        guard !converted.isEmpty else { return false }
        let engine = AVAudioEngine()
        let node = AVAudioPlayerNode()
        engine.attach(node)
        SpeakerBoost.shared.install(on: engine, after: node, format: format)
        do {
            try engine.start()
        } catch {
            VoiceDebug.shared.log("speaker buffer player failed: \(error.localizedDescription)")
            return false
        }
        SpeakerBoost.shared.refresh()
        streamEngine = engine
        streamToken += 1
        let token = streamToken
        if !node.isPlaying { node.play() }
        isSpeaking = true
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            streamContinuation = cont
            for (index, buffer) in converted.enumerated() {
                let last = index == converted.count - 1
                node.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { _ in
                    guard last else { return }
                    Task { @MainActor [weak self] in self?.finishStream(token) }
                }
            }
        }
        node.stop()
        engine.stop()
        if streamEngine === engine { streamEngine = nil }
        isSpeaking = false
        return true
    }

    private func finishStream(_ token: Int) {
        guard token == streamToken, let cont = streamContinuation else { return }
        streamContinuation = nil
        cont.resume()
    }

    private func appleBuffers(_ text: String, language: SpeechLanguage) async -> [AVAudioPCMBuffer] {
        let utterance = appleUtterance(text, language: language)
        let box = SpeechBufferBox()
        return await withCheckedContinuation { (cont: CheckedContinuation<[AVAudioPCMBuffer], Never>) in
            box.continuation = cont
            bufferSynth.write(utterance) { buffer in
                if let pcm = buffer as? AVAudioPCMBuffer, pcm.frameLength > 0 {
                    box.append(pcm)
                } else {
                    box.finish()
                }
            }
            // The synthesizer doesn't always send its final empty buffer (e.g. when stopped).
            DispatchQueue.main.asyncAfter(deadline: .now() + 4 + Double(text.count) / 40) { box.finish() }
        }
    }

    private func appleUtterance(_ text: String, language: SpeechLanguage) -> AVSpeechUtterance {
        let u = AVSpeechUtterance(string: text)
        u.voice = AVSpeechSynthesisVoice(language: language.bcp47)
            ?? AVSpeechSynthesisVoice(language: language == .portuguese ? "pt-BR" : "en-US")
        let mult = max(0.5, min(1.5, settings.appleTTSSpeed))
        let raw = AVSpeechUtteranceDefaultSpeechRate * Float(mult)
        u.rate = min(AVSpeechUtteranceMaximumSpeechRate, max(AVSpeechUtteranceMinimumSpeechRate, raw))
        return u
    }

    private func stopPlaybackOnly() {
        duplex?.stopPlayback()
        streamToken += 1
        streamEngine?.stop()
        streamEngine = nil
        if let cont = streamContinuation {
            streamContinuation = nil
            cont.resume()
        }
        if bufferSynth.isSpeaking { bufferSynth.stopSpeaking(at: .immediate) }
        player?.stop()
        player = nil
        if synth.isSpeaking { synth.stopSpeaking(at: .immediate) }
        if let cont = appleSpeakContinuation {
            appleSpeakContinuation = nil
            cont.resume()
        }
        if let cont = filePlayContinuation {
            filePlayContinuation = nil
            cont.resume()
        }
        isSpeaking = false
    }

    /// Fish through the hub as a stream, once the hub has answered with audio.
    /// nil means use a whole clip instead: the hub is older than streaming, or Fish refused
    /// (the whole-clip path then reports why).
    private func openFishStream(_ text: String, language: SpeechLanguage) async -> SpeechStream? {
        guard let hubStream, Date() >= streamPausedUntil else { return nil }
        let voice = settings.voiceName(for: settings.voiceId(for: language))
        do {
            let speed = max(0.5, min(2.0, settings.fishTTSSpeed))
            let stream = try await hubStream(text, settings.voiceId(for: language), speed, settings.fishModel)
            noteRoute("Fish stream via hub · \(voice)")
            return stream
        } catch {
            streamPausedUntil = Date().addingTimeInterval(60)
            VoiceDebug.shared.log("tts stream unavailable, whole clips for a minute: \(error.localizedDescription)")
            return nil
        }
    }

    /// A Fish voice clip made by the hub with its key. If that fails the reply falls back to the
    /// built-in voice, and the reason is logged and shown instead of being swallowed.
    private func fetchFish(_ text: String, language: SpeechLanguage) async throws -> URL {
        let voice = settings.voiceName(for: settings.voiceId(for: language))
        let why: String
        if let hubSynthesize {
            do {
                let speed = max(0.5, min(2.0, settings.fishTTSSpeed))
                let data = try await hubSynthesize(text, settings.voiceId(for: language), speed, settings.fishModel)
                let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("orbital-tts-\(UUID().uuidString).mp3")
                try data.write(to: tmp)
                noteRoute("Fish via hub · \(voice)")
                return tmp
            } catch {
                why = "hub: \((error as? OrbitalAPIError)?.shortReason ?? error.localizedDescription)"
            }
        } else {
            why = "not signed in to a hub"
        }
        VoiceDebug.shared.log("tts fish failed, built-in voice instead: \(why)")
        if lastIssue != why { onFallback?(why) }
        lastIssue = why
        throw TTSError.http(why)
    }

    private func noteRoute(_ route: String) {
        if route != lastRoute || lastIssue != nil {
            VoiceDebug.shared.log("tts \(route)")
        }
        lastRoute = route
        lastIssue = nil
    }

    private func fetchAzureAudio(_ text: String, language: SpeechLanguage) async throws -> URL {
        let key = settings.azureSpeechKey
        let region = settings.azureSpeechRegion
        guard !key.isEmpty else { throw TTSError.missingKey("Azure") }

        let voice = language == .portuguese ? "pt-BR-FranciscaNeural" : "en-US-JennyNeural"
        let pct = Int((max(0.5, min(2.0, settings.azureTTSSpeed)) * 100).rounded())
        let ssml = """
        <speak version='1.0' xml:lang='\(language.bcp47)'>
          <voice name='\(voice)'>
            <prosody rate="\(pct)%">\(xmlEscape(text))</prosody>
          </voice>
        </speak>
        """
        let url = URL(string: "https://\(region).tts.speech.microsoft.com/cognitiveservices/v1")!
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue(key, forHTTPHeaderField: "Ocp-Apim-Subscription-Key")
        req.setValue("application/ssml+xml", forHTTPHeaderField: "Content-Type")
        req.setValue("audio-16khz-128kbitrate-mono-mp3", forHTTPHeaderField: "X-Microsoft-OutputFormat")
        req.httpBody = Data(ssml.utf8)
        req.timeoutInterval = 30

        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw TTSError.http(String(data: data, encoding: .utf8) ?? "Azure TTS failed")
        }
        let tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("orbital-az-\(UUID().uuidString).mp3")
        try data.write(to: tmp)
        return tmp
    }

    private func playFile(_ url: URL) async {
        if SpeakerBoost.routeIsBuiltInSpeaker(), await playFileOnEngine(url) { return }
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            do {
                let p = try AVAudioPlayer(contentsOf: url)
                p.delegate = self
                p.prepareToPlay()
                isSpeaking = true
                player = p
                self.filePlayContinuation = cont
                if !p.play() {
                    self.filePlayContinuation = nil
                    isSpeaking = false
                    cont.resume()
                }
            } catch {
                isSpeaking = false
                cont.resume()
            }
        }
    }

    private func playApple(_ text: String, language: SpeechLanguage) async {
        try? configurePlaybackSession()
        if SpeakerBoost.routeIsBuiltInSpeaker() {
            let buffers = await appleBuffers(text, language: language)
            if await playBuffersOnEngine(buffers) { return }
        }
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            appleSpeakContinuation = cont
            isSpeaking = true
            synth.speak(appleUtterance(text, language: language))
        }
    }

    private func configurePlaybackSession() throws {
        // Continuous talk, including a muted mic and a locked phone, stays on
        // play-and-record. Switching to spoken-audio playback here would drop
        // the glasses mic, and iOS will not let us start it again from the lock screen.
        if duplex?.maintainsDuplexSession == true {
            duplex?.ensureEngineRunning()
            try AudioCaptureService.applySharedSession(
                preferGlassesOutput: AudioCaptureService.preferGlassesOutputNow()
            )
            return
        }
        // While the mic engine is up, stay on its route so echo cancellation
        // still hears the reply. With the mic closed, the phone speaker uses
        // plain playback (full side-button volume) and headphones stay on
        // spoken-audio, off the phone-call profile that rides the volume.
        if duplex?.isRunning == true {
            try AudioCaptureService.applySharedSession(
                preferGlassesOutput: AudioCaptureService.preferGlassesOutputNow()
            )
            return
        }
        try AudioCaptureService.applySpokenPlaybackSession()
    }

    private func xmlEscape(_ s: String) -> String {
        s.replacingOccurrences(of: "&", with: "&amp;")
            .replacingOccurrences(of: "<", with: "&lt;")
            .replacingOccurrences(of: ">", with: "&gt;")
    }

    static func stripFishEmotionTags(_ text: String) -> String {
        var t = text
        t = t.replacingOccurrences(of: #"\[[^\]]{1,40}\]"#, with: "", options: .regularExpression)
        return t.replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    enum TTSError: LocalizedError {
        case missingKey(String)
        case http(String)
        /// The provider rejected the key (401/402/403).
        case refused(String)
        var errorDescription: String? {
            switch self {
            case .missingKey(let s): return "Missing API key for \(s)"
            case .http(let s): return s
            case .refused(let s): return "key refused (\(s))"
            }
        }
    }
}

extension Notification.Name {
    static let agentTTSWillPlay = Notification.Name("agentTTSWillPlay")
    static let agentTTSDidFinish = Notification.Name("agentTTSDidFinish")
}

extension TTSService: AVAudioPlayerDelegate {
    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in
            self.isSpeaking = false
            if let cont = self.filePlayContinuation {
                self.filePlayContinuation = nil
                cont.resume()
            }
        }
    }
}

extension TTSService: AVSpeechSynthesizerDelegate {
    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        Task { @MainActor in
            self.isSpeaking = false
            if let cont = self.appleSpeakContinuation {
                self.appleSpeakContinuation = nil
                cont.resume()
            }
        }
    }

    nonisolated func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        Task { @MainActor in
            self.isSpeaking = false
            if let cont = self.appleSpeakContinuation {
                self.appleSpeakContinuation = nil
                cont.resume()
            }
        }
    }
}

/// Collects Apple speech buffers from the synthesizer's callback thread and resumes once.
private final class SpeechBufferBox: @unchecked Sendable {
    private let lock = NSLock()
    private var buffers: [AVAudioPCMBuffer] = []
    private var done = false
    var continuation: CheckedContinuation<[AVAudioPCMBuffer], Never>?

    func append(_ buffer: AVAudioPCMBuffer) {
        lock.lock(); defer { lock.unlock() }
        if !done { buffers.append(buffer) }
    }

    func finish() {
        lock.lock()
        guard !done, let cont = continuation else { lock.unlock(); return }
        done = true
        continuation = nil
        let out = buffers
        lock.unlock()
        cont.resume(returning: out)
    }
}
