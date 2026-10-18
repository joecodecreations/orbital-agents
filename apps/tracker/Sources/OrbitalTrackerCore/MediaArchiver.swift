import AVFoundation
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

/// Makes the small copy of an agent's screenshot or video that the tracker keeps.
/// Images: longest edge 480 px, JPEG q0.42 (typically 15–40 KB).
/// Videos: a 2×2 contact sheet of frames at 8/36/64/92 % — the video itself is never kept.
public enum MediaArchiver {
    public static let maxPixel = 480
    public static let jpegQuality = 0.42

    public static func normalizePath(_ raw: String, relativeTo cwd: String = FileManager.default.currentDirectoryPath) -> String {
        var s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.hasPrefix("file://"), let url = URL(string: s) { s = url.path }
        s = (s as NSString).expandingTildeInPath
        if !s.hasPrefix("/") { s = (cwd as NSString).appendingPathComponent(s) }
        return (s as NSString).standardizingPath
    }

    public static func kind(for url: URL) -> MediaKind {
        guard let type = UTType(filenameExtension: url.pathExtension.lowercased()) else { return .file }
        if type.conforms(to: .image) { return .image }
        if type.conforms(to: .movie) || type.conforms(to: .video) || type.conforms(to: .audiovisualContent) { return .video }
        return .file
    }

    /// Blocking; call off the main thread. `archivePath` stays nil when the file
    /// can't be read or isn't an image/video.
    public static func archive(path: String, caption: String?, itemID: String, paths: TrackerPaths) -> MediaRef {
        let url = URL(fileURLWithPath: path)
        var ref = MediaRef(kind: kind(for: url), originalPath: path, caption: caption)
        let attrs = try? FileManager.default.attributesOfItem(atPath: path)
        ref.originalBytes = (attrs?[.size] as? NSNumber)?.int64Value

        let relative = archiveRelativePath(itemID: itemID, mediaID: ref.id)
        let dest = paths.archiveURL(relative)
        guard (try? FileManager.default.createDirectory(at: dest.deletingLastPathComponent(), withIntermediateDirectories: true)) != nil else {
            return ref
        }

        switch ref.kind {
        case .image:
            if let image = thumbnail(url), writeJPEG(image, to: dest) { ref.archivePath = relative }
        case .video:
            let (sheet, duration) = contactSheet(url)
            ref.durationSeconds = duration
            if let sheet, writeJPEG(sheet, to: dest) { ref.archivePath = relative }
        case .file:
            break
        }
        return ref
    }

    /// `media/YYYY-MM/<item>-<media>.jpg`; anything but [A-Za-z0-9_-] in the ids becomes "_".
    public static func archiveRelativePath(itemID: String, mediaID: String, date: Date = Date()) -> String {
        "media/\(monthFormatter.string(from: date))/\(safeName(itemID))-\(safeName(mediaID)).jpg"
    }

    static func safeName(_ s: String) -> String {
        let cleaned = String(s.prefix(64).map { $0.isASCII && ($0.isLetter || $0.isNumber || $0 == "-" || $0 == "_") ? $0 : "_" })
        return cleaned.isEmpty ? "x" : cleaned
    }

    /// Base64 of the archived copy, for commands relayed to the hub (nil when there is none or it's too big).
    public static func inlineData(for ref: MediaRef, paths: TrackerPaths, maxBytes: Int = 256 * 1024) -> String? {
        guard let rel = ref.archivePath else { return nil }
        let url = paths.archiveURL(rel)
        guard let data = try? Data(contentsOf: url), !data.isEmpty, data.count <= maxBytes else { return nil }
        return data.base64EncodedString()
    }

    /// Stores an archived copy that arrived inline (hub relay) and returns the ref pointing at it.
    /// Only JPEG data up to `maxBytes` is accepted.
    public static func storeInline(_ base64: String, ref: MediaRef, itemID: String, paths: TrackerPaths,
                                   maxBytes: Int = 256 * 1024) -> MediaRef {
        var out = ref
        out.archivePath = nil
        guard base64.count <= maxBytes * 4 / 3 + 8, let data = Data(base64Encoded: base64), !data.isEmpty,
              data.count <= maxBytes, data.starts(with: [0xFF, 0xD8, 0xFF]) else { return out }
        let relative = archiveRelativePath(itemID: itemID, mediaID: ref.id)
        let dest = paths.archiveURL(relative)
        do {
            try FileManager.default.createDirectory(at: dest.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: dest, options: .atomic)
            out.archivePath = relative
        } catch {
            return out
        }
        return out
    }

    // MARK: - Images

