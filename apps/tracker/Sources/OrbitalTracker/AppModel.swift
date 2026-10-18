@preconcurrency import AVFoundation
import Foundation
import Observation
import OrbitalTrackerCore
import SwiftUI

enum ViewMode: String { case active, history }

/// Where an item's details open when you click a bubble or a history title.
enum DetailStyle: String, CaseIterable, Identifiable {
    case inline, window
    var id: String { rawValue }
    var label: String { self == .inline ? "Inline in the panel" : "In a floating window" }
}

@MainActor @Observable
final class AppModel {
    let config: OrbitalConfig
    let paths: TrackerPaths
    let store: TrackerStore
    let stt: CrisperEngine
    @ObservationIgnored let log: JSONLineLogger
    @ObservationIgnored let capture = AudioCapture()
    @ObservationIgnored let relay: HubRelay?

    /// `tracker.voice` in config.toml. Off: no microphone, no speech sidecar.
    let voiceEnabled: Bool
    /// Global shortcuts are on: `tracker.hotkeys` and no ORBITAL_TRACKER_NO_HOTKEYS in the environment.
    let hotkeysEnabled: Bool

    // Microphone
    var devices: [AudioInputDevice] = []
    var selectedDeviceID: String? {
        didSet {
            UserDefaults.standard.set(selectedDeviceID, forKey: "micDeviceID")
            if micLive { startCapture() }
        }
    }
    var micLive = false
    var level: Float = 0
    var hearingSpeech = false
    var transcribing = 0
    var micError: String?

    // UI state
    var filter: ItemFilter { didSet { UserDefaults.standard.set(filter.rawValue, forKey: "filter") } }
    var viewMode: ViewMode = .active { didSet { selectedID = nil } }
    var selectedID: String?
    var historyRange: HistoryRange = .today { didSet { historyPage = 0; expandedHistoryID = nil } }
    var historyPage = 0
    /// Items per history page; any whole number in `pageSizeRange` (set with `setHistoryPageSize`).
    private(set) var historyPageSize: Int
    static let pageSizeRange = 1...999
    var isEditingPageSize = false
    var expandedHistoryID: String?
    var detailStyle: DetailStyle {
        didSet {
            UserDefaults.standard.set(detailStyle.rawValue, forKey: "detailStyle")
            guard detailStyle == .window else { return }
            // Whatever is open inline right now moves into its own window.
            let open = [selectedID, expandedHistoryID].compactMap { $0 }
            selectedID = nil
            expandedHistoryID = nil
            open.forEach { onOpenDetailWindow?($0) }
        }
    }
    var toast: String?
    var isTyping = false
    var typedText = ""

    // Settings
    /// Grok titles on/off (menu); only offered when `tracker.titles` isn't "none" and grok is installed.
    var useGrok: Bool { didSet { UserDefaults.standard.set(useGrok, forKey: "useGrok") } }
    var requireWakeWord: Bool { didSet { UserDefaults.standard.set(requireWakeWord, forKey: "requireWakeWord") } }
    /// Comma-separated; each entry can be several words ("hey tracker, track").
    var wakeWordsText: String { didSet { UserDefaults.standard.set(wakeWordsText, forKey: "wakeWords") } }
    var isEditingWakeWord = false
    var pauseSeconds: Double {
        didSet {
            UserDefaults.standard.set(pauseSeconds, forKey: "pauseSeconds")
            capture.setHangover(pauseSeconds)
        }
    }
    let titleMode: TitleMode
    let grokPath: String?
    var grokAvailable: Bool { grokPath != nil }
    var grokAllowed: Bool { titleMode != .none && grokAvailable }
    /// Shared with the CLI and the Orbital Agents app through settings.json (screenshot evidence level).
    private(set) var shared: TrackerSettings
    /// How much screenshot / video proof agents must attach for UI, website and game work.
    var evidence: EvidenceLevel {
        get { shared.evidence }
        set { setEvidence(newValue, by: "tracker") }
    }

