import Carbon
import Foundation
import XCTest
@testable import OrbitalTrackerCore

final class HotkeyTests: XCTestCase {
    func testParsingCombinations() {
        XCTAssertEqual(HotkeyModifiers(parsing: "ctrl+option"), [.control, .option])
        XCTAssertEqual(HotkeyModifiers(parsing: " Control + Alt + Shift "), [.control, .option, .shift])
        XCTAssertEqual(HotkeyModifiers(parsing: "cmd+option"), [.command, .option])
        XCTAssertEqual(HotkeyModifiers(parsing: "command"), [.command])
        XCTAssertEqual(HotkeyModifiers(parsing: "⌃⌥"), nil, "symbols must be '+'-joined")
        XCTAssertEqual(HotkeyModifiers(parsing: "⌃+⌥"), [.control, .option])
        XCTAssertEqual(HotkeyModifiers(parsing: "ctrl+ctrl+opt"), [.control, .option], "repeats are harmless")
    }

    func testBadValuesAreRejected() {
        XCTAssertNil(HotkeyModifiers(parsing: ""))
        XCTAssertNil(HotkeyModifiers(parsing: "   "))
        XCTAssertNil(HotkeyModifiers(parsing: "ctrl+hyper"))
        XCTAssertNil(HotkeyModifiers(parsing: "shift"), "shift alone would swallow typing")
        XCTAssertNil(HotkeyModifiers(parsing: "fn+option"))
    }

    func testResolveFallsBackToCtrlOption() {
        let ok = HotkeyModifiers.resolve("ctrl+option+shift")
        XCTAssertTrue(ok.valid)
        XCTAssertEqual(ok.modifiers, [.control, .option, .shift])
        let bad = HotkeyModifiers.resolve("banana")
        XCTAssertFalse(bad.valid, "the app logs a warning for this")
        XCTAssertEqual(bad.modifiers, .default)
        XCTAssertEqual(HotkeyModifiers.resolve(nil).modifiers, [.control, .option])
        XCTAssertTrue(HotkeyModifiers.resolve(nil).valid)
    }

    func testDisplayAndCarbonFlags() {
        let all: HotkeyModifiers = [.command, .shift, .option, .control]
        XCTAssertEqual(all.symbols, "⌃⌥⇧⌘")
        XCTAssertEqual(all.configString, "ctrl+option+shift+cmd")
        XCTAssertEqual(HotkeyModifiers.default.shortcut("n"), "⌃⌥N")
        XCTAssertEqual(HotkeyModifiers.default.carbonFlags, UInt32(controlKey | optionKey))
        XCTAssertEqual(all.carbonFlags, UInt32(controlKey | optionKey | shiftKey | cmdKey))
    }

    func testConfigReadsHotkeySettings() {
        let home = FileManager.default.temporaryDirectory
        let defaults = OrbitalConfig.parse(data: nil, home: home)
        XCTAssertTrue(defaults.hotkeys)
        XCTAssertEqual(defaults.hotkeyModifiers, [.control, .option])
        XCTAssertTrue(defaults.hotkeyModifiersValid)

        let custom = OrbitalConfig.parse(data: Data(#"{"tracker":{"hotkeys":false,"hotkey_modifiers":"cmd+shift"}}"#.utf8), home: home)
        XCTAssertFalse(custom.hotkeys)
        XCTAssertEqual(custom.hotkeyModifiers, [.command, .shift])

        let bad = OrbitalConfig.parse(data: Data(#"{"tracker":{"hotkey_modifiers":"ctrl+meta"}}"#.utf8), home: home)
        XCTAssertTrue(bad.hotkeys)
        XCTAssertFalse(bad.hotkeyModifiersValid)
        XCTAssertEqual(bad.hotkeyModifiersRaw, "ctrl+meta")
        XCTAssertEqual(bad.hotkeyModifiers, .default)
    }
}
