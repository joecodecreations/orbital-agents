import Foundation

/// Where a secret goes when the keychain refuses it. Only the Mac build can be refused: built without a
/// provisioning profile (no Apple account in Xcode, or CODE_SIGNING_ALLOWED=NO), a Mac Catalyst app gets
/// errSecMissingEntitlement. There the value is a 0600 file under
/// ~/Library/Application Support/<app name>/secrets, readable by your user only. On iPhone every call is a no-op.
enum MacSecretFile {
    static func set(_ value: String, service: String, account: String) {
        #if targetEnvironment(macCatalyst)
        let fm = FileManager.default
        try? fm.createDirectory(at: folder, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let url = file(service: service, account: account)
        try? fm.removeItem(at: url)
        fm.createFile(atPath: url.path, contents: Data(value.utf8), attributes: [.posixPermissions: 0o600])
        #endif
    }

    static func get(service: String, account: String) -> String? {
        #if targetEnvironment(macCatalyst)
        guard let data = try? Data(contentsOf: file(service: service, account: account)) else { return nil }
        return String(data: data, encoding: .utf8)
        #else
        return nil
        #endif
    }

    static func delete(service: String, account: String) {
        #if targetEnvironment(macCatalyst)
        try? FileManager.default.removeItem(at: file(service: service, account: account))
        #endif
    }

    #if targetEnvironment(macCatalyst)
    private static var folder: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent(OrbitalBrand.appName, isDirectory: true)
            .appendingPathComponent("secrets", isDirectory: true)
    }

    private static func file(service: String, account: String) -> URL {
        folder.appendingPathComponent("\(service).\(account)")
    }
    #endif
}
