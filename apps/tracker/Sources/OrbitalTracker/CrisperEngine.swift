import Foundation
import Observation
import OrbitalTrackerCore

/// Runs and talks to the optional speech sidecar (stt/crisper_server.py: CrisperWhisper 2.0,
/// resident on Metal through PyTorch MPS) on 127.0.0.1:<tracker.stt_port>.
/// Only used when `tracker.voice = true`. Set it up with `apps/tracker/scripts/setup-voice.sh`.
@MainActor @Observable
final class CrisperEngine {
    enum Phase: Equatable {
        case stopped, starting, loading, ready
        case failed(String)
    }

    var phase: Phase = .stopped
    var device: String?
    var attention: String?
    var lastLatency: Double?

    let config: OrbitalConfig
    let paths: TrackerPaths
    let port: UInt16
    @ObservationIgnored let log: JSONLineLogger
    @ObservationIgnored private var process: Process?
    @ObservationIgnored private var intentionalStop = false
    @ObservationIgnored private var restarts = 0
    @ObservationIgnored private var startTask: Task<Void, Never>?

    init(config: OrbitalConfig, paths: TrackerPaths, log: JSONLineLogger) {
        self.config = config
        self.paths = paths
        self.port = config.sttPort
        self.log = log
    }

    var isReady: Bool { phase == .ready }

    var statusText: String {
        switch phase {
        case .stopped: return "Speech engine off"
        case .starting: return "Starting CrisperWhisper…"
        case .loading: return "Loading CrisperWhisper on Metal…"
        case .ready: return "CrisperWhisper · \(device == "mps" ? "Metal" : (device ?? "?"))"
        case .failed(let why): return why
        }
    }

    /// Where setup-voice.sh puts the Python env and a copy of the sidecar script.
    var voiceDir: URL { config.home.appendingPathComponent("voice", isDirectory: true) }

