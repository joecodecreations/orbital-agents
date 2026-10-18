import Foundation

/// Speech locale for STT / TTS (no translation).
enum SpeechLanguage: String, CaseIterable, Identifiable, Codable {
    case english = "en"
    case portuguese = "pt"

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .english: return "English"
        case .portuguese: return "Portuguese"
        }
    }

    var bcp47: String {
        switch self {
        case .english: return "en-US"
        case .portuguese: return "pt-BR"
        }
    }

    var appleSpeechLocale: String { bcp47 }

    var openaiWhisperCode: String { rawValue }
}

/// Speech-to-text backends: on-device (Apple, or a Whisper model downloaded from the hub), or a
/// cloud API called with your own key kept in this device's keychain.
enum STTProvider: String, CaseIterable, Identifiable, Codable {
    case appleOnDevice = "apple"
    case onDeviceWhisper = "ondevice_whisper"
    case openAIWhisper = "openai_whisper"
    case azureSpeech = "azure"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .appleOnDevice: return "Apple Speech (on-device)"
        case .onDeviceWhisper: return "Whisper (on-device, from your hub)"
        case .openAIWhisper: return "OpenAI Whisper (cloud)"
        case .azureSpeech: return "Azure Speech (cloud)"
        }
    }

    var detail: String {
        switch self {
        case .appleOnDevice:
            return "Built in · instant · works offline · nothing to set up"
        case .onDeviceWhisper:
            return "Free · runs on this device · offline once downloaded from your hub · Apple shows the live words, Whisper writes what is sent"
        case .openAIWhisper:
            return "This device → OpenAI Whisper API (needs internet + your key)"
        case .azureSpeech:
            return "This device → Azure Speech API (needs internet + your key)"
        }
    }

    var isCloud: Bool {
        switch self {
        case .openAIWhisper, .azureSpeech: return true
        default: return false
        }
    }

    var isBuffered: Bool {
        switch self {
        case .appleOnDevice, .onDeviceWhisper: return false
        case .openAIWhisper, .azureSpeech: return true
        }
    }

    /// Apple's live recognizer runs (alone, or for live words and barge-in under Whisper).
    var usesAppleLive: Bool {
        self == .appleOnDevice || self == .onDeviceWhisper
    }

    var needsSpeechPermission: Bool {
        usesAppleLive
    }
}

/// on-device Whisper's two output styles.
enum OnDeviceSpeechMode: String, CaseIterable, Identifiable, Codable {
    case intended
    case verbatim

    var id: String { rawValue }

    var title: String {
        switch self {
        case .intended: return "Clean"
        case .verbatim: return "Verbatim"
        }
    }

    var detail: String {
        switch self {
        case .intended: return "What you meant: no ums, stutters or restarts."
        case .verbatim: return "Every word as said, including um and uh."
        }
    }
}

/// Text-to-speech: the built-in voice, Fish voices made by your hub (its FISH_API_KEY), or Azure
/// with your own key.
enum TTSProvider: String, CaseIterable, Identifiable, Codable {
    case appleOnDevice = "apple"
    case fishAudio = "fish"
    case azureSpeech = "azure"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .appleOnDevice: return "Built-in voice"
        case .fishAudio: return "Fish voices (from your hub)"
        case .azureSpeech: return "Azure Speech (cloud)"
        }
    }

    var detail: String {
        switch self {
        case .appleOnDevice: return "The system voice. Works offline. Fish falls back to it when the hub can't make a clip."
        case .fishAudio: return "Made by your hub with its FISH_API_KEY, so no key is kept on this device. Voices come from the hub."
        case .azureSpeech: return "This device → Azure TTS (needs internet + your key)"
        }
    }
}

/// Extra feedback style. The two listen-state chimes always play as tones:
/// a rising chime when it starts listening, a low tone when it stops.
/// Spoken words are only used for the voice-mute beep, and never for the
/// listen chime, because the mic would send that word as a new turn.
enum PauseCueStyle: String, CaseIterable, Identifiable, Codable {
    case tones = "tones"
    case spoken = "spoken"
    case off = "off"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .tones: return "Tones"
        case .spoken: return "Spoken words"
        case .off: return "Off"
        }
    }

    var detail: String {
        switch self {
        case .tones:
            return "Rising chime when you can talk. Low tone when she stops listening. These two always play."
        case .spoken:
            return "The listen chimes stay tones, so the mic does not send a word. Mute uses a spoken word."
        case .off:
            return "Hides the mute beep. The rising chime and the low tone still play."
        }
    }
}

/// Phrase pair used when pause cues are spoken words.
enum PauseCueWords: String, CaseIterable, Identifiable, Codable {
    case listening = "listening"
    case audioOn = "audio_on"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .listening: return "Paused / Listening"
        case .audioOn: return "Audio off / Audio on"
        }
    }

    func phrase(paused: Bool, language: SpeechLanguage) -> String {
        switch (self, paused, language) {
        case (.listening, true, .english): return "Paused"
        case (.listening, false, .english): return "Listening"
        case (.listening, true, .portuguese): return "Pausado"
        case (.listening, false, .portuguese): return "Ouvindo"
        case (.audioOn, true, .english): return "Audio off"
        case (.audioOn, false, .english): return "Audio on"
        case (.audioOn, true, .portuguese): return "Áudio desligado"
        case (.audioOn, false, .portuguese): return "Áudio ligado"
        }
    }
}

/// Where the desk plays speech, pause cues, and spoken replies.
enum AudioOutputRoute: String, CaseIterable, Identifiable, Codable {
    case glasses = "glasses"
    case phoneSpeaker = "phone"
    case auto = "auto"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .glasses: return "Meta glasses"
        case .phoneSpeaker: return "iPhone speaker"
        case .auto: return "Auto (CarPlay / glasses)"
        }
    }

    var detail: String {
        switch self {
        case .glasses:
            return "Always play through paired Ray-Bans, even when CarPlay is connected. Uses the glasses HFP path so the car cannot steal the route."
        case .phoneSpeaker:
            return "Play from the iPhone speaker. CarPlay will still take over if the car is the system route."
        case .auto:
            return "Glasses when they are the only headset. If CarPlay is connected, audio goes to the car until you tap Glasses."
        }
    }

    func preferGlasses(connected: Bool) -> Bool {
        switch self {
        case .glasses: return true
        case .phoneSpeaker: return false
        case .auto: return connected
        }
    }
}

enum AudioInputSource: String, CaseIterable, Identifiable, Codable {
    case phoneMic = "phone"
    case bluetoothHFP = "bt_hfp"
    case auto = "auto"

    var id: String { rawValue }

    var title: String {
        switch self {
        case .phoneMic: return "iPhone microphone"
        case .bluetoothHFP: return "Glasses / Bluetooth mic"
        case .auto: return "Auto (phone mic)"
        }
    }

    var detail: String {
        switch self {
        case .phoneMic:
            return "Built-in mic. Best levels and noise control."
        case .bluetoothHFP:
            return "Meta glasses / headset HFP mic. Use this to talk through the Ray-Bans."
        case .auto:
            return "Prefers the built-in mic for consistent VAD. TTS still plays on paired glasses."
        }
    }
}

/// A Fish voice the hub offers (`/v1/speech/status` `voices`), or one typed in by id.
struct FishVoice: Identifiable, Codable, Equatable, Hashable {
    var id: String { referenceId }
    var name: String
    /// Fish reference id. Empty means the hub's default voice.
    var referenceId: String
    var language: String?

    /// The hub picks: its configured default voice.
    static let hubDefault = FishVoice(name: "Hub default", referenceId: "", language: nil)
}
