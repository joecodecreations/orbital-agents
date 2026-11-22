import AVFoundation
import Combine
import Foundation
import UIKit

/// Live voice: a phone call with the desk through the hub's OpenAI Realtime relay
/// (`WS /v1/desk/realtime`, `orbital/hub/desk_realtime.py`).
///
/// ```
/// mic ─ AudioCaptureService (voice processing) ─ 16 kHz ─▶ 24 kHz PCM16 ─▶ hub ─▶ OpenAI Realtime
/// speaker ◀─ AudioCaptureService player ◀─ SpeechStream ◀─ PCM16 24 kHz ◀─ hub ◀─ its voice
/// ```
///
/// It borrows the classic voice's mic engine (`AudioCaptureService`) instead of running a second
/// one: iOS gives voice processing to one engine at a time, and two fighting over it stop each
/// other. That engine already handles routes, the loud speaker, glasses, the car and the lock
/// screen. The classic voice is told to leave the audio alone while a call is open.
///
/// The hub runs every tool (files, pages, agents) and writes both sides into the desk thread, so
/// this class only moves audio and shows what is happening. Talking over the voice: the server
/// hears it (`speech_started`), playback stops at once, and the hub is told how much was heard so
/// the reply is cut there.
@MainActor
final class RealtimeVoiceSession: ObservableObject {
    enum Phase: Equatable {
        case idle
        case connecting
        case listening
        case hearing
        case thinking
        case speaking
        case working(String)
        case failed(String)
    }

    @Published private(set) var phase: Phase = .idle
    /// Your words as they are transcribed.
    @Published private(set) var userText = ""
    /// What the voice is saying right now.
    @Published private(set) var replyText = ""
    /// The latest agent result or note, shown for a few seconds.
    @Published private(set) var notice: String?
    @Published private(set) var isMuted = false
    @Published private(set) var level: Float = 0
    @Published private(set) var modelLabel = ""
    @Published private(set) var voiceName = ""

    /// The hub can't open live voice (no key, refused): the desk falls back to the classic voice.
    var onFatal: ((String) -> Void)?
    /// The Tracker's "say when something finishes": the call mentions finished items in passing.
    var announceTrackerDone: (() -> Bool)?

    var isActive: Bool {
        switch phase {
        case .idle, .failed: return false
        default: return true
        }
    }

    var statusLine: String {
        switch phase {
        case .idle: return ""
        case .connecting: return "Connecting…"
        case .listening: return isMuted ? "Mic off" : "Listening. Just talk."
        case .hearing: return "Listening…"
        case .thinking: return "Thinking…"
        case .speaking: return "Talking…"
        case .working(let label): return label.isEmpty ? "Working on it…" : label
        case .failed(let why): return why
        }
    }

    // MARK: Private

    /// The classic voice's mic engine, shared (set by DeskState).
    weak var capture: AudioCaptureService?
    private var socket: URLSessionWebSocketTask?
    private let urlSession = URLSession(configuration: .default)
    private var baseURL: URL?
    private var token: String?
    private var wantOpen = false
    private var reconnects = 0
    private var pingTask: Task<Void, Never>?
    private var noticeTask: Task<Void, Never>?
    private var statsTask: Task<Void, Never>?
    private var responseOpen = false
    private var startedAt = Date()

    // Mic → hub
    private var resampler = PCMResampler(to: 24_000)
    private var outgoing = Data()
    private var micBuffers = 0
    private var sentBytes = 0
    private var savedOnPCM: ((Data) -> Void)?
    private var savedOnLevel: ((Float) -> Void)?
    private var savedMute = false
    private var savedPreferVP = true

