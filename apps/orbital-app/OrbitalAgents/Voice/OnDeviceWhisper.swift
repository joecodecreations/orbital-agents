import Accelerate
import CoreML
import Foundation

/// A Whisper model converted for Core ML (the hub serves it from `/v1/stt/models`) running on
/// this device: free, offline, nothing leaves the device.
///
/// The model folder holds `Encoder.mlmodelc` (log-mel → cross-attention keys/values),
/// `Decoder.mlmodelc` (one token + self-attention cache → next-token logits), `speech.json`,
/// `tokens.json` and `mel_filters.bin`. Decoding is greedy. A model with styles picks one from
/// five tags fed before `<|startoftranscript|>`: `[intended_*]` (clean text) or `[verbatim_*]`.
///
/// Work runs on one serial queue: Core ML calls block, and transcriptions must not overlap.
@available(iOS 18.0, macCatalyst 18.0, *)
final class OnDeviceWhisper: @unchecked Sendable {
    struct Config: Decodable {
        let id: String
        let name: String
        let nMels: Int
        let maxTargetPositions: Int
        let decoderLayers: Int
        let dModel: Int
        let sot: Int
        let eot: Int
        let transcribe: Int
        let noTimestamps: Int
        let languages: [String: Int]
        let modes: [String: [Int]]
        let sounds: [String: String]
        let suppress: [Int]
        let skip: [Int]

        enum CodingKeys: String, CodingKey {
            case id, name, sot, eot, transcribe, languages, modes, sounds, suppress, skip
            case nMels = "n_mels"
            case maxTargetPositions = "max_target_positions"
            case decoderLayers = "decoder_layers"
            case dModel = "d_model"
            case noTimestamps = "no_timestamps"
        }
    }

    struct Result {
        let text: String
        let audioSeconds: Double
        let encodeSeconds: Double
        let decodeSeconds: Double
        let tokens: Int
    }

    enum Failure: LocalizedError {
        case missing(String)
        case badOutput(String)
        var errorDescription: String? {
            switch self {
            case .missing(let s): return "On-device model is missing \(s)"
            case .badOutput(let s): return "On-device model returned no \(s)"
            }
        }
    }

    static let sampleRate = LogMel.sampleRate
    static let windowSamples = LogMel.samples

    let config: Config
    private let encoder: MLModel
    private let decoder: MLModel
    private let mel: LogMel
    private let detokenizer: Detokenizer
    /// 0 for allowed tokens, -inf for suppressed ones; added to the logits before argmax.
    private let suppressMask: [Float]
    private let queue = DispatchQueue(label: "orbital.ondevice-whisper", qos: .userInitiated)

    /// Loads and warms the models. The first load on a device compiles them for the Neural
    /// Engine, which can take a minute; Core ML caches that, so later launches take seconds.
    static func load(folder: URL) async throws -> OnDeviceWhisper {
        let config = try JSONDecoder().decode(Config.self, from: Data(contentsOf: folder.appendingPathComponent("speech.json")))
        let tokens = try JSONDecoder().decode([String].self, from: Data(contentsOf: folder.appendingPathComponent("tokens.json")))
        let filters = try Data(contentsOf: folder.appendingPathComponent("mel_filters.bin"))
        let options = MLModelConfiguration()
        #if targetEnvironment(macCatalyst)
        // A Mac GPU runs the encoder three times faster than its Neural Engine.
        options.computeUnits = .cpuAndGPU
        #else
        // An iPhone's Neural Engine is far faster than its GPU for this.
        options.computeUnits = .cpuAndNeuralEngine
        #endif
        let encoder = try await MLModel.load(contentsOf: folder.appendingPathComponent("Encoder.mlmodelc"), configuration: options)
        let decoder = try await MLModel.load(contentsOf: folder.appendingPathComponent("Decoder.mlmodelc"), configuration: options)
        let engine = try OnDeviceWhisper(config: config, tokens: tokens, filters: filters, encoder: encoder, decoder: decoder)
        // One pass on silence so the first real phrase doesn't pay for specialization.
        _ = try? await engine.transcribe([Float](repeating: 0, count: sampleRate), language: "en", mode: "intended", maxTokens: 2)
        return engine
    }

