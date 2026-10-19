import AVFoundation
import Combine
import Foundation
import Speech
import UIKit

/// Live voice diagnostics: a one-line summary for the Desk tab and `[voice]` lines on the console
/// (`xcrun devicectl device process launch --console ...`).
@MainActor
final class VoiceDebug: ObservableObject {
    static let shared = VoiceDebug()
    @Published private(set) var line = ""
    var pcmFromMic = 0
    var pcmToSTT = 0
    var sttResults = 0
    var droppedAsEcho = 0
    var lastSTTError = ""
    private var lastPCM = 0
    /// Sends batched lines to the hub's logs when diagnostics are on in Voice settings, so they can
    /// be read without a cable. Set by DeskState.
    var sink: (([String]) async -> Void)?
    private var pending: [String] = []
    private var flushing = false

    func log(_ text: String) {
        print("[voice] \(text)")
        let stamp = Date().formatted(.dateTime.hour(.twoDigits(amPM: .omitted)).minute(.twoDigits).second(.twoDigits))
        pending.append("\(stamp) \(text)")
        if pending.count > 300 { pending.removeFirst(pending.count - 300) }
        guard !flushing, sink != nil else { return }
        flushing = true
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            let batch = self.pending
            self.pending = []
            self.flushing = false
            if !batch.isEmpty { await self.sink?(batch) }
        }
    }

    func reset() {
        pcmFromMic = 0; pcmToSTT = 0; sttResults = 0; droppedAsEcho = 0; lastSTTError = ""; lastPCM = 0; line = ""
    }

    /// Called every couple of seconds while listening.
    func tick(_ session: ConversationSession) {
        let flowing = pcmFromMic - lastPCM
        lastPCM = pcmFromMic
        var parts = [
            session.capture.levelDbLabel,
            "audio \(flowing > 0 ? "flowing" : "STOPPED") (\(pcmToSTT))",
            "heard \(sttResults)",
        ]
        if session.isAssistantSpeaking { parts.append("speaking") }
        if droppedAsEcho > 0 { parts.append("echo \(droppedAsEcho)") }
        if !lastSTTError.isEmpty { parts.append("err \(lastSTTError)") }
        line = parts.joined(separator: " · ")
        log("tick \(line) | \(session.capture.activeRouteLabel)")
    }
}

/// Always-on duplex conversation:
///
/// ```
/// Mic (always on)
///   → STT segments (parallel flushes OK)
///   → each utterance send-nows (an agent CLI double-Enter / cancel-and-send)
///   → latest reply only is spoken (no TTS backlog)
/// ```
///
/// The mic never stops between utterances. A new phrase interrupts the
/// in-flight desk turn instead of waiting in the prompt queue. Older
/// assistant speech is dropped — only the latest reply is spoken.
///
/// Barge-in: replies play through the mic's voice-processing engine. On the loud speaker
/// that canceller still lets its voice through, so a transcript is accepted as yours only
/// when it is not its recent line (`EchoGuard`). "Wait" or a clearly different sentence
/// stops playback. A tail of its own sentence does not, and it is not sent.
///
/// Meta glasses, after you have started conversation with the left-hand waveform button:
/// - Lock the phone. The mic engine, its playback and the glasses button stay up.
/// - One temple tap toggles your mic only. A rising chime means you can talk.
///   A low tone means your mic is off. Its voice keeps playing either way.
/// - While the app is on screen and conversation is off, a tap starts it. On a
///   locked phone with conversation off, a tap only plays the low tone.
/// Its own speech is not a user turn: the chimes mute the mic, and EchoGuard
/// drops a transcript that is only its line.
@MainActor
final class ConversationSession: ObservableObject {
    @Published var isListening = false
    /// Mic is off — your speech is not sent. TTS is unaffected.
    @Published var isPaused = false
    /// Double-tap mute: assistant voice is silent.
    @Published var isTTSMuted = false
    @Published var oneShot = false
    @Published var partialText: String = ""
    @Published var lastError: String?
    @Published var statusText: String = "Ready"
    @Published var micLevel: Float = 0
    @Published var micLevelLabel: String = "—"
    @Published var vadStatus: String = ""
    @Published var noiseControlLabel: String = ""
    @Published var queueDepth: Int = 0
    @Published var parallelStats: String = ""

    let settings = SpeechSettings.shared
    let capture = AudioCaptureService()
    let stt = STTService()
    let tts = TTSService()
    let vad = AdaptiveSilenceDetector()
    let glasses = MetaGlassesService()
    let pauseCues = PauseCuePlayer()