    // Talk-over gate: while its voice plays (and a moment after), the mic reaches the server only
    // once you are clearly louder than the echo of its own voice. Until then the server gets
    // silence, so its own words coming back through the speaker can't cut it off.
    private var preroll: [Data] = []
    private var prerollBytes = 0
    private var loudMs: Double = 0
    /// How much louder (+) or quieter (−) its voice comes back into the mic than it is played. Starts
    /// cautious (as loud as played) and settles during the call.
    private var coupling: Float = 0
    private var roomFloorDb: Float = -55
    private var lastPlayDb: Float = -90
    /// How far the echo level strays from `coupling`, word to word (dB).
    private var couplingSpread: Float = 4
    /// The echo test: its voice turned down to see whether the loud sound in the mic follows it.
    private struct Probe { var started: Date; var beforeDb: Float; var maxDb: Float }
    private var probe: Probe?
    private var falseAlarms = 0
    /// Highest your mic got over the cut-in bar since the last stats (dB), for tuning from the hub log.
    private var bestOverBar: Float = -90
    private var bargedIn = false
    private var gateUntil = Date.distantPast
    private var heldChunks = 0
    private var bargeIns = 0
    private var lastDb: Float = -90

    // Hub → speaker: one SpeechStream per reply item, played in order through the shared engine.
    /// One reply item: its audio stream and the level of every 20 ms of it, so the talk-over gate
    /// knows how loud its own voice is at each moment of playback.
    private final class PlayItem {
        let id: String?
        let stream: SpeechStream
        var envelope: [Float] = []
        init(id: String?, stream: SpeechStream) { self.id = id; self.stream = stream }
    }
    private var queue: [PlayItem] = []
    private var receiving: PlayItem?
    private var playing: PlayItem?
    private var feed: AsyncThrowingStream<Data, Error>.Continuation?
    private var playerTask: Task<Void, Never>?
    private var playingItem: String?
    private var playingStream: SpeechStream?
    private var playingSince: Date?
    /// After a cut-in, audio of the stopped reply still in flight is dropped until the next reply starts.
    private var discarding = false
    private var receivedAudio = 0

    private var isPlaying: Bool { playerTask != nil }
    private static let vpFailedKey = "orbital.live.voiceProcessingFailedAt"

    init() {}

    // MARK: Start and stop

    func start(baseURL: URL, token: String) {
        guard !isActive else { return }
        self.baseURL = baseURL
        self.token = token
        wantOpen = true
        reconnects = 0
        isMuted = false
        userText = ""
        replyText = ""
        micBuffers = 0
        sentBytes = 0
        receivedAudio = 0
        heldChunks = 0
        bargeIns = 0
        coupling = Self.savedCoupling()
        couplingSpread = 4
        falseAlarms = 0
        probe = nil
        roomFloorDb = -55
        resetGate()
        outgoing = Data()
        resampler = PCMResampler(to: 24_000)
        startedAt = Date()
        phase = .connecting
        VoiceDebug.shared.log("live voice start")
        // The hub connects to OpenAI while the mic comes up.
        open()
        Task { await self.startMic() }
    }

