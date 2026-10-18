import AppKit
import OrbitalTrackerCore

// Orbital Agents - Agent Satellite Tracker. Started at login by the LaunchAgent
// io.orbitalagents.tracker (written by `orbital services install`), or by opening the app.

let arguments = CommandLine.arguments.dropFirst()
if arguments.contains("--version") || arguments.contains("-v") {
    print("\(TrackerInfo.productName) \(TrackerInfo.version)")
    exit(0)
}
if arguments.contains("--help") || arguments.contains("-h") {
    print("""
    \(TrackerInfo.productName) \(TrackerInfo.version)
    The floating panel. Settings: $ORBITAL_HOME/config.resolved.json (default ~/.orbital-agents).
    Agents report to it with the orbital-track CLI; see `orbital-track help`.
    """)
    exit(0)
}

MainActor.assumeIsolated {
    let app = NSApplication.shared
    let delegate = AppDelegate()
    app.delegate = delegate
    app.setActivationPolicy(.accessory)
    app.run()
}
