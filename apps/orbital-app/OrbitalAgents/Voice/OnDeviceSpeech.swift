import CryptoKit
import Foundation
import Network

/// One on-device speech model the hub offers (`GET /v1/stt/models`).
struct OnDeviceModelInfo: Codable, Identifiable, Equatable {
    struct File: Codable, Equatable {
        let path: String
        let size: Int64
        let sha256: String
    }

    let id: String
    let name: String
    let source: String?
    let license: String?
    let version: String
    let totalBytes: Int64
    let int8: Bool?
    let files: [File]

    enum CodingKeys: String, CodingKey {
        case id, name, source, license, version, int8, files
        case totalBytes = "total_bytes"
    }

    var sizeLabel: String { ByteCountFormatter.string(fromByteCount: totalBytes, countStyle: .file) }
}

/// An on-device Whisper model: downloads it once from your hub (hash-checked, resumable per
/// file), loads it, and transcribes finished phrases for
/// STTService. Apple Speech still supplies the live words; this writes the text that is sent.
@MainActor
final class OnDeviceSpeech: ObservableObject {
    static let shared = OnDeviceSpeech()

    enum Phase: Equatable {
        case notInstalled
        case downloading(Double)
        case verifying
        case loading
        case ready
        case failed(String)
    }

    @Published private(set) var phase: Phase = .notInstalled
    @Published private(set) var available: [OnDeviceModelInfo] = []
    @Published private(set) var installed: OnDeviceModelInfo?
    @Published private(set) var lastRun = ""
    @Published private(set) var downloadedBytes: Int64 = 0

    /// The hub, set by DeskState once signed in.
    var api: (() -> DeskAPI?)?

    private let settings = SpeechSettings.shared
    private var engine: AnyObject?
    private var loadTask: Task<Void, Never>?
    private var downloadTask: Task<Void, Never>?
    private let path = NWPathMonitor()
    private var unmetered = false

    static var supported: Bool {
        if #available(iOS 18.0, macCatalyst 18.0, *) { return true }
        return false
    }