    private func startMic() async {
        guard let capture else {
            fail("The mic engine isn't available.")
            return
        }
        guard await capture.requestPermission() else {
            fail("Microphone access is off. Turn it on in Settings → Orbital Agents.")
            return
        }
        guard wantOpen else { return }
        savedOnPCM = capture.onPCM
        savedOnLevel = capture.onLevel
        savedMute = capture.mutePCMForSTT
        savedPreferVP = capture.preferVoiceProcessing
        // Echo cancelling always, whatever the classic voice's setting: on a speakerphone call
        // it is what keeps its own voice out of the mic.
        // Echo cancelling if this phone can run it. When it couldn't on a recent call (the engine
        // gives no mic audio with it on), skip it for a week so the mic starts at once; the
        // talk-over gate keeps its own voice out either way.
        let vpFailedAt = UserDefaults.standard.object(forKey: Self.vpFailedKey) as? Date
        let skipVP = vpFailedAt.map { Date().timeIntervalSince($0) < 7 * 86_400 } ?? false
        capture.preferVoiceProcessing = !skipVP
        capture.patientVoiceProcessing = !skipVP
        VoiceDebug.shared.liveSink = { [weak self] line in self?.send(["type": "log", "line": String(line.prefix(400))]) }
        let previousLevel = savedOnLevel
        capture.onPCM = { [weak self] pcm in self?.micPCM(pcm) }
        capture.onLevel = { [weak self] level in
            previousLevel?(level)
            self?.level = level
        }
        capture.mutePCMForSTT = false
        do {
            if !capture.isRunning || !capture.voiceProcessingEnabled {
                try capture.start(input: SpeechSettings.shared.audioInputSource)
            }
            // Keeps the engine (and its watchdog restarts) up across the lock screen.
            capture.setMaintainsDuplexSession(true)
        } catch {
            fail("Couldn't open the microphone: \(error.localizedDescription)")
            return
        }
        startStats()
        let flowing = await capture.waitForFirstBuffer(timeout: 3)
        VoiceDebug.shared.log("live voice mic \(flowing ? "flowing" : "NOT flowing") \(capture.activeRouteLabel)")
        guard !skipVP else {
            VoiceDebug.shared.log("live voice: echo cancelling skipped (it failed on this phone recently)")
            return
        }
        // Once the mic is up, note whether echo cancelling stayed on.
        for _ in 0 ..< 150 where wantOpen && !capture.hasMicAudio {
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        guard wantOpen, capture.hasMicAudio else { return }
        if capture.voiceProcessingEnabled {
            UserDefaults.standard.removeObject(forKey: Self.vpFailedKey)
        } else {
            UserDefaults.standard.set(Date(), forKey: Self.vpFailedKey)
            VoiceDebug.shared.log("live voice: echo cancelling won't run on this phone; skipping it for a week")
        }
    }

    private func releaseMic() {
        guard let capture else { return }
        capture.onPCM = savedOnPCM
        capture.onLevel = savedOnLevel
        capture.mutePCMForSTT = savedMute
        capture.preferVoiceProcessing = savedPreferVP
        capture.patientVoiceProcessing = false
        VoiceDebug.shared.liveSink = nil
        savedOnPCM = nil
        savedOnLevel = nil
        capture.setMaintainsDuplexSession(false)
        capture.stopPlayback()
        capture.stop()
    }

    func stop() {
        guard wantOpen || isActive else { return }
        VoiceDebug.shared.log("live voice stop")
        wantOpen = false
        pingTask?.cancel()
        statsTask?.cancel()
        if let socket {
            sendStats()
            socket.send(.string(#"{"type":"stop"}"#)) { _ in }
            socket.cancel(with: .normalClosure, reason: nil)
        }
        socket = nil
        if heldChunks > 30 { Self.saveCoupling(coupling) }
        stopPlaybackNow()
        releaseMic()
        phase = .idle
        userText = ""
        replyText = ""
        level = 0
    }

    func toggleMute() {
        isMuted.toggle()
        send(["type": "mute", "on": isMuted])
        if phase == .listening || phase == .hearing { phase = .listening }
    }

    /// Typed while the call is open: the voice answers it out loud, in the same conversation.
    func sendText(_ text: String) {
        send(["type": "text", "text": text])
        userText = text
        phase = .thinking
    }

    /// Tell the hub which news to bring up during the call.
    func sendPrefs() {
        send(["type": "prefs", "announce_tracker": announceTrackerDone?() ?? false])
    }

    /// Stop talking now (the stop button), the same as cutting in.
    func cutIn() {
        interruptPlayback()
    }

    #if DEBUG
    /// Evidence renders only (LiveVoiceEvidence): show a call state without a call.
    func debugShow(_ phase: Phase, user: String = "", reply: String = "", notice: String? = nil, muted: Bool = false) {
        self.phase = phase
        userText = user
        replyText = reply
        self.notice = notice
        isMuted = muted
        level = 0.55
        modelLabel = "gpt-realtime-2.1"
        voiceName = "marin"
    }

    /// Self-test on a real device (ORBITAL_LIVE_VOICE_SELFTEST=1): open a real call, stream the mic
    /// for a few seconds, report what the mic and the socket did.
    func selfTest(baseURL: URL, token: String, seconds: Double = 6) async -> String {
        var lines: [String] = []
        let t0 = Date()
        func at() -> String { String(format: "%.2fs", Date().timeIntervalSince(t0)) }
        let app = UIApplication.shared.applicationState
        lines.append("app state \(app == .active ? "active" : app == .inactive ? "inactive" : "background")")
        start(baseURL: baseURL, token: token)
        while Date().timeIntervalSince(t0) < 15 {
            if phase == .listening || phase == .hearing { break }
            if case .failed = phase { break }
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        lines.append("\(at()) phase \(phase) model \(modelLabel) voice \(voiceName)")
        try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
        lines.append("\(at()) mic buffers \(micBuffers), sent \(sentBytes) bytes (\(String(format: "%.1f", Double(sentBytes) / 48_000)) s), level \(level)")
        lines.append("engine running \(capture?.isRunning ?? false), voice processing \(capture?.voiceProcessingEnabled ?? false), \(capture?.activeRouteLabel ?? "")")
        stop()
        lines.append("\(at()) stopped")
        return lines.joined(separator: "\n")
    }
    #endif

    // MARK: Mic → hub

    private func micPCM(_ pcm: Data) {
        guard wantOpen, let socket else { return }
        micBuffers += 1
        guard !isMuted else { return }
        // AudioCaptureService hands over Float32 mono at 16 kHz.
        let count = pcm.count / MemoryLayout<Float>.size
        guard count > 0 else { return }
        let floats = pcm.withUnsafeBytes { raw in Array(raw.bindMemory(to: Float.self).prefix(count)) }
        let chunk = resampler.convert(floats, from: 16_000)
        let db = Self.rmsDb(floats)
        lastDb = db
        if (isPlaying || Date() < gateUntil) && !bargedIn {
            gate(chunk, db: db, ms: Double(count) / 16)
        } else {
            outgoing.append(chunk)
        }
        // About 40 ms per frame: small enough for a quick turn, big enough to keep the socket calm.
        if outgoing.count >= 1920 {
            let frame = outgoing
            outgoing = Data()
            sentBytes += frame.count
            socket.send(.data(frame)) { _ in }
        }
    }

    /// While it talks: you get through only when the mic is clearly louder than its own voice would
    /// be coming back. That echo level is what is playing right now plus how much of it the mic picks
    /// up (`coupling`, learned during the call). Works with or without echo cancelling.
    /// Your first words are kept (pre-roll), so nothing is lost when you do cut in.
    private func gate(_ chunk: Data, db: Float, ms: Double) {
        let (margin, confirm) = Self.talkOver(SpeechSettings.shared.liveTalkOver)
        let play = playbackLevelNow()
        lastPlayDb = play
        holdBack(chunk)
        if let probe {
            // Echo test running: its voice is turned right down. Its own echo drops with it; you don't.
            let elapsed = Date().timeIntervalSince(probe.started)
            if elapsed > 0.12 { self.probe?.maxDb = max(probe.maxDb, db) }
            guard elapsed >= 0.32 else { return }
            let heard = self.probe?.maxDb ?? -90
            self.probe = nil
            capture?.setReplyVolume(1)
            if heard >= probe.beforeDb - 7 {
                cutIn(db: db, play: play, note: "stayed at \(Int(heard)) dB with its voice down")
            } else {
                // It was its own voice: carry on, and expect echo this loud from now on.
                falseAlarms += 1
                loudMs = 0
                // A small nudge only: the echo test, not a higher bar, is what sorts echo from you.
                if coupling < -12 { coupling = min(-12, coupling + 2) }
                VoiceDebug.shared.log("live voice: echo, not you (\(Int(probe.beforeDb)) → \(Int(heard)) dB when ducked); keeps talking")
            }
            return
        }
        // Its own voice swings word to word (the speaker boost lifts soft syllables most), so allow
        // for that spread on top of the learned average.
        let expectedEcho: Float = play > -60 ? play + coupling + 1.5 * couplingSpread : -90
        let threshold = max(expectedEcho + margin, roomFloorDb + margin, -48)
        bestOverBar = max(bestOverBar, db - threshold)
        if db < threshold {
            // A dip between your syllables only takes back half: pauses don't reset a cut-in.
            loudMs = max(0, loudMs - ms * 0.5)
            if play > -45, db < play + coupling + 1.5 * couplingSpread + 3 || coupling >= 0 {
                // Its own voice coming back (not you speaking softly): learn how strongly, slowly.
                let x = db - play
                couplingSpread = couplingSpread * 0.95 + abs(x - coupling) * 0.05
                coupling = min(12, max(-40, coupling * 0.95 + x * 0.05))
            } else if play <= -45 {
                roomFloorDb = min(-30, roomFloorDb * 0.95 + db * 0.05)
            }
        } else {
            loudMs += ms
        }
        guard loudMs >= confirm else { return }
        if playing == nil {
            // Its voice already stopped (the echo tail after it): it's you.
            cutIn(db: db, play: play, note: "after it stopped")
        } else {
            // Maybe you, maybe its echo: turn its voice right down for a moment and see.
            probe = Probe(started: Date(), beforeDb: db, maxDb: -90)
            capture?.setReplyVolume(0.06)
        }
    }

    /// Hold the chunk back (the server gets silence) and keep the last ~0.9 s to send if you cut in.
    private func holdBack(_ chunk: Data) {
        preroll.append(chunk)
        prerollBytes += chunk.count
        while prerollBytes > 24_000 * 2 * 9 / 10, let first = preroll.first {
            preroll.removeFirst()
            prerollBytes -= first.count
        }
        heldChunks += 1
        outgoing.append(Data(count: chunk.count))  // silence keeps the server's clock moving
    }

    /// You're talking over it: stop its voice now and send what you've said so far.
    private func cutIn(db: Float, play: Float, note: String) {
        bargedIn = true
        bargeIns += 1
        // The silence just queued for this chunk is replaced by what you actually said.
        outgoing.removeAll()
        for piece in preroll { outgoing.append(piece) }
        preroll.removeAll()
        prerollBytes = 0
        capture?.setReplyVolume(1)
        VoiceDebug.shared.log("live voice: you cut in (mic \(Int(db)) dB, its voice \(Int(play)) dB, coupling \(Int(coupling))±\(Int(couplingSpread)) dB, \(note))")
        interruptPlayback()
    }

    private func resetGate() {
        preroll.removeAll()
        prerollBytes = 0
        loudMs = 0
        bargedIn = false
        if probe != nil {
            probe = nil
            capture?.setReplyVolume(1)
        }
    }

    /// (dB louder than the echo, ms of that) for each talk-over setting.
    private static func talkOver(_ setting: String) -> (Float, Double) {
        switch setting {
        case "easy": return (3, 100)
        case "firm": return (10, 240)
        default: return (6, 160)
        }
    }

    /// The echo level learned on earlier calls through the same output (speaker, headphones, car),
    /// so the first sentence of a call can be talked over too. 0 (cautious) when there is none.
    private static func couplingKey() -> String {
        let outs = AVAudioSession.sharedInstance().currentRoute.outputs.map(\.portType.rawValue).sorted().joined(separator: "+")
        return "orbital.live.coupling.\(outs.isEmpty ? "none" : outs)"
    }

    private static func savedCoupling() -> Float {
        guard let row = UserDefaults.standard.dictionary(forKey: couplingKey()),
              let value = row["db"] as? Double, let at = row["at"] as? Date,
              Date().timeIntervalSince(at) < 30 * 86_400 else { return 0 }
        return Float(value)
    }

    private static func saveCoupling(_ value: Float) {
        UserDefaults.standard.set(["db": Double(value), "at": Date()], forKey: couplingKey())
    }

    private static func rmsDb(_ x: [Float]) -> Float {
        guard !x.isEmpty else { return -90 }
        var sum: Float = 0
        for v in x { sum += v * v }
        return 20 * log10(max(sqrt(sum / Float(x.count)), 1e-6))
    }

    // MARK: Hub → speaker

    private func beginItem(_ id: String?) {
        feed?.finish()
        feed = nil
        discarding = false
        guard let stream = SpeechStream(sampleRate: 24_000) else { return }
        let (chunks, continuation) = AsyncThrowingStream<Data, Error>.makeStream()
        stream.start(chunks)
        feed = continuation
        let entry = PlayItem(id: id, stream: stream)
        receiving = entry
        queue.append(entry)
        pumpPlayback()
    }

    private func voiceChunk(_ data: Data) {
        receivedAudio += data.count
        guard !discarding else { return }
        if feed == nil { beginItem(nil) }
        receiving?.envelope.append(contentsOf: Self.envelope(data))
        feed?.yield(data)
    }

    /// dB of each 20 ms of PCM16 24 kHz.
    private static func envelope(_ data: Data) -> [Float] {
        data.withUnsafeBytes { raw in
            let samples = raw.bindMemory(to: Int16.self)
            var out: [Float] = []
            var i = 0
            while i < samples.count {
                let end = min(samples.count, i + 480)
                var sum: Float = 0
                for j in i ..< end {
                    let v = Float(samples[j]) / 32768
                    sum += v * v
                }
                out.append(20 * log10(max(sqrt(sum / Float(max(1, end - i))), 1e-6)))
                i = end
            }
            return out
        }
    }

    /// How loud its own voice was over the last quarter second of playback (dB): what the mic chunk
    /// in hand can contain of it. -90 when nothing is playing.
    private func playbackLevelNow() -> Float {
        guard let playing, !playing.envelope.isEmpty else { return -90 }
        // Where playback really is: what the speaker finished playing, plus the buffer under way.
        // (A clock from the start drifts ahead whenever playback stalls waiting for audio.)
        let pos: Double
        if let last = playing.stream.lastPlayedAt {
            pos = playing.stream.playedSeconds + min(0.3, Date().timeIntervalSince(last))
        } else if let start = capture?.playbackStartedAt {
            pos = Date().timeIntervalSince(start)
        } else {
            return -90
        }
        guard pos > -0.05 else { return -90 }
        let from = max(0, Int((pos - 0.25) / 0.02))
        let to = min(playing.envelope.count - 1, Int(max(0, pos) / 0.02))
        guard from <= to else { return -90 }
        return playing.envelope[from ... to].max() ?? -90
    }

    private func pumpPlayback() {
        guard playerTask == nil else { return }
        playerTask = Task { @MainActor [weak self] in
            while let self, !self.queue.isEmpty {
                let next = self.queue.removeFirst()
                self.playing = next
                self.playingItem = next.id
                self.playingStream = next.stream
                self.playingSince = Date()
                let played = await self.capture?.play(stream: next.stream) ?? false
                if !played {
                    VoiceDebug.shared.log("live voice: engine not ready to play")
                    next.stream.cancel()
                }
            }
            guard let self else { return }
            self.playing = nil
            self.playingItem = nil
            self.playingStream = nil
            self.playingSince = nil
            self.playerTask = nil
            if !self.bargedIn { self.gateUntil = Date().addingTimeInterval(0.3) }
            self.resetGate()
            self.playbackDrained()
        }
    }

    /// Everything queued or playing is dropped. Returns the item that was playing and the ms heard.
    @discardableResult
    private func stopPlaybackNow() -> (String?, Int)? {
        let wasPlaying = playingStream != nil || !queue.isEmpty
        var heard = 0
        if let since = playingSince, let stream = playingStream {
            heard = Int(max(0, min(stream.seconds, Date().timeIntervalSince(since) - SpeechStream.lead)) * 1000)
        }
        let item = playingItem
        discarding = true
        feed?.finish()
        feed = nil
        for entry in queue { entry.stream.cancel() }
        receiving = nil
        playing = nil
        queue.removeAll()
        playingStream?.cancel()
        capture?.stopPlayback()
        return wasPlaying ? (item, heard) : nil
    }

    // MARK: Socket

    private func open() {
        guard wantOpen, let baseURL, let token,
              var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else { return }
        components.scheme = (components.scheme?.lowercased() == "https") ? "wss" : "ws"
        let basePath = components.path.hasSuffix("/") ? String(components.path.dropLast()) : components.path
        components.path = basePath + "/v1/desk/realtime"
        #if targetEnvironment(macCatalyst)
        let platform = "mac"
        #else
        let platform = "ios"
        #endif
        components.queryItems = [URLQueryItem(name: "token", value: token), URLQueryItem(name: "platform", value: platform)]
        guard let url = components.url else { return }
        var request = URLRequest(url: url)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let task = urlSession.webSocketTask(with: request)
        task.maximumMessageSize = 8 * 1024 * 1024
        socket = task
        task.resume()
        receive(on: task)
        pingTask?.cancel()
        pingTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 15_000_000_000)
                await MainActor.run { self?.send(["type": "ping"]) }
            }
        }
    }

    private func send(_ object: [String: Any]) {
        guard let socket, let data = try? JSONSerialization.data(withJSONObject: object),
              let text = String(data: data, encoding: .utf8) else { return }
        socket.send(.string(text)) { _ in }
    }

    private func receive(on task: URLSessionWebSocketTask) {
        task.receive { [weak self] result in
            switch result {
            case .success(.data(let data)):
                Task { @MainActor in
                    guard let self, self.socket === task else { return }
                    self.voiceChunk(data)
                    if case .speaking = self.phase {} else if self.responseOpen { self.phase = .speaking }
                    self.receive(on: task)
                }
            case .success(.string(let text)):
                Task { @MainActor in
                    guard let self, self.socket === task else { return }
                    if let data = text.data(using: .utf8),
                       let event = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                        self.handle(event)
                    }
                    if self.socket === task { self.receive(on: task) }
                }
            case .success:
                Task { @MainActor in
                    guard let self, self.socket === task else { return }
                    self.receive(on: task)
                }
            case .failure(let error):
                Task { @MainActor in
                    guard let self, self.socket === task else { return }
                    self.dropped("connection lost: \(error.localizedDescription)")
                }
            }
        }
    }

