import Foundation

/// The modifier keys of the tracker's global shortcuts (`tracker.hotkey_modifiers`): any
/// '+'-joined combination of ctrl, option/alt, shift and cmd/command, e.g. "ctrl+option" (the
/// default) or "ctrl+option+shift". At least one of ctrl, option or cmd is required, so a
/// shortcut never swallows plain typing. The keys themselves are M (mic), T (show/hide), N (type).
public struct HotkeyModifiers: OptionSet, Hashable, Sendable {
    public let rawValue: Int
    public init(rawValue: Int) { self.rawValue = rawValue }

    public static let control = HotkeyModifiers(rawValue: 1 << 0)
    public static let option = HotkeyModifiers(rawValue: 1 << 1)
    public static let shift = HotkeyModifiers(rawValue: 1 << 2)
    public static let command = HotkeyModifiers(rawValue: 1 << 3)

    public static let `default`: HotkeyModifiers = [.control, .option]

    /// Parses the config string. nil for an empty string, an unknown word, or shift alone.
    public init?(parsing raw: String) {
        let tokens = raw.lowercased()
            .split(whereSeparator: { $0 == "+" || $0 == "-" || $0 == " " || $0 == "," })
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        guard !tokens.isEmpty else { return nil }
        var mods: HotkeyModifiers = []
        for token in tokens {
            switch token {
            case "ctrl", "control", "ctl", "⌃": mods.insert(.control)
            case "option", "opt", "alt", "⌥": mods.insert(.option)
            case "shift", "⇧": mods.insert(.shift)
            case "cmd", "command", "⌘": mods.insert(.command)
            default: return nil
            }
        }
        guard !mods.isDisjoint(with: [.control, .option, .command]) else { return nil }
        self = mods
    }

    /// The configured value, or ctrl+option when it can't be used (`valid` false: log a warning).
    public static func resolve(_ raw: String?) -> (modifiers: HotkeyModifiers, valid: Bool) {
        guard let raw else { return (.default, true) }
        if let parsed = HotkeyModifiers(parsing: raw) { return (parsed, true) }
        return (.default, false)
    }

    /// macOS order, as menus show it: "⌃⌥", "⌃⌥⇧⌘".
    public var symbols: String {
        var s = ""
        if contains(.control) { s += "⌃" }
        if contains(.option) { s += "⌥" }
        if contains(.shift) { s += "⇧" }
        if contains(.command) { s += "⌘" }
        return s
    }

    /// The canonical config spelling: "ctrl+option+shift+cmd" order.
    public var configString: String {
        var parts: [String] = []
        if contains(.control) { parts.append("ctrl") }
        if contains(.option) { parts.append("option") }
        if contains(.shift) { parts.append("shift") }
        if contains(.command) { parts.append("cmd") }
        return parts.joined(separator: "+")
    }

    /// Carbon `RegisterEventHotKey` modifier mask (cmdKey 0x100, shiftKey 0x200, optionKey 0x800, controlKey 0x1000).
    public var carbonFlags: UInt32 {
        var flags: UInt32 = 0
        if contains(.command) { flags |= 0x0100 }
        if contains(.shift) { flags |= 0x0200 }
        if contains(.option) { flags |= 0x0800 }
        if contains(.control) { flags |= 0x1000 }
        return flags
    }

    /// "⌃⌥N"
    public func shortcut(_ key: String) -> String { symbols + key.uppercased() }
}
