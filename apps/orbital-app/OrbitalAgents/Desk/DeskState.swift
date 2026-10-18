import Combine
import Foundation
import SwiftUI

extension Notification.Name {
    /// The store forwards `desk_message`, `desk_task`, `tracker`, and `tracker_settings` live-socket events here.
    static let orbitalDeskEvent = Notification.Name("orbital.deskEvent")
}

/// The Desk tab: one thread with the hub's fast desk model, the agents it starts on your machines,
/// the tracker's live items, and the voice loop (ConversationSession) that talks to all of it.
/// Everything goes through the hub you signed in to; model and voice keys live on the hub.
@MainActor
final class DeskState: ObservableObject {
    enum Keys {
        static let speakReplies = "orbital.desk.speakReplies"
        static let announceDone = "orbital.desk.announceTrackerDone"
        static let evidence = "orbital.desk.trackerEvidence"
        static let silence = "orbital.desk.silenceAutoSendSeconds"
    }

    // MARK: Thread

    @Published private(set) var messages: [DeskMessage] = []
    @Published private(set) var tasks: [String: DeskTask] = [:]
    @Published private(set) var model: DeskModelInfo?
    /// Reply text as it streams, by reply id. Cleared when the saved reply arrives.
    @Published private(set) var streaming: [String: String] = [:]
    @Published private(set) var waiting = 0
    @Published private(set) var loadError: String?
    @Published private(set) var hasOlder = true
    @Published private(set) var loaded = false
    /// False when the hub says it has no desk model set up. The tab explains how to turn it on.
    @Published private(set) var configured: Bool?
    /// The hub's own words about what is missing, when it sends them.
    @Published private(set) var setupNote: String?
    @Published var draft = ""
    /// Set by a notification or deep link; the thread scrolls to that task's card.
    @Published var focusTaskID: String?

    // MARK: Tracker

    @Published private(set) var trackerNow: [TrackerSummary] = []
    @Published private(set) var trackerActive = 0
    /// Bumped on every tracker change so open tracker screens refetch.
    @Published private(set) var trackerRevision = 0
    /// Say out loud when an agent finishes something, in the voice set in Voice settings, even when
    /// spoken replies are off. Per device.
    @Published var announceTrackerDone: Bool {
        didSet {
            UserDefaults.standard.set(announceTrackerDone, forKey: Keys.announceDone)
            if announceTrackerDone, !oldValue {
                conversation.enqueueSpeak("Okay. I'll tell you when something finishes.", force: true)
            }
        }
    }

    /// Screenshot evidence agents attach for visual work: Off, Min 1 or All. One value for every
    /// device and every Mac's tracker; the hub's `tracker_settings` event keeps it in step.
    @Published private(set) var trackerEvidence: TrackerEvidence
    /// Who set it last: "tracker" (a Mac), "app", or "cli".
    @Published private(set) var trackerEvidenceBy: String?
    @Published private(set) var trackerEvidenceSaving = false
    @Published private(set) var trackerEvidenceError: String?

    // MARK: Voice

    let conversation = ConversationSession()
    let speechSettings = SpeechSettings.shared
    /// What the hub can do for speech (Fish voices with its key). Refreshed with the thread.
    @Published private(set) var speechStatus = SpeechStatus()
    @Published var liveConversationOn = false
    @Published var speakRepliesOn: Bool {
        didSet {
            UserDefaults.standard.set(speakRepliesOn, forKey: Keys.speakReplies)
            speechSettings.ttsEnabled = speakRepliesOn || liveConversationOn
            if !speakRepliesOn, !liveConversationOn { conversation.tts.stop() }
        }
    }
    @Published var silenceAutoSendSeconds: Double {
        didSet {
            let clamped = min(max(silenceAutoSendSeconds, Self.silenceMin), Self.silenceMax)
            if clamped != silenceAutoSendSeconds {
                silenceAutoSendSeconds = clamped
                return
            }
            UserDefaults.standard.set(clamped, forKey: Keys.silence)
            speechSettings.silenceSeconds = clamped
            if conversation.isListening { conversation.vad.silenceSeconds = clamped }
        }
    }
    @Published var banner: String?

