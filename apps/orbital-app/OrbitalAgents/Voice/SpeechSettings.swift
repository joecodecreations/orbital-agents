import Foundation
import Security

/// Persisted speech settings for the desk: recognizer, voice, timing, microphone and speaker.
/// Cloud keys (OpenAI, Azure) stay in this device's keychain; Fish voices need no key here
/// because the hub makes them.
@MainActor
final class SpeechSettings: ObservableObject {
    static let shared = SpeechSettings()

    private let defaults = UserDefaults.standard

    @Published var sttProvider: STTProvider {
        didSet { defaults.set(sttProvider.rawValue, forKey: Keys.sttProvider) }
    }

    @Published var ttsProvider: TTSProvider {
        didSet { defaults.set(ttsProvider.rawValue, forKey: Keys.ttsProvider) }
    }

    @Published var speechLanguage: SpeechLanguage {
        didSet { defaults.set(speechLanguage.rawValue, forKey: Keys.speechLanguage) }
    }

    @Published var audioInputSource: AudioInputSource {
        didSet { defaults.set(audioInputSource.rawValue, forKey: Keys.audioInput) }
    }

    @Published var audioOutputRoute: AudioOutputRoute {
        didSet { defaults.set(audioOutputRoute.rawValue, forKey: Keys.audioOutput) }
    }

    /// Last non-glasses output so the speech-bar toggle can flip back.
    @Published var lastNonGlassesRoute: AudioOutputRoute {
        didSet { defaults.set(lastNonGlassesRoute.rawValue, forKey: Keys.lastNonGlasses) }
    }

    @Published var ttsEnabled: Bool {
        didSet { defaults.set(ttsEnabled, forKey: Keys.ttsEnabled) }
    }

    @Published var appleTTSSpeed: Double {
        didSet { defaults.set(appleTTSSpeed, forKey: Keys.appleTTSSpeed) }
    }

    @Published var fishTTSSpeed: Double {
        didSet { defaults.set(fishTTSSpeed, forKey: Keys.fishTTSSpeed) }
    }

    /// Extra decibels on the built-in speaker only. Headphones ignore this.
    /// 0 is the speakerphone level with no added gain. The default is already raised.
    @Published var speakerBoostDb: Double {
        didSet {
            let clamped = min(SpeakerBoost.maxDb, max(SpeakerBoost.minDb, speakerBoostDb))
            if clamped != speakerBoostDb {
                speakerBoostDb = clamped
                return
            }
            defaults.set(speakerBoostDb, forKey: Keys.speakerBoost)
            SpeakerBoost.shared.refresh()
        }
    }

    @Published var azureTTSSpeed: Double {
        didSet { defaults.set(azureTTSSpeed, forKey: Keys.azureTTSSpeed) }
    }

    /// Skip near-duplicate recent speech-to-text text (default off).
    @Published var filterDuplicates: Bool {
        didSet { defaults.set(filterDuplicates, forKey: Keys.filterDuplicates) }
    }

    @Published var minCharsToSend: Int {
        didSet { defaults.set(minCharsToSend, forKey: Keys.minChars) }
    }

    /// Silence seconds that finalize an utterance in hands-free mode.
    @Published var silenceSeconds: Double {
        didSet { defaults.set(silenceSeconds, forKey: Keys.silenceSeconds) }
    }

    /// Force-cut a long ramble so the next request can start.
    @Published var maxUtteranceSeconds: Double {
        didSet { defaults.set(maxUtteranceSeconds, forKey: Keys.maxUtterance) }
    }

    @Published var voiceProcessingEnabled: Bool {
        didSet { defaults.set(voiceProcessingEnabled, forKey: Keys.voiceProcessing) }
    }

    @Published var speechNoiseGateEnabled: Bool {
        didSet { defaults.set(speechNoiseGateEnabled, forKey: Keys.speechGate) }
    }

    /// Glasses and hands-free pause and resume feedback: tones, spoken words, or off.
    @Published var pauseCueStyle: PauseCueStyle {
        didSet { defaults.set(pauseCueStyle.rawValue, forKey: Keys.pauseCueStyle) }
    }

    @Published var pauseCueWords: PauseCueWords {
        didSet { defaults.set(pauseCueWords.rawValue, forKey: Keys.pauseCueWords) }
    }

