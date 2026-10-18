import SwiftUI

/// The Mac app is this target built for Mac Catalyst. Its window opens large and never gets narrow
/// enough for the iPhone tab bar, so the Mac always shows the sidebar layout. No-ops on iPhone.
extension Scene {
    func macWindowSize() -> some Scene {
        #if targetEnvironment(macCatalyst)
        return defaultSize(width: 1280, height: 860)
        #else
        return self
        #endif
    }
}

extension View {
    func macWindowLimits() -> some View {
        #if targetEnvironment(macCatalyst)
        return onAppear {
            for case let scene as UIWindowScene in UIApplication.shared.connectedScenes {
                scene.sizeRestrictions?.minimumSize = CGSize(width: 960, height: 620)
            }
        }
        #else
        return self
        #endif
    }
}
