import CryptoKit
import Foundation
import UIKit

/// Loads `/v1/media/...` and https images once, then serves them from memory and disk.
/// App icons repeat on every list row, so they must not refetch on each redraw.
actor OrbitalImageLoader {
    static let shared = OrbitalImageLoader()

    /// NSCache is thread-safe, so the synchronous peek below can read it from any thread.
    private nonisolated(unsafe) static let memory = NSCache<NSString, UIImage>()
    private var inflight: [String: Task<UIImage?, Never>] = [:]
    private let folder: URL = {
        let base = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
        let dir = base.appendingPathComponent("OrbitalImages", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        return dir
    }()

    /// Synchronous peek so a cached icon draws on the first frame instead of flashing the fallback.
    nonisolated static func cached(_ src: String) -> UIImage? {
        memory.object(forKey: src as NSString)
    }

    /// `key` names the cache entry when `src` changes for the same picture (signed tracker URLs).
    func image(src: String, baseURL: URL, token: String?, key: String? = nil) async -> UIImage? {
        let cacheKey = key ?? src
        if let hit = Self.memory.object(forKey: cacheKey as NSString) { return hit }
        if let running = inflight[cacheKey] { return await running.value }
        let task = Task<UIImage?, Never> { [folder] in
            let file = folder.appendingPathComponent(Self.key(cacheKey))
            if let data = try? Data(contentsOf: file), let image = UIImage(data: data) {
                return image
            }
            guard let data = await Self.fetch(src: src, baseURL: baseURL, token: token),
                  let image = UIImage(data: data) else { return nil }
            try? data.write(to: file, options: .atomic)
            return image
        }
        inflight[cacheKey] = task
        let image = await task.value
        inflight[cacheKey] = nil
        if let image { Self.memory.setObject(image, forKey: cacheKey as NSString) }
        return image
    }

    private static func key(_ src: String) -> String {
        SHA256.hash(data: Data(src.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    private static func fetch(src: String, baseURL: URL, token: String?) async -> Data? {
        #if DEBUG
        if let local = OrbitalPreview.mediaFile(for: src) {
            return try? Data(contentsOf: local)
        }
        #endif
        let url: URL?
        if src.hasPrefix("/") {
            url = URL(string: src, relativeTo: baseURL)?.absoluteURL
        } else {
            url = URL(string: src)
        }
        guard let url, url.scheme == "https" || url.host == baseURL.host else { return nil }
        var request = URLRequest(url: url)
        if url.host == baseURL.host, let token {
            request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        guard let (data, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse, (200 ... 299).contains(http.statusCode)
        else { return nil }
        return data
    }
}