    private static var root: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        return base.appendingPathComponent("OnDeviceSpeech", isDirectory: true)
    }

    private init() {
        path.pathUpdateHandler = { [weak self] update in
            let ok = update.status == .satisfied && !update.isExpensive && !update.isConstrained
            Task { @MainActor in self?.unmetered = ok }
        }
        path.start(queue: DispatchQueue(label: "orbital.ondevice-speech.path"))
        installed = Self.readInstalled(preferring: settings.onDeviceModelID)
    }

    var isReady: Bool { phase == .ready }

    var statusLine: String {
        switch phase {
        case .notInstalled:
            return Self.supported ? "Not downloaded" : "Needs iOS 18"
        case .downloading(let fraction):
            return "Downloading \(Int(fraction * 100))%"
        case .verifying: return "Checking the download…"
        case .loading: return "Preparing the model (the first time takes about a minute)…"
        case .ready: return "Ready · \(installed?.name ?? "")"
        case .failed(let why): return why
        }
    }

    var updateAvailable: OnDeviceModelInfo? {
        guard let installed, let remote = available.first(where: { $0.id == installed.id }) else { return nil }
        return remote.version == installed.version ? nil : remote
    }

    // MARK: Load

    /// Loads the installed model if it isn't loaded yet. Cheap to call often.
    func prepare() {
        guard Self.supported, engine == nil, loadTask == nil, let installed else { return }
        if case .downloading = phase { return }
        phase = .loading
        let folder = Self.folder(for: installed.id)
        loadTask = Task {
            defer { self.loadTask = nil }
            guard #available(iOS 18.0, macCatalyst 18.0, *) else { return }
            let started = Date()
            do {
                let loaded = try await OnDeviceWhisper.load(folder: folder)
                self.engine = loaded
                self.phase = .ready
                VoiceDebug.shared.log("ondevice loaded \(installed.id) in \(String(format: "%.1f", Date().timeIntervalSince(started)))s")
            } catch {
                self.phase = .failed("Could not load the model: \(error.localizedDescription)")
                VoiceDebug.shared.log("ondevice load failed: \(error.localizedDescription)")
            }
        }
    }

    /// Transcribes one phrase (16 kHz mono). nil when the model isn't ready or fails.
    func transcribe(_ audio: [Float], language: SpeechLanguage) async -> String? {
        guard #available(iOS 18.0, macCatalyst 18.0, *), let engine = engine as? OnDeviceWhisper else {
            prepare()
            return nil
        }
        let span = OnDeviceWhisper.speechSpan(audio)
        let clip = Array(audio[span])
        guard clip.count > OnDeviceWhisper.sampleRate / 5 else { return nil }
        do {
            let result = try await engine.transcribe(clip, language: language.rawValue, mode: settings.onDeviceMode.rawValue)
            lastRun = String(format: "%.1f s of speech in %.2f s", result.audioSeconds, result.encodeSeconds + result.decodeSeconds)
            VoiceDebug.shared.log(String(format: "ondevice %.1fs → enc %.2fs + %d tok %.2fs: %@",
                                         result.audioSeconds, result.encodeSeconds, result.tokens, result.decodeSeconds,
                                         String(result.text.prefix(80))))
            return result.text
        } catch {
            VoiceDebug.shared.log("ondevice failed: \(error.localizedDescription)")
            return nil
        }
    }

    // MARK: Download

    func refreshAvailable() async {
        guard let api = api?() else { return }
        if let models = try? await api.speechModels() {
            available = models
        }
    }

    /// iPhone only, on Wi-Fi: fetch the chosen model if on-device Whisper is the speech-to-text
    /// provider and nothing is installed yet. Otherwise load what is installed.
    func autoInstallIfNeeded() {
        guard Self.supported, settings.sttProvider == .onDeviceWhisper else { return }
        if installed != nil {
            prepare()
            return
        }
        #if !targetEnvironment(macCatalyst)
        guard unmetered, downloadTask == nil else { return }
        Task {
            await refreshAvailable()
            guard installed == nil, downloadTask == nil else { return }
            let pick = available.first(where: { $0.id == settings.onDeviceModelID }) ?? available.first
            if let pick { download(pick) }
        }
        #endif
    }

    func download(_ model: OnDeviceModelInfo) {
        guard downloadTask == nil, let api = api?() else {
            if api?() == nil { phase = .failed("Sign in to your hub first") }
            return
        }
        downloadTask = Task {
            defer { self.downloadTask = nil }
            do {
                try await self.fetch(model, api: api)
                self.settings.onDeviceModelID = model.id
                self.engine = nil
                self.installed = model
                self.phase = .notInstalled
                self.prepare()
            } catch is CancellationError {
                self.phase = self.installed == nil ? .notInstalled : .ready
            } catch {
                self.phase = .failed("Download failed: \(error.localizedDescription)")
            }
        }
    }

    func cancelDownload() {
        downloadTask?.cancel()
    }

    func remove() {
        downloadTask?.cancel()
        engine = nil
        try? FileManager.default.removeItem(at: Self.root)
        installed = nil
        phase = .notInstalled
        lastRun = ""
    }

    private func fetch(_ model: OnDeviceModelInfo, api: DeskAPI) async throws {
        let fm = FileManager.default
        try fm.createDirectory(at: Self.root, withIntermediateDirectories: true)
        var root = Self.root
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try? root.setResourceValues(values)

        if let free = try? Self.root.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey]).volumeAvailableCapacityForImportantUsage,
           free < model.totalBytes + 500_000_000 {
            throw DownloadError.noSpace(ByteCountFormatter.string(fromByteCount: model.totalBytes, countStyle: .file))
        }

        let staging = Self.root.appendingPathComponent(model.id + ".partial", isDirectory: true)
        try fm.createDirectory(at: staging, withIntermediateDirectories: true)
        let done = Self.folder(for: model.id)
        var finished: Int64 = 0
        phase = .downloading(0)
        downloadedBytes = 0
        for file in model.files {
            try Task.checkCancellation()
            let target = staging.appendingPathComponent(file.path)
            try fm.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
            // Kept from an earlier, interrupted run (or the installed copy of an older version).
            let previous = done.appendingPathComponent(file.path)
            if !fm.fileExists(atPath: target.path), fm.fileExists(atPath: previous.path),
               await Self.sha256(of: previous) == file.sha256 {
                try fm.copyItem(at: previous, to: target)
            }
            if fm.fileExists(atPath: target.path), (try? fm.attributesOfItem(atPath: target.path)[.size] as? Int64) == file.size,
               await Self.sha256(of: target) == file.sha256 {
                finished += file.size
                continue
            }
            var attempt = 0
            while true {
                attempt += 1
                do {
                    let request = try api.speechModelFileRequest(model: model.id, path: file.path)
                    let base = finished
                    try await Self.download(request, to: target) { got in
                        Task { @MainActor in
                            self.downloadedBytes = base + got
                            self.phase = .downloading(Double(base + got) / Double(max(1, model.totalBytes)))
                        }
                    }
                    phase = .verifying
                    guard await Self.sha256(of: target) == file.sha256 else { throw DownloadError.corrupt(file.path) }
                    break
                } catch {
                    try? fm.removeItem(at: target)
                    if error is CancellationError || attempt >= 3 { throw error }
                }
            }
            finished += file.size
            phase = .downloading(Double(finished) / Double(max(1, model.totalBytes)))
        }
        try JSONEncoder().encode(model).write(to: staging.appendingPathComponent("manifest.json"))
        if fm.fileExists(atPath: done.path) { try fm.removeItem(at: done) }
        try fm.moveItem(at: staging, to: done)
        // Only one model is kept.
        for other in (try? fm.contentsOfDirectory(at: Self.root, includingPropertiesForKeys: nil)) ?? [] where other.lastPathComponent != model.id {
            try? fm.removeItem(at: other)
        }
    }

    enum DownloadError: LocalizedError {
        case noSpace(String)
        case corrupt(String)
        case http(Int)
        var errorDescription: String? {
            switch self {
            case .noSpace(let size): return "Not enough free space for \(size)"
            case .corrupt(let path): return "\(path) did not match its checksum"
            case .http(let code): return "server answered \(code)"
            }
        }
    }

    // MARK: Files

    private static func folder(for id: String) -> URL {
        root.appendingPathComponent(id, isDirectory: true)
    }

    private static func readInstalled(preferring id: String) -> OnDeviceModelInfo? {
        let fm = FileManager.default
        let folders = (try? fm.contentsOfDirectory(at: root, includingPropertiesForKeys: nil)) ?? []
        let models = folders.compactMap { folder -> OnDeviceModelInfo? in
            guard let data = try? Data(contentsOf: folder.appendingPathComponent("manifest.json")) else { return nil }
            return try? JSONDecoder().decode(OnDeviceModelInfo.self, from: data)
        }
        return models.first(where: { $0.id == id }) ?? models.first
    }

    nonisolated private static func sha256(of url: URL) async -> String {
        await Task.detached(priority: .utility) {
            guard let handle = try? FileHandle(forReadingFrom: url) else { return "" }
            defer { try? handle.close() }
            var hasher = SHA256()
            while let chunk = try? handle.read(upToCount: 8 << 20), !chunk.isEmpty {
                hasher.update(data: chunk)
            }
            return hasher.finalize().map { String(format: "%02x", $0) }.joined()
        }.value
    }

    private static let session: URLSession = {
        let config = URLSessionConfiguration.default
        config.timeoutIntervalForRequest = 60
        config.timeoutIntervalForResource = 4 * 3600
        config.waitsForConnectivity = true
        return URLSession(configuration: config)
    }()

    /// Downloads to `target`, reporting bytes received about three times a second.
    nonisolated private static func download(_ request: URLRequest, to target: URL, progress: @escaping @Sendable (Int64) -> Void) async throws {
        var task: URLSessionDownloadTask?
        try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
                let download = session.downloadTask(with: request) { temp, response, error in
                    if let error {
                        continuation.resume(throwing: (error as? URLError)?.code == .cancelled ? CancellationError() : error)
                        return
                    }
                    let code = (response as? HTTPURLResponse)?.statusCode ?? -1
                    guard let temp, (200 ... 299).contains(code) else {
                        continuation.resume(throwing: DownloadError.http(code))
                        return
                    }
                    do {
                        try? FileManager.default.removeItem(at: target)
                        try FileManager.default.moveItem(at: temp, to: target)
                        continuation.resume()
                    } catch {
                        continuation.resume(throwing: error)
                    }
                }
                task = download
                download.resume()
                Task.detached {
                    while download.state == .running || download.state == .suspended {
                        progress(download.countOfBytesReceived)
                        try? await Task.sleep(nanoseconds: 300_000_000)
                    }
                }
            }
        } onCancel: {
            task?.cancel()
        }
    }
}
