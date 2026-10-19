import SwiftUI
import UIKit
import UserNotifications

/// This device: who is signed in, which hub, notifications, voice, and sign out.
struct SettingsView: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var desk: DeskState
    @EnvironmentObject private var router: AppRouter
    @Environment(\.openURL) private var openURL
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.orbitalPushed) private var pushed
    @State private var hubURL = ""
    @State private var permission: UNAuthorizationStatus?
    @State private var confirmLogout = false
    @State private var showVoice = false

    private let accent = OrbitalTheme.accent

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                OrbitalScreenHeader(eyebrow: "System", title: "Settings", accent: accent)
                AgentSettingsCards()
                if let pending = store.pendingHubURL {
                    pendingHubCard(pending)
                }
                if let error = store.editorError, !error.isEmpty {
                    errorText(error)
                }
                group("Notifications", symbol: "bell.badge.fill") {
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 10) {
                            permissionLabel
                            Spacer(minLength: 8)
                            OrbitalChip(text: permissionText, symbol: permissionSymbol, tint: permissionTint)
                        }
                        VStack(alignment: .leading, spacing: 6) {
                            permissionLabel
                            OrbitalChip(text: permissionText, symbol: permissionSymbol, tint: permissionTint)
                        }
                    }
                    Text("Alerts arrive while \(OrbitalBrand.appName) can reach the hub, and reminders already scheduled on this device fire on time even when it can't. A banner inside the app shows too, because the system banner is easy to miss. Clearing an alert here clears it on your other devices.")
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    ViewThatFits(in: .horizontal) {
                        HStack(spacing: 10) {
                            testAlertsButton
                            systemSettingsButton
                        }
                        VStack(spacing: 10) {
                            testAlertsButton
                            systemSettingsButton
                        }
                    }
                }
                group("Voice", symbol: "waveform") {
                    row("Fish voices on the hub", desk.speechStatus.tts ? "Ready" : "Not set up")
                    OrbitalActionButton(title: "Voice settings", symbol: "waveform", kind: .secondary, accent: accent) {
                        showVoice = true
                    }
                }
                Text("The hub's address, connection and sign out are at the top of System.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
                Text("\(OrbitalBrand.appName) \(OrbitalBrand.version)")
                    .font(.caption2)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .frame(maxWidth: .infinity)
                    .padding(.top, 4)
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, 36)
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
        }
        .scrollIndicators(.hidden)
        .scrollDismissesKeyboard(.interactively)
        .orbitalScrollFade()
        .orbitalBackdrop(accent)
        .navigationTitle("Settings")
        .orbitalNavigationBar(pushed: pushed)
        .confirmationDialog("Sign out of \(store.hubInfo?.name ?? "this hub")?", isPresented: $confirmLogout, titleVisibility: .visible) {
            Button("Sign out", role: .destructive) {
                store.logout()
                desk.logout()
            }
            Button("Cancel", role: .cancel) {}
        }
        .sheet(isPresented: $showVoice) {
            DeskVoiceSettingsView()
                .environmentObject(desk)
        }
        .onAppear {
            hubURL = store.pendingHubURL ?? store.hubURLString
        }
        .onChange(of: store.pendingHubURL) { _, pending in
            if let pending { hubURL = pending }
        }
        .task {
            permission = await OrbitalNotifications.authorizationStatus()
            await desk.refreshSpeechStatus()
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                Task { permission = await OrbitalNotifications.authorizationStatus() }
            }
        }
    }

    private func pendingHubCard(_ url: String) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            Label("A link asked to switch hubs", systemImage: "link")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(OrbitalTheme.text)
            Text(url)
                .font(.caption.monospaced())
                .foregroundStyle(OrbitalTheme.textSecondary)
                .textSelection(.enabled)
            HStack(spacing: 10) {
                OrbitalActionButton(title: "Switch", symbol: "arrow.right.circle.fill", kind: .primary, accent: accent) {
                    store.updateHubURL(url)
                }
                OrbitalActionButton(title: "Ignore", symbol: "xmark", kind: .secondary, accent: accent) {
                    store.pendingHubURL = nil
                    hubURL = store.hubURLString
                }
            }
        }
        .orbitalCard(padding: 16, tint: accent)
    }

    private var permissionLabel: some View {
        Text("System permission")
            .font(.subheadline)
            .foregroundStyle(OrbitalTheme.textSecondary)
            .fixedSize()
    }

    private var testAlertsButton: some View {
        OrbitalActionButton(title: "Send a test", symbol: "bell.badge.fill", kind: .secondary, accent: accent) {
            router.openAlerts()
        }
    }

    private var systemSettingsButton: some View {
        OrbitalActionButton(title: "System settings", symbol: "gearshape.fill", kind: .secondary, accent: accent) {
            if let url = URL(string: UIApplication.openNotificationSettingsURLString) {
                openURL(url)
            }
        }
    }

    private var profileCard: some View {
        HStack(spacing: 14) {
            ZStack {
                Circle()
                    .fill(LinearGradient(colors: [accent, OrbitalTheme.violet], startPoint: .topLeading, endPoint: .bottomTrailing))
                Text(initial)
                    .font(.title2.weight(.bold))
                    .foregroundStyle(OrbitalTheme.backgroundDeep)
            }
            .frame(width: 54, height: 54)
            .shadow(color: accent.opacity(0.45), radius: 12)
            VStack(alignment: .leading, spacing: 5) {
                Text(store.username.isEmpty ? "Signed in" : store.username)
                    .font(.headline)
                    .foregroundStyle(OrbitalTheme.text)
                    .lineLimit(1)
                Text(store.baseURL.host ?? store.hubURLString)
                    .font(.caption.monospaced())
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                ConnectionPill(label: store.connectionLabel)
            }
            Spacer(minLength: 0)
            Button {
                Task { await store.refresh() }
            } label: {
                Image(systemName: "arrow.clockwise")
                    .font(.subheadline.weight(.bold))
                    .foregroundStyle(accent)
                    .frame(width: 44, height: 44)
                    .background(accent.opacity(0.12), in: Circle())
            }
            .buttonStyle(OrbitalPressStyle())
            .accessibilityLabel("Reconnect")
        }
        .orbitalCard(padding: 16, tint: accent)
    }

    private func group<Content: View>(_ title: String, symbol: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                Image(systemName: symbol)
                    .font(.caption.weight(.bold))
                    .foregroundStyle(accent)
                OrbitalEyebrow(text: title, color: OrbitalTheme.textSecondary)
            }
            content()
        }
        .orbitalCard(padding: 16)
    }

    private func row(_ title: String, _ value: String) -> some View {
        HStack {
            Text(title).foregroundStyle(OrbitalTheme.textSecondary)
            Spacer()
            Text(value)
                .foregroundStyle(OrbitalTheme.text)
                .multilineTextAlignment(.trailing)
        }
        .font(.subheadline)
    }

    private func errorText(_ text: String) -> some View {
        HStack(alignment: .top, spacing: 6) {
            Image(systemName: "exclamationmark.triangle.fill")
            Text(text)
                .fixedSize(horizontal: false, vertical: true)
        }
        .font(.footnote)
        .foregroundStyle(OrbitalTheme.danger)
    }

    private var initial: String {
        store.username.first.map { String($0).uppercased() } ?? "O"
    }

    private var permissionText: String {
        switch permission {
        case .authorized: return "Allowed"
        case .provisional: return "Quiet delivery"
        case .ephemeral: return "Temporary"
        case .denied: return "Off"
        case .notDetermined: return "Not asked yet"
        default: return "Checking"
        }
    }

    private var permissionSymbol: String {
        switch permission {
        case .authorized, .provisional, .ephemeral: return "checkmark"
        case .denied: return "bell.slash.fill"
        default: return "questionmark"
        }
    }

    private var permissionTint: Color {
        switch permission {
        case .authorized: return OrbitalTheme.success
        case .provisional, .ephemeral: return OrbitalTheme.warning
        case .denied: return OrbitalTheme.danger
        default: return OrbitalTheme.textSecondary
        }
    }
}
