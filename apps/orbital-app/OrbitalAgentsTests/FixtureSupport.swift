import Foundation
import XCTest
@testable import OrbitalAgents

/// Reads the sample payloads in apps/orbital-app/Fixtures/preview (copied into the test bundle),
/// with the preview loader's `@now` / `@day` date tokens swapped for real dates.
enum Fixtures {
    static var root: URL {
        let bundle = Bundle(for: BundleMarker.self)
        guard let url = bundle.url(forResource: "Fixtures", withExtension: nil)?.appendingPathComponent("preview", isDirectory: true) else {
            fatalError("Fixtures folder missing from the test bundle")
        }
        return url
    }

    static func data(_ path: String, file: StaticString = #filePath, line: UInt = #line) throws -> Data {
        let url = root.appendingPathComponent(path)
        let raw = try Data(contentsOf: url)
        return OrbitalPreview.expand(raw)
    }

    static func decode<T: Decodable>(_ type: T.Type, _ path: String) throws -> T {
        try JSONDecoder().decode(T.self, from: data(path))
    }

    static func sectionFiles() throws -> [URL] {
        let folder = root.appendingPathComponent("sections", isDirectory: true)
        return try FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)
            .filter { $0.pathExtension == "json" }
            .sorted { $0.lastPathComponent < $1.lastPathComponent }
    }

    static func sections() throws -> [SectionDocument] {
        try sectionFiles().map { url in
            try JSONDecoder().decode(SectionDocument.self, from: OrbitalPreview.expand(Data(contentsOf: url)))
        }
    }

    static func section(_ id: String) throws -> SectionDocument {
        guard let found = try sections().first(where: { $0.id == id }) else {
            throw NSError(domain: "Fixtures", code: 1, userInfo: [NSLocalizedDescriptionKey: "no fixture section \(id)"])
        }
        return found
    }

    /// Decodes a JSON literal written in a test.
    static func json<T: Decodable>(_ type: T.Type, _ text: String) throws -> T {
        try JSONDecoder().decode(T.self, from: Data(text.utf8))
    }
}

private final class BundleMarker {}

extension SectionDocument {
    /// Every block on the page, its tabs, and its nodes (and their tabs).
    var everyBlock: [ContentBlock] {
        var out = blocks + tabs.flatMap(\.blocks)
        for node in nodes {
            out += node.blocks + node.tabs.flatMap(\.blocks)
        }
        return out
    }

    func block(_ id: String) -> ContentBlock? {
        everyBlock.first { $0.id == id }
    }
}
