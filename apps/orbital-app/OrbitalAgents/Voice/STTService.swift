import AVFoundation
import Foundation
import Speech

/// Always-on speech-to-text. Mic PCM is never dropped while listening.
/// Apple: hot-swap recognition requests. Buffered (cloud Whisper / Azure): flush on VAD.
/// on-device Whisper on-device: Apple runs live (words on screen, barge-in, end of phrase) and the
/// phrase's audio is transcribed on the device when it ends; Apple's text is the fallback.
@MainActor
final class STTService: ObservableObject {
    @Published var partialText: String = ""
    @Published var isRecognizing = false
    @Published var hasPartial = false
    @Published var lastError: String?

    private var appleRecognizer: SFSpeechRecognizer?
    private var appleRequest: SFSpeechAudioBufferRecognitionRequest?
    private var appleTask: SFSpeechRecognitionTask?
    private var pcmAccumulator = Data()
    private var liveLocale: SpeechLanguage = .english
    private var lastEmittedFinal: String = ""
    private var lastEmittedAt = Date.distantPast
    private var swapping = false
    private var holdover: [Data] = []
    private let maxHoldoverChunks = 75
    /// On-device recognition failed once: use Apple's server recognizer for the rest of this run.
    private var preferServer = false
    private var lastErrorSwapAt = Date.distantPast
    private var resultCount = 0

    /// on-device Whisper on-device: audio of the phrase in progress (16 kHz mono) and Apple's words for it.
    private var phraseAudio: [Float] = []
    private var phraseApple = ""
    /// Closed Apple segments kept when VAD owns end-of-turn (Apple `isFinal` mid-phrase).
    private var applePrefix = ""
    /// Phrases being transcribed, in order: each waits for the one before it.
    private var finalChain: Task<Void, Never>?
    /// Audio kept from before Apple hears the first word of a phrase.
    private let preRollSamples = 16_000 * 3
    private let onDevice = OnDeviceSpeech.shared

    var onFinalUtterance: ((String) -> Void)?
    var onPartial: ((String) -> Void)?
    /// When true (hands-free / desk listen), Apple's own `isFinal` only rotates the
    /// recognition request — the silence detector decides when the phrase is done.
    /// Stops mid-sentence cutoffs when Apple segments early during a natural pause.
    var vadOwnsUtteranceEnd = false

    private let settings = SpeechSettings.shared

    var cloudBufferSeconds: Double {
        Double(pcmAccumulator.count) / (16_000.0 * 4.0)
    }

    func requestPermission() async -> Bool {
        await withCheckedContinuation { cont in
            SFSpeechRecognizer.requestAuthorization { status in
                cont.resume(returning: status == .authorized)
            }
        }
    }

    func startLive(locale: SpeechLanguage) throws {
        liveLocale = locale
        swapping = false
        holdover.removeAll(keepingCapacity: true)
        tearDownAppleRequest(endAudio: true)

        let recognizer = SFSpeechRecognizer(locale: Locale(identifier: locale.appleSpeechLocale))
        VoiceDebug.shared.log("stt start \(locale.appleSpeechLocale) available=\(recognizer?.isAvailable ?? false) onDevice=\(recognizer?.supportsOnDeviceRecognition ?? false) auth=\(SFSpeechRecognizer.authorizationStatus().rawValue)")
        guard let recognizer, recognizer.isAvailable else {
            throw STTError.unavailable("Apple Speech not available for \(locale.displayName)")
        }
        appleRecognizer = recognizer
        phraseAudio.removeAll(keepingCapacity: true)
        phraseApple = ""
        applePrefix = ""
        if settings.sttProvider == .onDeviceWhisper {
            onDevice.prepare()
        }
        try openNewAppleRequest(locale: locale)
        isRecognizing = true
    }

