import AppKit
import SwiftUI
import OrbitalTrackerCore

/// A floating, resizable glass window holding one item's details. Like the tracker panel it
/// doesn't take focus from the app you're in; Esc or the x shuts it.
final class DetailPanel: NSPanel {
    override var canBecomeKey: Bool { true }
    override func cancelOperation(_ sender: Any?) { close() }
}

/// One window per item (… › Item details › In a floating window). Opening an item that
/// already has a window brings that window forward instead of making another.
@MainActor
final class DetailWindows: NSObject, NSWindowDelegate {
    private let model: AppModel
    /// The tracker panel's frame; new windows open beside it.
    private let anchor: () -> NSRect
    private var windows: [String: DetailPanel] = [:]
    static let defaultSize = NSSize(width: 460, height: 540)

    init(model: AppModel, anchor: @escaping () -> NSRect) {
        self.model = model
        self.anchor = anchor
    }

    var openIDs: [String] { Array(windows.keys) }
    /// For `screencapture -l` in tests.
    var windowNumbers: [String: Int] { windows.mapValues(\.windowNumber) }

    func open(_ id: String) {
        if let panel = windows[id] {
            panel.orderFrontRegardless()
            return
        }
        guard let item = model.store.item(id) else { return }
        let panel = DetailPanel(contentRect: NSRect(origin: .zero, size: Self.savedSize()),
                                styleMask: [.borderless, .resizable, .nonactivatingPanel],
                                backing: .buffered, defer: false)
        panel.title = item.title
        // Clear window; the glass is drawn by DetailWindowView.
        panel.backgroundColor = .clear
        panel.isOpaque = false
        panel.hasShadow = false
        panel.ignoresMouseEvents = false
        panel.isFloatingPanel = true
        panel.level = .floating
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.isReleasedWhenClosed = false
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.appearance = NSAppearance(named: .darkAqua)
        panel.minSize = NSSize(width: 320, height: 220)
        panel.identifier = NSUserInterfaceItemIdentifier(id)
        panel.delegate = self
        let host = NSHostingView(rootView: DetailWindowView(id: id, model: model) { [weak panel] in panel?.close() })
        host.sizingOptions = []
        panel.contentView = host
        panel.setFrameOrigin(origin(for: panel.frame.size))
        windows[id] = panel
        panel.orderFrontRegardless()
    }

    func closeAll() {
        for panel in windows.values { panel.close() }
    }

    func windowWillClose(_ notification: Notification) {
        guard let panel = notification.object as? DetailPanel, let id = panel.identifier?.rawValue else { return }
        // The next window opens at the size you last left one at.
        let size = panel.contentRect(forFrameRect: panel.frame).size
        UserDefaults.standard.set(Double(size.width), forKey: "detailWindowW")
        UserDefaults.standard.set(Double(size.height), forKey: "detailWindowH")
        windows[id] = nil
    }

    private static func savedSize() -> NSSize {
        let d = UserDefaults.standard
        let w = d.double(forKey: "detailWindowW"), h = d.double(forKey: "detailWindowH")
        return w >= 320 && h >= 220 ? NSSize(width: w, height: h) : defaultSize
    }

    /// Left of the tracker panel if it fits, otherwise right; each extra window steps down and out.
    private func origin(for size: NSSize) -> NSPoint {
        let home = anchor()
        let screen = NSScreen.screens.first { $0.frame.intersects(home) } ?? NSScreen.main
        let visible = screen?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        // Both windows have clear margins (panel 14, this 10); line the glass up 10 pt apart.
        let gap = 10 - Metrics.margin - DetailWindowView.margin
        let step = CGFloat(windows.count % 8) * 26
        let onLeft = home.minX - gap - size.width >= visible.minX
        var x = onLeft ? home.minX - gap - size.width - step : home.maxX + gap + step
        x = min(max(x, visible.minX), visible.maxX - size.width)
        let top = min(home.maxY - (Metrics.margin - DetailWindowView.margin) - step, visible.maxY)
        let y = max(top - size.height, visible.minY)
        return NSPoint(x: x, y: y)
    }
}
