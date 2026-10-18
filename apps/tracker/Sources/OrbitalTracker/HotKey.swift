import Carbon
import Foundation

/// System-wide shortcut via Carbon (no Accessibility permission needed).
final class HotKey {
    private var ref: EventHotKeyRef?
    private let id: UInt32
    /// False when another app already owns the same shortcut.
    private(set) var registered = false
    private nonisolated(unsafe) static var actions: [UInt32: () -> Void] = [:]
    private nonisolated(unsafe) static var handlerInstalled = false

    init(keyCode: Int, modifiers: Int, id: UInt32, action: @escaping () -> Void) {
        self.id = id
        Self.installHandler()
        Self.actions[id] = action
        let hotKeyID = EventHotKeyID(signature: OSType(0x4F52_4254), id: id)  // 'ORBT'
        let status = RegisterEventHotKey(UInt32(keyCode), UInt32(modifiers), hotKeyID, GetApplicationEventTarget(), 0, &ref)
        registered = status == noErr
    }

    deinit {
        if let ref { UnregisterEventHotKey(ref) }
        Self.actions[id] = nil
    }

    private static func installHandler() {
        guard !handlerInstalled else { return }
        handlerInstalled = true
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, event, _ in
            var hotKeyID = EventHotKeyID()
            GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID),
                              nil, MemoryLayout<EventHotKeyID>.size, nil, &hotKeyID)
            let id = hotKeyID.id
            DispatchQueue.main.async { HotKey.actions[id]?() }
            return noErr
        }, 1, &spec, nil, nil)
    }
}