    @ObservationIgnored var onContentSize: ((CGSize) -> Void)?
    @ObservationIgnored var onWantsKeyFocus: (() -> Void)?
    @ObservationIgnored var onOpenDetailWindow: ((String) -> Void)?
    @ObservationIgnored private var toastToken = 0
    @ObservationIgnored private var wakeArmedUntil: Date?
    @ObservationIgnored private var ticker: Timer?
    @ObservationIgnored private var deviceObservers: [NSObjectProtocol] = []
    @ObservationIgnored private var settingsStamp: Date?
    @ObservationIgnored private var settingsTimer: Timer?
    @ObservationIgnored private var hubSettingsTimer: Timer?
    @ObservationIgnored private let hubQueue = DispatchQueue(label: "orbital.tracker.hub", qos: .utility)

    init(config: OrbitalConfig, paths: TrackerPaths, log: JSONLineLogger) {
        self.config = config
        self.paths = paths
        self.log = log
        voiceEnabled = config.voice
        hotkeysEnabled = config.hotkeys && ProcessInfo.processInfo.environment["ORBITAL_TRACKER_NO_HOTKEYS"] == nil
        titleMode = config.titles
        grokPath = config.titles == .none ? nil : GrokProcessor.locate()
        relay = HubRelay(config: config, paths: paths)
        store = TrackerStore(paths: paths, config: config, log: log)
        stt = CrisperEngine(config: config, paths: paths, log: log)
        let d = UserDefaults.standard
        filter = ItemFilter(rawValue: d.string(forKey: "filter") ?? "") ?? .all
        historyPageSize = Self.clampPageSize(d.object(forKey: "historyPageSize") as? Int ?? 10)
        detailStyle = DetailStyle(rawValue: d.string(forKey: "detailStyle") ?? "") ?? .inline
        useGrok = d.object(forKey: "useGrok") as? Bool ?? true
        requireWakeWord = d.bool(forKey: "requireWakeWord")
        wakeWordsText = d.string(forKey: "wakeWords") ?? "hey tracker, track"
        pauseSeconds = d.object(forKey: "pauseSeconds") as? Double ?? 1.1
        selectedDeviceID = d.string(forKey: "micDeviceID")
        shared = TrackerSettings.load(paths)
        settingsStamp = Self.modified(paths.settingsFile)

        ticker = Timer.scheduledTimer(withTimeInterval: 5, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.store.now = Date() }
        }
        // The hub (for the Orbital Agents app) and the CLI change settings.json behind our back.
        settingsTimer = Timer.scheduledTimer(withTimeInterval: 1.5, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.reloadSettingsIfChanged() }
        }
        if relay != nil {
            // Satellites: the hub's level wins when it was changed more recently.
            hubSettingsTimer = Timer.scheduledTimer(withTimeInterval: 30, repeats: true) { [weak self] _ in
                MainActor.assumeIsolated { self?.pullHubSettings() }
            }
            pullHubSettings()
        }

        guard voiceEnabled else { return }
        capture.setHangover(pauseSeconds)
        capture.onLevel = { [weak self] level, speaking in
            Task { @MainActor in
                self?.level = level
                self?.hearingSpeech = speaking
            }
        }
        capture.onUtterance = { [weak self] samples in
            Task { @MainActor in self?.handleAudio(samples) }
        }
        refreshDevices()
        for name in [AVCaptureDevice.wasConnectedNotification, AVCaptureDevice.wasDisconnectedNotification] {
            deviceObservers.append(NotificationCenter.default.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                MainActor.assumeIsolated { self?.refreshDevices() }
            })
        }
        if d.bool(forKey: "micWasLive") { setMic(true) }
    }

    // MARK: Shared settings (settings.json)

    /// Saves a new screenshot evidence level. `by` is "tracker", "app" or "cli".
    func setEvidence(_ level: EvidenceLevel, by: String) {
        guard level != shared.evidence else { return }
        shared = TrackerSettings(evidence: level, updatedAt: Date(), updatedBy: by)
        do {
            try shared.save(paths)
        } catch {
            showToast("Couldn't save settings: \(error.localizedDescription)")
            log.error("settings save failed", ["error": error.localizedDescription])
        }
        settingsStamp = Self.modified(paths.settingsFile)
        log.info("evidence level changed", ["evidence": level.rawValue, "by": shared.updatedBy])
        announceEvidence()
        if shared.updatedBy == "tracker", let relay {
            let snapshot = shared
            hubQueue.async { relay.pushSettings(snapshot, by: "tracker") }
        }
    }

    func reloadSettingsIfChanged() {
        let stamp = Self.modified(paths.settingsFile)
        guard stamp != settingsStamp else { return }
        settingsStamp = stamp
        let fresh = TrackerSettings.load(paths)
        let changed = fresh.evidence != shared.evidence
        shared = fresh
        if changed {
            log.info("evidence level changed", ["evidence": fresh.evidence.rawValue, "by": fresh.updatedBy])
            announceEvidence()
        }
    }

    private func pullHubSettings() {
        guard let relay else { return }
        hubQueue.async { [weak self] in
            guard let remote = relay.fetchSettings() else { return }
            DispatchQueue.main.async {
                guard let self else { return }
                let winner = TrackerSettings.newest(self.shared, remote)
                guard winner != self.shared else { return }
                let changed = winner.evidence != self.shared.evidence
                self.shared = winner
                try? winner.save(self.paths)
                self.settingsStamp = Self.modified(self.paths.settingsFile)
                if changed { self.announceEvidence() }
            }
        }
    }

    private func announceEvidence() {
        var text = "📷 Agent screenshots: \(shared.evidence.label)"
        switch shared.updatedBy {
        case "app": text += " · set from Orbital Agents"
        case "cli": text += " · set from the CLI"
        default: break
        }
        showToast(text)
    }

    private static func modified(_ url: URL) -> Date? {
        (try? FileManager.default.attributesOfItem(atPath: url.path))?[.modificationDate] as? Date
    }

    // MARK: Microphone

    func refreshDevices() {
        guard voiceEnabled else { return }
        devices = AudioCapture.devices()
        if let id = selectedDeviceID, !devices.contains(where: { $0.id == id }), micLive {
            micError = "Selected mic disconnected — using the system default"
        }
    }

    var currentDeviceName: String {
        if let id = selectedDeviceID, let d = devices.first(where: { $0.id == id }) { return d.name }
        return AudioCapture.defaultDeviceName() ?? "Default mic"
    }

    func toggleMic() {
        guard voiceEnabled else {
            showToast("Voice is off: set tracker.voice = true in config.toml, then orbital config --apply")
            return
        }
        setMic(!micLive)
    }

    func setMic(_ on: Bool) {
        guard voiceEnabled else { return }
        UserDefaults.standard.set(on, forKey: "micWasLive")
        guard on else {
            capture.stop()
            micLive = false
            hearingSpeech = false
            level = 0
            micError = nil
            return
        }
        Task {
            guard await Self.microphoneAllowed() else {
                micError = "Mic access denied — System Settings › Privacy & Security › Microphone"
                micLive = false
                return
            }
            stt.ensureRunning()
            startCapture()
        }
    }

    private func startCapture() {
        do {
            let id = selectedDeviceID.flatMap { id in devices.contains { $0.id == id } ? id : nil }
            try capture.start(deviceID: id)
            micLive = true
            micError = nil
        } catch {
            micLive = false
            micError = error.localizedDescription
        }
    }

    private static func microphoneAllowed() async -> Bool {
        switch AVCaptureDevice.authorizationStatus(for: .audio) {
        case .authorized: return true
        case .notDetermined: return await AVCaptureDevice.requestAccess(for: .audio)
        default: return false
        }
    }

    // MARK: Voice pipeline: VAD phrase → CrisperWhisper (Metal) → title → bubble

    private func handleAudio(_ samples: [Float], fromMic: Bool = true) {
        if fromMic, !micLive { return }
        transcribing += 1
        Task {
            defer { transcribing -= 1 }
            // The model may still be warming up right after launch: wait for it.
            stt.ensureRunning()
            for _ in 0..<90 where !stt.isReady {
                if case .failed = stt.phase { break }
                try? await Task.sleep(nanoseconds: 500_000_000)
            }
            guard stt.isReady else {
                showToast("Speech engine isn't ready: \(stt.statusText)")
                return
            }
            do {
                let text = try await stt.transcribe(samples)
                if !text.isEmpty { handlePhrase(text, source: .voice) }
            } catch {
                showToast("Transcription failed: \(error.localizedDescription)")
                log.warning("transcription failed", ["error": error.localizedDescription])
            }
        }
    }

    /// Testing hook (POST /v1/debug/audio): runs an audio file through the same
    /// CrisperWhisper → title → bubble path the microphone uses.
    func injectAudio(_ url: URL) throws {
        guard voiceEnabled else { throw StoreError(message: "voice is off (tracker.voice = false)") }
        let file = try AVAudioFile(forReading: url)
        let source = file.processingFormat
        guard let target = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16_000, channels: 1, interleaved: false),
              let converter = AVAudioConverter(from: source, to: target),
              let input = AVAudioPCMBuffer(pcmFormat: source, frameCapacity: AVAudioFrameCount(file.length)) else {
            throw StoreError(message: "unsupported audio file")
        }
        try file.read(into: input)
        let capacity = AVAudioFrameCount(Double(file.length) * 16_000 / source.sampleRate) + 1024
        guard let output = AVAudioPCMBuffer(pcmFormat: target, frameCapacity: capacity) else { throw StoreError(message: "buffer") }
        var fed = false
        var error: NSError?
        converter.convert(to: output, error: &error) { _, status in
            if fed {
                status.pointee = .endOfStream
                return nil
            }
            fed = true
            status.pointee = .haveData
            return input
        }
        if let error { throw error }
        guard let channel = output.floatChannelData?[0] else { throw StoreError(message: "no audio") }
        handleAudio(Array(UnsafeBufferPointer(start: channel, count: Int(output.frameLength))), fromMic: false)
    }

    func submitTyped() {
        let text = typedText.trimmingCharacters(in: .whitespacesAndNewlines)
        typedText = ""
        isTyping = false
        guard !text.isEmpty else { return }
        handlePhrase(text, source: .manual)
    }

    func handlePhrase(_ raw: String, source: ItemSource) {
        var phrase = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if source == .voice, requireWakeWord {
            if let rest = Self.stripWakeWord(phrase, wakeWords: wakeWords) {
                if rest.isEmpty {
                    // Just the wake word: take the next phrase as the item.
                    wakeArmedUntil = Date().addingTimeInterval(8)
                    showToast("Listening… say what you're working on")
                    return
                }
                phrase = rest
            } else if let until = wakeArmedUntil, until > Date() {
                // Follow-up after a bare wake word.
            } else {
                return
            }
            wakeArmedUntil = nil
        }
        let words = phrase.split(separator: " ").count
        if source == .voice, words < 2 { return }
        let transcript = source == .voice ? phrase : nil
        let open = store.items.filter { $0.status == .active && !$0.dismissed }
        let completionLike = VoiceHeuristics.looksLikeCompletion(phrase)

        // Short typed text is already a title.
        if source == .manual, words <= 5, !completionLike {
            store.addPersonal(title: phrase, details: phrase, transcript: nil, source: .manual, processing: false)
            return
        }

        guard useGrok, grokAllowed, let grok = grokPath else {
            localDecide(phrase, transcript: transcript, source: source, open: open, completionLike: completionLike)
            return
        }

        var provisional: String?
        if completionLike {
            showToast("Checking “\(phrase)”…")
        } else {
            provisional = store.addPersonal(title: VoiceHeuristics.title(phrase), details: phrase,
                                            transcript: transcript, source: source, processing: true)
        }
        let model = config.grokModel
        let scratch = paths.scratchDir
        Task {
            let decision = await GrokProcessor.decide(grok: grok, model: model, phrase: phrase, openItems: open, scratch: scratch)
            apply(decision, phrase: phrase, transcript: transcript, source: source, provisional: provisional, open: open)
        }
    }

    private func apply(_ decision: GrokDecision?, phrase: String, transcript: String?, source: ItemSource,
                       provisional: String?, open: [TrackedItem]) {
        guard let decision else {
            // Grok unavailable or timed out: keep the offline guess.
            if let provisional {
                store.finishProcessing(provisional, title: nil, details: nil)
            } else {
                localDecide(phrase, transcript: transcript, source: source, open: open, completionLike: true)
            }
            return
        }
        // Grok names things; these guards keep a flaky answer from losing a real task.
        let words = phrase.split(separator: " ").count
        var action = decision.action
        if action == "complete", !VoiceHeuristics.saysDone(phrase) { action = "add" }
        if action == "ignore", words > 3 { action = "add" }
        // "I'm done with X" that was misread as new work: close the matching item instead.
        if action == "add", provisional == nil, VoiceHeuristics.bestMatch(phrase, in: open) != nil { action = "complete" }

        switch action {
        case "complete":
            if let provisional { store.discard(provisional, reason: "it was a completion") }
            let target = store.item(decision.matchID).flatMap { $0.status == .active ? $0 : nil }
                ?? VoiceHeuristics.bestMatch(phrase, in: open)
            if let target {
                store.markDoneByVoice(target.id, phrase: phrase)
                showToast("✓ Done: \(target.title)")
            } else {
                showToast("Couldn't tell which item you finished — click its X")
            }
        case "ignore":
            if let provisional {
                store.discard(provisional, reason: "not a task")
                showToast("Skipped: “\(phrase.prefix(48))”")
            }
        default:
            let title = VoiceHeuristics.tidyTitle(decision.title, phrase: phrase)
            let details = decision.details.isEmpty ? phrase : decision.details
            if let provisional {
                store.finishProcessing(provisional, title: title, details: details)
            } else {
                store.addPersonal(title: title, details: details, transcript: transcript, source: source, processing: false)
            }
        }
    }

    private func localDecide(_ phrase: String, transcript: String?, source: ItemSource, open: [TrackedItem], completionLike: Bool) {
        if completionLike, let target = VoiceHeuristics.bestMatch(phrase, in: open) {
            store.markDoneByVoice(target.id, phrase: phrase)
            showToast("✓ Done: \(target.title)")
            return
        }
        store.addPersonal(title: VoiceHeuristics.title(phrase), details: phrase, transcript: transcript,
                          source: source, processing: false)
    }

    // MARK: Wake words

    /// Each wake word as lowercase words: "Hey, Tracker" → ["hey", "tracker"].
    var wakeWords: [[String]] {
        wakeWordsText.split(separator: ",").map { Self.words(String($0)) }.filter { !$0.isEmpty }
    }

    var wakeWordsDisplay: String {
        let list = wakeWords.map { "“\($0.joined(separator: " "))”" }
        return list.isEmpty ? "none set" : list.joined(separator: " or ")
    }

    func editWakeWords() {
        isEditingWakeWord = true
        onWantsKeyFocus?()
    }

    func saveWakeWords(_ text: String) {
        let cleaned = text.split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        isEditingWakeWord = false
        guard !cleaned.isEmpty else { return }
        wakeWordsText = cleaned.joined(separator: ", ")
        requireWakeWord = true
        showToast("Wake word on: \(wakeWordsDisplay)")
    }

    private static let wordRegex = try! NSRegularExpression(pattern: "[\\p{L}\\p{N}']+")
    private static let fillers: Set<String> = ["ok", "okay", "hey", "so", "um", "uh", "alright", "and"]

    static func words(_ s: String) -> [String] {
        let ns = s as NSString
        return wordRegex.matches(in: s, range: NSRange(location: 0, length: ns.length)).map { ns.substring(with: $0.range).lowercased() }
    }

    /// The phrase with its leading wake word removed; "" if it was only the wake word;
    /// nil if it doesn't start with one. Punctuation and case from speech-to-text are
    /// ignored ("Hey, tracker. Fixing the login bug" matches "hey tracker"), and one
    /// leading filler word ("okay hey tracker …") is allowed.
    static func stripWakeWord(_ phrase: String, wakeWords: [[String]]) -> String? {
        let ns = phrase as NSString
        let tokens = wordRegex.matches(in: phrase, range: NSRange(location: 0, length: ns.length))
            .map { (word: ns.substring(with: $0.range).lowercased(), range: $0.range) }
        for wake in wakeWords.sorted(by: { $0.count > $1.count }) {
            for offset in 0...1 where offset + wake.count <= tokens.count {
                if offset == 1, !fillers.contains(tokens[0].word) { continue }
                guard tokens[offset..<(offset + wake.count)].map(\.word) == wake else { continue }
                let end = NSMaxRange(tokens[offset + wake.count - 1].range)
                return ns.substring(from: end).trimmingCharacters(in: .whitespacesAndNewlines.union(.punctuationCharacters))
            }
        }
        return nil
    }

    // MARK: UI helpers

    func showToast(_ message: String) {
        toastToken += 1
        let token = toastToken
        withAnimation(.easeOut(duration: 0.2)) { toast = message }
        DispatchQueue.main.asyncAfter(deadline: .now() + 3.5) { [weak self] in
            guard let self, self.toastToken == token else { return }
            withAnimation(.easeIn(duration: 0.2)) { self.toast = nil }
        }
    }

    func select(_ id: String) {
        if detailStyle == .window { onOpenDetailWindow?(id); return }
        withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) {
            selectedID = selectedID == id ? nil : id
        }
    }

    /// A history title was clicked.
    func toggleHistoryDetail(_ id: String) {
        if detailStyle == .window { onOpenDetailWindow?(id); return }
        withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) {
            expandedHistoryID = expandedHistoryID == id ? nil : id
        }
    }

    /// The bubble's inline card → its own window, whatever the setting.
    func popOut(_ id: String) {
        if selectedID == id { selectedID = nil }
        onOpenDetailWindow?(id)
    }

    static func clampPageSize(_ n: Int) -> Int { min(max(n, pageSizeRange.lowerBound), pageSizeRange.upperBound) }

    func setHistoryPageSize(_ n: Int) {
        let size = Self.clampPageSize(n)
        guard size != historyPageSize else { return }
        // Stay on the page that holds the first row you were looking at.
        historyPage = historyPage * historyPageSize / size
        historyPageSize = size
        UserDefaults.standard.set(size, forKey: "historyPageSize")
    }

    func editPageSize() {
        isEditingPageSize = true
        onWantsKeyFocus?()
    }

    func markDone(_ id: String) {
        guard let item = store.item(id), item.status == .active else { return }
        showToast("✓ Done: \(item.title)")
        close(id)
    }

    func close(_ id: String) {
        if selectedID == id { selectedID = nil }
        store.userClose(id)
    }

    func startTyping() {
        isTyping = true
        onWantsKeyFocus?()
    }

    /// "⌃⌥N" with the configured modifiers, or nil when global shortcuts are off.
    func shortcut(_ key: String) -> String? {
        hotkeysEnabled ? config.hotkeyModifiers.shortcut(key) : nil
    }

    /// " (⌃⌥N)" for help texts and menus, or "" when shortcuts are off.
    func shortcutSuffix(_ key: String) -> String {
        shortcut(key).map { " (\($0))" } ?? ""
    }

    enum Tone { case muted, live, warn }

    var statusLine: (text: String, tone: Tone) {
        let count = store.activeCount
        guard voiceEnabled else {
            let via = config.relaysToHub ? " · \(config.machineName) → hub" : ""
            let type = shortcut("N").map { " · \($0) to type" } ?? " · + to type"
            return ("\(count) active\(type)\(via)", .muted)
        }
        if let micError { return (micError, .warn) }
        if transcribing > 0 { return ("Transcribing…", .live) }
        if micLive {
            if hearingSpeech { return ("Hearing you…", .live) }
            switch stt.phase {
            case .ready:
                if requireWakeWord, let first = wakeWords.first {
                    return ("Say “\(first.joined(separator: " "))…” · \(currentDeviceName)", .live)
                }
                return ("Listening · \(currentDeviceName)", .live)
            case .failed(let why): return (why, .warn)
            default: return (stt.statusText, .muted)
            }
        }
        let talk = shortcut("M").map { "\($0) to talk · " } ?? ""
        return ("Mic off · \(talk)\(count) active", .muted)
    }
}
