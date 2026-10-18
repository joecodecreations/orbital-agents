import UIKit

/// The on-demand alert behind the Alerts screen's test card.
///
/// It is a normal `orbital.alert.v1` object that only lives on this device. It runs through the same
/// `OrbitalNotifications.deliver` path a pushed alert uses, with every field the phone maps turned on:
/// subtitle, sound, badge, thread, Time Sensitive level, relevance, summary argument, an image attachment,
/// four actions (Open, Reply with a text field, Snooze, Dismiss), and a deep link back to Alerts.
/// It is never posted to the server, and acking it does not call the server.
enum OrbitalTestAlert {
    static let idPrefix = "orbital-test-"

    static func isTest(_ id: String) -> Bool {
        id.hasPrefix(idPrefix)
    }

    static func make(delay: TimeInterval, badge: Int) -> OrbitalAlert {
        let now = Date()
        let fireDate = now.addingTimeInterval(delay)
        let id = "\(idPrefix)\(Int(now.timeIntervalSince1970 * 1000))"
        let preview = writePreview(id: id, fireDate: fireDate)
        let timeText = fireDate.formatted(date: .omitted, time: .shortened)
        return OrbitalAlert(
            schema: OrbitalSchema.alert,
            id: id,
            kind: delay > 0 ? "reminder" : "event",
            sectionId: nil,
            nodeId: nil,
            tabId: nil,
            title: "\(OrbitalBrand.appName) test alert",
            subtitle: delay > 0 ? "Scheduled reminder · \(timeText)" : "Every notification feature at once",
            body: "Long-press this notification to see the image and the Open, Reply, Snooze, and Dismiss buttons. Reply text goes to the Agent composer. Snooze brings it back in an hour.",
            badge: badge,
            sound: "default",
            threadId: "orbital-test",
            categoryId: "orbital-test",
            interruptionLevel: "timeSensitive",
            relevanceScore: 1.0,
            targetContentId: "orbital/alerts",
            summaryArgument: "\(OrbitalBrand.appName) test",
            summaryArgumentCount: 1,
            userInfo: ["source": .string("orbital-test"), "test": .bool(true)],
            attachments: preview.map { [AlertAttachment(id: "preview", url: $0.absoluteString, typeHint: "image")] },
            actions: [
                AlertAction(id: "open", title: "Open", style: "default", foreground: true, textInput: nil),
                AlertAction(
                    id: "reply",
                    title: "Reply",
                    style: "default",
                    foreground: true,
                    textInput: AlertTextInput(buttonTitle: "Send", placeholder: "Note for the agent")
                ),
                AlertAction(id: "snooze", title: "Snooze 1 hour", style: "default", foreground: false, textInput: nil),
                AlertAction(id: "dismiss", title: "Dismiss", style: "destructive", foreground: false, textInput: nil),
            ],
            launchURL: "\(OrbitalBrand.urlScheme)://alerts",
            deliverAt: delay > 0 ? OrbitalDates.iso(fireDate) : nil,
            repeats: nil,
            createdAt: OrbitalDates.iso(now),
            acked: false,
            responseText: nil
        )
    }

    /// Where the inbox copy of each preview image lives. iOS moves the file it attaches, so delivery attaches a copy.
    static var directory: URL? {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first?
            .appendingPathComponent("orbital-test", isDirectory: true)
    }

    /// A stored file URL can go stale when an update moves the app container. Look the file up by name.
    static func localFile(for url: URL) -> URL? {
        if FileManager.default.fileExists(atPath: url.path) { return url }
        guard let candidate = directory?.appendingPathComponent(url.lastPathComponent),
              FileManager.default.fileExists(atPath: candidate.path) else { return nil }
        return candidate
    }

    static func isOwnedFile(_ url: URL) -> Bool {
        guard url.isFileURL, let directory else { return false }
        let root = directory.resolvingSymlinksInPath().path
        let path = (localFile(for: url) ?? url).resolvingSymlinksInPath().path
        return path.hasPrefix(root + "/")
    }

    static func removeFiles() {
        guard let directory else { return }
        try? FileManager.default.removeItem(at: directory)
    }

    // MARK: - Preview image

