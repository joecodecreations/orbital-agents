import Foundation

/// Names and defaults that come from the build, not the code. The project generator writes the display
/// name from `apple.app_name` and the bundle id from `apple.bundle_prefix` in your config.
enum OrbitalBrand {
    /// The home-screen name this build was made with.
    static var appName: String {
        (Bundle.main.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String).flatMap { $0.isEmpty ? nil : $0 }
            ?? "Orbital Agents"
    }

    static let urlScheme = "orbital"

    /// `<bundle_prefix>.orbitalagents`. Also the keychain service.
    static var bundleID: String { Bundle.main.bundleIdentifier ?? "com.example.orbitalagents" }

    /// The hub's port when an address has none (`hub.port` in the config).
    static let defaultHubPort = 7700

    /// What the sign-in field shows before anything is typed.
    static let hubPlaceholder = "http://your-hub.your-tailnet.ts.net:7700"

    static var version: String {
        let info = Bundle.main.infoDictionary
        let version = info?["CFBundleShortVersionString"] as? String ?? "?"
        let build = info?["CFBundleVersion"] as? String ?? "?"
        return "\(version) (\(build))"
    }
}