    /// Called the moment an utterance is finalized — send to the desk immediately.
    var onUtterance: ((String) -> Void)?
    /// You talked over a reply: its tag (the desk's reply id) and the part you heard.
    var onBargeIn: ((String?, String) -> Void)?
    /// Talking over a reply stops it. On by default.
    var bargeInEnabled = true
    var onPauseChanged: ((Bool) -> Void)?
    var onTTSMuteChanged: ((Bool) -> Void)?
    /// Temple tap while continuous talk is off and the app is on screen. The desk starts hands-free.
    var onRequestLiveConversation: (() -> Void)?

    /// Allow the flush that happens at the same moment as a mic pause to still send.
    private var allowFinalWhilePaused = false
    private var lastRawTapAt = Date.distantPast
    private var debugTicker: Task<Void, Never>?
    /// AVRCP often fires play and pause for one physical tap.
    private let tapDedup: TimeInterval = 0.20
    private var lockObservers: [NSObjectProtocol] = []
    private var lockTask: UIBackgroundTaskIdentifier = .invalid

    private var recentSources: [(text: String, at: Date)] = []
    private var nextSpeakSeq = 1
    private var speakSlots: [Int: SpeakSlot] = [:]
    private var speakCursor = 1
    private var ttsInFlight = 0
    private var playbackRunning = false
    private var launchingTTS: Set<Int> = []
    private let maxParallelTTS = 3
    /// Bumped whenever pending speech is discarded so stale synth/play tasks no-op.
    private var speakGeneration = 0
    private var glassesCancellable: AnyCancellable?
    /// Delays tearing the mic down until the "not listening" chime has played.
    private var tearDownTask: Task<Void, Never>?
    /// Bumped so a stale unmute does not open the mic after a newer chime.
    private var earconToken = 0

    /// Words of each tagged reply that finished playing, for "what did you hear".
    private var heardByTag: [String: String] = [:]
    private var playingSlot: SpeakSlot?
    /// What the desk said out loud recently, to tell your words from the speaker's echo.
    private var recentSpeech: [(text: String, at: Date)] = []
    /// Playback just stopped because you cut in. The final transcript still has its words in front of your.
    private var cutInForNextFinal = false

    private struct SpeakSlot {
        let seq: Int
        let text: String
        var tag: String?
        /// Spoken even when replies are set to silent (the Tracker tab's finished-item announcements).
        var force = false
        var clip: TTSService.PreparedClip?
        var done = false
        var failed = false
    }

    init() {
        tts.duplex = capture
        capture.onLevel = { [weak self] level in
            guard let self else { return }
            if self.isPaused {
                self.micLevel = 0
                self.micLevelLabel = "mic off"
                return
            }
            self.micLevel = level
            self.micLevelLabel = self.capture.levelDbLabel
            self.noiseControlLabel = self.noiseLabel()
        }
        capture.onDb = { [weak self] db in
            guard let self, self.isListening, !self.isPaused else { return }
            self.vad.process(db: db)
            self.vadStatus = self.vad.statusLine
        }
        vad.onUtteranceEnded = { [weak self] in
            Task { @MainActor in
                self?.handleSilenceEnd()
            }
        }
        glasses.onTempleToggle = { [weak self] in
            self?.togglePauseFromGlasses()
        }
        capture.onRouteChanged = { [weak self] in
            self?.glasses.refreshBluetoothStatus()
        }
        glasses.bootstrap()
        armGlassesClick()
        installPhoneLockObservers()
        glassesCancellable = glasses.objectWillChange.sink { [weak self] _ in
            self?.objectWillChange.send()
        }
    }