    static let silenceMin = 0.4
    static let silenceMax = 10.0

    // MARK: Private

    private weak var store: OrbitalStore?
    private var conversationSink: AnyCancellable?
    private var eventObserver: NSObjectProtocol?
    /// What has been handed to TTS for each streaming reply.
    private var spokenUpTo: [String: Int] = [:]
    private var spokenEventIDs: Set<String> = []
    private var lastVoiceAt: Date = .distantPast
    /// The newest reply (its id from the turn's `start`), for cutting in on it.
    private var latestReplyID: String?
    /// Replies you talked over: the rest of their text is not spoken.
    private var cutReplies: Set<String> = []
    /// Set when you cut into a reply; sent with the words you say next.
    private var pendingInterrupt: DeskInterrupt?
    /// A message is out and its reply hasn't started yet.
    private var replyPendingStart = false
    /// Glasses tap (or the waveform button) is already starting continuous talk.
    private var startingLive = false
    /// Bumped when hands-free is turned off, so a start still in flight does not turn it back on.
    private var liveGeneration = 0

    init() {
        speakRepliesOn = (UserDefaults.standard.object(forKey: Keys.speakReplies) as? Bool) ?? false
        announceTrackerDone = UserDefaults.standard.bool(forKey: Keys.announceDone)
        // Last level seen, until the hub answers.
        trackerEvidence = TrackerEvidence(rawValue: UserDefaults.standard.string(forKey: Keys.evidence) ?? "") ?? .off
        let silence = (UserDefaults.standard.object(forKey: Keys.silence) as? Double) ?? 1.5
        silenceAutoSendSeconds = min(max(silence, Self.silenceMin), Self.silenceMax)
        speechSettings.silenceSeconds = silenceAutoSendSeconds
        speechSettings.ttsEnabled = speakRepliesOn

        conversation.onUtterance = { [weak self] text in
            self?.send(text, source: "voice")
        }
        conversation.onBargeIn = { [weak self] tag, heard in
            self?.noteCutIn(replyID: tag, heard: heard)
        }
        conversation.onRequestLiveConversation = { [weak self] in
            self?.beginLiveFromGlasses()
        }
        conversation.onPauseChanged = { [weak self] paused in
            self?.banner = paused ? "Not listening" : "Listening. Just talk."
        }
        conversation.onTTSMuteChanged = { [weak self] muted in
            self?.banner = muted ? "Voice muted" : "You can hear the desk again"
        }
        conversation.tts.onFallback = { [weak self] why in
            self?.banner = "Fish voice failed, using the built-in voice: \(why)"
        }
        conversationSink = conversation.objectWillChange.sink { [weak self] _ in
            self?.objectWillChange.send()
        }
        eventObserver = NotificationCenter.default.addObserver(forName: .orbitalDeskEvent, object: nil, queue: .main) { [weak self] note in
            guard let event = note.userInfo as? [String: Any] else { return }
            MainActor.assumeIsolated { self?.handleLive(event) }
        }
    }

    func attach(_ store: OrbitalStore) {
        self.store = store
        OnDeviceSpeech.shared.api = { [weak self] in self?.api }
        // Fish voices made by the hub with its key.
        conversation.tts.hubSynthesize = { [weak self] text, voiceID, speed, model in
            guard let api = await self?.api else { throw OrbitalAPIError.unauthorized }
            return try await api.fishSpeech(text: text, voiceID: voiceID, speed: speed, model: model)
        }
        conversation.tts.hubStream = { [weak self] text, voiceID, speed, model in
            guard let api = self?.api else { throw OrbitalAPIError.unauthorized }
            let (rate, chunks) = try await api.fishSpeechStream(text: text, voiceID: voiceID, speed: speed, model: model)
            guard let stream = SpeechStream(sampleRate: rate) else { throw OrbitalAPIError.http(502, "bad sample rate \(rate)") }
            stream.start(chunks)
            return stream
        }
        VoiceDebug.shared.sink = { [weak self] lines in
            guard SpeechSettings.shared.sendDiagnostics, let api = self?.api else { return }
            #if targetEnvironment(macCatalyst)
            let device = "mac"
            #else
            let device = "iphone"
            #endif
            await api.voiceLog(lines, device: device)
        }
    }

