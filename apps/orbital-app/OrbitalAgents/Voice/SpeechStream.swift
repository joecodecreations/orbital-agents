import AVFoundation
import Foundation

/// A Fish clip that plays while the hub is still making it.
/// The hub sends 16-bit little-endian mono PCM as Fish produces it, so the first words play
/// about a second after the request instead of after the whole sentence is made (2–5 s).
@MainActor
final class SpeechStream {
    /// Audio held before the first word plays. Fish sends faster than real time but in bursts;
    /// this covers the bursts plus Tailscale jitter, so a sentence doesn't stutter.
    static let lead = 0.35

    /// Float32 mono at the hub's rate.
    let format: AVAudioFormat
    private(set) var buffers: [AVAudioPCMBuffer] = []
    private(set) var finished = false
    private var frames: AVAudioFrameCount = 0
    private var waiters: [CheckedContinuation<Void, Never>] = []
    private var reader: Task<Void, Never>?
    private let opened = Date()

    /// Seconds of audio received so far.
    var seconds: Double { Double(frames) / format.sampleRate }

    init?(sampleRate: Double) {
        guard sampleRate > 0,
              let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: sampleRate, channels: 1, interleaved: false)
        else { return nil }
        self.format = format
    }

    /// Read the response body in the background. A body cut short still plays what arrived.
    func start(_ chunks: AsyncThrowingStream<Data, Error>) {
        reader = Task { [weak self] in
            var carry = Data()
            do {
                for try await chunk in chunks {
                    guard let self else { return }
                    carry.append(chunk)
                    let whole = carry.count & ~1
                    guard whole > 0 else { continue }
                    if let buffer = Self.pcm(carry.prefix(whole), format: self.format) { self.append(buffer) }
                    carry = Data(carry.dropFirst(whole))
                }
            } catch {
                if !Task.isCancelled, let self {
                    VoiceDebug.shared.log("tts stream cut short after \(String(format: "%.1f", self.seconds))s of audio: \(error.localizedDescription)")
                }
            }
            self?.finish()
        }
    }

    /// Stop downloading (the reply was cut or replaced).
    func cancel() {
        reader?.cancel()
        reader = nil
        finish()
    }

    /// Wait until `lead` seconds have arrived, or the clip is complete.
    func prebuffer(_ lead: Double) async {
        while seconds < lead, !finished {
            await withCheckedContinuation { waiters.append($0) }
        }
    }

    /// Buffer `index`, waiting for it to arrive; nil once the clip has ended.
    func buffer(at index: Int) async -> AVAudioPCMBuffer? {
        while index >= buffers.count, !finished {
            await withCheckedContinuation { waiters.append($0) }
        }
        return index < buffers.count ? buffers[index] : nil
    }

    private func append(_ buffer: AVAudioPCMBuffer) {
        if buffers.isEmpty {
            VoiceDebug.shared.log("tts stream first audio in \(Int(Date().timeIntervalSince(opened) * 1000)) ms")
        }
        buffers.append(buffer)
        frames += buffer.frameLength
        wake()
    }

    private func finish() {
        finished = true
        wake()
    }

    private func wake() {
        let ready = waiters
        waiters.removeAll()
        ready.forEach { $0.resume() }
    }

    // MARK: Playback

    /// Schedule the clip on `player` in `format` as it arrives, once `lead` seconds are in.
    /// `added` hears the length of each piece scheduled; `ended` runs after the last of it plays.
    /// Stops scheduling when `isCurrent` turns false (playback was cut or replaced).
    func schedule(
        on player: AVAudioPlayerNode,
        format: AVAudioFormat,
        lead: Double,
        isCurrent: @escaping () -> Bool,
        added: (Double) -> Void,
        ended: @escaping @MainActor () -> Void
    ) async {
        await prebuffer(lead)
        let converter = format == self.format ? nil : AVAudioConverter(from: self.format, to: format)
        var index = 0
        while isCurrent(), let next = await buffer(at: index) {
            index += 1
            guard isCurrent() else { return }
            let piece = converter.map { Self.convert(next, with: $0, to: format) } ?? next
            guard let piece else { continue }
            if !player.isPlaying, player.engine?.isRunning == true { player.play() }
            player.scheduleBuffer(piece, completionCallbackType: .dataPlayedBack) { _ in }
            added(Double(piece.frameLength) / format.sampleRate)
        }
        guard isCurrent() else { return }
        // The resampler holds a few frames back until it is told the clip is over.
        if let converter, let tail = Self.convert(nil, with: converter, to: format) {
            player.scheduleBuffer(tail, completionCallbackType: .dataPlayedBack) { _ in }
            added(Double(tail.frameLength) / format.sampleRate)
        }
        // Buffers play in order, so a short silence after the last one marks the end.
        guard let marker = Self.silence(format: format, seconds: 0.02) else {
            ended()
            return
        }
        if !player.isPlaying, player.engine?.isRunning == true { player.play() }
        player.scheduleBuffer(marker, completionCallbackType: .dataPlayedBack) { _ in
            Task { @MainActor in ended() }
        }
    }

    // MARK: Conversion

    static func pcm(_ data: Data, format: AVAudioFormat) -> AVAudioPCMBuffer? {
        let count = data.count / 2
        guard count > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(count)),
              let out = buffer.floatChannelData?[0] else { return nil }
        data.withUnsafeBytes { (raw: UnsafeRawBufferPointer) in
            for i in 0 ..< count {
                let sample = Int16(bitPattern: UInt16(raw[2 * i]) | UInt16(raw[2 * i + 1]) << 8)
                out[i] = Float(sample) / 32768
            }
        }
        buffer.frameLength = AVAudioFrameCount(count)
        return buffer
    }

    /// One piece through a converter that lives for the whole clip, so the resampler keeps its
    /// state across pieces (a fresh one per piece clicks at every seam). nil input flushes the end.
    private static func convert(_ buffer: AVAudioPCMBuffer?, with converter: AVAudioConverter, to format: AVAudioFormat) -> AVAudioPCMBuffer? {
        let inFrames = Double(buffer?.frameLength ?? 0)
        let capacity = AVAudioFrameCount(inFrames * format.sampleRate / converter.inputFormat.sampleRate) + 2048
        guard let out = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: capacity) else { return nil }
        var fed = false
        var error: NSError?
        let status = converter.convert(to: out, error: &error) { _, outStatus in
            guard let buffer else {
                outStatus.pointee = .endOfStream
                return nil
            }
            if fed {
                outStatus.pointee = .noDataNow
                return nil
            }
            fed = true
            outStatus.pointee = .haveData
            return buffer
        }
        guard status != .error, out.frameLength > 0 else { return nil }
        return out
    }

    private static func silence(format: AVAudioFormat, seconds: Double) -> AVAudioPCMBuffer? {
        let frames = AVAudioFrameCount(max(1, format.sampleRate * seconds))
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames) else { return nil }
        buffer.frameLength = frames
        // Interleaved formats keep every channel in the first pointer.
        let planes = format.isInterleaved ? 1 : Int(format.channelCount)
        let count = Int(frames) * (format.isInterleaved ? Int(format.channelCount) : 1)
        for plane in 0 ..< planes {
            if let data = buffer.floatChannelData {
                data[plane].update(repeating: 0, count: count)
            } else if let data = buffer.int16ChannelData {
                data[plane].update(repeating: 0, count: count)
            } else if let data = buffer.int32ChannelData {
                data[plane].update(repeating: 0, count: count)
            }
        }
        return buffer
    }
}