    private func installPhoneLockObservers() {
        #if !targetEnvironment(macCatalyst)
        let center = NotificationCenter.default
        let away: [Notification.Name] = [
            UIApplication.didEnterBackgroundNotification,
            UIApplication.protectedDataWillBecomeUnavailableNotification,
        ]
        for name in away {
            lockObservers.append(center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.phoneWillLeaveForeground() }
            })
        }
        lockObservers.append(center.addObserver(
            forName: UIApplication.didBecomeActiveNotification, object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.phoneDidBecomeActive() }
        })
        #endif
    }

    /// The screen locked or the app went to the background. An open conversation
    /// keeps the mic engine (muted or not), its playback, and the glasses button.
    private func phoneWillLeaveForeground() {
        guard isListening, !oneShot else { return }
        VoiceDebug.shared.log("phone left foreground; holding conversation paused=\(isPaused)")
        beginLockTask()
        holdConversationAudio()
        Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 3_000_000_000)
            self?.endLockTask()
        }
    }

    /// Back on screen. Re-check the route and the engine if conversation is still on.
    private func phoneDidBecomeActive() {
        endLockTask()
        if isListening, !oneShot {
            holdConversationAudio()
        } else {
            armGlassesClick()
        }
    }

    /// Keep the play-and-record session, the engine and Now Playing alive so the
    /// next temple tap arrives and its reply has a route to the glasses.
    private func holdConversationAudio() {
        capture.setMaintainsDuplexSession(true)
        capture.ensureEngineRunning()
        glasses.enableRemoteControls()
        glasses.startKeepAlive()
        refreshGlassesNowPlaying()
    }

    /// A few seconds of guaranteed run time while iOS moves the app off screen.
    private func beginLockTask() {
        guard lockTask == .invalid else { return }
        lockTask = UIApplication.shared.beginBackgroundTask(withName: "orbital-voice-lock") { [weak self] in
            MainActor.assumeIsolated { self?.endLockTask() }
        }
    }

    private func endLockTask() {
        guard lockTask != .invalid else { return }
        UIApplication.shared.endBackgroundTask(lockTask)
        lockTask = .invalid
    }

    /// Hold the glasses button so a temple tap can start continuous talk.
    /// No-op on the Mac app: there are no glasses, and it must not take the audio session.
    func armGlassesClick() {
        #if !targetEnvironment(macCatalyst)
        glasses.enableRemoteControls(idleSession: !capture.isRunning && !isListening)
        if !isListening {
            glasses.updateNowPlaying(live: false, micPaused: false, ttsMuted: false)
        }
        #endif
    }

    /// Re-apply the persisted glasses / car / speaker route (CarPlay steal, toggle).
    func reassertAudioRoute() {
        capture.applyOutputPreference()
        glasses.refreshBluetoothStatus()
        if isListening, settings.audioOutputRoute == .glasses {
            glasses.startKeepAlive()
        }
    }

    private func noiseLabel() -> String {
        let vp = capture.voiceProcessingEnabled ? "VP+NS" : "no-VP"
        let gate = capture.speechGateOpen ? "speech" : "blocked"
        return "\(vp) · gate \(gate)"
    }

    func startListening(oneShot: Bool = false) async {
        tearDownTask?.cancel()
        tearDownTask = nil
        if isListening {
            stopListening()
        }
        lastError = nil
        self.oneShot = oneShot

        let micOK = await capture.requestPermission()
        guard micOK else {
            lastError = "Microphone permission denied."
            statusText = lastError ?? ""
            return
        }

        if settings.sttProvider.needsSpeechPermission {
            let speechOK = await stt.requestPermission()
            guard speechOK else {
                lastError = "Speech recognition permission denied."
                statusText = lastError ?? ""
                return
            }
        }

        do {
            isPaused = false
            isTTSMuted = false
            capture.speechGateEnabled = settings.speechNoiseGateEnabled
            capture.preferVoiceProcessing = settings.voiceProcessingEnabled
            try capture.configureSession(input: settings.audioInputSource)

            stt.onPartial = { [weak self] text in
                Task { @MainActor in
                    guard let self, self.isListening, !self.isPaused else { return }
                    let spoken = self.spokenText()
                    if self.isAssistantSpeaking {
                        let decision = EchoGuard.decide(heard: text, spoken: spoken, guarding: true)
                        if self.bargeInEnabled, !self.isTTSMuted, decision.barge, !decision.send.isEmpty {
                            self.bargeIn()
                            self.partialText = decision.send
                            self.vad.notePartialText(decision.send)
                            self.showHearing(decision.send)
                            return
                        }
                        // Its voice, or not enough yet to be sure it's you. Don't stop its.
                        // The final transcript is checked again, so this copy is not sent.
                        if EchoGuard.isSpeakerEcho(text, spoken: spoken) {
                            VoiceDebug.shared.droppedAsEcho += 1
                        }
                        return
                    }
                    if EchoGuard.isSpeakerEcho(text, spoken: spoken) {
                        VoiceDebug.shared.droppedAsEcho += 1
                        return
                    }
                    self.partialText = text
                    let echo = self.recentSources.contains {
                        Date().timeIntervalSince($0.at) < 4 && self.similar($0.text, text)
                    }
                    if !echo {
                        self.vad.notePartialText(text)
                    }
                    if !text.isEmpty, text != "…" {
                        self.showHearing(text)
                    }
                }
            }
            stt.onFinalUtterance = { [weak self] text in
                Task { @MainActor in
                    self?.handleFinalUtterance(text)
                }
            }

            if settings.sttProvider == .onDeviceWhisper {
                OnDeviceSpeech.shared.autoInstallIfNeeded()
            }
            if settings.sttProvider.usesAppleLive {
                // Silence detector owns end-of-turn so Apple's early isFinal cannot cut mid-sentence.
                stt.vadOwnsUtteranceEnd = true
                try stt.startLive(locale: settings.speechLanguage)
            }

            // Mute STT until the ready chime finishes so that tone is never a user turn.
            // Talk (one-shot) and hands-free both wait for real mic audio before the chime.
            capture.mutePCMForSTT = true
            capture.onPCM = { [weak self] pcm in
                Task { @MainActor in
                    VoiceDebug.shared.pcmFromMic += 1
                    guard let self, self.isListening, !self.isPaused else { return }
                    switch self.settings.sttProvider {
                    case .appleOnDevice, .onDeviceWhisper:
                        self.stt.appendPCMForApple(pcm)
                    case .openAIWhisper, .azureSpeech:
                        self.stt.appendPCMForBuffered(pcm, locale: self.settings.speechLanguage)
                    }
                }
            }

            VoiceDebug.shared.reset()
            VoiceDebug.shared.log("listen start stt=\(settings.sttProvider.rawValue) tts=\(settings.ttsProvider.rawValue) hubTTS=\(tts.hubSynthesize != nil) route=\(settings.audioOutputRoute.rawValue) mic=\(AVAudioApplicationPermission()) speech=\(SFSpeechRecognizer.authorizationStatus().rawValue)")
            try capture.start(input: settings.audioInputSource)
            VoiceDebug.shared.log("capture started \(capture.levelDbLabel) | \(capture.activeRouteLabel)")
            debugTicker?.cancel()
            debugTicker = Task { @MainActor [weak self] in
                while !Task.isCancelled {
                    try? await Task.sleep(nanoseconds: 2_000_000_000)
                    guard let self, self.isListening else { return }
                    VoiceDebug.shared.tick(self)
                }
            }
            vad.reset(
                silenceSeconds: settings.silenceSeconds,
                maxUtteranceSeconds: settings.maxUtteranceSeconds
            )
            isListening = true
            micLevel = 0
            partialText = ""
            if !oneShot {
                // Keeps the engine up across the lock screen, muted or not.
                capture.setMaintainsDuplexSession(true)
                glasses.enableRemoteControls()
                refreshGlassesNowPlaying()
            }
            // Not ready yet — engine start can return before the first mic buffer
            // (Mac Catalyst / voice-processing reconfigure). Cue plays after audio flows.
            statusText = "Getting mic ready…"
            scheduleListeningReadyCue()
        } catch {
            lastError = error.localizedDescription
            statusText = error.localizedDescription
            VoiceDebug.shared.log("listen failed: \(error.localizedDescription)")
        }
    }

    private func AVAudioApplicationPermission() -> String {
        if #available(iOS 17.0, *) {
            switch AVAudioApplication.shared.recordPermission {
            case .granted: return "granted"
            case .denied: return "denied"
            default: return "undetermined"
            }
        }
        return "?"
    }

    /// `cue` plays the low "not listening" tone when continuous talk was open.
    /// One-shot dictation stays quiet: that mode is the mic button, not the glasses.
    func stopListening(cue: Bool = false) {
        let playStop = cue && isListening && !oneShot && !isPaused
        tearDownTask?.cancel()
        tearDownTask = nil
        debugTicker?.cancel()
        debugTicker = nil
        vad.stop()
        capture.setMaintainsDuplexSession(false)
        if playStop {
            capture.mutePCMForSTT = true
            stt.resetUtterance()
            partialText = ""
            playListenCue(.micOff)
        }
        stt.stopLive()
        isListening = false
        isPaused = false
        isTTSMuted = false
        allowFinalWhilePaused = false
        oneShot = false
        partialText = ""
        micLevel = 0
        micLevelLabel = "—"
        vadStatus = ""
        noiseControlLabel = ""
        statusText = settings.ttsEnabled ? "Speak on" : "Ready"
        if playStop {
            let delay = PauseCuePlayer.earconSeconds(.micOff)
            tearDownTask = Task { @MainActor in
                try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
                guard !Task.isCancelled else { return }
                self.finishListenTeardown()
            }
        } else {
            finishListenTeardown()
        }
    }

    /// Drop the mic engine. On iPhone, stay Now Playing so the next temple tap can start talk again.
    private func finishListenTeardown() {
        guard !isListening else { return }
        capture.mutePCMForSTT = false
        capture.stop()
        #if targetEnvironment(macCatalyst)
        if !isAssistantSpeaking {
            AudioCaptureService.releaseSession()
        }
        glasses.disableRemoteControls()
        #else
        if !isAssistantSpeaking {
            try? AudioCaptureService.applyIdleRemoteSession()
        }
        glasses.enableRemoteControls()
        glasses.updateNowPlaying(live: false, micPaused: false, ttsMuted: false)
        glasses.startKeepAlive()
        #endif
    }

    /// Glasses temple tap. In continuous talk: mute or unmute your mic only (its
    /// voice keeps playing). Off and on screen: start continuous talk. Off and
    /// locked: the low tone, so you can hear that it is not listening.
    func togglePauseFromGlasses() {
        let now = Date()
        if now.timeIntervalSince(lastRawTapAt) < tapDedup { return }
        lastRawTapAt = now
        let continuous = isListening && !oneShot
        let onScreen = UIApplication.shared.applicationState == .active
        VoiceDebug.shared.log("glasses tap continuous=\(continuous) paused=\(isPaused) onScreen=\(onScreen)")
        if continuous {
            if isPaused {
                resumeSending()
            } else {
                pauseSending()
            }
        } else if onScreen {
            onRequestLiveConversation?()
        } else {
            playListenCue(.micOff)
        }
    }

    func pauseSending() {
        guard isListening, !isPaused else { return }
        // Close the phrase you were saying and send it, then stop the mic only.
        allowFinalWhilePaused = true
        stt.forceFinalizeFromSilence()
        isPaused = true
        capture.mutePCMForSTT = true
        micLevel = 0
        micLevelLabel = "mic off"
        vadStatus = ""
        partialText = ""
        refreshGlassesNowPlaying()
        refreshDuplexStatus()
        playListenCue(.micOff)
        onPauseChanged?(true)
    }

    func resumeSending() {
        guard isListening, isPaused else { return }
        isPaused = false
        allowFinalWhilePaused = false
        vad.reset(
            silenceSeconds: settings.silenceSeconds,
            maxUtteranceSeconds: settings.maxUtteranceSeconds
        )
        refreshGlassesNowPlaying()
        refreshDuplexStatus()
        armEarconMute(.micOn)
        onPauseChanged?(false)
    }

    func toggleTTSMute() {
        if isTTSMuted {
            isTTSMuted = false
            refreshGlassesNowPlaying()
            refreshDuplexStatus()
            pauseCues.play(.ttsOn)
            onTTSMuteChanged?(false)
        } else {
            isTTSMuted = true
            discardPendingSpeech()
            refreshGlassesNowPlaying()
            refreshDuplexStatus()
            pauseCues.play(.ttsOff)
            onTTSMuteChanged?(true)
        }
    }

    private func refreshGlassesNowPlaying() {
        glasses.updateNowPlaying(live: true, micPaused: isPaused, ttsMuted: isTTSMuted)
    }

    /// Play the listen-state chime through the mic engine when it is up, so the
    /// glasses speakers get it and echo cancellation can remove it. Otherwise
    /// play it on the current route. The mic is muted separately for the same window.
    private func playListenCue(_ cue: PauseCuePlayer.Cue) {
        if let buffer = PauseCuePlayer.buffer(for: cue), capture.playCue(buffer) {
            VoiceDebug.shared.log("listen cue \(cue) engine")
            return
        }
        pauseCues.playEarcon(cue)
        VoiceDebug.shared.log("listen cue \(cue) speaker")
    }

    /// After the mic engine is up, wait until PCM is actually flowing, then play the
    /// rising ready chime. Skips the sound on a cancelled listen or a silent false start.
    /// Talk button (one-shot) and hands-free share this path.
    private func scheduleListeningReadyCue() {
        earconToken += 1
        let token = earconToken
        Task { @MainActor [weak self] in
            guard let self else { return }
            let flowing = await self.capture.waitForFirstBuffer(timeout: 2.5)
            guard token == self.earconToken else { return }
            guard self.isListening, !self.isPaused else {
                VoiceDebug.shared.log("ready cue skipped (listen ended before mic audio)")
                return
            }
            guard flowing else {
                // Engine said it started but no buffer arrived — do not cue a false ready.
                VoiceDebug.shared.log("ready cue skipped (no mic audio)")
                if self.isListening, !self.isPaused {
                    self.capture.mutePCMForSTT = false
                    self.statusText = self.oneShot
                        ? "Talk — pause to send"
                        : "Listening. Just talk. Tap the glasses when you're done."
                }
                return
            }
            VoiceDebug.shared.log("ready cue (mic audio live, oneShot=\(self.oneShot))")
            self.armEarconMute(.micOn)
            self.statusText = self.oneShot
                ? "Talk — pause to send"
                : "Listening. Just talk. Tap the glasses when you're done."
        }
    }

    /// Mute speech-to-text for the chime, then open the mic again if you are still the one talking.
    private func armEarconMute(_ cue: PauseCuePlayer.Cue) {
        earconToken += 1
        let token = earconToken
        capture.mutePCMForSTT = true
        stt.resetUtterance()
        partialText = ""
        playListenCue(cue)
        let delay = PauseCuePlayer.earconSeconds(cue)
        Task { @MainActor in
            try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000))
            guard token == self.earconToken else { return }
            guard self.isListening, !self.isPaused else { return }
            self.stt.resetUtterance()
            self.partialText = ""
            self.capture.mutePCMForSTT = false
        }
    }

    private func refreshDuplexStatus() {
        if isPaused, isTTSMuted {
            statusText = "Not listening · voice muted"
        } else if isPaused {
            statusText = "Not listening · tap the glasses to talk"
        } else if isTTSMuted {
            statusText = "Listening · voice muted"
        } else {
            statusText = "Listening. Just talk. Tap the glasses when you're done."
        }
    }

    func stopAll() {
        stopListening()
        discardPendingSpeech()
    }

    /// Drop in-flight / queued TTS without touching the mic (duplex send-now).
    func cancelPendingSpeech() {
        discardPendingSpeech()
        if isListening {
            refreshDuplexStatus()
        }
    }

    // MARK: - STT → immediate request

    private func handleSilenceEnd() {
        guard isListening else { return }
        if isPaused, !allowFinalWhilePaused { return }
        let text = partialText.trimmingCharacters(in: .whitespacesAndNewlines)
        let hasText = (!text.isEmpty && text != "…") || stt.hasPartial
        let buffered = settings.sttProvider.isBuffered
        let buf = stt.cloudBufferSeconds
        guard hasText || (buffered && buf > 0.3) else { return }
        stt.forceFinalizeFromSilence()
    }

    private func showHearing(_ text: String) {
        let preview = text.count > 48 ? String(text.prefix(48)) + "…" : text
        statusText = "Hearing: \(preview)"
    }

    /// What it has said out loud in the last half minute, including the line playing now.
    private func spokenText() -> String {
        let cutoff = Date().addingTimeInterval(-30)
        return recentSpeech.filter { $0.at > cutoff }.map(\.text).joined(separator: " ")
    }

    private func handleFinalUtterance(_ raw: String) {
        VoiceDebug.shared.log("final \"\(raw.prefix(80))\" paused=\(isPaused) speaking=\(isAssistantSpeaking)")
        if isPaused, !allowFinalWhilePaused { return }
        allowFinalWhilePaused = false
        var cleaned = STTService.cleanTranscription(raw)
        let now = Date()
        // A second, shorter copy of the sentence you just said ("You keep on." after the full line).
        if let last = recentSources.last, now.timeIntervalSince(last.at) < 6,
           EchoGuard.isFragment(of: last.text, fragment: cleaned) {
            VoiceDebug.shared.droppedAsEcho += 1
            VoiceDebug.shared.log("echo dropped fragment \"\(cleaned.prefix(60))\"")
            partialText = ""
            return
        }
        // A cut-in stops playback before this transcript arrives, so "is it speaking"
        // is already false. Keep the stricter check for that one final.
        let guarding = isAssistantSpeaking || cutInForNextFinal
        cutInForNextFinal = false
        let decision = EchoGuard.decide(heard: cleaned, spoken: spokenText(), guarding: guarding)
        guard !decision.send.isEmpty else {
            VoiceDebug.shared.droppedAsEcho += 1
            VoiceDebug.shared.log("echo dropped \"\(cleaned.prefix(80))\"")
            partialText = ""
            return
        }
        if decision.barge, isAssistantSpeaking {
            bargeIn()
        }
        cleaned = decision.send
        let minChars = max(2, settings.minCharsToSend)
        guard cleaned.count >= minChars else { return }
        if recentSources.contains(where: { similar($0.text, cleaned) && now.timeIntervalSince($0.at) < 4 }) {
            return
        }
        if settings.filterDuplicates, recentSources.contains(where: { similar($0.text, cleaned) }) {
            return
        }
        recentSources.append((cleaned, now))
        if recentSources.count > 24 { recentSources.removeFirst() }
        partialText = cleaned
        statusText = "Sent: \(cleaned.count > 40 ? String(cleaned.prefix(40)) + "…" : cleaned)"
        onUtterance?(cleaned)

        if oneShot {
            // Keep duplex listen off after a single tap-to-talk turn.
            stopListening()
        }
    }

    // MARK: - Parallel TTS + ordered play

    /// Enqueue a reply to speak. One-shot / typed Speak keeps order. `tag` names the reply
    /// (the desk's reply id) so a barge-in can say which reply was cut and what was heard.
    /// `force` speaks it even when spoken replies are off.
    func enqueueSpeak(_ text: String, tag: String? = nil, force: Bool = false) {
        guard !isTTSMuted else { return }
        guard force || settings.ttsEnabled else { return }
        let cleaned = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleaned.isEmpty else { return }

        let seq = nextSpeakSeq
        nextSpeakSeq += 1
        speakSlots[seq] = SpeakSlot(seq: seq, text: cleaned, tag: tag, force: force)
        forgetOldTags(keeping: tag)
        pumpTTS()
    }

    private func forgetOldTags(keeping tag: String?) {
        guard let tag, heardByTag[tag] == nil, heardByTag.count > 8 else { return }
        heardByTag.removeAll()
    }

    /// Duplex: drop any backlog and speak only this reply. Latest wins.
    func speakLatest(_ text: String, tag: String? = nil) {
        guard !isTTSMuted else { return }
        guard settings.ttsEnabled else { return }
        let cleaned = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !cleaned.isEmpty else { return }

        discardPendingSpeech()
        let seq = nextSpeakSeq
        nextSpeakSeq += 1
        speakSlots[seq] = SpeakSlot(seq: seq, text: cleaned, tag: tag)
        forgetOldTags(keeping: tag)
        speakCursor = seq
        if isListening {
            statusText = "Speaking latest"
        }
        pumpTTS()
    }

    private func pumpTTS() {
        let need = speakSlots.values
            .filter { !$0.done && !$0.failed && $0.clip == nil }
            .sorted { $0.seq < $1.seq }
        for slot in need {
            if ttsInFlight >= maxParallelTTS { break }
            if launchingTTS.contains(slot.seq) { continue }
            launchingTTS.insert(slot.seq)
            ttsInFlight += 1
            let seq = slot.seq
            let text = slot.text
            let force = slot.force
            let lang = settings.speechLanguage
            let gen = speakGeneration
            Task { [weak self] in
                guard let self else { return }
                do {
                    let clip = try await self.tts.synthesize(text, language: lang, force: force)
                    guard self.speakGeneration == gen else {
                        if case .stream(let stream) = clip.kind { stream.cancel() }
                        return
                    }
                    self.launchingTTS.remove(seq)
                    self.ttsInFlight = max(0, self.ttsInFlight - 1)
                    if var s = self.speakSlots[seq] {
                        s.clip = clip
                        s.done = true
                        self.speakSlots[seq] = s
                    }
                    self.pumpPlayback()
                    self.pumpTTS()
                    self.refreshStats()
                } catch {
                    guard self.speakGeneration == gen else { return }
                    // Apple fallback so a Fish/Azure outage still talks.
                    self.launchingTTS.remove(seq)
                    self.ttsInFlight = max(0, self.ttsInFlight - 1)
                    let plain = TTSService.stripFishEmotionTags(text)
                    if var s = self.speakSlots[seq] {
                        s.clip = TTSService.PreparedClip(
                            kind: .appleUtterance(plain),
                            language: lang,
                            text: plain
                        )
                        s.done = true
                        self.speakSlots[seq] = s
                    }
                    self.pumpPlayback()
                    self.pumpTTS()
                    self.refreshStats()
                }
            }
        }
        pumpPlayback()
        refreshStats()
    }

    private func pumpPlayback() {
        guard !isTTSMuted else { return }
        guard !playbackRunning else { return }
        advanceCursorPastFailed()
        guard let slot = speakSlots[speakCursor],
              slot.done,
              !slot.failed,
              let clip = slot.clip else {
            refreshStats()
            return
        }
        playbackRunning = true
        let seq = slot.seq
        let gen = speakGeneration
        playingSlot = slot
        recentSpeech.append((slot.text, Date()))
        if recentSpeech.count > 12 { recentSpeech.removeFirst() }
        statusText = isListening ? "Speaking · talk to cut in" : "Speaking"
        Task {
            await self.tts.playPrepared(clip)
            await MainActor.run {
                guard self.speakGeneration == gen else { return }
                if let tag = slot.tag {
                    self.heardByTag[tag] = [self.heardByTag[tag], slot.text].compactMap { $0 }.joined(separator: " ")
                }
                self.playingSlot = nil
                self.speakSlots[seq] = nil
                self.speakCursor = seq + 1
                self.playbackRunning = false
                self.advanceCursorPastFailed()
                self.pumpPlayback()
                self.pumpTTS()
                self.refreshStats()
                if self.isListening {
                    self.refreshDuplexStatus()
                }
                if !self.isAssistantSpeaking {
                    self.speechDrained()
                }
            }
        }
    }

    // MARK: - Barge-in

    /// The desk has speech playing or queued.
    var isAssistantSpeaking: Bool {
        playbackRunning || speakSlots.values.contains { !$0.failed }
    }

    /// The words of a tagged reply you have heard so far, including the part of the clip playing now.
    func heardText(for tag: String) -> String {
        var parts: [String] = []
        if let done = heardByTag[tag] { parts.append(done) }
        if let slot = playingSlot, slot.tag == tag {
            let words = slot.text.split(whereSeparator: \.isWhitespace)
            let count = Int((Double(words.count) * tts.playbackFraction).rounded(.down))
            if count > 0 { parts.append(words.prefix(count).joined(separator: " ")) }
        }
        return parts.joined(separator: " ")
    }

    /// Stop the desk mid-sentence because you started talking (or tapped the mic).
    func bargeIn() {
        guard isAssistantSpeaking else { return }
        cutInForNextFinal = true
        let tag = playingSlot?.tag ?? speakSlots.values.sorted { $0.seq < $1.seq }.first?.tag
        let heard = tag.map { heardText(for: $0) } ?? ""
        discardPendingSpeech()
        statusText = "Go ahead…"
        vad.reset(silenceSeconds: settings.silenceSeconds, maxUtteranceSeconds: settings.maxUtteranceSeconds)
        onBargeIn?(tag, heard)
    }

    private func speechDrained() {
        if isListening {
            // A partial that is still its line must not start your next sentence.
            // An empty partial is left alone: you may already be talking, and the final
            // filter drops its transcript if that is all the recognizer has.
            if EchoGuard.isSpeakerEcho(partialText, spoken: spokenText()) {
                stt.resetUtterance()
                partialText = ""
            }
        } else {
            #if targetEnvironment(macCatalyst)
            AudioCaptureService.releaseSession()
            #else
            try? AudioCaptureService.applyIdleRemoteSession()
            glasses.startKeepAlive()
            glasses.updateNowPlaying(live: false, micPaused: false, ttsMuted: false)
            #endif
        }
    }

    private func advanceCursorPastFailed() {
        while let s = speakSlots[speakCursor], s.failed {
            speakCursor += 1
        }
    }

    private func discardPendingSpeech() {
        speakGeneration += 1
        tts.stop()
        playingSlot = nil
        resetSpeakQueue()
    }

    private func resetSpeakQueue() {
        for slot in speakSlots.values {
            if case .stream(let stream)? = slot.clip?.kind { stream.cancel() }
        }
        speakSlots.removeAll()
        nextSpeakSeq = 1
        speakCursor = 1
        ttsInFlight = 0
        playbackRunning = false
        launchingTTS.removeAll()
        queueDepth = 0
        parallelStats = ""
    }

    private func refreshStats() {
        let pendingPlay = speakSlots.values.filter { $0.done && !$0.failed && $0.seq >= speakCursor }.count
        queueDepth = pendingPlay + ttsInFlight
        parallelStats = "TTS×\(ttsInFlight) play@\(speakCursor) q=\(queueDepth)"
    }

    private func similar(_ a: String, _ b: String) -> Bool {
        let x = a.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        let y = b.lowercased().trimmingCharacters(in: .whitespacesAndNewlines)
        if x == y { return true }
        if x.count > 12, y.contains(x) || x.contains(y) { return true }
        return false
    }
}