    private var api: DeskAPI? {
        guard let store, store.isLoggedIn else { return nil }
        return DeskAPI(baseURL: store.baseURL, token: store.imageToken, deviceID: store.deviceID)
    }

    /// The desk is turned off on the hub (no model key). Talking and typing are disabled; Agents still works.
    var isUnavailable: Bool { configured == false }

    // MARK: Loading

    func refresh() async {
        guard let api else { return }
        do {
            let snap = try await api.snapshot()
            merge(snap.messages, replacingTail: true)
            tasks = Dictionary(snap.tasks.map { ($0.id, $0) }, uniquingKeysWith: { _, new in new })
            model = snap.model
            trackerActive = snap.trackerActive ?? trackerActive
            configured = snap.configured ?? snap.model?.configured
            setupNote = snap.setup ?? (configured == false ? snap.model?.note : nil)
            loadError = nil
            loaded = true
            if snap.messages.count < 60 { hasOlder = false }
        } catch {
            if let api = error as? OrbitalAPIError, Self.meansNotConfigured(api) {
                configured = false
                setupNote = api.status == 404 ? "This hub has no Agent desk." : OrbitalAPIError.message(in: Self.body(api) ?? "")
                loadError = nil
                loaded = true
            } else {
                loadError = (error as? OrbitalAPIError)?.shortReason ?? error.localizedDescription
            }
        }
        await refreshTrackerNow()
        await refreshTrackerSettings()
        await refreshSpeechStatus()
        // On-device speech: fetch the hub's model on Wi-Fi the first time, load it after that.
        OnDeviceSpeech.shared.autoInstallIfNeeded()
    }

    private static func meansNotConfigured(_ error: OrbitalAPIError) -> Bool {
        if let code = error.code, code.contains("not_configured") || code.contains("unconfigured") || code == "no_key" { return true }
        switch error.status {
        case 404, 501, 503: return true
        default: return false
        }
    }

    private static func body(_ error: OrbitalAPIError) -> String? {
        if case .http(_, let body) = error { return body }
        return nil
    }

    func refreshSpeechStatus() async {
        guard let api, let status = try? await api.speechStatus() else { return }
        speechStatus = status
        speechSettings.applyHubVoices(status)
    }

    /// Fresh context for the desk model. Its notes and the running jobs carry over.
    func clearContext() async {
        guard let api, let marker = try? await api.clearContext() else { return }
        merge([marker], replacingTail: false)
    }

    /// The engine answering changed (a backup in, or the main model back): update the badge.
    private func refreshModel() async {
        guard let api, let snap = try? await api.snapshot() else { return }
        model = snap.model
    }

    func loadOlder() async {
        guard let api, hasOlder, let first = messages.first(where: { !$0.pending }) else { return }
        guard let rows = try? await api.older(before: first.id) else { return }
        if rows.isEmpty {
            hasOlder = false
            return
        }
        let known = Set(messages.map(\.id))
        messages.insert(contentsOf: rows.filter { !known.contains($0.id) }, at: 0)
    }

    func refreshTrackerNow() async {
        guard let api, let page = try? await api.trackerPage(scope: .active, who: .all, limit: 30) else { return }
        trackerNow = page.items
        trackerActive = page.active
    }

    // MARK: Screenshot evidence

    func refreshTrackerSettings() async {
        guard let api, let settings = try? await api.trackerSettings() else { return }
        applyTrackerSettings(settings)
    }

