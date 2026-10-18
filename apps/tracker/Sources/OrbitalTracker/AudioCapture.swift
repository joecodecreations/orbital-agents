import AVFoundation
import Foundation

struct AudioInputDevice: Identifiable, Hashable {
    let id: String
    let name: String
}

/// Energy VAD with an adaptive noise floor. Works on 20 ms frames at 16 kHz.
struct VoiceActivityDetector {
    static let sampleRate = 16_000
    static let frameSize = 320

    var hangoverSeconds: Double = 1.1
    private(set) var inSpeech = false
    private var noiseFloor: Float = 0.003
    private var onsetFrames = 0
    private var silenceFrames = 0
    private var speechFrames = 0
    private var preRoll: [Float] = []
    private var utterance: [Float] = []

    private let preRollSamples = Int(0.4 * Double(sampleRate))
    private let maxSamples = 28 * sampleRate
    private let minSpeechFrames = 15  // 0.3 s of actual voice

    /// Feeds one frame; returns a finished utterance when the speaker pauses.
    mutating func feed(_ frame: [Float], rms: Float) -> [Float]? {
        let startThreshold = max(noiseFloor * 3.2, 0.008)
        let keepThreshold = max(noiseFloor * 2.0, 0.005)

        if !inSpeech {
            // Track the room's noise level while nobody is talking.
            if rms < noiseFloor { noiseFloor = noiseFloor * 0.9 + rms * 0.1 }
            else if rms < noiseFloor * 3 { noiseFloor = noiseFloor * 0.995 + rms * 0.005 }
            noiseFloor = min(max(noiseFloor, 0.0005), 0.05)

            preRoll.append(contentsOf: frame)
            if preRoll.count > preRollSamples { preRoll.removeFirst(preRoll.count - preRollSamples) }

            onsetFrames = rms > startThreshold ? onsetFrames + 1 : 0
            if onsetFrames >= 3 {
                inSpeech = true
                utterance = preRoll
                preRoll.removeAll(keepingCapacity: true)
                silenceFrames = 0
                speechFrames = onsetFrames
            }
            return nil
        }

        utterance.append(contentsOf: frame)
        if rms > keepThreshold {
            silenceFrames = 0
            speechFrames += 1
        } else {
            silenceFrames += 1
        }
        let hangoverFrames = Int(hangoverSeconds * Double(Self.sampleRate) / Double(Self.frameSize))
        if silenceFrames >= hangoverFrames || utterance.count >= maxSamples {
            inSpeech = false
            onsetFrames = 0
            let done = utterance
            utterance.removeAll(keepingCapacity: true)
            // Trim most of the trailing silence; keep a little tail.
            let tail = max(0, silenceFrames * Self.frameSize - Self.sampleRate / 4)
            let trimmed = Array(done.prefix(done.count - min(tail, done.count)))
            return speechFrames >= minSpeechFrames ? trimmed : nil
        }
        return nil
    }

    mutating func reset() {
        inSpeech = false
        onsetFrames = 0
        silenceFrames = 0
        speechFrames = 0
        preRoll.removeAll()
        utterance.removeAll()
    }
}

/// Captures the chosen microphone as 16 kHz mono float and hands finished
/// phrases to `onUtterance`. All callbacks arrive on a private queue.
final class AudioCapture: NSObject, AVCaptureAudioDataOutputSampleBufferDelegate, @unchecked Sendable {
    var onLevel: (@Sendable (Float, Bool) -> Void)?
    var onUtterance: (@Sendable ([Float]) -> Void)?

    private let session = AVCaptureSession()
    private let output = AVCaptureAudioDataOutput()
    private let queue = DispatchQueue(label: "orbital.tracker.audio", qos: .userInitiated)
    private var input: AVCaptureDeviceInput?
    private var vad = VoiceActivityDetector()
    private var pending: [Float] = []
    private var frameCount = 0

    static func devices() -> [AudioInputDevice] {
        let discovery = AVCaptureDevice.DiscoverySession(deviceTypes: [.microphone, .external],
                                                         mediaType: .audio, position: .unspecified)
        var seen = Set<String>()
        return discovery.devices.compactMap { d in
            seen.insert(d.uniqueID).inserted ? AudioInputDevice(id: d.uniqueID, name: d.localizedName) : nil
        }
    }

    static func defaultDeviceName() -> String? { AVCaptureDevice.default(for: .audio)?.localizedName }

    func setHangover(_ seconds: Double) {
        queue.async { self.vad.hangoverSeconds = seconds }
    }