    private func dropped(_ why: String) {
        socket = nil
        guard wantOpen else { return }
        VoiceDebug.shared.log("live voice \(why)")
        if reconnects < 3 {
            reconnects += 1
            phase = .connecting
            Task { @MainActor in
                try? await Task.sleep(nanoseconds: UInt64(reconnects) * 800_000_000)
                self.open()
            }
        } else {
            fail("Live voice lost the hub. Tap the waveform to start again.")
        }
    }

    private func fail(_ why: String) {
        let wasOpen = wantOpen
        wantOpen = false
        socket?.cancel(with: .normalClosure, reason: nil)
        socket = nil
        pingTask?.cancel()
        statsTask?.cancel()
        stopPlaybackNow()
        releaseMic()
        phase = .failed(why)
        if wasOpen { onFatal?(why) }
    }

    // MARK: Events from the hub

    private func handle(_ event: [String: Any]) {
        let kind = event["type"] as? String ?? ""
        switch kind {
        case "connecting":
            phase = .connecting
        case "ready":
            reconnects = 0
            modelLabel = event["model"] as? String ?? ""
            voiceName = event["voice"] as? String ?? ""
            phase = .listening
            sendPrefs()
            VoiceDebug.shared.log("live voice ready \(modelLabel)")
        case "speech_started":
            interruptPlayback()
            userText = ""
            phase = .hearing
        case "speech_stopped":
            phase = .thinking
        case "user_partial":
            userText += event["delta"] as? String ?? ""
        case "user_final":
            userText = event["text"] as? String ?? userText
        case "audio_start":
            if !isPlaying { resetGate() }
            beginItem(event["item_id"] as? String)
            replyText = ""
            responseOpen = true
            phase = .speaking
        case "assistant_delta":
            replyText += event["delta"] as? String ?? ""
        case "tool":
            if (event["state"] as? String) == "running" {
                phase = .working(event["label"] as? String ?? "")
            } else if case .working = phase {
                phase = .thinking
            }
        case "response_done":
            responseOpen = false
            feed?.finish()
            feed = nil
            if !isPlaying, phase == .speaking || phase == .thinking { phase = .listening }
        case "agent_update":
            show(event["text"] as? String)
        case "echo":
            // The hub heard its own voice as yours and dropped it.
            userText = ""
            if !isPlaying, phase == .hearing || phase == .thinking { phase = .listening }
        case "error":
            let message = event["message"] as? String ?? "Live voice error"
            if (event["fatal"] as? Bool) == true {
                fail(message)
            } else {
                show("Voice: \(message)")
            }
        case "closed":
            dropped(event["reason"] as? String ?? "closed")
        default:
            break
        }
    }