    /// Shown at once; put back if the hub refuses. Every Mac's tracker follows within a second or two.
    func setTrackerEvidence(_ level: TrackerEvidence) {
        guard level != trackerEvidence, let api else { return }
        let previous = (trackerEvidence, trackerEvidenceBy)
        trackerEvidence = level
        trackerEvidenceBy = "app"
        trackerEvidenceSaving = true
        trackerEvidenceError = nil
        Task {
            defer { trackerEvidenceSaving = false }
            do {
                applyTrackerSettings(try await api.setTrackerEvidence(level))
            } catch {
                (trackerEvidence, trackerEvidenceBy) = previous
                trackerEvidenceError = "Couldn't change it: \((error as? OrbitalAPIError)?.shortReason ?? error.localizedDescription)"
            }
        }
    }

    private func applyTrackerSettings(_ settings: TrackerSettings) {
        trackerEvidence = settings.evidence
        trackerEvidenceBy = settings.updatedBy
        trackerEvidenceError = nil
        UserDefaults.standard.set(settings.evidence.rawValue, forKey: Keys.evidence)
    }

    func warm() {
        guard let api, !isUnavailable else { return }
        Task { await api.warm() }
    }

    private func merge(_ rows: [DeskMessage], replacingTail: Bool) {
        var byID = Dictionary(messages.map { ($0.id, $0) }, uniquingKeysWith: { _, new in new })
        for row in rows {
            byID[row.id] = row
            // The hub's copy of a message sent from here replaces the local placeholder.
            if let client = row.clientId {
                for (id, msg) in byID where msg.pending && msg.clientId == client { byID.removeValue(forKey: id) }
            }
        }
        let pending = byID.values.filter(\.pending)
        let saved = byID.values.filter { !$0.pending }.sorted { ($0.at ?? "", $0.id) < ($1.at ?? "", $1.id) }
        messages = saved + pending
    }

    // MARK: Sending

    var isBusy: Bool { waiting > 0 }