    /// When Fish is the voice, keep `[happy]`-style markers if the model emits them.
    @Published var fishEmotionTagsEnabled: Bool {
        didSet { defaults.set(fishEmotionTagsEnabled, forKey: Keys.fishEmotionTags) }
    }

    /// The hub's voices, its default first. Refreshed from `/v1/speech/status`.
    @Published private(set) var fishVoices: [FishVoice] = [FishVoice.hubDefault]

    @Published var fishDefaultPTVoiceId: String {
        didSet { defaults.set(fishDefaultPTVoiceId, forKey: Keys.fishPT) }
    }

    @Published var fishDefaultENVoiceId: String {
        didSet { defaults.set(fishDefaultENVoiceId, forKey: Keys.fishEN) }
    }

    /// Fish model name sent to the hub. Empty lets the hub choose.
    @Published var fishModel: String {
        didSet { defaults.set(fishModel, forKey: Keys.fishModel) }
    }

    @Published var openAIAPIKey: String {
        didSet { KeychainStore.set(openAIAPIKey, account: Keys.openAIKey) }
    }

    @Published var azureSpeechKey: String {
        didSet { KeychainStore.set(azureSpeechKey, account: Keys.azureKey) }
    }

    @Published var azureSpeechRegion: String {
        didSet { defaults.set(azureSpeechRegion, forKey: Keys.azureRegion) }
    }

    /// Which on-device model to download (`GET /v1/stt/models` ids). Empty takes the hub's first.
    @Published var onDeviceModelID: String {
        didSet { defaults.set(onDeviceModelID, forKey: Keys.onDeviceModel) }
    }

    @Published var onDeviceMode: OnDeviceSpeechMode {
        didSet { defaults.set(onDeviceMode.rawValue, forKey: Keys.onDeviceMode) }
    }

    /// Send voice diagnostics (timings, recognizer events, no audio) to the hub's logs. Off by default.
    @Published var sendDiagnostics: Bool {
        didSet { defaults.set(sendDiagnostics, forKey: Keys.diagnostics) }
    }

    private enum Keys {
        static let sttProvider = "orbital.speech.stt"
        static let ttsProvider = "orbital.speech.tts"
        static let speechLanguage = "orbital.speech.lang"
        static let audioInput = "orbital.speech.audioInput"
        static let audioOutput = "orbital.speech.audioOutput"
        static let lastNonGlasses = "orbital.speech.lastNonGlasses"
        static let ttsEnabled = "orbital.speech.ttsEnabled"
        static let appleTTSSpeed = "orbital.speech.appleTTSSpeed"
        static let fishTTSSpeed = "orbital.speech.fishTTSSpeed"
        static let speakerBoost = "orbital.speech.speakerBoostDb"
        static let azureTTSSpeed = "orbital.speech.azureTTSSpeed"
        static let filterDuplicates = "orbital.speech.filterDuplicates"
        static let minChars = "orbital.speech.minChars"
        static let silenceSeconds = "orbital.speech.silence"
        static let maxUtterance = "orbital.speech.maxUtterance"
        static let voiceProcessing = "orbital.speech.voiceProcessing"
        static let speechGate = "orbital.speech.speechGate"
        static let pauseCueStyle = "orbital.speech.pauseCueStyle"
        static let pauseCueWords = "orbital.speech.pauseCueWords"
        static let fishEmotionTags = "orbital.speech.fishEmotionTags"
        static let fishPT = "orbital.speech.fishPT"
        static let fishEN = "orbital.speech.fishEN"
        static let fishModel = "orbital.speech.fishModel"
        static let openAIKey = "speech.openai"
        static let azureKey = "speech.azure"
        static let azureRegion = "orbital.speech.azureRegion"
        static let onDeviceModel = "orbital.speech.onDeviceModel"
        static let onDeviceMode = "orbital.speech.onDeviceMode"
        static let diagnostics = "orbital.speech.diagnostics"
        static let classicDefaults = "orbital.speech.classicDefaults"
    }

    /// The hub's speed for Fish voices when the app sends none; a touch quicker than normal.
    static let defaultFishSpeed = 1.05
    /// The on-device model to download when the hub offers it (else the hub's first model).
    static let defaultOnDeviceModel = "crisperwhisper2-turbo-int8"

