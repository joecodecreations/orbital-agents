import AVFoundation
import Foundation

/// Always-on PCM capture with Apple voice processing (echo cancellation + noise suppression).
///
/// While the mic is on, spoken replies play through this same engine (`play(fileURL:)`,
/// `play(buffers:)`), so the voice-processing unit knows exactly what the speaker is saying and
/// cancels it from the mic. That is what lets you talk over a reply without the app hearing itself.
///
/// The speech gate only labels the level and feeds the silence detector. It never drops audio
/// on its way to speech-to-text: it calibrates on the first quarter second, so when you started
/// talking right after tapping the mic it learned your voice as "noise" and the recognizer got nothing.
@MainActor
final class AudioCaptureService: NSObject, ObservableObject {
    @Published var isRunning = false
    @Published var level: Float = 0
    @Published var levelDbLabel: String = "—"
    @Published var activeRouteLabel: String = "—"
    @Published var inputPortName: String = "—"
    @Published var voiceProcessingEnabled = false
    @Published var noiseGateStatus: String = "—"
    @Published var speechGateOpen = false

    private var engine: AVAudioEngine?
    private var converter: AVAudioConverter?
    var onPCM: ((Data) -> Void)?
    var onLevel: ((Float) -> Void)?
    var onDb: ((Float) -> Void)?

    var mutePCMForSTT = false

    /// Continuous talk stays play-and-record while the phone is locked, including
    /// when your mic is muted. Its playback uses this same session. One-shot dictation
    /// leaves this off.
    private(set) var maintainsDuplexSession = false
    private var holdWatchdog: Task<Void, Never>?
    private var holdRestarts = 0
    private var holdGaveUpUntil: CFAbsoluteTime = 0
    private var startingEngine = false
    private var lastEnsureAt: CFAbsoluteTime = 0

    var speechGateEnabled = true {
        didSet { noiseGate.setEnabled(speechGateEnabled) }
    }

    var preferVoiceProcessing = true

    private let noiseGate = SpeechNoiseGate()
    private let targetSampleRate: Double = 16_000
    private var displayLevel: Float = 0
    private var lastLevelPublish: CFAbsoluteTime = 0
    private var lastDb: Float = -80
    private var gateOpenForPCM = true

    // Keeping the mic alive. On a real iPhone, iOS reconfigures the audio hardware right after
    // the engine starts (voice processing, route, sample rate) and the engine stops itself.
    // Nothing restarted it, so "Listening…" never got a single buffer.
    private var configObserver: NSObjectProtocol?
    private var lastInput: AudioInputSource = .auto
    private var lastBufferAt: CFAbsoluteTime = 0
    private var startedAt: CFAbsoluteTime = 0
    private var restarts: [CFAbsoluteTime] = []
    private var silentStarts = 0
    private var skipVoiceProcessing = false
    private var watchdog: Task<Void, Never>?

    // Playback through the voice-processing engine.
    private var player: AVAudioPlayerNode?
    /// Chimes play here so they come out the glasses and echo cancellation hears them.
    /// Separate from `player`, which is its reply: a chime must not count as its speaking.
    private var cuePlayer: AVAudioPlayerNode?
    private var playerFormat: AVAudioFormat?
    private var playToken = 0
    private var playContinuation: CheckedContinuation<Void, Never>?
    private var playStartedAt: Date?
    private var playDuration: TimeInterval = 0
    /// A reply is playing through the engine right now.
    @Published private(set) var isPlaying = false

    /// How much of the current clip has played, 0...1, for "what did you hear before you cut in".
    var playbackFraction: Double {
        guard isPlaying, let start = playStartedAt, playDuration > 0 else { return 0 }
        return min(1, max(0, Date().timeIntervalSince(start) / playDuration))
    }

    /// Engine playback is available (the mic is on).
    var canPlay: Bool { engine?.isRunning == true && player != nil && playerFormat != nil }

    /// True once this start has delivered at least one mic buffer (engine may be "running" before that).
    var hasMicAudio: Bool {
        isRunning && lastBufferAt > 0 && lastBufferAt >= startedAt
    }

