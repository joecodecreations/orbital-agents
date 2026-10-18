import CoreGraphics
import Foundation
import ImageIO
import XCTest
@testable import OrbitalTrackerCore

/// A temp folder per test, removed afterwards.
class TempDirTestCase: XCTestCase {
    var tmp: URL!

    override func setUpWithError() throws {
        tmp = FileManager.default.temporaryDirectory
            .appendingPathComponent("orbital-tracker-tests-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: tmp, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws {
        if let tmp { try? FileManager.default.removeItem(at: tmp) }
    }

    var paths: TrackerPaths {
        TrackerPaths(root: tmp.appendingPathComponent("data/tracker", isDirectory: true),
                     logsDir: tmp.appendingPathComponent("logs", isDirectory: true),
                     runDir: tmp.appendingPathComponent("run", isDirectory: true))
    }

    func write(_ text: String, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(text.utf8).write(to: url)
    }

    func setModified(_ url: URL, daysAgo: Double) throws {
        try FileManager.default.setAttributes([.modificationDate: Date().addingTimeInterval(-daysAgo * 86_400)],
                                              ofItemAtPath: url.path)
    }

    /// A small PNG made on the fly (no fixtures in the repo).
    func makePNG(at url: URL, size: Int = 64) throws {
        let ctx = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8, bytesPerRow: 0,
                            space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
        ctx.setFillColor(CGColor(red: 0.2, green: 0.5, blue: 0.9, alpha: 1))
        ctx.fill(CGRect(x: 0, y: 0, width: size, height: size))
        let image = ctx.makeImage()!
        let dest = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil)!
        CGImageDestinationAddImage(dest, image, nil)
        XCTAssertTrue(CGImageDestinationFinalize(dest))
    }
}