    private init() {
        // Defaults match the Orbital Classic app: on-device Whisper writes what is sent (Apple's
        // words until the model is downloaded) and replies speak in a Fish voice from the hub
        // (the built-in voice whenever the hub can't make one).
        var stt = STTProvider(rawValue: defaults.string(forKey: Keys.sttProvider) ?? "") ?? .onDeviceWhisper
        var tts = TTSProvider(rawValue: defaults.string(forKey: Keys.ttsProvider) ?? "") ?? .fishAudio
        // Earlier builds defaulted both to Apple; move a device still on them over once.
        if !defaults.bool(forKey: Keys.classicDefaults) {
            defaults.set(true, forKey: Keys.classicDefaults)
            if stt == .appleOnDevice {
                stt = .onDeviceWhisper
                defaults.set(stt.rawValue, forKey: Keys.sttProvider)
            }
            if tts == .appleOnDevice {
                tts = .fishAudio
                defaults.set(tts.rawValue, forKey: Keys.ttsProvider)
            }
        }
        sttProvider = stt
        ttsProvider = tts
        speechLanguage = SpeechLanguage(rawValue: defaults.string(forKey: Keys.speechLanguage) ?? "") ?? .english
        audioInputSource = AudioInputSource(rawValue: defaults.string(forKey: Keys.audioInput) ?? "") ?? .auto
        audioOutputRoute = AudioOutputRoute(rawValue: defaults.string(forKey: Keys.audioOutput) ?? "") ?? .glasses
        let storedFallback = AudioOutputRoute(rawValue: defaults.string(forKey: Keys.lastNonGlasses) ?? "")
        lastNonGlassesRoute = (storedFallback == nil || storedFallback == .glasses) ? .auto : storedFallback!
        ttsEnabled = defaults.object(forKey: Keys.ttsEnabled) as? Bool ?? true
        appleTTSSpeed = Self.clamped(defaults.object(forKey: Keys.appleTTSSpeed) as? Double ?? 1.0, 0.5, 1.5)
        fishTTSSpeed = Self.clamped(defaults.object(forKey: Keys.fishTTSSpeed) as? Double ?? Self.defaultFishSpeed, 0.5, 2.0)
        if defaults.object(forKey: Keys.speakerBoost) != nil {
            speakerBoostDb = Self.clamped(defaults.object(forKey: Keys.speakerBoost) as? Double ?? SpeakerBoost.defaultDb, SpeakerBoost.minDb, SpeakerBoost.maxDb)
        } else {
            speakerBoostDb = SpeakerBoost.defaultDb
        }
        azureTTSSpeed = Self.clamped(defaults.object(forKey: Keys.azureTTSSpeed) as? Double ?? 1.0, 0.5, 2.0)
        filterDuplicates = defaults.object(forKey: Keys.filterDuplicates) as? Bool ?? false
        minCharsToSend = defaults.object(forKey: Keys.minChars) as? Int ?? 4
        silenceSeconds = Self.clamped(defaults.object(forKey: Keys.silenceSeconds) as? Double ?? 2.0, 0.4, 10)
        maxUtteranceSeconds = Self.clamped(defaults.object(forKey: Keys.maxUtterance) as? Double ?? 90, 10, 180)
        voiceProcessingEnabled = defaults.object(forKey: Keys.voiceProcessing) as? Bool ?? true
        speechNoiseGateEnabled = defaults.object(forKey: Keys.speechGate) as? Bool ?? true
        pauseCueStyle = PauseCueStyle(rawValue: defaults.string(forKey: Keys.pauseCueStyle) ?? "") ?? .tones
        pauseCueWords = PauseCueWords(rawValue: defaults.string(forKey: Keys.pauseCueWords) ?? "") ?? .listening
        fishEmotionTagsEnabled = defaults.object(forKey: Keys.fishEmotionTags) as? Bool ?? true
        fishDefaultPTVoiceId = defaults.string(forKey: Keys.fishPT) ?? ""
        fishDefaultENVoiceId = defaults.string(forKey: Keys.fishEN) ?? ""
        fishModel = defaults.string(forKey: Keys.fishModel) ?? ""
        openAIAPIKey = KeychainStore.get(account: Keys.openAIKey) ?? ""
        azureSpeechKey = KeychainStore.get(account: Keys.azureKey) ?? ""
        azureSpeechRegion = defaults.string(forKey: Keys.azureRegion) ?? "eastus"
        onDeviceModelID = defaults.string(forKey: Keys.onDeviceModel) ?? Self.defaultOnDeviceModel
        onDeviceMode = OnDeviceSpeechMode(rawValue: defaults.string(forKey: Keys.onDeviceMode) ?? "") ?? .intended
        sendDiagnostics = defaults.bool(forKey: Keys.diagnostics)
    }

