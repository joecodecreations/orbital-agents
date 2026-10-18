import SwiftUI

/// The app's mark: the icon artwork in a rounded tile.
struct OrbitalMark: View {
    var size: CGFloat = 30

    var body: some View {
        Image("BrandMark")
            .resizable()
            .interpolation(.high)
            .scaledToFill()
            .frame(width: size, height: size)
            .clipShape(RoundedRectangle(cornerRadius: size * 0.24, style: .continuous))
            .overlay(
                RoundedRectangle(cornerRadius: size * 0.24, style: .continuous)
                    .strokeBorder(Color.white.opacity(0.14), lineWidth: 0.5)
            )
            .accessibilityHidden(true)
    }
}

/// First run and signed-out: the hub's address, then the hub's username and password.
/// Errors say what to check: Tailscale and the hub for an unreachable address, the password for a 401.
struct SignInView: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var desk: DeskState
    @State private var username = ""
    @State private var password = ""
    @FocusState private var focus: Field?

    private enum Field { case hub, username, password }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 22) {
                VStack(alignment: .leading, spacing: 16) {
                    OrbitalMark(size: 72)
                        .shadow(color: OrbitalTheme.accent.opacity(0.45), radius: 24, y: 8)
                    VStack(alignment: .leading, spacing: 6) {
                        Text(OrbitalBrand.appName)
                            .font(.largeTitle.weight(.bold))
                            .foregroundStyle(OrbitalTheme.text)
                        Text("Your dashboards, alerts, and agents, from the hub on your tailnet.")
                            .font(.body)
                            .foregroundStyle(OrbitalTheme.textSecondary)
                    }
                }
                .padding(.top, 40)

                if let notice = store.signInNotice {
                    Label(notice, systemImage: "link")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.accent)
                        .fixedSize(horizontal: false, vertical: true)
                }

                VStack(alignment: .leading, spacing: 12) {
                    OrbitalEyebrow(text: "Hub address")
                    OrbitalField(placeholder: OrbitalBrand.hubPlaceholder, text: $store.hubURLString, symbol: "server.rack", monospaced: true)
                        .keyboardType(.URL)
                        .focused($focus, equals: .hub)
                        .submitLabel(.next)
                        .onSubmit { focus = .username }
                    Text("The hub's Tailscale name or 100.x address. Port 7700 is added when you leave it out. `orbital status` on the hub prints it.")
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                    OrbitalEyebrow(text: "Sign in")
                        .padding(.top, 6)
                    OrbitalField(placeholder: "Username", text: $username, symbol: "person.fill")
                        .textContentType(.username)
                        .focused($focus, equals: .username)
                        .submitLabel(.next)
                        .onSubmit { focus = .password }
                    OrbitalField(placeholder: "Password", text: $password, symbol: "lock.fill", secure: true)
                        .textContentType(.password)
                        .focused($focus, equals: .password)
                        .submitLabel(.go)
                        .onSubmit(signIn)
                    if let error = store.loginError {
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: "exclamationmark.triangle.fill")
                            Text(error)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.danger)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .accessibilityElement(children: .combine)
                    }
                    OrbitalActionButton(
                        title: store.isLoggingIn ? "Signing in…" : "Sign in",
                        symbol: "arrow.right.circle.fill",
                        busy: store.isLoggingIn,
                        action: signIn
                    )
                    .disabled(!canSubmit)
                    .opacity(canSubmit ? 1 : 0.55)
                    .padding(.top, 4)
                }
                .orbitalCard(padding: 16)

                VStack(alignment: .leading, spacing: 6) {
                    Label("No hub yet?", systemImage: "questionmark.circle")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.textSecondary)
                    Text("Run ./setup on your main computer. It installs the hub, asks for a username and password, and prints an orbital://connect link that fills in this address.")
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(24)
            .frame(maxWidth: 480)
            .frame(maxWidth: .infinity)
        }
        .scrollDismissesKeyboard(.interactively)
        .orbitalBackdrop(OrbitalTheme.accent)
        .foregroundStyle(OrbitalTheme.text)
        .onAppear {
            username = store.username
            if store.hubURLString.isEmpty { focus = .hub }
        }
    }

    private var canSubmit: Bool {
        !store.isLoggingIn && !username.isEmpty && !password.isEmpty && !store.hubURLString.trimmingCharacters(in: .whitespaces).isEmpty
    }

    private func signIn() {
        guard canSubmit else { return }
        focus = nil
        Task {
            await store.login(username: username, password: password)
            if store.isLoggedIn {
                password = ""
                desk.attach(store)
            }
        }
    }
}