    private init(config: Config, tokens: [String], filters: Data, encoder: MLModel, decoder: MLModel) throws {
        guard tokens.count >= config.eot else { throw Failure.missing("tokens") }
        self.config = config
        self.encoder = encoder
        self.decoder = decoder
        mel = try LogMel(filters: filters, nMels: config.nMels)
        detokenizer = Detokenizer(tokens: tokens, skip: Set(config.skip), sounds: config.sounds)
        var mask = [Float](repeating: 0, count: tokens.count)
        for id in config.suppress where id < mask.count { mask[id] = -.infinity }
        suppressMask = mask
    }

    /// Transcribes 16 kHz mono audio. Longer than 30 s is cut into windows at quiet points.
    func transcribe(_ audio: [Float], language: String, mode: String, maxTokens: Int? = nil) async throws -> Result {
        try await withCheckedThrowingContinuation { continuation in
            queue.async {
                continuation.resume(with: Swift.Result { try self.run(audio, language: language, mode: mode, maxTokens: maxTokens) })
            }
        }
    }

    // MARK: - Pipeline

    private func run(_ audio: [Float], language: String, mode: String, maxTokens: Int?) throws -> Result {
        var texts: [String] = []
        var encodeTime = 0.0, decodeTime = 0.0, count = 0
        for window in Self.windows(audio.count, audio: audio) {
            let clip = Array(audio[window])
            let started = Date()
            let (crossK, crossV) = try encode(clip)
            let encoded = Date()
            let seconds = Double(clip.count) / Double(Self.sampleRate)
            let limit = maxTokens ?? Int(16 + seconds * 8)
            let ids = try decodeTokens(crossK: crossK, crossV: crossV, language: language, mode: mode, maxTokens: limit)
            encodeTime += encoded.timeIntervalSince(started)
            decodeTime += Date().timeIntervalSince(encoded)
            count += ids.count
            let text = detokenizer.text(ids, verbatim: mode == "verbatim")
            if !text.isEmpty { texts.append(text) }
        }
        return Result(
            text: texts.joined(separator: " "),
            audioSeconds: Double(audio.count) / Double(Self.sampleRate),
            encodeSeconds: encodeTime,
            decodeSeconds: decodeTime,
            tokens: count
        )
    }

    private func encode(_ clip: [Float]) throws -> (MLMultiArray, MLMultiArray) {
        let input = try MLMultiArray(shape: [1, NSNumber(value: config.nMels), NSNumber(value: LogMel.frames)], dataType: .float32)
        mel.compute(clip, into: input)
        let out = try encoder.prediction(from: MLDictionaryFeatureProvider(dictionary: ["mel": input]))
        guard let k = out.featureValue(for: "cross_k")?.multiArrayValue,
              let v = out.featureValue(for: "cross_v")?.multiArrayValue else { throw Failure.badOutput("cross keys") }
        return (k, v)
    }

    private func decodeTokens(crossK: MLMultiArray, crossV: MLMultiArray, language: String, mode: String, maxTokens: Int) throws -> [Int] {
        let layers = config.decoderLayers, positions = config.maxTargetPositions, width = config.dModel
        let cacheShape: [NSNumber] = [NSNumber(value: layers), 1, NSNumber(value: positions), NSNumber(value: width)]
        let selfK = try MLMultiArray(shape: cacheShape, dataType: .float16)
        let selfV = try MLMultiArray(shape: cacheShape, dataType: .float16)
        for cache in [selfK, selfV] {
            cache.withUnsafeMutableBytes { raw, _ in _ = memset(raw.baseAddress!, 0, raw.count) }
        }
        let inputID = try MLMultiArray(shape: [1, 1], dataType: .int32)
        let position = try MLMultiArray(shape: [1], dataType: .int32)

        let languageToken = config.languages[language] ?? config.languages["en"] ?? config.sot + 1
        var tokens = (config.modes[mode] ?? []) + [config.sot, languageToken, config.transcribe, config.noTimestamps]
        let promptCount = tokens.count
        let limit = min(maxTokens, positions - promptCount - 1)
        var scratch = [Float](repeating: 0, count: suppressMask.count)

        var pos = 0
        while pos < tokens.count {
            inputID[0] = NSNumber(value: Int32(tokens[pos]))
            position[0] = NSNumber(value: Int32(pos))
            let out = try decoder.prediction(from: MLDictionaryFeatureProvider(dictionary: [
                "input_id": inputID, "position": position,
                "self_k": selfK, "self_v": selfV, "cross_k": crossK, "cross_v": crossV,
            ]))
            guard let newK = out.featureValue(for: "new_k")?.multiArrayValue,
                  let newV = out.featureValue(for: "new_v")?.multiArrayValue,
                  let logits = out.featureValue(for: "logits")?.multiArrayValue else { throw Failure.badOutput("logits") }
            Self.write(newK, into: selfK, at: pos, layers: layers, width: width)
            Self.write(newV, into: selfV, at: pos, layers: layers, width: width)
            pos += 1
            if pos < tokens.count { continue } // still feeding the prompt

            let next = argmax(logits, scratch: &scratch)
            if next == config.eot || tokens.count - promptCount >= limit { break }
            tokens.append(next)
            if Self.looping(Array(tokens[promptCount...])) {
                tokens.removeLast(16)
                break
            }
        }
        return Array(tokens[promptCount...])
    }

