import AppKit
import SwiftUI
import OrbitalTrackerCore

/// Where to show a piece of media from: the original while it exists on this machine, else
/// the compressed archive copy. Media from another machine (relayed through the hub) only
/// ever shows its archive, since the original path is on that machine's disk.
struct ResolvedMedia {
    let url: URL
    let isArchive: Bool
    let kind: MediaKind

    static func resolve(_ ref: MediaRef, paths: TrackerPaths, machine: String) -> ResolvedMedia? {
        let local = ref.machine == nil || ref.machine == machine
        if local, FileManager.default.fileExists(atPath: ref.originalPath) {
            return ResolvedMedia(url: URL(fileURLWithPath: ref.originalPath), isArchive: false, kind: ref.kind)
        }
        if let archive = ref.archivePath {
            let url = paths.archiveURL(archive)
            if FileManager.default.fileExists(atPath: url.path) {
                return ResolvedMedia(url: url, isArchive: true, kind: .image)  // archives are always JPEGs
            }
        }
        return nil
    }

    static func contentType(_ url: URL) -> String {
        switch url.pathExtension.lowercased() {
        case "jpg", "jpeg": return "image/jpeg"
        case "png": return "image/png"
        case "gif": return "image/gif"
        case "heic": return "image/heic"
        case "webp": return "image/webp"
        case "mp4", "m4v": return "video/mp4"
        case "mov": return "video/quicktime"
        case "pdf": return "application/pdf"
        case "txt", "md", "log": return "text/plain; charset=utf-8"
        default: return "application/octet-stream"
        }
    }
}

@MainActor
final class ThumbnailCache {
    static let shared = ThumbnailCache()
    private let cache: NSCache<NSString, NSImage> = {
        let c = NSCache<NSString, NSImage>()
        c.countLimit = 300
        return c
    }()

    func image(for media: ResolvedMedia, maxPixel: Int) async -> NSImage? {
        let key = "\(media.url.path)#\(maxPixel)" as NSString
        if let hit = cache.object(forKey: key) { return hit }
        let url = media.url, kind = media.kind
        let cg: CGImage? = await Task.detached(priority: .utility) {
            kind == .video ? MediaArchiver.posterFrame(url, maxPixel: maxPixel) : MediaArchiver.thumbnail(url, maxPixel: maxPixel)
        }.value
        guard let cg else { return nil }
        let image = NSImage(cgImage: cg, size: NSSize(width: cg.width, height: cg.height))
        cache.setObject(image, forKey: key)
        return image
    }
}

struct MediaThumb: View {
    let ref: MediaRef
    let paths: TrackerPaths
    let machine: String
    var height: CGFloat = 74
    var maxWidth: CGFloat = 132
    @State private var image: NSImage?
    @State private var hover = false

    private var resolved: ResolvedMedia? { ResolvedMedia.resolve(ref, paths: paths, machine: machine) }

    var body: some View {
        let media = resolved
        ZStack {
            RoundedRectangle(cornerRadius: 8, style: .continuous).fill(Color.white.opacity(0.07))
            if let image {
                Image(nsImage: image).resizable().aspectRatio(contentMode: .fill)
            } else {
                Image(systemName: icon).font(.system(size: 18)).foregroundStyle(.white.opacity(0.5))
            }
            if ref.kind == .video, media?.isArchive == false {
                Image(systemName: "play.fill")
                    .font(.system(size: 12, weight: .bold)).foregroundStyle(.white)
                    .frame(width: 26, height: 26).background(Circle().fill(Color.black.opacity(0.55)))
            }
            VStack {
                Spacer()
                HStack(spacing: 4) {
                    if media?.isArchive == true {
                        badge("archived", color: Palette.warn)
                    } else if media == nil {
                        badge("missing", color: .gray)
                    }
                    if let d = ref.durationSeconds { badge(Fmt.duration(d), color: .black) }
                    Spacer()
                }
                .padding(4)
            }
        }
        .frame(width: thumbWidth, height: height)
        .clipShape(RoundedRectangle(cornerRadius: 8, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 8, style: .continuous).strokeBorder(Color.white.opacity(hover ? 0.5 : 0.15)))
        .onHover { hover = $0 }
        .onTapGesture { if let media { NSWorkspace.shared.open(media.url) } }
        .help(helpText(media))
        .task(id: ref.id + (media?.url.path ?? "")) {
            if let media { image = await ThumbnailCache.shared.image(for: media, maxPixel: 360) }
        }
    }

    private var thumbWidth: CGFloat {
        guard let image, image.size.height > 0 else { return height * 1.5 }
        return min(max(height * image.size.width / image.size.height, height * 0.75), maxWidth)
    }

    private var icon: String {
        switch ref.kind {
        case .image: return "photo"
        case .video: return "film"
        case .file: return "doc"
        }
    }

    private func badge(_ text: String, color: Color) -> some View {
        Text(text)
            .font(.system(size: 8.5, weight: .bold, design: .rounded))
            .foregroundStyle(.white)
            .padding(.horizontal, 4).padding(.vertical, 1.5)
            .background(Capsule().fill(color.opacity(0.75)))
    }

    private func helpText(_ media: ResolvedMedia?) -> String {
        var parts = [ref.caption ?? ref.fileName]
        if let m = ref.machine, m != machine { parts.append("From \(m) — showing the archived copy") }
        else if media?.isArchive == true { parts.append("Original is gone — showing the archived copy") }
        else if media == nil { parts.append("Original is gone and no archive was made") }
        else { parts.append("Click to open the full file") }
        if let b = ref.originalBytes { parts.append(Fmt.bytes(b)) }
        return parts.joined(separator: "\n")
    }
}

struct MediaGallery: View {
    let media: [MediaRef]
    let store: TrackerStore
    var height: CGFloat = 74

    var body: some View {
        FlowLayout(spacing: 6, lineSpacing: 6) {
            ForEach(media) { ref in MediaThumb(ref: ref, paths: store.paths, machine: store.machineName, height: height) }
        }
    }
}

/// Inline image from markdown (![alt](path)), falling back to the archive.
struct MarkdownImage: View {
    let source: String
    let alt: String
    let store: TrackerStore

    var body: some View {
        let path = MarkdownMedia.localPath(source) ?? (source as NSString).expandingTildeInPath
        if source.hasPrefix("http://") || source.hasPrefix("https://"), let url = URL(string: source) {
            AsyncImage(url: url) { image in
                image.resizable().aspectRatio(contentMode: .fit)
            } placeholder: {
                ProgressView().controlSize(.small)
            }
            .frame(maxHeight: 220)
            .clipShape(RoundedRectangle(cornerRadius: 8))
            .onTapGesture { NSWorkspace.shared.open(url) }
        } else {
            let ref = store.mediaRef(forPath: path)
                ?? MediaRef(kind: MediaArchiver.kind(for: URL(fileURLWithPath: path)), originalPath: path, caption: alt)
            MediaThumb(ref: ref, paths: store.paths, machine: store.machineName, height: 150, maxWidth: 400)
        }
    }
}