    func start(deviceID: String?) throws {
        let device = deviceID.flatMap { AVCaptureDevice(uniqueID: $0) } ?? AVCaptureDevice.default(for: .audio)
        guard let device else { throw StoreError(message: "No microphone found") }
        let newInput = try AVCaptureDeviceInput(device: device)

        session.beginConfiguration()
        if let input { session.removeInput(input) }
        guard session.canAddInput(newInput) else {
            session.commitConfiguration()
            throw StoreError(message: "Can't use \(device.localizedName)")
        }
        session.addInput(newInput)
        input = newInput
        if !session.outputs.contains(output) {
            output.audioSettings = [
                AVFormatIDKey: kAudioFormatLinearPCM,
                AVSampleRateKey: VoiceActivityDetector.sampleRate,
                AVNumberOfChannelsKey: 1,
                AVLinearPCMBitDepthKey: 32,
                AVLinearPCMIsFloatKey: true,
                AVLinearPCMIsNonInterleaved: false,
                AVLinearPCMIsBigEndianKey: false,
            ]
            output.setSampleBufferDelegate(self, queue: queue)
            if session.canAddOutput(output) { session.addOutput(output) }
        }
        session.commitConfiguration()
        queue.async {
            self.vad.reset()
            self.pending.removeAll()
            if !self.session.isRunning { self.session.startRunning() }
        }
    }

    func stop() {
        queue.async {
            if self.session.isRunning { self.session.stopRunning() }
            self.vad.reset()
            self.pending.removeAll()
            self.onLevel?(0, false)
        }
    }

    // MARK: Sample buffers

    func captureOutput(_ output: AVCaptureOutput, didOutput sampleBuffer: CMSampleBuffer, from connection: AVCaptureConnection) {
        guard let samples = Self.monoFloat16k(sampleBuffer) else { return }
        pending.append(contentsOf: samples)
        let n = VoiceActivityDetector.frameSize
        while pending.count >= n {
            let frame = Array(pending.prefix(n))
            pending.removeFirst(n)
            var sum: Float = 0
            for s in frame { sum += s * s }
            let rms = (sum / Float(n)).squareRoot()
            if let phrase = vad.feed(frame, rms: rms) { onUtterance?(phrase) }
            frameCount += 1
            if frameCount % 3 == 0 {
                let db = 20 * log10(max(rms, 1e-6))
                onLevel?(min(max((db + 55) / 45, 0), 1), vad.inSpeech)
            }
        }
    }

    /// Reads the buffer as mono float at 16 kHz. Capture output normally converts
    /// for us (audioSettings); if a device ignores that, downmix + resample here.
    private static func monoFloat16k(_ buffer: CMSampleBuffer) -> [Float]? {
        guard let block = CMSampleBufferGetDataBuffer(buffer),
              let format = CMSampleBufferGetFormatDescription(buffer),
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(format)?.pointee else { return nil }
        let length = CMBlockBufferGetDataLength(block)
        guard length > 0 else { return nil }
        var bytes = [UInt8](repeating: 0, count: length)
        guard CMBlockBufferCopyDataBytes(block, atOffset: 0, dataLength: length, destination: &bytes) == noErr else { return nil }

        let channels = max(Int(asbd.mChannelsPerFrame), 1)
        let isFloat = asbd.mFormatFlags & kAudioFormatFlagIsFloat != 0
        let bits = Int(asbd.mBitsPerChannel)
        var interleaved: [Float]
        if isFloat && bits == 32 {
            interleaved = bytes.withUnsafeBytes { Array($0.bindMemory(to: Float.self)) }
        } else if !isFloat && bits == 16 {
            interleaved = bytes.withUnsafeBytes { $0.bindMemory(to: Int16.self).map { Float($0) / 32768 } }
        } else if !isFloat && bits == 32 {
            interleaved = bytes.withUnsafeBytes { $0.bindMemory(to: Int32.self).map { Float($0) / 2_147_483_648 } }
        } else {
            return nil
        }

        let nonInterleaved = asbd.mFormatFlags & kAudioFormatFlagIsNonInterleaved != 0
        var mono: [Float]
        if channels == 1 {
            mono = interleaved
        } else if nonInterleaved {
            mono = Array(interleaved.prefix(interleaved.count / channels))  // first channel plane
        } else {
            let frames = interleaved.count / channels
            mono = [Float](repeating: 0, count: frames)
            for f in 0..<frames {
                var s: Float = 0
                for c in 0..<channels { s += interleaved[f * channels + c] }
                mono[f] = s / Float(channels)
            }
        }

        let rate = asbd.mSampleRate
        let target = Double(VoiceActivityDetector.sampleRate)
        guard rate > 0, abs(rate - target) > 1 else { return mono }
        let ratio = rate / target
        let outCount = Int(Double(mono.count) / ratio)
        var out = [Float](repeating: 0, count: outCount)
        let window = max(Int(ratio.rounded()), 1)  // boxcar pre-filter against aliasing
        for i in 0..<outCount {
            let start = Int(Double(i) * ratio)
            let end = min(start + window, mono.count)
            var s: Float = 0
            for j in start..<end { s += mono[j] }
            out[i] = s / Float(max(end - start, 1))
        }
        return out
    }
}