    private static func clamped(_ value: Double, _ minV: Double, _ maxV: Double) -> Double {
        min(maxV, max(minV, value))
    }

    /// The hub's voice list. A language with no voice picked yet takes the hub's default for it
    /// (`[speech.voices]` on the hub), so the pickers show that voice by name.
    func applyHubVoices(_ status: SpeechStatus) {
        if fishDefaultENVoiceId.isEmpty, let id = status.defaultVoice(for: .english) { fishDefaultENVoiceId = id }
        if fishDefaultPTVoiceId.isEmpty, let id = status.defaultVoice(for: .portuguese) { fishDefaultPTVoiceId = id }
        var voices = [FishVoice.hubDefault]
        for voice in status.voices where !voices.contains(where: { $0.referenceId == voice.id }) {
            voices.append(FishVoice(name: voice.name, referenceId: voice.id, language: voice.language))
        }
        // Keep a voice typed in by id, so it still shows as chosen.
        for id in [fishDefaultENVoiceId, fishDefaultPTVoiceId] where !id.isEmpty && !voices.contains(where: { $0.referenceId == id }) {
            voices.append(FishVoice(name: "Custom · \(id.prefix(8))", referenceId: id, language: nil))
        }
        if voices != fishVoices { fishVoices = voices }
    }

    func resetActiveTTSSpeed() {
        switch ttsProvider {
        case .appleOnDevice: appleTTSSpeed = 1.0
        case .fishAudio: fishTTSSpeed = Self.defaultFishSpeed
        case .azureSpeech: azureTTSSpeed = 1.0
        }
    }

    /// Short label for the speaker-volume slider.
    var speakerBoostLabel: String {
        let db = Int(speakerBoostDb.rounded())
        if db <= 0 { return "Standard" }
        if db < 8 { return "+\(db)" }
        if db < 15 { return "Loud +\(db)" }
        return "Max +\(db)"
    }

    var activeTTSSpeed: Double {
        switch ttsProvider {
        case .appleOnDevice: return appleTTSSpeed
        case .fishAudio: return fishTTSSpeed
        case .azureSpeech: return azureTTSSpeed
        }
    }

    var activeFishVoiceId: String {
        voiceId(for: speechLanguage)
    }

    var activeFishVoiceName: String {
        voiceName(for: activeFishVoiceId)
    }

    func voiceId(for language: SpeechLanguage) -> String {
        switch language {
        case .english: return fishDefaultENVoiceId
        case .portuguese: return fishDefaultPTVoiceId
        }
    }

    func voiceName(for id: String) -> String {
        if id.isEmpty { return "hub default" }
        return fishVoices.first(where: { $0.referenceId == id })?.name ?? id
    }

    /// Persistently flip glasses ↔ the last car or phone route.
    func toggleGlassesOutput() -> AudioOutputRoute {
        if audioOutputRoute == .glasses {
            audioOutputRoute = lastNonGlassesRoute == .glasses ? .auto : lastNonGlassesRoute
        } else {
            lastNonGlassesRoute = audioOutputRoute
            audioOutputRoute = .glasses
        }
        return audioOutputRoute
    }
}

/// Cloud speech keys this device keeps (OpenAI, Azure). Never sent anywhere but those providers.
enum KeychainStore {
    private static var service: String { "\(OrbitalBrand.bundleID).voice" }

    static func set(_ value: String, account: String) {
        guard !value.isEmpty else {
            delete(account: account)
            return
        }
        let data = Data(value.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
        var add = query
        add[kSecValueData as String] = data
        add[kSecAttrAccessible as String] = kSecAttrAccessibleAfterFirstUnlock
        if SecItemAdd(add as CFDictionary, nil) != errSecSuccess {
            MacSecretFile.set(value, service: service, account: account)
        }
    }

    static func get(account: String) -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var out: AnyObject?
        let status = SecItemCopyMatching(query as CFDictionary, &out)
        guard status == errSecSuccess, let data = out as? Data else {
            return MacSecretFile.get(service: service, account: account)
        }
        return String(data: data, encoding: .utf8)
    }

    static func delete(account: String) {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        SecItemDelete(query as CFDictionary)
        MacSecretFile.delete(service: service, account: account)
    }
}