    private func argmax(_ logits: MLMultiArray, scratch: inout [Float]) -> Int {
        let n = min(scratch.count, logits.count)
        if logits.dataType == .float32 {
            logits.withUnsafeBufferPointer(ofType: Float.self) { values in
                scratch.withUnsafeMutableBufferPointer { out in
                    vDSP_vadd(values.baseAddress!, 1, suppressMask, 1, out.baseAddress!, 1, vDSP_Length(n))
                }
            }
        } else {
            for i in 0 ..< n { scratch[i] = logits[i].floatValue + suppressMask[i] }
        }
        var best: Float = 0
        var index: vDSP_Length = 0
        vDSP_maxvi(scratch, 1, &best, &index, vDSP_Length(n))
        return Int(index)
    }

    /// Copies one token's keys or values [L, 1, 1, d] into the cache [L, 1, P, d] at `position`.
    private static func write(_ slice: MLMultiArray, into cache: MLMultiArray, at position: Int, layers: Int, width: Int) {
        let src = slice.strides.map(\.intValue)
        let bytes = MemoryLayout<UInt16>.size
        slice.withUnsafeBytes { from in
            cache.withUnsafeMutableBytes { to, dst in
                for layer in 0 ..< layers {
                    let target = to.baseAddress! + (layer * dst[0] + position * dst[2]) * bytes
                    let source = from.baseAddress! + layer * src[0] * bytes
                    if src[3] == 1, dst[3] == 1 {
                        memcpy(target, source, width * bytes)
                    } else {
                        for i in 0 ..< width {
                            memcpy(target + i * dst[3] * bytes, source + i * src[3] * bytes, bytes)
                        }
                    }
                }
            }
        }
    }

    /// The last 8 tokens repeated three times in a row: a decoding loop.
    private static func looping(_ out: [Int]) -> Bool {
        guard out.count >= 24 else { return false }
        let a = out[(out.count - 8)...], b = out[(out.count - 16) ..< (out.count - 8)], c = out[(out.count - 24) ..< (out.count - 16)]
        return a.elementsEqual(b) && b.elementsEqual(c)
    }

    /// ≤ 30 s windows, each cut at the quietest 100 ms of its last 6 seconds.
    static func windows(_ count: Int, audio: [Float]) -> [Range<Int>] {
        var out: [Range<Int>] = []
        var start = 0
        while count - start > windowSamples {
            let block = sampleRate / 10
            var best = start + windowSamples, quietest = Float.greatestFiniteMagnitude
            var cut = start + windowSamples - 6 * sampleRate
            while cut + block <= start + windowSamples {
                var energy: Float = 0
                audio.withUnsafeBufferPointer { vDSP_svesq($0.baseAddress! + cut, 1, &energy, vDSP_Length(block)) }
                if energy < quietest { quietest = energy; best = cut + block / 2 }
                cut += block
            }
            out.append(start ..< best)
            start = best
        }
        if count > start { out.append(start ..< count) }
        return out
    }