    private func show(_ text: String?) {
        guard let text, !text.isEmpty else { return }
        notice = text
        noticeTask?.cancel()
        noticeTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 9_000_000_000)
            if !Task.isCancelled { self.notice = nil }
        }
    }

    /// Stop the voice where it is and tell the hub how much was heard.
    private func interruptPlayback() {
        guard let (item, ms) = stopPlaybackNow() else { return }
        if let item { send(["type": "truncate", "item_id": item, "audio_end_ms": ms]) }
        VoiceDebug.shared.log("live voice cut in after \(ms) ms")
    }

    private func playbackDrained() {
        guard isActive else { return }
        if phase == .speaking, !responseOpen { phase = .listening }
    }

    // MARK: Stats for the hub log (what the mic is doing, without a cable)

    private func startStats() {
        statsTask?.cancel()
        statsTask = Task { @MainActor [weak self] in
            var first = true
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: first ? 3_000_000_000 : 10_000_000_000)
                first = false
                self?.sendStats()
            }
        }
    }

    private func sendStats() {
        defer { bestOverBar = -90 }
        let session = AVAudioSession.sharedInstance()
        send([
            "type": "stats",
            "buffers": micBuffers,
            "sent": sentBytes,
            "heard": receivedAudio,
            "format": "shared engine 16k",
            "vp": capture?.voiceProcessingEnabled ?? false,
            "running": capture?.isRunning ?? false,
            "held": heldChunks,
            "bargeins": bargeIns,
            "coupling": Int(coupling),
            "room_db": Int(roomFloorDb),
            "play_db": Int(lastPlayDb),
            "best_over_bar": Int(bestOverBar),
            "spread": Int(couplingSpread),
            "echo_tests_failed": falseAlarms,
            "mic_db": Int(lastDb),
            "talkover": SpeechSettings.shared.liveTalkOver,
            "muted": isMuted,
            "phase": statusLine,
            "in": session.currentRoute.inputs.map(\.portType.rawValue).joined(separator: "+"),
            "out": session.currentRoute.outputs.map(\.portType.rawValue).joined(separator: "+"),
            "category": session.category.rawValue.replacingOccurrences(of: "AVAudioSessionCategory", with: ""),
        ])
    }
}