    private func openNewAppleRequest(locale: SpeechLanguage) throws {
        guard let recognizer = appleRecognizer else { return }

        let request = SFSpeechAudioBufferRecognitionRequest()
        request.shouldReportPartialResults = true
        request.requiresOnDeviceRecognition = recognizer.supportsOnDeviceRecognition && !preferServer
        if #available(iOS 16.0, *) {
            request.addsPunctuation = true
        }
        if #available(iOS 13.0, *) {
            request.taskHint = .dictation
        }
        appleRequest = request
        isRecognizing = true

        let pending = holdover
        holdover.removeAll(keepingCapacity: true)
        for chunk in pending {
            appendPCMToRequest(chunk, request: request)
        }

        appleTask = recognizer.recognitionTask(with: request) { [weak self] result, error in
            Task { @MainActor in
                guard let self else { return }
                guard self.appleRequest === request || self.appleRequest == nil else { return }

                if let result {
                    let text = result.bestTranscription.formattedString
                    self.resultCount += 1
                    VoiceDebug.shared.sttResults += 1
                    if self.resultCount <= 3 || result.isFinal {
                        VoiceDebug.shared.log("stt \(result.isFinal ? "final" : "partial"): \(text.prefix(60))")
                    }
                    if !self.swapping {
                        let full = self.mergedAppleText(text)
                        self.phraseApple = full
                        self.applyPartial(full)
                    }
                    if result.isFinal, !self.swapping {
                        if self.vadOwnsUtteranceEnd {
                            // Keep phrase audio; stitch this segment and keep listening.
                            // VAD / forceFinalizeFromSilence ends the turn.
                            if !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                                self.applePrefix = self.mergedAppleText(text)
                                self.phraseApple = self.applePrefix
                                self.applyPartial(self.applePrefix)
                            }
                            VoiceDebug.shared.log("stt apple segment closed; waiting for silence (\(self.applePrefix.prefix(40)))")
                            self.hotSwapAppleRequest()
                        } else {
                            self.endPhrase(self.mergedAppleText(text))
                            self.hotSwapAppleRequest()
                        }
                    }
                }
                if let error {
                    // Any error ends this recognition task. The old code ignored "no speech"
                    // (1110), "retry" (203) and friends and kept feeding audio to the dead task,
                    // so after a few quiet seconds nothing was ever transcribed again. Always
                    // open a fresh request (this callback only runs for the current one).
                    let ns = error as NSError
                    VoiceDebug.shared.log("stt error \(ns.domain) \(ns.code): \(ns.localizedDescription)")
                    let quiet = ns.domain == "kAFAssistantErrorDomain" && [216, 203, 1110, 1].contains(ns.code)
                        || ns.localizedDescription.lowercased().contains("cancel")
                    if !quiet {
                        self.lastError = error.localizedDescription
                        VoiceDebug.shared.lastSTTError = "\(ns.code) \(ns.localizedDescription)"
                        if request.requiresOnDeviceRecognition, !self.preferServer {
                            self.preferServer = true
                            VoiceDebug.shared.log("stt: on-device failed, using Apple's server recognizer")
                        }
                    }
                    guard !self.swapping, self.appleRecognizer != nil else { return }
                    // Don't spin if errors come back to back.
                    let wait = Date().timeIntervalSince(self.lastErrorSwapAt) < 1 ? 0.8 : 0
                    self.lastErrorSwapAt = Date()
                    Task { @MainActor in
                        if wait > 0 { try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000)) }
                        guard self.appleRequest === request, self.appleRecognizer != nil else { return }
                        self.hotSwapAppleRequest()
                    }
                }
            }
        }
    }

    private func tearDownAppleRequest(endAudio: Bool) {
        appleTask?.cancel()
        appleTask = nil
        if endAudio {
            appleRequest?.endAudio()
        }
        appleRequest = nil
    }

    private func hotSwapAppleRequest() {
        swapping = true
        tearDownAppleRequest(endAudio: true)
        do {
            try openNewAppleRequest(locale: liveLocale)
        } catch {
            lastError = error.localizedDescription
        }
        swapping = false
        if !hasPartial {
            partialText = ""
        }
    }

    private func applyPartial(_ text: String) {
        partialText = text
        hasPartial = !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        onPartial?(text)
    }

    /// Throw away what has been heard so far (the desk's own voice leaking in) without sending it.
    func resetUtterance() {
        switch settings.sttProvider {
        case .appleOnDevice, .onDeviceWhisper:
            phraseAudio.removeAll(keepingCapacity: true)
            phraseApple = ""
            applePrefix = ""
            hotSwapAppleRequest()
        case .openAIWhisper, .azureSpeech:
            pcmAccumulator = Data()
        }
        partialText = ""
        hasPartial = false
    }

    func forceFinalizeFromSilence() {
        switch settings.sttProvider {
        case .appleOnDevice:
            let text = phraseApple.isEmpty ? partialText : phraseApple
            applePrefix = ""
            emitFinal(text)
            hotSwapAppleRequest()
            hasPartial = false
        case .onDeviceWhisper:
            endPhrase(phraseApple)
            hotSwapAppleRequest()
            hasPartial = false
        case .openAIWhisper, .azureSpeech:
            Task {
                await self.flushBuffered(force: true)
            }
        }
    }

    /// Join a live Apple segment onto any closed segments from earlier `isFinal`s.
    private func mergedAppleText(_ segment: String) -> String {
        let part = segment.trimmingCharacters(in: .whitespacesAndNewlines)
        let prefix = applePrefix.trimmingCharacters(in: .whitespacesAndNewlines)
        if prefix.isEmpty { return part }
        if part.isEmpty { return prefix }
        if part.hasPrefix(prefix) || Self.cleanTranscription(part).localizedCaseInsensitiveContains(Self.cleanTranscription(prefix)) {
            return part
        }
        return prefix + " " + part
    }

    /// A phrase ended (Apple's final result or the silence timer). With on-device Whisper, its
    /// audio is transcribed on the device; Apple's words go out if the model isn't ready,
    /// fails, or runs long.
    private func endPhrase(_ appleText: String) {
        let joined = mergedAppleText(appleText)
        applePrefix = ""
        guard settings.sttProvider == .onDeviceWhisper else {
            emitFinal(joined)
            return
        }
        let audio = phraseAudio
        let apple = Self.cleanTranscription(joined)
        phraseAudio.removeAll(keepingCapacity: true)
        phraseApple = ""
        // Apple heard no words: nothing was said (keeps noise and echo away from Whisper).
        guard !apple.isEmpty else { return }
        guard onDevice.isReady else {
            onDevice.prepare()
            emitFinal(apple)
            return
        }
        let previous = finalChain
        let language = liveLocale
        let seconds = Double(audio.count) / 16_000
        finalChain = Task { @MainActor [weak self] in
            await previous?.value
            guard let self else { return }
            let text = await self.transcribeOnDevice(audio, language: language, timeout: 4 + seconds * 0.3)
            let chosen = Self.pickFinal(onDevice: text, apple: apple)
            let source = text.map(Self.cleanTranscription) == chosen ? "whisper" : "apple"
            VoiceDebug.shared.log("phrase from \(source): \(chosen.prefix(60)) | apple heard: \(apple.prefix(60))")
            self.emitFinal(chosen)
        }
    }

    /// The on-device text, or nil if it isn't back within `timeout` seconds.
    private func transcribeOnDevice(_ audio: [Float], language: SpeechLanguage, timeout: Double) async -> String? {
        final class Once { var done = false }
        let once = Once()
        return await withCheckedContinuation { continuation in
            Task { @MainActor in
                let text = await self.onDevice.transcribe(audio, language: language)
                guard !once.done else { return }
                once.done = true
                continuation.resume(returning: text)
            }
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: UInt64(timeout * 1_000_000_000))
                guard !once.done else { return }
                once.done = true
                VoiceDebug.shared.log("ondevice slower than \(Int(timeout))s, sending Apple's text")
                continuation.resume(returning: nil)
            }
        }
    }

    /// on-device Whisper's text unless it is empty or looks invented (far longer than what Apple heard).
    static func pickFinal(onDevice: String?, apple: String) -> String {
        guard let text = onDevice.map(cleanTranscription), !text.isEmpty else { return apple }
        let appleWords = apple.split(whereSeparator: \.isWhitespace).count
        let words = text.split(whereSeparator: \.isWhitespace).count
        if appleWords >= 2, words > appleWords * 3 + 8 { return apple }
        return text
    }

    private func emitFinal(_ text: String) {
        let cleaned = Self.cleanTranscription(text)
        guard !cleaned.isEmpty else { return }
        let now = Date()
        if now.timeIntervalSince(lastEmittedAt) < 2.8,
           cleaned == lastEmittedFinal
            || cleaned.localizedCaseInsensitiveContains(lastEmittedFinal)
            || lastEmittedFinal.localizedCaseInsensitiveContains(cleaned)
        {
            return
        }
        lastEmittedFinal = cleaned
        lastEmittedAt = now
        partialText = cleaned
        hasPartial = false
        onPartial?(cleaned)
        onFinalUtterance?(cleaned)
    }

    func appendPCMForApple(_ pcmFloat16k: Data) {
        guard pcmFloat16k.count >= MemoryLayout<Float>.size else { return }
        VoiceDebug.shared.pcmToSTT += 1
        if settings.sttProvider == .onDeviceWhisper {
            appendPhraseAudio(pcmFloat16k)
        }
        if let request = appleRequest, !swapping {
            appendPCMToRequest(pcmFloat16k, request: request)
        } else {
            holdover.append(pcmFloat16k)
            if holdover.count > maxHoldoverChunks {
                holdover.removeFirst(holdover.count - maxHoldoverChunks)
            }
        }
    }

    private func appendPhraseAudio(_ pcmFloat16k: Data) {
        pcmFloat16k.withUnsafeBytes { raw in
            phraseAudio.append(contentsOf: raw.bindMemory(to: Float.self))
        }
        // Until Apple hears a word, keep only a few seconds: the start of the next phrase.
        let cap = phraseApple.isEmpty ? preRollSamples : 16_000 * Int(settings.maxUtteranceSeconds + 30)
        if phraseAudio.count > cap + 16_000 {
            phraseAudio.removeFirst(phraseAudio.count - cap)
        }
    }

    private func appendPCMToRequest(_ pcmFloat16k: Data, request: SFSpeechAudioBufferRecognitionRequest) {
        let frameCount = pcmFloat16k.count / MemoryLayout<Float>.size
        guard frameCount > 0 else { return }
        guard let format = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: 16_000,
            channels: 1,
            interleaved: false
        ) else { return }
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frameCount)) else { return }
        buffer.frameLength = AVAudioFrameCount(frameCount)
        pcmFloat16k.withUnsafeBytes { raw in
            guard let src = raw.bindMemory(to: Float.self).baseAddress,
                  let dst = buffer.floatChannelData?[0] else { return }
            dst.update(from: src, count: frameCount)
        }
        request.append(buffer)
    }

    func appendPCMForBuffered(_ pcmFloat16k: Data, locale: SpeechLanguage) {
        liveLocale = locale
        pcmAccumulator.append(pcmFloat16k)
        if !hasPartial, pcmAccumulator.count > 16_000 * 4 * 1, partialText.isEmpty {
            applyPartial("…")
        }
        let threshold = Int(16_000 * 4 * 4.0)
        if pcmAccumulator.count >= threshold {
            let chunk = pcmAccumulator
            pcmAccumulator = Data()
            Task {
                await self.transcribeBuffered(pcmFloat16k: chunk)
            }
        }
    }

    func flushBuffered(force: Bool = false) async {
        let chunk = pcmAccumulator
        pcmAccumulator = Data()
        let minBytes = force ? (16_000 * 4 / 3) : (16_000 * 4 / 2)
        guard chunk.count > minBytes else { return }
        await transcribeBuffered(pcmFloat16k: chunk)
    }

    private func transcribeBuffered(pcmFloat16k: Data) async {
        do {
            lastError = nil
            let wav = Self.float32ToWAV(pcmFloat16k, sampleRate: 16_000)
            let text: String
            switch settings.sttProvider {
            case .appleOnDevice, .onDeviceWhisper:
                return
            case .openAIWhisper:
                text = try await whisperTranscribe(wav: wav, language: liveLocale)
            case .azureSpeech:
                text = try await azureTranscribe(wav: wav, language: liveLocale)
            }
            let cleaned = Self.cleanTranscription(text)
            guard !cleaned.isEmpty else { return }
            applyPartial(cleaned)
            emitFinal(cleaned)
        } catch {
            lastError = error.localizedDescription
            applyPartial("STT error: \(error.localizedDescription)")
        }
    }

    func stopLive(clearPartial: Bool = true) {
        swapping = false
        holdover.removeAll()
        tearDownAppleRequest(endAudio: true)
        appleRecognizer = nil
        isRecognizing = false
        vadOwnsUtteranceEnd = false
        if clearPartial {
            partialText = ""
            hasPartial = false
        }
        pcmAccumulator = Data()
        phraseAudio.removeAll()
        phraseApple = ""
        applePrefix = ""
    }

    private func whisperTranscribe(wav: Data, language: SpeechLanguage) async throws -> String {
        let key = settings.openAIAPIKey
        guard !key.isEmpty else { throw STTError.missingKey("OpenAI") }

        let boundary = "Boundary-\(UUID().uuidString)"
        var body = Data()
        func append(_ s: String) { body.append(Data(s.utf8)) }

        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"model\"\r\n\r\n")
        append("whisper-1\r\n")
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"language\"\r\n\r\n")
        append("\(language.openaiWhisperCode)\r\n")
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"response_format\"\r\n\r\n")
        append("json\r\n")
        append("--\(boundary)\r\n")
        append("Content-Disposition: form-data; name=\"file\"; filename=\"audio.wav\"\r\n")
        append("Content-Type: audio/wav\r\n\r\n")
        body.append(wav)
        append("\r\n--\(boundary)--\r\n")

        var req = URLRequest(url: URL(string: "https://api.openai.com/v1/audio/transcriptions")!)
        req.httpMethod = "POST"
        req.setValue("Bearer \(key)", forHTTPHeaderField: "Authorization")
        req.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        req.httpBody = body
        req.timeoutInterval = 60

        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let msg = String(data: data, encoding: .utf8) ?? "whisper failed"
            throw STTError.http(msg)
        }
        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        return (json?["text"] as? String ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    private func azureTranscribe(wav: Data, language: SpeechLanguage) async throws -> String {
        let key = settings.azureSpeechKey
        let region = settings.azureSpeechRegion
        guard !key.isEmpty else { throw STTError.missingKey("Azure Speech") }

        let locale = language.bcp47
        let url = URL(string: "https://\(region).stt.speech.microsoft.com/speech/recognition/conversation/cognitiveservices/v1?language=\(locale)")!
        var req = URLRequest(url: url)
        req.httpMethod = "POST"
        req.setValue(key, forHTTPHeaderField: "Ocp-Apim-Subscription-Key")
        req.setValue("audio/wav; codecs=audio/pcm; samplerate=16000", forHTTPHeaderField: "Content-Type")
        req.setValue("application/json", forHTTPHeaderField: "Accept")
        req.httpBody = wav
        req.timeoutInterval = 60

        let (data, resp) = try await URLSession.shared.data(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let msg = String(data: data, encoding: .utf8) ?? "azure stt failed"
            throw STTError.http(msg)
        }
        let json = try JSONSerialization.jsonObject(with: data) as? [String: Any]
        return (json?["DisplayText"] as? String ?? json?["Text"] as? String ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func cleanTranscription(_ text: String) -> String {
        text
            .replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func float32ToWAV(_ floatData: Data, sampleRate: Int) -> Data {
        let floatCount = floatData.count / MemoryLayout<Float>.size
        var int16 = [Int16](repeating: 0, count: floatCount)
        floatData.withUnsafeBytes { raw in
            let floats = raw.bindMemory(to: Float.self)
            for i in 0..<floatCount {
                let s = max(-1, min(1, floats[i]))
                int16[i] = Int16(s * 32767)
            }
        }
        let dataSize = int16.count * 2
        var header = Data()
        func appendU32(_ v: UInt32) {
            var le = v.littleEndian
            header.append(Data(bytes: &le, count: 4))
        }
        func appendU16(_ v: UInt16) {
            var le = v.littleEndian
            header.append(Data(bytes: &le, count: 2))
        }
        header.append(contentsOf: Array("RIFF".utf8))
        appendU32(UInt32(36 + dataSize))
        header.append(contentsOf: Array("WAVE".utf8))
        header.append(contentsOf: Array("fmt ".utf8))
        appendU32(16)
        appendU16(1)
        appendU16(1)
        appendU32(UInt32(sampleRate))
        appendU32(UInt32(sampleRate * 2))
        appendU16(2)
        appendU16(16)
        header.append(contentsOf: Array("data".utf8))
        appendU32(UInt32(dataSize))
        var out = header
        int16.withUnsafeBytes { out.append(contentsOf: $0) }
        return out
    }

    enum STTError: LocalizedError {
        case unavailable(String)
        case missingKey(String)
        case http(String)
        var errorDescription: String? {
            switch self {
            case .unavailable(let s): return s
            case .missingKey(let s): return "Missing API key for \(s). Set it in Settings."
            case .http(let s): return s
            }
        }
    }
}