    /// The part of a recording that holds speech: from a quarter second before the first loud
    /// 20 ms block to 0.4 s after the last. Long silences around a phrase only cost time.
    static func speechSpan(_ audio: [Float]) -> Range<Int> {
        let block = sampleRate / 50
        guard audio.count > block * 4 else { return 0 ..< audio.count }
        var levels: [Float] = []
        levels.reserveCapacity(audio.count / block)
        audio.withUnsafeBufferPointer { buf in
            var i = 0
            while i + block <= buf.count {
                var rms: Float = 0
                vDSP_rmsqv(buf.baseAddress! + i, 1, &rms, vDSP_Length(block))
                levels.append(rms)
                i += block
            }
        }
        let peak = levels.max() ?? 0
        let threshold = max(peak * 0.06, 0.002)
        guard let first = levels.firstIndex(where: { $0 >= threshold }),
              let last = levels.lastIndex(where: { $0 >= threshold }) else { return 0 ..< audio.count }
        let lower = max(0, first * block - sampleRate / 4)
        let upper = min(audio.count, (last + 1) * block + sampleRate * 2 / 5)
        return lower ..< upper
    }
}

// MARK: - Log-mel

/// Whisper's log-mel spectrogram (WhisperFeatureExtractor), computed with Accelerate:
/// 30 s zero-padded, reflect-padded STFT (n_fft 400, hop 160, periodic Hann), power,
/// mel filterbank, log10, floor at max − 8, (x + 4) / 4. `convert.py --check` verifies the
/// same algorithm against the reference.
final class LogMel {
    static let sampleRate = 16_000
    static let samples = 30 * sampleRate
    static let nFFT = 400
    static let hop = 160
    static let frames = 3000
    static let bins = nFFT / 2 + 1

    let nMels: Int
    private let filtersT: [Float] // [bins × nMels]
    private let cosBasis: [Float] // [nFFT × bins]
    private let sinBasis: [Float]
    private let window: [Float]

    init(filters: Data, nMels: Int) throws {
        let bins = Self.bins
        guard filters.count == nMels * bins * 4 else { throw OnDeviceWhisperError.badFilters }
        self.nMels = nMels
        let raw = filters.withUnsafeBytes { Array($0.bindMemory(to: Float.self)) } // [nMels × bins]
        var transposed = [Float](repeating: 0, count: raw.count)
        vDSP_mtrans(raw, 1, &transposed, 1, vDSP_Length(bins), vDSP_Length(nMels))
        filtersT = transposed
        var c = [Float](repeating: 0, count: Self.nFFT * bins)
        var s = [Float](repeating: 0, count: Self.nFFT * bins)
        for n in 0 ..< Self.nFFT {
            for k in 0 ..< bins {
                let angle = 2 * Double.pi * Double(n * k) / Double(Self.nFFT)
                c[n * bins + k] = Float(cos(angle))
                s[n * bins + k] = Float(sin(angle))
            }
        }
        cosBasis = c
        sinBasis = s
        window = (0 ..< Self.nFFT).map { Float(0.5 - 0.5 * cos(2 * Double.pi * Double($0) / Double(Self.nFFT))) }
    }