    private static func writePreview(id: String, fireDate: Date) -> URL? {
        guard let directory else { return nil }
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let file = directory.appendingPathComponent("\(id).png")
        guard let data = renderPreview(fireDate: fireDate).pngData() else { return nil }
        do {
            try data.write(to: file, options: .atomic)
            return file
        } catch {
            return nil
        }
    }

    private static func renderPreview(fireDate: Date) -> UIImage {
        let size = CGSize(width: 1200, height: 675)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        return UIGraphicsImageRenderer(size: size, format: format).image { context in
            let cg = context.cgContext
            let space = CGColorSpaceCreateDeviceRGB()
            let base = UIColor(red: 0x0E / 255, green: 0x10 / 255, blue: 0x14 / 255, alpha: 1)
            base.setFill()
            cg.fill(CGRect(origin: .zero, size: size))

            let accent = UIColor(red: 0x8B / 255, green: 0xA4 / 255, blue: 1, alpha: 1)
            let violet = UIColor(red: 0xC0 / 255, green: 0x84 / 255, blue: 0xFC / 255, alpha: 1)
            let mint = UIColor(red: 0x3D / 255, green: 0xDC / 255, blue: 0x97 / 255, alpha: 1)

            func glow(_ color: UIColor, center: CGPoint, radius: CGFloat, alpha: CGFloat) {
                let colors = [color.withAlphaComponent(alpha).cgColor, color.withAlphaComponent(0).cgColor] as CFArray
                if let gradient = CGGradient(colorsSpace: space, colors: colors, locations: [0, 1]) {
                    cg.drawRadialGradient(gradient, startCenter: center, startRadius: 0, endCenter: center, endRadius: radius, options: [])
                }
            }
            glow(accent, center: CGPoint(x: 1050, y: 80), radius: 620, alpha: 0.55)
            glow(violet, center: CGPoint(x: 180, y: 640), radius: 520, alpha: 0.40)
            glow(mint, center: CGPoint(x: 900, y: 600), radius: 300, alpha: 0.18)

            // Icon tile
            let tile = CGRect(x: 90, y: 80, width: 170, height: 170)
            let tilePath = UIBezierPath(roundedRect: tile, cornerRadius: 48)
            cg.saveGState()
            tilePath.addClip()
            let tileColors = [accent.cgColor, violet.cgColor] as CFArray
            if let gradient = CGGradient(colorsSpace: space, colors: tileColors, locations: [0, 1]) {
                cg.drawLinearGradient(gradient, start: tile.origin, end: CGPoint(x: tile.maxX, y: tile.maxY), options: [])
            }
            cg.restoreGState()
            let symbolConfig = UIImage.SymbolConfiguration(pointSize: 84, weight: .bold)
            if let bell = UIImage(systemName: "bell.badge.fill", withConfiguration: symbolConfig)?
                .withTintColor(base, renderingMode: .alwaysOriginal) {
                let origin = CGPoint(x: tile.midX - bell.size.width / 2, y: tile.midY - bell.size.height / 2)
                bell.draw(at: origin)
            }

            let eyebrow = NSAttributedString(
                string: "\(OrbitalBrand.appName.uppercased()) · TEST",
                attributes: [
                    .font: UIFont.systemFont(ofSize: 38, weight: .heavy),
                    .foregroundColor: accent,
                    .kern: 5,
                ]
            )
            eyebrow.draw(at: CGPoint(x: 90, y: 292))

            let title = NSAttributedString(
                string: "Every feature, on demand",
                attributes: [
                    .font: UIFont.systemFont(ofSize: 92, weight: .bold),
                    .foregroundColor: UIColor(white: 0.97, alpha: 1),
                ]
            )
            title.draw(at: CGPoint(x: 86, y: 344))

            let stamp = fireDate.formatted(date: .abbreviated, time: .standard)
            let detail = NSAttributedString(
                string: "Image · Actions · Reply · Snooze · Time Sensitive · \(stamp)",
                attributes: [
                    .font: UIFont.systemFont(ofSize: 36, weight: .semibold),
                    .foregroundColor: UIColor(red: 0xA8 / 255, green: 0xB0 / 255, blue: 0xC2 / 255, alpha: 1),
                ]
            )
            detail.draw(in: CGRect(x: 90, y: 476, width: 1040, height: 150))
        }
    }
}