    func sendDraft() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        draft = ""
        send(text, source: "text")
    }

    func send(_ raw: String, source: String) {
        let text = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        guard let api else {
            banner = "Sign in to your hub first"
            return
        }
        guard !isUnavailable else {
            banner = "The desk is not set up on your hub"
            return
        }
        let clientID = UUID().uuidString.lowercased()
        if source == "voice" { lastVoiceAt = Date() }
        let speak = source == "voice" || liveConversationOn || speakRepliesOn
        let interrupt = takeInterrupt()
        replyPendingStart = true
        if source == "voice" { VoiceDebug.shared.log("send voice (\(text.count) chars) interrupt=\(interrupt.map { "\($0.replyID ?? "pending") heard \($0.heard.count) chars" } ?? "none")") }
        messages.append(DeskMessage(id: "local-\(clientID)", role: .user, text: text, at: nil, source: source, clientId: clientID, pending: true))
        waiting += 1
        #if DEBUG
        if OrbitalPreview.isActive {
            waiting -= 1
            replyPendingStart = false
            if let index = messages.firstIndex(where: { $0.clientId == clientID }) { messages[index].pending = false }
            messages.append(DeskMessage(id: "preview-\(clientID)", role: .assistant, text: "Preview mode has no hub. Sign in to a hub to talk to the desk.", at: OrbitalDates.iso(Date())))
            return
        }
        #endif
        Task {
            defer { waiting -= 1 }
            var replyID: String?
            do {
                for try await event in api.say(text: text, source: source, clientID: clientID, interrupt: interrupt) {
                    replyID = handleTurnEvent(event, speak: speak) ?? replyID
                }
            } catch {
                let reason = (error as? OrbitalAPIError)?.shortReason ?? error.localizedDescription
                if let replyID, streaming[replyID] != nil {
                    // The hub keeps going without us; the saved reply arrives on the live socket or the next refresh.
                    banner = "Connection dropped. The hub still has it."
                } else {
                    markFailed(clientID: clientID, reason: reason)
                }
            }
        }
    }

    /// One NDJSON event from our own turn. Returns the reply id once it is known.
    private func handleTurnEvent(_ event: [String: Any], speak: Bool) -> String? {
        switch event["type"] as? String {
        case "user":
            if let msg = DeskDecode.value(DeskMessage.self, from: event["message"]) { merge([msg], replacingTail: false) }
        case "start":
            if let id = event["id"] as? String {
                streaming[id] = ""
                spokenUpTo[id] = 0
                latestReplyID = id
                replyPendingStart = false
                return id
            }
        case "delta":
            guard let id = event["id"] as? String, let text = event["text"] as? String else { break }
            streaming[id, default: ""] += text
            if speak { speakStreaming(id: id, final: false) }
        case "said":
            // The spoken part is complete; the last sentence goes now, not after the actions run.
            guard let id = event["id"] as? String else { break }
            if speak { speakStreaming(id: id, final: true) }
        case "reply":
            guard let msg = DeskDecode.value(DeskMessage.self, from: event["message"]) else { break }
            if streaming[msg.id] == nil || (streaming[msg.id] ?? "").isEmpty {
                streaming[msg.id] = msg.text
            }
            if speak { speakStreaming(id: msg.id, final: true) }
            streaming.removeValue(forKey: msg.id)
            spokenUpTo.removeValue(forKey: msg.id)
            merge([msg], replacingTail: false)
        case "echo":
            // The hub dropped a transcript of the desk's own voice. Take the pending bubble back.
            let id = event["client_id"] as? String
            messages.removeAll { $0.pending && (id == nil || $0.clientId == id) }
            replyPendingStart = false
        case "event":
            guard let msg = DeskDecode.value(DeskMessage.self, from: event["message"]) else { break }
            merge([msg], replacingTail: false)
            if msg.kind == "not_configured" {
                configured = false
                setupNote = msg.text
            }
            if msg.kind == "engine" { Task { await refreshModel() } }
            if speak, let line = msg.speak, !spokenEventIDs.contains(msg.id) {
                spokenEventIDs.insert(msg.id)
                conversation.enqueueSpeak(line)
            }
        case "error":
            let reason = (event["error"] as? String) ?? (event["message"] as? String) ?? "the desk failed"
            banner = reason
        default:
            break
        }
        return nil
    }

    private func markFailed(clientID: String, reason: String) {
        replyPendingStart = false
        if let index = messages.firstIndex(where: { $0.clientId == clientID && $0.pending }) {
            messages[index].error = reason
        }
        banner = "Not sent: \(reason)"
    }

    func retry(_ message: DeskMessage) {
        messages.removeAll { $0.id == message.id }
        send(message.text, source: message.source ?? "text")
    }

    func cancel(task id: String) {
        guard let api else { return }
        Task {
            if let task = try? await api.cancel(task: id) { tasks[task.id] = task }
        }
    }

    // MARK: Live events

    private func handleLive(_ event: [String: Any]) {
        switch event["type"] as? String {
        case "desk_message":
            guard let msg = DeskDecode.value(DeskMessage.self, from: event["message"]) else { return }
            if msg.role == .assistant, streaming[msg.id] != nil { return } // our own turn finishes it
            let isNew = !messages.contains { $0.id == msg.id }
            merge([msg], replacingTail: false)
            if msg.kind == "not_configured" {
                configured = false
                setupNote = msg.text
            }
            if isNew, msg.kind == "engine" { Task { await refreshModel() } }
            // A desk task's done report also answers the Tracker's "say when it finishes".
            let announce = announceTrackerDone && (msg.kind == "task_done" || msg.kind == "task_failed")
            if isNew, msg.role == .event, let line = msg.speak, shouldSpeakUpdates || announce, !spokenEventIDs.contains(msg.id) {
                spokenEventIDs.insert(msg.id)
                conversation.enqueueSpeak(line, force: announce)
            }
        case "desk_task":
            if let task = DeskDecode.value(DeskTask.self, from: event["task"]) { tasks[task.id] = task }
        case "tracker_settings":
            // Changed on a Mac's tracker, by an agent's orbital-track, or on another device.
            if !trackerEvidenceSaving, let settings = DeskDecode.value(TrackerSettings.self, from: event["settings"]) {
                applyTrackerSettings(settings)
            }
        case "tracker":
            let changed = (event["changed"] as? [Any] ?? []).compactMap { DeskDecode.value(TrackerSummary.self, from: $0) }
            let removed = Set(event["removed"] as? [String] ?? [])
            var now = trackerNow.filter { !removed.contains($0.id) }
            for row in changed {
                now.removeAll { $0.id == row.id }
                if row.isActive { now.insert(row, at: 0) }
            }
            trackerNow = now
            trackerActive = now.count
            trackerRevision += 1
            if announceTrackerDone {
                let finished = Set(event["finished"] as? [String] ?? [])
                // Desk tasks are left to their own done report above, which says more.
                let deskTrackers = Set(tasks.values.compactMap(\.trackerId))
                for row in changed where finished.contains(row.id) && !deskTrackers.contains(row.id) {
                    conversation.enqueueSpeak(TrackerSpeech.finished(row), force: true)
                }
            }
        default:
            break
        }
    }

    /// Task-finished updates are spoken while you are talking to the desk, not while you read.
    private var shouldSpeakUpdates: Bool {
        liveConversationOn || Date().timeIntervalSince(lastVoiceAt) < 15 * 60
    }

    // MARK: Voice

    var isListening: Bool { conversation.isListening }

    // MARK: Cutting in

    /// You talked over a reply (or tapped the mic while it spoke): stop speaking it, and send
    /// what you heard with whatever you say next.
    private func noteCutIn(replyID: String?, heard: String) {
        guard let id = replyID ?? openReplyID else {
            if replyPendingStart { pendingInterrupt = DeskInterrupt(replyID: nil, heard: "") }
            return
        }
        cutReplies.insert(id)
        pendingInterrupt = DeskInterrupt(replyID: id, heard: heard)
    }

    /// The newest reply if it is still being written or read out.
    private var openReplyID: String? {
        guard let id = latestReplyID, !cutReplies.contains(id) else { return nil }
        return streaming[id] != nil || conversation.isAssistantSpeaking ? id : nil
    }

    /// The interrupt for the next message: a cut-in you made, or one implied by talking again
    /// before the last reply finished (you haven't heard the rest, or any of it).
    private func takeInterrupt() -> DeskInterrupt? {
        if let pending = pendingInterrupt {
            pendingInterrupt = nil
            return pending
        }
        if let id = openReplyID {
            let heard = conversation.heardText(for: id)
            cutReplies.insert(id)
            conversation.cancelPendingSpeech()
            return DeskInterrupt(replyID: id, heard: heard)
        }
        if replyPendingStart {
            // Still thinking about your last message: the desk cuts that reply and answers both.
            return DeskInterrupt(replyID: nil, heard: "")
        }
        return nil
    }

    /// Tap: one phrase, sent when you pause. Tap again while talking to send now.
    /// Tapping while the desk is talking stops it, and what you say next picks up from there.
    func tapMic() {
        VoiceDebug.shared.log("tap mic listening=\(conversation.isListening) speaking=\(conversation.isAssistantSpeaking) stt=\(speechSettings.sttProvider.rawValue)")
        if conversation.isAssistantSpeaking {
            conversation.bargeIn()
            if conversation.isListening { return }
        }
        if conversation.isListening {
            conversation.stt.forceFinalizeFromSilence()
            return
        }
        Task {
            guard ensureSTTReady() else { return }
            speechSettings.ttsEnabled = true
            await conversation.startListening(oneShot: true)
            if let err = conversation.lastError { banner = err }
        }
    }

    /// Hands-free: the mic stays open, every phrase goes to the desk, replies and updates are spoken.
    func toggleLive() {
        VoiceDebug.shared.log("hands-free \(liveConversationOn ? "off" : "on") stt=\(speechSettings.sttProvider.rawValue)")
        if liveConversationOn {
            liveGeneration += 1
            liveConversationOn = false
            conversation.stopListening(cue: true)
            speechSettings.ttsEnabled = speakRepliesOn
            banner = "Not listening"
            return
        }
        beginLiveFromGlasses()
    }

    /// Glasses temple tap, or the waveform button. Continuous talk, not one phrase.
    func beginLiveFromGlasses() {
        guard !liveConversationOn, !startingLive, !isUnavailable else { return }
        startingLive = true
        liveGeneration += 1
        let generation = liveGeneration
        Task {
            defer { startingLive = false }
            guard ensureSTTReady() else { return }
            guard generation == self.liveGeneration else { return }
            liveConversationOn = true
            speechSettings.ttsEnabled = true
            lastVoiceAt = Date()
            conversation.vad.silenceSeconds = silenceAutoSendSeconds
            await conversation.startListening(oneShot: false)
            guard generation == self.liveGeneration else {
                liveConversationOn = false
                if conversation.isListening { conversation.stopListening() }
                return
            }
            if let err = conversation.lastError {
                liveConversationOn = false
                banner = err
            } else {
                banner = "Listening. Just talk."
                warm()
            }
        }
    }

    func stopSpeaking() {
        conversation.cancelPendingSpeech()
        conversation.tts.stop()
    }

    /// A cloud recognizer without its key falls back to Apple's on-device speech instead of refusing.
    private func ensureSTTReady() -> Bool {
        guard !isUnavailable else {
            banner = "The desk is not set up on your hub"
            return false
        }
        switch speechSettings.sttProvider {
        case .appleOnDevice, .onDeviceWhisper:
            return true
        case .openAIWhisper:
            if speechSettings.openAIAPIKey.trimmingCharacters(in: .whitespaces).isEmpty {
                speechSettings.sttProvider = .appleOnDevice
                banner = "No OpenAI key for Whisper. Switched to Apple Speech."
            }
            return true
        case .azureSpeech:
            if speechSettings.azureSpeechKey.trimmingCharacters(in: .whitespaces).isEmpty {
                speechSettings.sttProvider = .appleOnDevice
                banner = "No Azure key. Switched to Apple Speech."
            }
            return true
        }
    }

    /// Speak a streaming reply sentence by sentence, starting before the reply is finished.
    private func speakStreaming(id: String, final: Bool) {
        guard !cutReplies.contains(id), let full = streaming[id] else { return }
        let start = spokenUpTo[id] ?? 0
        guard start < full.count else { return }
        let rest = String(full.dropFirst(start))
        let chunk = final ? rest : Self.readyChunk(rest)
        let trimmed = chunk.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        spokenUpTo[id] = start + chunk.count
        if start == 0, liveConversationOn {
            conversation.speakLatest(trimmed, tag: id)
        } else {
            conversation.enqueueSpeak(trimmed, tag: id)
        }
    }

    /// The first full sentence, or a long enough phrase, including its trailing space.
    private static func readyChunk(_ text: String) -> String {
        var index = text.startIndex
        while index < text.endIndex {
            let char = text[index]
            let next = text.index(after: index)
            if ".!?".contains(char), next < text.endIndex, text[next].isWhitespace {
                let sentence = String(text[..<next])
                if sentence.trimmingCharacters(in: .whitespaces).count >= 8 { return String(text[...next]) }
            }
            index = next
        }
        if text.count >= 60, let space = text.lastIndex(of: " ") {
            let prefix = String(text[...space])
            if prefix.count >= 30 { return prefix }
        }
        return ""
    }

    /// Signed out of the hub: stop talking and forget the thread on this device.
    func logout() {
        liveConversationOn = false
        conversation.stopAll()
        messages = []
        tasks = [:]
        trackerNow = []
        loaded = false
        configured = nil
        setupNote = nil
    }
}