    public static func thumbnail(_ url: URL, maxPixel: Int = maxPixel) -> CGImage? {
        guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceThumbnailMaxPixelSize: maxPixel,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceShouldCacheImmediately: true,
        ]
        return CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
    }

    static func writeJPEG(_ image: CGImage, to url: URL) -> Bool {
        let flat = flatten(image) ?? image
        guard let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.jpeg.identifier as CFString, 1, nil) else { return false }
        CGImageDestinationAddImage(dest, flat, [kCGImageDestinationLossyCompressionQuality: jpegQuality] as CFDictionary)
        return CGImageDestinationFinalize(dest)
    }

    /// JPEG has no alpha; put transparent screenshots on white instead of black.
    private static func flatten(_ image: CGImage) -> CGImage? {
        guard let ctx = CGContext(data: nil, width: image.width, height: image.height, bitsPerComponent: 8,
                                  bytesPerRow: 0, space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
        let rect = CGRect(x: 0, y: 0, width: image.width, height: image.height)
        ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 1))
        ctx.fill(rect)
        ctx.draw(image, in: rect)
        return ctx.makeImage()
    }

    // MARK: - Videos

    /// Grabs a single frame (used for posters in the UI).
    public static func posterFrame(_ url: URL, maxPixel: Int = 360) -> CGImage? {
        frames(url, fractions: [0.1], maxPixel: maxPixel).frames.first
    }

    static func contactSheet(_ url: URL) -> (CGImage?, Double?) {
        let result = frames(url, fractions: [0.08, 0.36, 0.64, 0.92], maxPixel: 360)
        return (tile(result.frames), result.duration)
    }

    private final class Box<T>: @unchecked Sendable { var value: T? }

    private static func frames(_ url: URL, fractions: [Double], maxPixel: Int) -> (frames: [CGImage], duration: Double?) {
        let box = Box<([CGImage], Double?)>()
        let done = DispatchSemaphore(value: 0)
        Task.detached {
            defer { done.signal() }
            let asset = AVURLAsset(url: url)
            guard let duration = try? await asset.load(.duration) else { return }
            let seconds = CMTimeGetSeconds(duration)
            let generator = AVAssetImageGenerator(asset: asset)
            generator.appliesPreferredTrackTransform = true
            generator.maximumSize = CGSize(width: maxPixel, height: maxPixel)
            let tolerance = CMTime(seconds: 0.5, preferredTimescale: 600)
            generator.requestedTimeToleranceBefore = tolerance
            generator.requestedTimeToleranceAfter = tolerance
            var images: [CGImage] = []
            let times = seconds.isFinite && seconds > 1 ? fractions.map { seconds * $0 } : [0]
            for t in times {
                if let frame = try? await generator.image(at: CMTime(seconds: t, preferredTimescale: 600)) {
                    images.append(frame.image)
                }
            }
            box.value = (images, seconds.isFinite ? seconds : nil)
        }
        _ = done.wait(timeout: .now() + 20)
        return (box.value?.0 ?? [], box.value?.1)
    }

    private static func tile(_ frames: [CGImage]) -> CGImage? {
        guard let first = frames.first else { return nil }
        let columns = frames.count > 1 ? 2 : 1
        let rows = Int(ceil(Double(frames.count) / Double(columns)))
        let gap = 3
        let tileW = (maxPixel - gap * (columns + 1)) / columns
        let tileH = max(1, Int(Double(tileW) * Double(first.height) / Double(max(first.width, 1))))
        let width = columns * tileW + (columns + 1) * gap
        let height = rows * tileH + (rows + 1) * gap
        guard let ctx = CGContext(data: nil, width: width, height: height, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
        ctx.setFillColor(CGColor(red: 0.07, green: 0.07, blue: 0.09, alpha: 1))
        ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
        ctx.interpolationQuality = .high
        for (i, frame) in frames.enumerated() {
            let col = i % columns, row = i / columns
            let x = gap + col * (tileW + gap)
            let y = height - (row + 1) * (tileH + gap)
            ctx.draw(frame, in: CGRect(x: x, y: y, width: tileW, height: tileH))
        }
        // Small play badge so the sheet reads as "this was a video".
        let r: CGFloat = 16
        let c = CGPoint(x: CGFloat(width) / 2, y: CGFloat(height) / 2)
        ctx.setFillColor(CGColor(red: 0, green: 0, blue: 0, alpha: 0.55))
        ctx.fillEllipse(in: CGRect(x: c.x - r, y: c.y - r, width: 2 * r, height: 2 * r))
        ctx.setFillColor(CGColor(red: 1, green: 1, blue: 1, alpha: 0.95))
        ctx.beginPath()
        ctx.move(to: CGPoint(x: c.x - 5, y: c.y - 8))
        ctx.addLine(to: CGPoint(x: c.x - 5, y: c.y + 8))
        ctx.addLine(to: CGPoint(x: c.x + 9, y: c.y))
        ctx.closePath()
        ctx.fillPath()
        return ctx.makeImage()
    }

    private static let monthFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = "yyyy-MM"
        return f
    }()
}
