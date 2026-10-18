import SwiftUI

/// The Desk tab: one thread. Talk or type; the hub's desk model answers at once, starts agents on
/// your machines for real work, and their results come back here, out loud, and as alerts.
struct DeskScreen: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var desk: DeskState
    @EnvironmentObject private var router: AppRouter
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.orbitalPushed) private var pushed
    @FocusState private var composerFocused: Bool
    @State private var showVoiceSettings = false
    @ObservedObject private var voiceDebug = VoiceDebug.shared
    @ObservedObject private var speech = SpeechSettings.shared

    var body: some View {
        VStack(spacing: 0) {
            topBar
            if desk.isUnavailable {
                ScrollView {
                    notConfigured
                        .padding(16)
                        .frame(maxWidth: OrbitalTheme.columnMax)
                        .frame(maxWidth: .infinity)
                }
                .refreshable { await desk.refresh() }
            } else {
                backupStrip
                DeskNowStrip()
                thread
                composer
            }
        }
        .background(OrbitalTheme.background.ignoresSafeArea())
        .overlay(alignment: .top) { banner }
        .toolbar(.hidden, for: .navigationBar)
        .task {
            desk.attach(store)
            await desk.refresh()
            desk.warm()
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                desk.conversation.armGlassesClick()
                Task { await desk.refresh() }
            }
        }
        .onChange(of: store.isLoggedIn) { _, loggedIn in
            if loggedIn { Task { await desk.refresh() } }
        }
        .sheet(isPresented: $showVoiceSettings) {
            DeskVoiceSettingsView()
                .environmentObject(desk)
        }
    }

    // MARK: Header

    private var topBar: some View {
        HStack(alignment: .center, spacing: 10) {
            AgentMark(size: 32)
            VStack(alignment: .leading, spacing: 1) {
                Text("Desk")
                    .font(.title3.weight(.bold))
                    .foregroundStyle(OrbitalTheme.text)
                modelLine
            }
            Spacer(minLength: 4)
            if !desk.isUnavailable {
                Button {
                    desk.speakRepliesOn.toggle()
                    if !desk.speakRepliesOn { desk.stopSpeaking() }
                } label: {
                    Image(systemName: desk.speakRepliesOn ? "speaker.wave.2.fill" : "speaker.slash.fill")
                        .frame(width: 40, height: 40)
                }
                .foregroundStyle(desk.speakRepliesOn ? OrbitalTheme.accent : OrbitalTheme.textTertiary)
                .accessibilityLabel(desk.speakRepliesOn ? "Spoken replies on" : "Spoken replies off")
            }
            Button {
                router.openTracker(item: nil)
            } label: {
                ZStack(alignment: .topTrailing) {
                    Image(systemName: "dot.radiowaves.left.and.right")
                        .frame(width: 40, height: 40)
                    if desk.trackerActive > 0 {
                        Text("\(desk.trackerActive)")
                            .font(.caption2.weight(.bold).monospacedDigit())
                            .foregroundStyle(OrbitalTheme.backgroundDeep)
                            .padding(.horizontal, 5)
                            .frame(minWidth: 18, minHeight: 18)
                            .background(OrbitalTheme.success, in: Capsule())
                            .offset(x: 2, y: 2)
                    }
                }
            }
            .foregroundStyle(OrbitalTheme.text)
            .accessibilityLabel("Tracker, \(desk.trackerActive) in progress")
            Menu {
                Button("Run an agent", systemImage: "play.circle") { router.runAgent() }
                Button("Voice settings", systemImage: "waveform") { showVoiceSettings = true }
                Button("Refresh", systemImage: "arrow.clockwise") { Task { await desk.refresh() } }
                if !desk.isUnavailable {
                    Button("Clear context", systemImage: "eraser") { Task { await desk.clearContext() } }
                }
            } label: {
                Image(systemName: "ellipsis.circle")
                    .frame(width: 40, height: 40)
            }
            .foregroundStyle(OrbitalTheme.textSecondary)
        }
        .font(.title3)
        .padding(.horizontal, 14)
        .padding(.top, 6)
        .padding(.bottom, 2)
    }

    @ViewBuilder
    private var modelLine: some View {
        if desk.isUnavailable {
            Text("Not configured on your hub")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(OrbitalTheme.warning)
        } else if let model = desk.model {
            HStack(spacing: 4) {
                Image(systemName: model.badgeSymbol)
                Text(model.badgeText)
                    .lineLimit(1)
            }
            .font(.caption2.weight(.semibold))
            .foregroundStyle(model.fast && !model.isBackup ? OrbitalTheme.success : OrbitalTheme.warning)
            .accessibilityLabel(model.note ?? model.model)
        } else if let error = desk.loadError {
            Text(error)
                .font(.caption2)
                .foregroundStyle(OrbitalTheme.warning)
                .lineLimit(1)
        } else {
            Text(desk.loaded ? store.systemName : "Connecting…")
                .font(.caption2)
                .foregroundStyle(OrbitalTheme.textTertiary)
        }
    }

    /// The hub has no desk model (no key): what that means and how to turn it on.
    private var notConfigured: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 12) {
                IconTile(symbol: "sparkles", tint: OrbitalTheme.warning, size: 44, filled: true)
                VStack(alignment: .leading, spacing: 3) {
                    Text("The desk is not configured on your hub")
                        .font(.headline)
                        .foregroundStyle(OrbitalTheme.text)
                    Text("It needs a model key on the hub. Nothing is set up on this device.")
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if let note = Insights.nonEmpty(desk.setupNote) {
                Label(note, systemImage: "info.circle")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.text.opacity(0.9))
                    .fixedSize(horizontal: false, vertical: true)
            }
            VStack(alignment: .leading, spacing: 8) {
                OrbitalEyebrow(text: "On the hub")
                step(1, "Add your key to ~/.orbital-agents/secrets.env (the hub picks it up without a restart):", code: "XAI_API_KEY=…")
                step(2, "The model is in config.toml under [desk]. After changing that file:", code: "orbital services restart hub")
            }
            Text("You can still run agents on any machine from the Agents tab, and the tracker works without the desk.")
                .font(.footnote)
                .foregroundStyle(OrbitalTheme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
            HStack(spacing: 10) {
                OrbitalActionButton(title: "Open Agents", symbol: "cpu", kind: .primary) {
                    router.openAgents()
                }
                OrbitalActionButton(title: "Check again", symbol: "arrow.clockwise", kind: .secondary) {
                    Task { await desk.refresh() }
                }
            }
        }
        .orbitalCard(padding: 16, tint: OrbitalTheme.warning)
        .padding(.top, 8)
    }

    private func step(_ number: Int, _ text: String, code: String?) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text("\(number)")
                .font(.caption.weight(.bold).monospacedDigit())
                .foregroundStyle(OrbitalTheme.accent)
                .frame(minWidth: 20)
                .padding(.vertical, 2)
                .background(OrbitalTheme.accent.opacity(0.14), in: RoundedRectangle(cornerRadius: 6, style: .continuous))
            VStack(alignment: .leading, spacing: 4) {
                Text(text)
                    .font(.subheadline)
                    .foregroundStyle(OrbitalTheme.text.opacity(0.92))
                if let code {
                    Text(code)
                        .font(.caption.monospaced())
                        .foregroundStyle(OrbitalTheme.text)
                        .textSelection(.enabled)
                        .padding(.horizontal, 8)
                        .padding(.vertical, 4)
                        .background(OrbitalTheme.backgroundDeep.opacity(0.8), in: RoundedRectangle(cornerRadius: 6, style: .continuous))
                }
            }
        }
    }

    /// The main model is out and a backup is answering: why, and what brings it back.
    @ViewBuilder
    private var backupStrip: some View {
        if let model = desk.model, model.isBackup, let note = model.note, !note.isEmpty {
            HStack(alignment: .top, spacing: 8) {
                Image(systemName: "lifepreserver.fill")
                    .foregroundStyle(OrbitalTheme.warning)
                Text(note)
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .lineLimit(4)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .background(OrbitalTheme.warning.opacity(0.10), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            .padding(.horizontal, 14)
            .padding(.vertical, 4)
            .accessibilityElement(children: .combine)
        }
    }

    // MARK: Thread

    private var thread: some View {
        ScrollViewReader { proxy in
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    if desk.hasOlder, desk.messages.count >= 40 {
                        Button("Earlier messages") { Task { await desk.loadOlder() } }
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(OrbitalTheme.accent)
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 6)
                    }
                    if desk.messages.isEmpty, desk.loaded {
                        intro
                    }
                    ForEach(desk.messages) { message in
                        DeskMessageRow(message: message)
                            .id(message.id)
                    }
                    ForEach(desk.streaming.keys.sorted(), id: \.self) { id in
                        DeskStreamingRow(text: desk.streaming[id] ?? "")
                            .id("stream-\(id)")
                    }
                    if desk.isBusy, desk.streaming.isEmpty {
                        DeskStreamingRow(text: "")
                    }
                    Color.clear.frame(height: 1).id("bottom")
                }
                .padding(.horizontal, 14)
                .padding(.vertical, 10)
                .frame(maxWidth: OrbitalTheme.columnMax)
                .frame(maxWidth: .infinity)
            }
            .defaultScrollAnchor(.bottom)
            .scrollDismissesKeyboard(.interactively)
            .refreshable { await desk.refresh() }
            .onChange(of: desk.loaded) {
                // The first load lands at the newest message, after layout.
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { proxy.scrollTo("bottom", anchor: .bottom) }
            }
            .onChange(of: desk.messages.last?.id) {
                withAnimation(.easeOut(duration: 0.2)) { proxy.scrollTo("bottom", anchor: .bottom) }
            }
            .onChange(of: desk.streaming) {
                proxy.scrollTo("bottom", anchor: .bottom)
            }
            .onChange(of: desk.focusTaskID) { _, id in
                guard let id, let target = desk.messages.last(where: { $0.taskId == id }) else { return }
                withAnimation { proxy.scrollTo(target.id, anchor: .center) }
                desk.focusTaskID = nil
            }
        }
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Talk or type. The desk answers right away, hands real work to agents on your machines, and tells you when it's done.")
                .font(.callout)
                .foregroundStyle(OrbitalTheme.textSecondary)
            FlowLayout(spacing: 8) {
                ForEach(["What's being worked on right now?", "Run the test suite on the build machine", "Remind me at 5 to check the backups"], id: \.self) { example in
                    Button {
                        desk.draft = example
                        composerFocused = true
                    } label: {
                        Text(example)
                            .font(.footnote.weight(.medium))
                            .padding(.horizontal, 12)
                            .padding(.vertical, 8)
                            .background(OrbitalTheme.surfaceRaised, in: Capsule())
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(OrbitalTheme.text)
                }
            }
        }
        .orbitalCard(padding: 16)
        .padding(.top, 8)
    }

    // MARK: Composer

    private var composer: some View {
        VStack(spacing: 6) {
            if desk.conversation.isListening {
                listeningLine
            }
            HStack(alignment: .bottom, spacing: 8) {
                Button {
                    desk.toggleLive()
                } label: {
                    Image(systemName: desk.liveConversationOn ? "waveform.circle.fill" : "waveform.circle")
                        .font(.system(size: 30))
                        .symbolEffect(.variableColor.iterative, isActive: desk.liveConversationOn)
                        .frame(width: 44, height: 44)
                }
                .foregroundStyle(desk.liveConversationOn ? OrbitalTheme.success : OrbitalTheme.textSecondary)
                .accessibilityLabel(desk.liveConversationOn ? "Hands-free on" : "Hands-free off")

                TextField("Talk or type to the desk", text: $desk.draft, axis: .vertical)
                    .lineLimit(1 ... 5)
                    .font(.body)
                    .foregroundStyle(OrbitalTheme.text)
                    .focused($composerFocused)
                    .submitLabel(.send)
                    .onSubmit { desk.sendDraft() }
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(OrbitalTheme.backgroundDeep.opacity(0.8), in: RoundedRectangle(cornerRadius: 20, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 20, style: .continuous).strokeBorder(OrbitalTheme.hairline))

                if desk.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    Button {
                        desk.tapMic()
                    } label: {
                        Image(systemName: micSymbol)
                            .font(.system(size: 20, weight: .bold))
                            .foregroundStyle(OrbitalTheme.backgroundDeep)
                            .frame(width: 44, height: 44)
                            .background(desk.conversation.isListening ? OrbitalTheme.danger : OrbitalTheme.accent, in: Circle())
                    }
                    .accessibilityLabel(desk.conversation.isListening ? "Send what I said" : "Talk")
                } else {
                    Button {
                        desk.sendDraft()
                    } label: {
                        Image(systemName: "arrow.up")
                            .font(.system(size: 20, weight: .bold))
                            .foregroundStyle(OrbitalTheme.backgroundDeep)
                            .frame(width: 44, height: 44)
                            .background(OrbitalTheme.accent, in: Circle())
                    }
                    .accessibilityLabel("Send")
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.top, 8)
        .padding(.bottom, 8)
        .background(OrbitalTheme.backgroundDeep.opacity(0.94))
        .overlay(alignment: .top) { OrbitalTheme.hairline.frame(height: 1) }
    }

    private var micSymbol: String {
        desk.conversation.isListening && !desk.liveConversationOn ? "paperplane.fill" : "mic.fill"
    }

    private var listeningText: String {
        let partial = desk.conversation.partialText
        if !partial.isEmpty, partial != "…" { return partial }
        if desk.conversation.isAssistantSpeaking { return "Talking. Just speak to cut in." }
        if desk.isBusy { return "Thinking…" }
        return desk.liveConversationOn ? "Listening. Just talk." : "Listening. Pause to send."
    }

    private var listeningLine: some View {
        VStack(alignment: .leading, spacing: 2) {
            listeningRow
            // Voice diagnostics, when they are turned on in Voice settings.
            if speech.sendDiagnostics, !voiceDebug.line.isEmpty {
                Text(voiceDebug.line)
                    .font(.caption2.monospaced())
                    .foregroundStyle(OrbitalTheme.textSecondary.opacity(0.8))
                    .lineLimit(2)
                    .padding(.horizontal, 4)
            }
        }
    }

    private var listeningRow: some View {
        HStack(spacing: 8) {
            Capsule()
                .fill(OrbitalTheme.success)
                .frame(width: 4 + CGFloat(min(1, max(0, desk.conversation.micLevel))) * 40, height: 4)
                .animation(.linear(duration: 0.08), value: desk.conversation.micLevel)
            Text(listeningText)
                .font(.caption)
                .foregroundStyle(OrbitalTheme.textSecondary)
                .lineLimit(2)
            Spacer(minLength: 0)
            if desk.conversation.isPaused {
                Text("mic paused").font(.caption2).foregroundStyle(OrbitalTheme.warning)
            }
        }
        .padding(.horizontal, 4)
    }

    // MARK: Banner

    private var banner: some View {
        ZStack {
            if let text = desk.banner {
                Text(text)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 9)
                    .background(OrbitalTheme.surfaceRaised.opacity(0.96), in: Capsule())
                    .shadow(color: .black.opacity(0.4), radius: 10, y: 4)
                    .padding(.top, 54)
                    .transition(.move(edge: .top).combined(with: .opacity))
                    .onTapGesture { desk.banner = nil }
                    .task(id: text) {
                        try? await Task.sleep(nanoseconds: 3_000_000_000)
                        if desk.banner == text { desk.banner = nil }
                    }
            }
        }
        .animation(.spring(response: 0.35, dampingFraction: 0.85), value: desk.banner)
    }
}