    /// Python with crisperwhisper[transformers] + torch (MPS). First match wins:
    /// `defaults write <bundle id> sttPython /path/to/python`, `ORBITAL_TRACKER_STT_PYTHON`,
    /// then `$ORBITAL_HOME/voice/venv/bin/python` (made by setup-voice.sh).
    var pythonPath: String? {
        let candidates = [
            UserDefaults.standard.string(forKey: "sttPython"),
            ProcessInfo.processInfo.environment["ORBITAL_TRACKER_STT_PYTHON"],
            voiceDir.appendingPathComponent("venv/bin/python").path,
        ].compactMap { $0 }.filter { !$0.isEmpty }
        return candidates.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    /// `ORBITAL_TRACKER_STT_SCRIPT`, the copy inside the app bundle, then `$ORBITAL_HOME/voice/`.
    var scriptPath: String? {
        let candidates = [
            ProcessInfo.processInfo.environment["ORBITAL_TRACKER_STT_SCRIPT"],
            Bundle.main.path(forResource: "crisper_server", ofType: "py"),
            voiceDir.appendingPathComponent("crisper_server.py").path,
        ].compactMap { $0 }.filter { !$0.isEmpty }
        return candidates.first { FileManager.default.fileExists(atPath: $0) }
    }

    func ensureRunning() {
        guard config.voice else { return }
        switch phase {
        case .starting, .loading, .ready: return
        default: break
        }
        startTask?.cancel()
        startTask = Task { await start() }
    }

    private func start() async {
        phase = .starting
        // A sidecar from a previous launch may still be up (it exits with its parent,
        // but give it a chance): adopt it if healthy.
        if let health = await health(), health.ready {
            adopt(health)
            return
        }
        guard let python = pythonPath else {
            phase = .failed("No speech Python env — run apps/tracker/scripts/setup-voice.sh")
            return
        }
        guard let script = scriptPath else {
            phase = .failed("crisper_server.py is missing — run apps/tracker/scripts/setup-voice.sh")
            return
        }

        let proc = Process()
        proc.executableURL = URL(fileURLWithPath: python)
        proc.arguments = [script, "--port", "\(port)", "--parent-pid", "\(getpid())",
                          "--device", "mps", "--attn", "sdpa", "--mode", "intended"]
        var env = ProcessInfo.processInfo.environment
        env["PYTORCH_ENABLE_MPS_FALLBACK"] = "1"
        env["HF_HUB_OFFLINE"] = env["HF_HUB_OFFLINE"] ?? "1"   // setup-voice.sh downloads the model up front
        env["PYTHONUNBUFFERED"] = "1"
        env["TOKENIZERS_PARALLELISM"] = "false"
        proc.environment = env
        proc.currentDirectoryURL = paths.root
        let logURL = paths.voiceLogFile
        JSONLineLogger.rollIfLarge(logURL, maxBytes: config.logMaxBytes, backups: config.logBackups)
        if !FileManager.default.fileExists(atPath: logURL.path) {
            FileManager.default.createFile(atPath: logURL.path, contents: nil)
        }
        if let handle = try? FileHandle(forWritingTo: logURL) {
            _ = try? handle.seekToEnd()
            proc.standardOutput = handle
            proc.standardError = handle
        }
        proc.standardInput = FileHandle.nullDevice
        proc.terminationHandler = { [weak self] p in
            let status = p.terminationStatus
            Task { @MainActor in self?.exited(status) }
        }
        do {
            intentionalStop = false
            try proc.run()
            process = proc
            log.info("speech sidecar started", ["port": Int(port), "pid": Int(proc.processIdentifier)])
        } catch {
            phase = .failed("Couldn't start CrisperWhisper: \(error.localizedDescription)")
            log.error("speech sidecar failed to start", ["error": error.localizedDescription])
            return
        }

        phase = .loading
        for _ in 0..<240 {
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            if Task.isCancelled || process == nil { return }
            guard let health = await health() else { continue }
            if health.ready {
                adopt(health)
                return
            }
            if !health.loading, let error = health.error {
                phase = .failed("CrisperWhisper failed: \(error)")
                log.error("speech model failed to load", ["error": error])
                return
            }
        }
        phase = .failed("CrisperWhisper took too long to load (see logs/tracker-voice.stderr.log)")
    }

    private func adopt(_ health: Health) {
        device = health.device
        attention = health.attn
        phase = .ready
        restarts = 0
        log.info("speech model ready", ["device": health.device, "attn": health.attn])
    }

    private func exited(_ status: Int32) {
        process = nil
        if intentionalStop {
            phase = .stopped
            return
        }
        restarts += 1
        log.warning("speech sidecar exited", ["status": Int(status), "restarts": restarts])
        if restarts <= 3 {
            phase = .stopped
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { [weak self] in self?.ensureRunning() }
        } else {
            phase = .failed("CrisperWhisper keeps crashing (exit \(status)) — see logs/tracker-voice.stderr.log")
        }
    }

    func stop() {
        intentionalStop = true
        startTask?.cancel()
        process?.terminate()
        process = nil
        phase = .stopped
    }

    // MARK: HTTP

    struct Health: Decodable {
        var ready: Bool
        var loading: Bool
        var error: String?
        var device: String?
        var attn: String?
    }

    private func health() async -> Health? {
        var req = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/health")!, timeoutInterval: 2)
        req.httpMethod = "GET"
        guard let (data, _) = try? await URLSession.shared.data(for: req) else { return nil }
        return try? JSONDecoder().decode(Health.self, from: data)
    }

    struct Transcript: Decodable {
        var text: String?
        var latency_seconds: Double?
        var error: String?
    }

    func transcribe(_ samples: [Float]) async throws -> String {
        var req = URLRequest(url: URL(string: "http://127.0.0.1:\(port)/transcribe")!, timeoutInterval: 60)
        req.httpMethod = "POST"
        req.setValue("application/octet-stream", forHTTPHeaderField: "Content-Type")
        req.setValue("\(VoiceActivityDetector.sampleRate)", forHTTPHeaderField: "X-Sample-Rate")
        req.httpBody = samples.withUnsafeBufferPointer { Data(buffer: $0) }
        let (data, response) = try await URLSession.shared.data(for: req)
        let status = (response as? HTTPURLResponse)?.statusCode ?? 0
        let result = try JSONDecoder().decode(Transcript.self, from: data)
        guard status == 200 else { throw StoreError(message: result.error ?? "transcription failed (\(status))") }
        lastLatency = result.latency_seconds
        return result.text ?? ""
    }
}