    /// Fills `out` ([1, nMels, 3000] float32) with the log-mel of up to 30 s of audio.
    func compute(_ audio: [Float], into out: MLMultiArray) {
        let samples = Self.samples
        let pad = Self.nFFT / 2
        let count = min(audio.count, samples)
        // The padded signal: [reflection | audio, zeros to 30 s | reflection].
        var x = [Float](repeating: 0, count: samples + 2 * pad)
        audio.withUnsafeBufferPointer { src in
            x.withUnsafeMutableBufferPointer { dst in
                if count > 0 { memcpy(dst.baseAddress! + pad, src.baseAddress!, count * 4) }
            }
        }
        for k in 0 ..< pad {
            let i = pad - k
            x[k] = i < count ? audio[i] : 0
        }
        if count > samples - pad - 2 {
            for j in 0 ..< pad {
                let i = samples - 2 - j
                x[pad + samples + j] = i < count ? audio[i] : 0
            }
        }

        // Only frames that can touch audio need the DFT; the rest are silence (power 0).
        let active = min(Self.frames, (count + pad) / Self.hop + 3)
        let bins = Self.bins
        var frames = [Float](repeating: 0, count: active * Self.nFFT)
        x.withUnsafeBufferPointer { xs in
            frames.withUnsafeMutableBufferPointer { fs in
                for t in 0 ..< active {
                    vDSP_vmul(xs.baseAddress! + t * Self.hop, 1, window, 1, fs.baseAddress! + t * Self.nFFT, 1, vDSP_Length(Self.nFFT))
                }
            }
        }
        var re = [Float](repeating: 0, count: active * bins)
        var im = [Float](repeating: 0, count: active * bins)
        vDSP_mmul(frames, 1, cosBasis, 1, &re, 1, vDSP_Length(active), vDSP_Length(bins), vDSP_Length(Self.nFFT))
        vDSP_mmul(frames, 1, sinBasis, 1, &im, 1, vDSP_Length(active), vDSP_Length(bins), vDSP_Length(Self.nFFT))
        var power = [Float](repeating: 0, count: active * bins)
        vDSP_vmma(re, 1, re, 1, im, 1, im, 1, &power, 1, vDSP_Length(power.count)) // re² + im²

        var melRows = [Float](repeating: 0, count: Self.frames * nMels) // [frames × nMels]
        vDSP_mmul(power, 1, filtersT, 1, &melRows, 1, vDSP_Length(active), vDSP_Length(nMels), vDSP_Length(bins))
        melRows.withUnsafeMutableBufferPointer { buf in
            let p = buf.baseAddress!
            let length = vDSP_Length(buf.count)
            var n = Int32(buf.count)
            var floor: Float = 1e-10
            vDSP_vthr(p, 1, &floor, p, 1, length)
            vvlog10f(p, p, &n)
            var top: Float = 0
            vDSP_maxv(p, 1, &top, length)
            var lowest = top - 8
            vDSP_vthr(p, 1, &lowest, p, 1, length)
            var scale: Float = 0.25, offset: Float = 1
            vDSP_vsmsa(p, 1, &scale, &offset, p, 1, length)
        }

        out.withUnsafeMutableBufferPointer(ofType: Float.self) { dst, _ in
            vDSP_mtrans(melRows, 1, dst.baseAddress!, 1, vDSP_Length(nMels), vDSP_Length(Self.frames))
        }
    }
}

enum OnDeviceWhisperError: Error {
    case badFilters
}

// MARK: - Detokenizer

/// Byte-level BPE ids back to text (GPT-2's byte↔unicode table). Control tokens and
/// The model's prompt tags are dropped; in verbatim mode [UM]/[UH] become "um"/"uh".
struct Detokenizer {
    let tokens: [String]
    let skip: Set<Int>
    let sounds: [Int: String]

    init(tokens: [String], skip: Set<Int>, sounds: [String: String]) {
        self.tokens = tokens
        self.skip = skip
        self.sounds = Dictionary(uniqueKeysWithValues: sounds.compactMap { key, value in Int(key).map { ($0, value) } })
    }

    private static let byteFor: [Character: UInt8] = {
        var bytes = Array(33 ... 126) + Array(161 ... 172) + Array(174 ... 255)
        var chars = bytes
        var extra = 0
        for b in 0 ..< 256 where !bytes.contains(b) {
            bytes.append(b)
            chars.append(256 + extra)
            extra += 1
        }
        var map: [Character: UInt8] = [:]
        for (b, c) in zip(bytes, chars) {
            map[Character(UnicodeScalar(UInt32(c))!)] = UInt8(b)
        }
        return map
    }()

    func text(_ ids: [Int], verbatim: Bool) -> String {
        var bytes: [UInt8] = []
        for id in ids where id >= 0 && id < tokens.count {
            if let sound = sounds[id] {
                if verbatim, sound == "UM" || sound == "UH" { bytes.append(contentsOf: Array(" \(sound.lowercased())".utf8)) }
                continue
            }
            if skip.contains(id) { continue }
            for ch in tokens[id] {
                bytes.append(Self.byteFor[ch] ?? 32)
            }
        }
        let text = String(decoding: bytes, as: UTF8.self)
        return text.split(whereSeparator: \.isWhitespace).joined(separator: " ")
    }
}