/// Float samples at any rate to 16-bit PCM at `rate`, continuous across calls: a light low-pass when
/// coming down from a higher rate, then linear interpolation.
struct PCMResampler {
    let rate: Double
    private var pos: Double = 0
    private var lastRaw: Float = 0
    private var lastFiltered: Float = 0

    init(to rate: Double) { self.rate = rate }

    mutating func convert(_ input: [Float], from inRate: Double) -> Data {
        let n = input.count
        guard n > 0, inRate > 0 else { return Data() }
        var x = input
        if inRate > rate * 1.25 {
            var prev = lastRaw
            for i in 0 ..< n {
                let next = i + 1 < n ? input[i + 1] : input[i]
                x[i] = 0.25 * prev + 0.5 * input[i] + 0.25 * next
                prev = input[i]
            }
        }
        lastRaw = input[n - 1]
        let step = inRate / rate
        var out = [Int16]()
        out.reserveCapacity(Int(Double(n) / step) + 2)
        var p = pos
        while p <= Double(n - 1) {
            let base = p.rounded(.down)
            let i = Int(base)
            let frac = Float(p - base)
            let a = i < 0 ? lastFiltered : x[i]
            let b = i + 1 < n ? x[i + 1] : a
            let v = max(-1, min(1, a + (b - a) * frac))
            out.append(Int16(v * 32767))
            p += step
        }
        pos = p - Double(n)
        lastFiltered = x[n - 1]
        return out.withUnsafeBufferPointer { Data(buffer: $0) }
    }
}