    /// Wait until mic PCM is actually flowing, or `timeout` elapses. Used so the ready chime
    /// plays only when listening is live — not on button press, and not on a silent false start.
    func waitForFirstBuffer(timeout: TimeInterval = 2.5) async -> Bool {
        if hasMicAudio { return true }
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if hasMicAudio { return true }
            if !isRunning { return false }
            try? await Task.sleep(nanoseconds: 40_000_000)
        }
        return hasMicAudio
    }

    /// Give the audio session back when voice is off. Holding a play-and-record session after
    /// the conversation ends mutes notification sounds, like being on a call.
    static func releaseSession() {
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    func requestPermission() async -> Bool {
        await withCheckedContinuation { cont in
            if #available(iOS 17.0, *) {
                AVAudioApplication.requestRecordPermission { ok in
                    cont.resume(returning: ok)
                }
            } else {
                AVAudioSession.sharedInstance().requestRecordPermission { ok in
                    cont.resume(returning: ok)
                }
            }
        }
    }

    /// Called after a route change so ConversationSession can reclaim glasses.
    var onRouteChanged: (() -> Void)?

    private var routeObserver: NSObjectProtocol?
    private var interruptionObserver: NSObjectProtocol?
    private var lastRouteAssertAt: CFAbsoluteTime = 0

    /// Shared playAndRecord route.
    ///
    /// Glasses stay on their HFP input (iOS has no setPreferredOutput). CarPlay
    /// will steal the route unless we reclaim it after every route change.
    ///
    /// A normal Bluetooth headset must NOT use `.voiceChat` or `.videoChat`.
    /// Those modes force the phone-call profile (HFP). The headset's own gain
    /// control and the iPhone's voice-processing gain control then ride the same
    /// words, so a reply gets louder and quieter while it is still talking.
    /// Music playback (A2DP) keeps one steady level.
    ///
    /// The built-in speaker uses `.videoChat`: the loud speakerphone curve, and
    /// voice processing can still cancel the reply. `.voiceChat` is the call
    /// curve, which stays quiet in a car even with the side buttons up.
    static func applySharedSession(preferGlassesOutput: Bool) throws {
        let session = AVAudioSession.sharedInstance()
        if sessionAlreadyMatches(preferGlassesOutput: preferGlassesOutput) {
            // The session may have been released after the last reply; make sure it's live.
            try session.setActive(true, options: [])
            SpeakerBoost.shared.refresh()
            return
        }

        let car = carPlayOutputConnected(session) || carPlayAvailable(in: session)
        let musicHeadphones = !preferGlassesOutput && !car && genericBluetoothHeadphonesConnected(session)
        let headsetMic = !preferGlassesOutput && !car && userWantsHeadsetMic()

        let options: AVAudioSession.CategoryOptions
        let mode: AVAudioSession.Mode
        if preferGlassesOutput || headsetMic {
            options = [.allowBluetooth, .allowBluetoothA2DP]
            // `.default` lets setPreferredInput move HFP onto the glasses.
            // `.voiceChat` would also turn on a second gain rider.
            mode = .default
        } else if musicHeadphones {
            options = [.allowBluetoothA2DP, .defaultToSpeaker]
            mode = .default
        } else {
            options = [.allowBluetoothA2DP, .defaultToSpeaker]
            mode = .videoChat
        }
        try session.setCategory(.playAndRecord, mode: mode, options: options)
        try session.setActive(true, options: [])
        try? session.setPreferredSampleRate(48_000)
        try? session.setPreferredIOBufferDuration(0.02)

        if preferGlassesOutput {
            try? session.overrideOutputAudioPort(.none)
            forceGlassesInput(session)
            if carPlayOutputConnected(), glassesInput(in: session) != nil {
                try? session.setActive(false)
                try session.setCategory(.playAndRecord, mode: .default, options: [
                    .allowBluetooth,
                    .allowBluetoothA2DP,
                ])
                try session.setActive(true, options: [])
                try? session.overrideOutputAudioPort(.none)
                forceGlassesInput(session)
            }
        } else if car || musicHeadphones || headsetMic {
            // Forcing the speaker here would pull a reply out of the headphones.
            try? session.overrideOutputAudioPort(.none)
        } else {
            try? session.overrideOutputAudioPort(.speaker)
        }
        SpeakerBoost.shared.refresh()
    }

    /// Playback-only session so a glasses temple tap still reaches us after a
    /// conversation ends, without holding a phone-call route (that mutes other sounds).
    static func applyIdleRemoteSession() throws {
        let session = AVAudioSession.sharedInstance()
        if session.category == .playback, session.mode == .default {
            try session.setActive(true, options: [])
            return
        }
        try session.setCategory(.playback, mode: .default, options: [.allowBluetoothA2DP])
        try session.setActive(true, options: [])
    }

    /// Keep the duplex session alive across the lock screen. Your mic mute only
    /// stops speech-to-text; the engine keeps running so iOS does not suspend us
    /// and its voice still has a route to the glasses.
    func setMaintainsDuplexSession(_ on: Bool) {
        maintainsDuplexSession = on
        holdWatchdog?.cancel()
        holdWatchdog = nil
        holdRestarts = 0
        holdGaveUpUntil = 0
        if #available(iOS 14.5, *) {
            try? AVAudioSession.sharedInstance().setPrefersNoInterruptionsFromSystemAlerts(on)
        }
        guard on else { return }
        holdWatchdog = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 2_000_000_000)
                guard let self, self.maintainsDuplexSession else { return }
                let now = CFAbsoluteTimeGetCurrent()
                if now < self.holdGaveUpUntil { continue }
                let age = now - self.startedAt
                let noBuffers = self.lastBufferAt == 0 && age > 3 && self.startedAt > 0
                let stalled = self.lastBufferAt > 0 && now - self.lastBufferAt > 3
                let dead = self.engine?.isRunning != true || noBuffers || stalled
                guard dead else {
                    self.holdRestarts = 0
                    continue
                }
                self.holdRestarts += 1
                if self.holdRestarts > 8 {
                    self.holdGaveUpUntil = now + 15
                    self.holdRestarts = 0
                    VoiceDebug.shared.log("duplex hold: engine stalled, pausing restarts for 15s")
                    continue
                }
                VoiceDebug.shared.log("duplex hold: engine stalled (running=\(self.engine?.isRunning ?? false)), restart \(self.holdRestarts)")
                self.ensureEngineRunning()
            }
        }
    }

    /// Rebuild the mic engine if the lock screen stopped it. No-op while a start is already in flight.
    func ensureEngineRunning() {
        guard maintainsDuplexSession || isRunning else { return }
        let prefer = shouldPreferGlassesOutput()
        try? Self.applySharedSession(preferGlassesOutput: prefer)
        if prefer {
            Self.forceGlassesInput(AVAudioSession.sharedInstance())
        }
        guard engine?.isRunning != true else { return }
        let now = CFAbsoluteTimeGetCurrent()
        if startingEngine || now - lastEnsureAt < 0.5 { return }
        lastEnsureAt = now
        do {
            try startEngine(input: lastInput)
        } catch {
            VoiceDebug.shared.log("ensure engine failed: \(error.localizedDescription)")
        }
    }

    var engineIsRunning: Bool { engine?.isRunning == true }

    /// The live route is the phone itself (speaker or earpiece), or nothing yet.
    /// Headphones, glasses, CarPlay and other ports are external.
    static func phoneBodyOutput(_ session: AVAudioSession = .sharedInstance()) -> Bool {
        let outs = session.currentRoute.outputs
        if outs.isEmpty { return true }
        return outs.allSatisfy { $0.portType == .builtInSpeaker || $0.portType == .builtInReceiver }
    }

    /// Spoken reply with the mic closed. Not a call, so don't use the call profile.
    ///
    /// On the phone speaker this is plain playback, which follows the side-button
    /// volume at full level. An external route keeps `.spokenAudio` and is not boosted.
    static func applySpokenPlaybackSession() throws {
        let session = AVAudioSession.sharedInstance()
        func setMode(_ mode: AVAudioSession.Mode) throws {
            if session.category == .playback, session.mode == mode {
                try session.setActive(true, options: [])
            } else {
                // allowBluetoothA2DP lets headphones take the route. Do not
                // override to the speaker here: that would pull sound out of them.
                try session.setCategory(.playback, mode: mode, options: [.allowBluetoothA2DP])
                try session.setActive(true, options: [])
            }
        }
        // The route is known after activation. Plain playback on the phone is
        // louder than spoken-audio. An external route keeps spoken-audio.
        let wantSpeaker = phoneBodyOutput(session)
        try setMode(wantSpeaker ? .default : .spokenAudio)
        let speaker = phoneBodyOutput(session)
        if speaker != wantSpeaker {
            try setMode(speaker ? .default : .spokenAudio)
        }
        SpeakerBoost.shared.refresh()
    }

    func configureSession(input: AudioInputSource) throws {
        startWatchingRouteIfNeeded()
        let preferGlasses = shouldPreferGlassesOutput()
        try Self.applySharedSession(preferGlassesOutput: preferGlasses)

        let session = AVAudioSession.sharedInstance()
        if preferGlasses {
            // Phone-mic preference would yank output back to CarPlay / speaker.
            Self.forceGlassesInput(session)
        } else {
            switch input {
            case .phoneMic, .auto:
                if let builtIn = session.availableInputs?.first(where: { $0.portType == .builtInMic }) {
                    try? session.setPreferredInput(builtIn)
                }
            case .bluetoothHFP:
                if let hfp = session.availableInputs?.first(where: { $0.portType == .bluetoothHFP }) {
                    try? session.setPreferredInput(hfp)
                }
            }
        }
        updateRouteLabel()
    }

    func applyOutputPreference() {
        let now = CFAbsoluteTimeGetCurrent()
        if now - lastRouteAssertAt < 0.35 { return }
        lastRouteAssertAt = now
        let preferGlasses = shouldPreferGlassesOutput()
        try? Self.applySharedSession(preferGlassesOutput: preferGlasses)
        if preferGlasses {
            Self.forceGlassesInput(AVAudioSession.sharedInstance())
        }
        updateRouteLabel()
    }

    func shouldPreferGlassesOutput() -> Bool {
        Self.preferGlassesOutputNow()
    }

    static func preferGlassesOutputNow() -> Bool {
        switch SpeechSettings.shared.audioOutputRoute {
        case .glasses:
            // Glasses picked but not connected: fall back to the speaker, not the earpiece.
            // An unnamed HFP headset counts only for this explicit choice.
            return glassesInput(in: .sharedInstance()) != nil || glassesOnCurrentRoute()
        case .phoneSpeaker:
            return false
        case .auto:
            // Named glasses only. Any other headset (the AKG, for one) is not the glasses,
            // and must not be pulled onto the phone-call profile.
            return glassesAvailable() && !carPlayOutputConnected()
        }
    }

    func currentRouteLooksLikeGlasses() -> Bool {
        Self.glassesOnCurrentRoute()
    }

    static func bluetoothOutputConnected() -> Bool {
        glassesAvailable()
    }

    static func glassesAvailable() -> Bool {
        let session = AVAudioSession.sharedInstance()
        let ports = (session.availableInputs ?? []) + session.currentRoute.inputs + session.currentRoute.outputs
        return ports.contains(where: isGlassesPort)
    }

    static func glassesOnCurrentRoute(_ session: AVAudioSession = .sharedInstance()) -> Bool {
        (session.currentRoute.outputs + session.currentRoute.inputs).contains(where: isGlassesPort)
    }

    static func carPlayOutputConnected(_ session: AVAudioSession = .sharedInstance()) -> Bool {
        session.currentRoute.outputs.contains(where: isCarPlayPort)
    }

    static func carPlayAvailable(in session: AVAudioSession) -> Bool {
        if carPlayOutputConnected(session) { return true }
        return session.currentRoute.outputs.contains(where: isCarPlayPort)
            || (session.availableInputs ?? []).contains(where: isCarPlayPort)
    }

    static func isGlassesPort(_ port: AVAudioSessionPortDescription) -> Bool {
        let n = port.portName.lowercased()
        if n.contains("ray-ban") || n.contains("rayban")
            || n.contains("meta") || n.contains("glasses")
            || n.contains("stories")
        {
            return true
        }
        return false
    }

    static func isCarPlayPort(_ port: AVAudioSessionPortDescription) -> Bool {
        if port.portType == .carAudio { return true }
        let n = port.portName.lowercased()
        return n.contains("carplay") || n.contains("car play")
    }

    static func isBluetoothHeadset(_ port: AVAudioSessionPortDescription) -> Bool {
        port.portType == .bluetoothA2DP
            || port.portType == .bluetoothHFP
            || port.portType == .bluetoothLE
    }

    /// The live route is a headset that is not the glasses and not the car.
    /// Paired-but-idle headphones do not count: Auto still plays from the speaker
    /// until the headset is actually the route.
    static func genericBluetoothHeadphonesConnected(_ session: AVAudioSession = .sharedInstance()) -> Bool {
        if SpeechSettings.shared.audioOutputRoute == .phoneSpeaker { return false }
        if userWantsHeadsetMic() { return false }
        let ports = session.currentRoute.inputs + session.currentRoute.outputs
        return ports.contains { port in
            isBluetoothHeadset(port) && !isGlassesPort(port) && !isCarPlayPort(port)
        }
    }

    /// Mic picker set to the headset. Glasses use their own route.
    static func userWantsHeadsetMic() -> Bool {
        SpeechSettings.shared.audioInputSource == .bluetoothHFP
    }

    /// iPhone voice processing on top of a headset's own call processing pumps the volume.
    static func skipVoiceProcessingForRoute() -> Bool {
        if preferGlassesOutputNow() { return false }
        if genericBluetoothHeadphonesConnected() || userWantsHeadsetMic() { return true }
        let session = AVAudioSession.sharedInstance()
        let ports = session.currentRoute.inputs + session.currentRoute.outputs
        return ports.contains { $0.portType == .bluetoothHFP && !isGlassesPort($0) && !isCarPlayPort($0) }
    }

    static func glassesInput(in session: AVAudioSession) -> AVAudioSessionPortDescription? {
        let inputs = session.availableInputs ?? []
        if let named = inputs.first(where: isGlassesPort) { return named }
        // Explicit Glasses choice only. Auto used to treat every HFP headset as the
        // glasses, which put the AKG on the call profile and pumped the reply.
        guard SpeechSettings.shared.audioOutputRoute == .glasses else { return nil }
        return inputs.first(where: { $0.portType == .bluetoothHFP && !isCarPlayPort($0) })
    }

    static func forceGlassesInput(_ session: AVAudioSession) {
        if let glasses = glassesInput(in: session) {
            try? session.setPreferredInput(glasses)
        }
    }

    private static func sessionAlreadyMatches(preferGlassesOutput: Bool) -> Bool {
        let session = AVAudioSession.sharedInstance()
        guard session.category == .playAndRecord else { return false }
        if preferGlassesOutput {
            return session.mode == .default
                && glassesOnCurrentRoute(session)
                && !carPlayOutputConnected(session)
        }
        if carPlayOutputConnected(session) || carPlayAvailable(in: session) { return true }
        if genericBluetoothHeadphonesConnected(session) {
            let outputs = session.currentRoute.outputs
            let music = outputs.contains { $0.portType == .bluetoothA2DP || $0.portType == .bluetoothLE }
            let call = outputs.contains { $0.portType == .bluetoothHFP }
            return session.mode == .default && music && !call
        }
        if userWantsHeadsetMic() {
            return session.mode == .default
                && session.currentRoute.inputs.contains { $0.portType == .bluetoothHFP }
        }
        let speaker = session.currentRoute.outputs.contains { $0.portType == .builtInSpeaker }
        return session.mode == .videoChat && speaker
    }

    private func startWatchingRouteIfNeeded() {
        if interruptionObserver == nil {
            interruptionObserver = NotificationCenter.default.addObserver(
                forName: AVAudioSession.interruptionNotification,
                object: nil,
                queue: .main
            ) { [weak self] note in
                let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
                let ended = raw.flatMap(AVAudioSession.InterruptionType.init(rawValue:)) == .ended
                Task { @MainActor in
                    self?.handleInterruption(ended: ended)
                }
            }
        }
        guard routeObserver == nil else { return }
        routeObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in
                self?.handleRouteChange()
            }
        }
    }

    /// A call, Siri or an alarm took the audio. On a locked phone nothing else
    /// would bring the mic back, so restart it as soon as the session is ours again.
    private func handleInterruption(ended: Bool) {
        VoiceDebug.shared.log("audio interruption \(ended ? "ended" : "began") hold=\(maintainsDuplexSession)")
        guard ended, maintainsDuplexSession else { return }
        holdRestarts = 0
        holdGaveUpUntil = 0
        ensureEngineRunning()
    }

    private func handleRouteChange() {
        updateRouteLabel()
        SpeakerBoost.shared.refresh()
        let prefer = shouldPreferGlassesOutput()
        // Only a live play-and-record session. A mic-off reply is plain playback;
        // reapplying here would pull it back onto the call route.
        // Headphones plugged in mid-reply must leave the speakerphone curve,
        // or the call profile (and the speaker boost) would follow them.
        let session = AVAudioSession.sharedInstance()
        if !prefer, session.category == .playAndRecord, !Self.sessionAlreadyMatches(preferGlassesOutput: false) {
            applyOutputPreference()
        }
        if prefer, Self.carPlayOutputConnected() || !Self.glassesOnCurrentRoute() {
            applyOutputPreference()
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: 450_000_000)
                guard self.shouldPreferGlassesOutput() else { return }
                if Self.carPlayOutputConnected() || !Self.glassesOnCurrentRoute() {
                    self.lastRouteAssertAt = 0
                    self.applyOutputPreference()
                }
            }
        }
        if maintainsDuplexSession, engine?.isRunning != true {
            ensureEngineRunning()
        }
        onRouteChanged?()
    }

    func start(input: AudioInputSource) throws {
        silentStarts = 0
        skipVoiceProcessing = false
        restarts = []
        try startEngine(input: input)
    }

    private func startEngine(input: AudioInputSource) throws {
        if startingEngine { return }
        startingEngine = true
        defer { startingEngine = false }
        stop()
        lastInput = input
        try configureSession(input: input)

        let engine = AVAudioEngine()
        let inputNode = engine.inputNode

        var vpOn = false
        let skipForRoute = Self.skipVoiceProcessingForRoute()
        if preferVoiceProcessing, !skipVoiceProcessing, !skipForRoute {
            do {
                try inputNode.setVoiceProcessingEnabled(true)
                vpOn = true
                // AGC is on by default and turns playback up and down to chase the mic.
                // Echo cancellation stays on; only the gain rider is off.
                inputNode.isVoiceProcessingAGCEnabled = false
                if #available(iOS 17.0, *) {
                    var duck = AVAudioVoiceProcessingOtherAudioDuckingConfiguration()
                    duck.enableAdvancedDucking = false
                    duck.duckingLevel = .min
                    inputNode.voiceProcessingOtherAudioDuckingConfiguration = duck
                }
            } catch {
                vpOn = false
                try? inputNode.setVoiceProcessingEnabled(false)
            }
        }
        voiceProcessingEnabled = vpOn

        let inputFormat = inputNode.outputFormat(forBus: 0)
        guard inputFormat.sampleRate > 0, inputFormat.channelCount > 0 else {
            throw CaptureError.invalidFormat
        }

        let targetFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: targetSampleRate,
            channels: 1,
            interleaved: false
        )!

        converter = AVAudioConverter(from: inputFormat, to: targetFormat)

        // Replies play through this engine so voice processing cancels them from the mic.
        let player = AVAudioPlayerNode()
        engine.attach(player)
        let outRate = engine.outputNode.outputFormat(forBus: 0).sampleRate
        let playFormat = AVAudioFormat(standardFormatWithSampleRate: outRate > 0 ? outRate : 48_000, channels: 1)!
        // Limiter sits on its reply only. Chimes stay at unity so a loud
        // speaker setting does not turn the listen tones into a blast.
        SpeakerBoost.shared.install(on: engine, after: player, format: playFormat)
        let cue = AVAudioPlayerNode()
        engine.attach(cue)
        engine.connect(cue, to: engine.mainMixerNode, format: playFormat)
        self.player = player
        self.cuePlayer = cue
        self.playerFormat = playFormat

        displayLevel = 0
        lastLevelPublish = 0
        noiseGate.setEnabled(speechGateEnabled)
        noiseGate.reset()
        gateOpenForPCM = !speechGateEnabled

        inputNode.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { [weak self] buffer, _ in
            // The engine reuses tap buffers; copy before hopping to the main actor.
            guard let copy = Self.copyBuffer(buffer) else { return }
            Task { @MainActor [weak self] in
                self?.handleBuffer(copy, targetFormat: targetFormat)
            }
        }

        engine.prepare()
        try engine.start()
        SpeakerBoost.shared.refresh()
        player.play()
        cue.play()
        self.engine = engine
        startedAt = CFAbsoluteTimeGetCurrent()
        lastBufferAt = 0
        configObserver = NotificationCenter.default.addObserver(
            forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.engineReconfigured() }
        }
        let why = skipForRoute ? " reason=headset-route" : ""
        VoiceDebug.shared.log("engine started vp=\(vpOn) agc=\(vpOn ? "off" : "n/a")\(why) in=\(inputFormat.sampleRate)Hz/\(inputFormat.channelCount)ch out=\(playFormat.sampleRate)Hz")
        watchdog?.cancel()
        watchdog = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: 1_500_000_000)
            self?.checkAudioArrived()
        }
        isRunning = true
        level = 0
        levelDbLabel = vpOn ? "Listening… VP on" : "Listening… VP off"
        noiseGateStatus = noiseGate.statusLabel
        updateRouteLabel()
    }

    /// iOS changed the hardware under the engine; it has stopped. Build it again.
    private func engineReconfigured() {
        guard isRunning else { return }
        let now = CFAbsoluteTimeGetCurrent()
        restarts = restarts.filter { now - $0 < 10 } + [now]
        VoiceDebug.shared.log("engine reconfigured (running=\(engine?.isRunning ?? false)), restart #\(restarts.count)")
        guard restarts.count <= 4 else {
            VoiceDebug.shared.log("engine keeps reconfiguring; leaving it")
            if maintainsDuplexSession {
                Task { @MainActor [weak self] in
                    try? await Task.sleep(nanoseconds: 1_000_000_000)
                    guard let self, self.maintainsDuplexSession, self.engine?.isRunning != true else { return }
                    self.restarts.removeAll()
                    self.ensureEngineRunning()
                }
            }
            return
        }
        do {
            try startEngine(input: lastInput)
        } catch {
            VoiceDebug.shared.log("engine restart failed: \(error.localizedDescription)")
        }
    }

    /// No mic audio 1.5 s after starting: restart once, then try without voice processing.
    private func checkAudioArrived() {
        guard isRunning, lastBufferAt == 0 else { return }
        silentStarts += 1
        VoiceDebug.shared.log("no mic audio after start (running=\(engine?.isRunning ?? false)), attempt \(silentStarts)")
        guard silentStarts <= 3 else { return }
        if silentStarts >= 2 { skipVoiceProcessing = true }
        do {
            try startEngine(input: lastInput)
        } catch {
            VoiceDebug.shared.log("engine restart failed: \(error.localizedDescription)")
        }
    }

    func stop() {
        watchdog?.cancel()
        watchdog = nil
        if let configObserver {
            NotificationCenter.default.removeObserver(configObserver)
            self.configObserver = nil
        }
        stopPlayback()
        player?.stop()
        player = nil
        cuePlayer?.stop()
        cuePlayer = nil
        playerFormat = nil
        if let engine {
            engine.inputNode.removeTap(onBus: 0)
            try? engine.inputNode.setVoiceProcessingEnabled(false)
            engine.stop()
        }
        engine = nil
        converter = nil
        isRunning = false
        voiceProcessingEnabled = false
        level = 0
        levelDbLabel = "—"
        noiseGateStatus = "—"
        speechGateOpen = false
        displayLevel = 0
        onLevel?(0)
    }

    private func handleBuffer(_ buffer: AVAudioPCMBuffer, targetFormat: AVAudioFormat) {
        lastBufferAt = CFAbsoluteTimeGetCurrent()
        let peak = Self.peakAmplitude(buffer) ?? 0
        let db = 20 * log10(max(peak, 1e-7))
        let normalized = max(0, min(1, (db + 55) / 47))
        if normalized > displayLevel {
            displayLevel = displayLevel * 0.35 + normalized * 0.65
        } else {
            displayLevel = displayLevel * 0.82 + normalized * 0.18
        }

        let now = CFAbsoluteTimeGetCurrent()
        if now - lastLevelPublish >= 0.033 {
            lastLevelPublish = now
            let ui = displayLevel
            lastDb = db
            let pass = self.noiseGate.shouldPass(db: db)
            self.gateOpenForPCM = pass || !self.speechGateEnabled
            self.speechGateOpen = self.noiseGate.isOpen
            self.noiseGateStatus = self.noiseGate.statusLabel

            let label: String
            if !pass && self.speechGateEnabled {
                label = String(format: "noise %.0f dB", db)
            } else if db < -50 {
                label = "quiet"
            } else {
                label = String(format: "%.0f dB", db)
            }
            self.level = ui
            self.levelDbLabel = label
            self.onLevel?(ui)
            if pass || !self.speechGateEnabled {
                self.onDb?(db)
            } else {
                self.onDb?(min(db, self.noiseGate.noiseFloorDb - 1))
            }
        }

        guard !self.mutePCMForSTT else { return }
        self.convertAndEmitPCM(buffer, targetFormat: targetFormat)
    }

    private func convertAndEmitPCM(_ buffer: AVAudioPCMBuffer, targetFormat: AVAudioFormat) {
        let converter = self.converter ?? AVAudioConverter(from: buffer.format, to: targetFormat)
        guard let converter else { return }

        let ratio = targetFormat.sampleRate / buffer.format.sampleRate
        let outFrames = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 32
        guard let outBuffer = AVAudioPCMBuffer(pcmFormat: targetFormat, frameCapacity: outFrames) else { return }

        var error: NSError?
        var consumed = false
        let status = converter.convert(to: outBuffer, error: &error) { _, outStatus in
            if consumed {
                outStatus.pointee = .noDataNow
                return nil
            }
            consumed = true
            outStatus.pointee = .haveData
            return buffer
        }

        guard status != .error, let ch = outBuffer.floatChannelData?[0] else { return }
        let byteCount = Int(outBuffer.frameLength) * MemoryLayout<Float>.size
        let data = Data(bytes: ch, count: byteCount)
        guard !self.mutePCMForSTT else { return }
        self.onPCM?(data)
    }

    // MARK: Playback

    /// Play an audio file (mp3/wav from TTS) through the engine. Returns false if the mic engine
    /// isn't running or the file can't be read, so the caller can use its own player.
    func play(fileURL: URL) async -> Bool {
        guard canPlay, let format = playerFormat,
              let file = try? AVAudioFile(forReading: fileURL),
              let buffer = Self.readAll(file, as: format) else { return false }
        await schedule([buffer])
        return true
    }

    /// Play a listen-state chime through the engine. Does not mark the assistant as speaking
    /// and does not cut off a reply. Returns false when the mic engine is down, so the caller
    /// can use a standalone player instead.
    func playCue(_ buffer: AVAudioPCMBuffer) -> Bool {
        guard let cuePlayer, let format = playerFormat, engine?.isRunning == true else { return false }
        guard let converted = Self.convert(buffer, to: format) else { return false }
        if !cuePlayer.isPlaying { cuePlayer.play() }
        // Duck a reply for the length of the chime so the glasses tone is the thing you hear.
        player?.volume = 0.15
        cuePlayer.scheduleBuffer(converted, completionCallbackType: .dataPlayedBack) { _ in }
        let seconds = Double(converted.frameLength) / format.sampleRate
        Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: UInt64((seconds + 0.05) * 1_000_000_000))
            self?.player?.volume = 1
        }
        return true
    }

    /// Play PCM buffers (Apple speech written to buffers) through the engine.
    func play(buffers: [AVAudioPCMBuffer]) async -> Bool {
        guard canPlay, let format = playerFormat else { return false }
        let converted = buffers.compactMap { Self.convert($0, to: format) }
        guard !converted.isEmpty else { return false }
        await schedule(converted)
        return true
    }

    /// Play a clip while it is still downloading (`SpeechStream`). Returns false if the mic engine
    /// isn't running, so the caller can use its own player.
    func play(stream: SpeechStream) async -> Bool {
        guard canPlay, let player, let format = playerFormat else { return false }
        stopPlayback()
        let token = playToken
        playDuration = 0
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            playContinuation = cont
            isPlaying = true
            playStartedAt = nil
            Task { @MainActor [weak self] in
                await stream.schedule(
                    on: player,
                    format: format,
                    lead: SpeechStream.lead,
                    isCurrent: { [weak self] in self?.playToken == token },
                    added: { [weak self] seconds in
                        guard let self else { return }
                        if self.playStartedAt == nil { self.playStartedAt = Date() }
                        self.playDuration += seconds
                    },
                    ended: { [weak self] in self?.finishPlayback(token) }
                )
                guard let self, token == self.playToken, self.playContinuation != nil else { return }
                // Watchdog, as in `schedule`: an engine reset must not leave its "talking" forever.
                let left = max(0, self.playDuration - Date().timeIntervalSince(self.playStartedAt ?? Date())) + 3
                try? await Task.sleep(nanoseconds: UInt64(left * 1_000_000_000))
                guard token == self.playToken, self.playContinuation != nil else { return }
                VoiceDebug.shared.log("playback watchdog: streamed clip never finished, releasing")
                self.player?.stop()
                if let player = self.player, self.engine?.isRunning == true { player.play() }
                self.finishPlayback(token)
            }
        }
        return true
    }

    /// Cut the reply off now (you started talking).
    func stopPlayback() {
        playToken += 1
        if isPlaying { player?.stop() }
        if let player, engine?.isRunning == true { player.play() }
        isPlaying = false
        playStartedAt = nil
        if let cont = playContinuation {
            playContinuation = nil
            cont.resume()
        }
    }

    private func schedule(_ buffers: [AVAudioPCMBuffer]) async {
        guard let player, let format = playerFormat else { return }
        stopPlayback()
        let token = playToken
        let frames = buffers.reduce(0.0) { $0 + Double($1.frameLength) }
        playDuration = frames / format.sampleRate
        if !player.isPlaying { player.play() }
        // Watchdog: if the engine never reports the clip played (route change, engine reset),
        // don't leave the app thinking the desk is still talking; that would ignore your words.
        let limit = playDuration + 3
        Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(limit * 1_000_000_000))
            guard let self, token == self.playToken, self.playContinuation != nil else { return }
            VoiceDebug.shared.log("playback watchdog: clip never finished, releasing")
            self.player?.stop()
            if let player = self.player, self.engine?.isRunning == true { player.play() }
            self.finishPlayback(token)
        }
        await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
            playContinuation = cont
            isPlaying = true
            playStartedAt = Date()
            for (index, buffer) in buffers.enumerated() {
                let last = index == buffers.count - 1
                player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
                    guard last else { return }
                    Task { @MainActor [weak self] in self?.finishPlayback(token) }
                }
            }
        }
    }

    private func finishPlayback(_ token: Int) {
        guard token == playToken else { return }
        isPlaying = false
        playStartedAt = nil
        if let cont = playContinuation {
            playContinuation = nil
            cont.resume()
        }
    }

    private static func readAll(_ file: AVAudioFile, as format: AVAudioFormat) -> AVAudioPCMBuffer? {
        let frames = AVAudioFrameCount(file.length)
        guard frames > 0, let raw = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: frames) else { return nil }
        do { try file.read(into: raw) } catch { return nil }
        return convert(raw, to: format)
    }

    /// Resample / remix to the player's format (TTS mp3s are 44.1 kHz stereo, Apple speech 22 kHz int16).
    static func convert(_ buffer: AVAudioPCMBuffer, to format: AVAudioFormat) -> AVAudioPCMBuffer? {
        guard buffer.frameLength > 0 else { return nil }
        if buffer.format == format { return buffer }
        guard let converter = AVAudioConverter(from: buffer.format, to: format) else { return nil }
        let ratio = format.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio) + 1024
        guard let out = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: capacity) else { return nil }
        var fed = false
        var error: NSError?
        let status = converter.convert(to: out, error: &error) { _, outStatus in
            if fed {
                outStatus.pointee = .endOfStream
                return nil
            }
            fed = true
            outStatus.pointee = .haveData
            return buffer
        }
        guard status != .error, out.frameLength > 0 else { return nil }
        return out
    }

    private nonisolated static func copyBuffer(_ buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard let copy = AVAudioPCMBuffer(pcmFormat: buffer.format, frameCapacity: buffer.frameLength) else { return nil }
        copy.frameLength = buffer.frameLength
        let frames = Int(buffer.frameLength)
        let channels = Int(buffer.format.channelCount)
        let interleaved = buffer.format.isInterleaved
        let perChannel = interleaved ? frames * channels : frames
        let count = interleaved ? 1 : channels
        if let src = buffer.floatChannelData, let dst = copy.floatChannelData {
            for c in 0..<count { dst[c].update(from: src[c], count: perChannel) }
        } else if let src = buffer.int16ChannelData, let dst = copy.int16ChannelData {
            for c in 0..<count { dst[c].update(from: src[c], count: perChannel) }
        } else if let src = buffer.int32ChannelData, let dst = copy.int32ChannelData {
            for c in 0..<count { dst[c].update(from: src[c], count: perChannel) }
        } else {
            return nil
        }
        return copy
    }

    private static func peakAmplitude(_ buffer: AVAudioPCMBuffer) -> Float? {
        let n = Int(buffer.frameLength)
        guard n > 0 else { return nil }

        if let ch = buffer.floatChannelData?[0] {
            var peak: Float = 0
            for i in 0..<n { peak = max(peak, abs(ch[i])) }
            return peak
        }
        if let ch = buffer.int16ChannelData?[0] {
            var peak: Int16 = 0
            for i in 0..<n {
                let v = ch[i]
                let a = v == Int16.min ? Int16.max : abs(v)
                if a > peak { peak = a }
            }
            return Float(peak) / Float(Int16.max)
        }
        if let ch = buffer.int32ChannelData?[0] {
            var peak: Int32 = 0
            for i in 0..<n {
                let v = ch[i]
                let a = v == Int32.min ? Int32.max : abs(v)
                if a > peak { peak = a }
            }
            return Float(peak) / Float(Int32.max)
        }
        return nil
    }

    func updateRouteLabel() {
        let session = AVAudioSession.sharedInstance()
        let ins = session.currentRoute.inputs.map { "\($0.portName) (\($0.portType.rawValue))" }.joined(separator: ", ")
        let outs = session.currentRoute.outputs.map { "\($0.portName) (\($0.portType.rawValue))" }.joined(separator: ", ")
        inputPortName = ins.isEmpty ? "none" : ins
        let vp = voiceProcessingEnabled ? " · VP+NS" : ""
        activeRouteLabel = "in: \(inputPortName) · out: \(outs.isEmpty ? "none" : outs)\(vp)"
    }

    enum CaptureError: LocalizedError {
        case invalidFormat
        var errorDescription: String? {
            "Invalid audio input format — check mic permissions / Bluetooth route."
        }
    }
}
