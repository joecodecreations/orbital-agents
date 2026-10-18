import SwiftUI

/// Orbital Agents for iPhone, iPad, and Mac (the same target built for Mac Catalyst).
@main
struct OrbitalApp: App {
    @UIApplicationDelegateAdaptor(OrbitalAppDelegate.self) private var appDelegate
    @StateObject private var store = OrbitalStore()
    @StateObject private var desk = DeskState()
    @StateObject private var router = AppRouter()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(store)
                .environmentObject(desk)
                .environmentObject(router)
                .preferredColorScheme(.dark)
                .tint(OrbitalTheme.accent)
                .macWindowLimits()
        }
        .macWindowSize()
    }
}

final class OrbitalAppDelegate: NSObject, UIApplicationDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        OrbitalNotifications.configure()
        return true
    }

    // Optional Apple push: only asked for when the hub says it sends pushes. See OrbitalStore.registerDevice.

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        NotificationCenter.default.post(name: .orbitalPushToken, object: nil, userInfo: ["token": OrbitalNotifications.hex(deviceToken)])
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        // No push entitlement in this build, or no network: alerts keep coming by the live socket and sync.
        print("[orbital] push registration failed: \(error.localizedDescription)")
    }

    /// A push with content-available: sync now, so the inbox and badge match before the app is opened.
    func application(
        _ application: UIApplication,
        didReceiveRemoteNotification userInfo: [AnyHashable: Any]
    ) async -> UIBackgroundFetchResult {
        OrbitalNotifications.markPushed(userInfo)
        guard let sync = await OrbitalNotifications.backgroundSync else { return .noData }
        await sync()
        return .newData
    }
}
