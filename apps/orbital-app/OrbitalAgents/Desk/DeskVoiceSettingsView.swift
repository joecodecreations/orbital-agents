import SwiftUI

/// Speech options for the desk: how talking works, the recognizer, the voice, the microphone and
/// speaker, cloud keys kept on this device, and glasses.
struct DeskVoiceSettingsView: View {
    @EnvironmentObject private var desk: DeskState
    @ObservedObject private var speech = SpeechSettings.shared
    @ObservedObject private var onDevice = OnDeviceSpeech.shared
    @Environment(\.dismiss) private var dismiss
    @State private var testStatus = ""
    @State private var testBusy = false
    @State private var customVoice = ""

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    group("Talking to the desk", symbol: "waveform") {
                        Text("Tap Talk for one phrase; it sends when you pause. Hands-free (the waveform on the left) keeps the mic open: every phrase goes to the desk, replies are spoken, and finished agents are announced. With glasses, start hands-free, then lock the phone: each temple tap turns your mic off or on. A rising chime plays only after the mic is actually listening — that is when you can talk. A low tone means your mic is off.")
                            .font(.footnote)
                            .foregroundStyle(OrbitalTheme.textSecondary)
                        Toggle("Speak replies to typed messages", isOn: $desk.speakRepliesOn)
                        HStack {
                            Text("Send after a pause of")
                            Spacer()
                            Text(String(format: "%.1fs", desk.silenceAutoSendSeconds))
                                .font(.body.monospacedDigit().weight(.semibold))
                                .foregroundStyle(OrbitalTheme.accent)
                        }
                        Slider(value: $desk.silenceAutoSendSeconds, in: DeskState.silenceMin ... DeskState.silenceMax, step: 0.1)
                        Text("How long you can pause mid-thought before the desk sends. Raise this if phrases get cut off; lower it to send sooner.")
                            .font(.caption2)
                            .foregroundStyle(OrbitalTheme.textSecondary)
                        HStack {
                            Text("Longest phrase")
                            Slider(value: $speech.maxUtteranceSeconds, in: 15 ... 120, step: 5)
                            Text(String(format: "%.0fs", speech.maxUtteranceSeconds))
                                .font(.caption.monospacedDigit())
                                .frame(width: 40, alignment: .trailing)
                        }
                        Stepper("Ignore phrases under \(speech.minCharsToSend) characters", value: $speech.minCharsToSend, in: 2 ... 40)
                        Toggle("Skip repeated phrases", isOn: $speech.filterDuplicates)
                    }

                    group("Speech to text", symbol: "mic.badge.plus") {
                        picker("Recognizer", selection: $speech.sttProvider) {
                            ForEach(STTProvider.allCases) { Text($0.title).tag($0) }
                        }
                        caption(speech.sttProvider.detail)
                        if speech.sttProvider == .onDeviceWhisper {
                            onDeviceSection
                        }
                        picker("Language", selection: $speech.speechLanguage) {
                            ForEach(SpeechLanguage.allCases) { Text($0.displayName).tag($0) }
                        }
                    }

                    group("Voice", symbol: "person.wave.2") {
                        picker("Voice", selection: $speech.ttsProvider) {
                            ForEach(TTSProvider.allCases) { Text($0.title).tag($0) }
                        }
                        caption(speech.ttsProvider.detail)
                        if speech.ttsProvider == .fishAudio {
                            fishSection
                        }
                        speedControl
                        SpeakerVolumeControl()
                        HStack(spacing: 10) {
                            Button {
                                runTest(.english)
                            } label: {
                                Label("Test voice", systemImage: "speaker.wave.2.fill").frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.borderedProminent)
                            Button {
                                runTest(.portuguese)
                            } label: {
                                Label("PT", systemImage: "speaker.wave.2").frame(maxWidth: .infinity)
                            }
                            .buttonStyle(.bordered)
                        }
                        .disabled(testBusy)
                        if !testStatus.isEmpty { caption(testStatus) }
                    }

                    group("Microphone and sound", symbol: "mic") {
                        picker("Mic", selection: $speech.audioInputSource) {
                            ForEach(AudioInputSource.allCases) { Text($0.title).tag($0) }
                        }
                        caption(speech.audioInputSource.detail)
                        picker("Play sound on", selection: $speech.audioOutputRoute) {
                            ForEach(AudioOutputRoute.allCases) { Text($0.title).tag($0) }
                        }
                        caption(speech.audioOutputRoute.detail)
                        picker("Pause and listen cue", selection: $speech.pauseCueStyle) {
                            ForEach(PauseCueStyle.allCases) { Text($0.title).tag($0) }
                        }
                        caption("Talk and hands-free play a rising chime only after the mic is actually listening, and a low tone when it stops, so you can tell with your eyes closed. The mic does not send those sounds.")
                        HStack(spacing: 10) {
                            Button("Play listening") { desk.conversation.pauseCues.playEarcon(.micOn) }
                                .buttonStyle(.bordered)
                            Button("Play not listening") { desk.conversation.pauseCues.playEarcon(.micOff) }
                                .buttonStyle(.bordered)
                        }
                        Toggle("Apple noise suppression and echo cancel", isOn: $speech.voiceProcessingEnabled)
                        Toggle("Speech-only noise gate", isOn: $speech.speechNoiseGateEnabled)
                    }
                    .onChange(of: speech.audioOutputRoute) { desk.conversation.reassertAudioRoute() }

                    group("Cloud keys on this device", symbol: "key") {
                        caption("Only for the cloud recognizers and Azure's voice. Kept in this device's keychain and sent only to that provider. The desk's model key and Fish voices live on your hub.")
                        SecureField("OpenAI API key (Whisper)", text: $speech.openAIAPIKey)
                        SecureField("Azure Speech key", text: $speech.azureSpeechKey)
                        TextField("Azure region", text: $speech.azureSpeechRegion)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                    }

                    group("Glasses and car", symbol: "eyeglasses") {
                        row("Glasses remotes", desk.conversation.glasses.remoteControlsEnabled ? "On" : "Off")
                        row("Glasses audio", desk.conversation.glasses.isGlassesAudioConnected ? "Connected" : "Not on this route")
                        row("CarPlay", desk.conversation.glasses.isCarPlayConnected ? "Connected" : "No")
                        row("Route", desk.conversation.glasses.bluetoothLabel)
                    }

                    group("Diagnostics", symbol: "stethoscope") {
                        Toggle("Send voice diagnostics to the hub", isOn: $speech.sendDiagnostics)
                        caption("Recognizer and playback events (no audio, phrase lengths only) go to the hub's logs, and a live line shows under the mic while you talk. Useful when speech misbehaves; off by default.")
                    }
                }
                .padding(16)
                .frame(maxWidth: OrbitalTheme.columnMax)
                .frame(maxWidth: .infinity)
            }
            .background(OrbitalTheme.background.ignoresSafeArea())
            .foregroundStyle(OrbitalTheme.text)
            .tint(OrbitalTheme.accent)
            .navigationTitle("Voice")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } }
            }
            .onAppear {
                Task { await desk.refreshSpeechStatus() }
                Task { await onDevice.refreshAvailable() }
            }
        }
        .preferredColorScheme(.dark)
    }

    /// Fish voices from the hub: on or off there, its voices, and a voice by id.
    @ViewBuilder
    private var fishSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            row("Fish on your hub", desk.speechStatus.tts ? "Ready" : "Not configured")
            if !desk.speechStatus.tts {
                caption("Add FISH_API_KEY to ~/.orbital-agents/secrets.env on the hub and restart it. Until then replies use the built-in voice.")
                    .foregroundStyle(OrbitalTheme.warning)
            }
            picker("English voice", selection: $speech.fishDefaultENVoiceId) {
                ForEach(speech.fishVoices) { Text($0.name).tag($0.referenceId) }
            }
            picker("Portuguese voice", selection: $speech.fishDefaultPTVoiceId) {
                ForEach(speech.fishVoices) { Text($0.name).tag($0.referenceId) }
            }
            HStack(spacing: 8) {
                TextField("Fish voice id", text: $customVoice)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .font(.footnote.monospaced())
                Button("Use for \(speech.speechLanguage.displayName)") {
                    let id = customVoice.trimmingCharacters(in: .whitespacesAndNewlines)
                    guard !id.isEmpty else { return }
                    if speech.speechLanguage == .portuguese { speech.fishDefaultPTVoiceId = id } else { speech.fishDefaultENVoiceId = id }
                    speech.applyHubVoices(desk.speechStatus)
                    customVoice = ""
                }
                .font(.footnote.weight(.semibold))
                .disabled(customVoice.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            Toggle("Keep Fish emotion tags", isOn: $speech.fishEmotionTagsEnabled)
        }
    }

    /// On-device Whisper: status, download from the hub, style.
    @ViewBuilder
    private var onDeviceSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            row("On-device model", onDevice.statusLine)
            if case .downloading(let fraction) = onDevice.phase {
                ProgressView(value: fraction)
                HStack {
                    caption("\(ByteCountFormatter.string(fromByteCount: onDevice.downloadedBytes, countStyle: .file)) · keep \(OrbitalBrand.appName) open until it finishes")
                    Spacer()
                    Button("Cancel") { onDevice.cancelDownload() }.font(.caption)
                }
            }
            if !onDevice.lastRun.isEmpty { caption("Last phrase: \(onDevice.lastRun)") }
            if onDevice.installed == nil || onDevice.updateAvailable != nil {
                if onDevice.available.isEmpty {
                    caption("Your hub offers no on-device models yet. Until one is downloaded, Apple's words are sent.")
                }
                ForEach(onDevice.available) { model in
                    Button {
                        speech.onDeviceModelID = model.id
                        onDevice.download(model)
                    } label: {
                        Label(
                            "\(onDevice.installed == nil ? "Download" : "Update to") \(model.name) · \(model.sizeLabel)",
                            systemImage: "arrow.down.circle"
                        )
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    .buttonStyle(.bordered)
                    .disabled(isDownloading)
                }
            }
            picker("Text style", selection: $speech.onDeviceMode) {
                ForEach(OnDeviceSpeechMode.allCases) { Text($0.title).tag($0) }
            }
            caption(speech.onDeviceMode.detail)
            if let installed = onDevice.installed {
                if onDevice.updateAvailable == nil {
                    ForEach(onDevice.available.filter { $0.id != installed.id }) { model in
                        Button("Switch to \(model.name) · \(model.sizeLabel)") {
                            speech.onDeviceModelID = model.id
                            onDevice.download(model)
                        }
                        .font(.footnote)
                        .disabled(isDownloading)
                    }
                }
                Button("Delete the downloaded model (\(installed.sizeLabel))", role: .destructive) { onDevice.remove() }
                    .font(.footnote)
                caption("Weights: \(installed.license ?? "see the model's license"). Until the model is ready, Apple's words are sent.")
            }
        }
    }

    private var isDownloading: Bool {
        switch onDevice.phase {
        case .downloading, .verifying: return true
        default: return false
        }
    }

    private var speedControl: some View {
        let binding: Binding<Double>
        let range: ClosedRange<Double>
        switch speech.ttsProvider {
        case .appleOnDevice:
            binding = $speech.appleTTSSpeed
            range = 0.5 ... 1.5
        case .fishAudio:
            binding = $speech.fishTTSSpeed
            range = 0.5 ... 2.0
        case .azureSpeech:
            binding = $speech.azureTTSSpeed
            range = 0.5 ... 2.0
        }
        return VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text("Speaking speed")
                Spacer()
                Text(String(format: "%.2f×", binding.wrappedValue))
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(OrbitalTheme.textSecondary)
                Button("Reset") { speech.resetActiveTTSSpeed() }
                    .font(.caption)
            }
            Slider(value: binding, in: range, step: 0.05)
        }
    }

    private func runTest(_ language: SpeechLanguage) {
        testBusy = true
        testStatus = "Playing…"
        Task {
            let tts = desk.conversation.tts
            await tts.playSample(language: language)
            testBusy = false
            if speech.ttsProvider == .fishAudio, let issue = tts.lastIssue {
                testStatus = "Fish failed, played the built-in voice: \(issue)"
            } else if speech.ttsProvider == .fishAudio {
                testStatus = "Played the \(language.displayName) sample · \(tts.lastRoute)"
            } else {
                testStatus = "Played the \(language.displayName) sample"
            }
        }
    }

    private func group<Content: View>(_ title: String, symbol: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            Label(title, systemImage: symbol)
                .font(.headline)
                .foregroundStyle(OrbitalTheme.accent)
            content()
        }
        .orbitalCard(padding: 14)
    }

    private func picker<T: Hashable, Content: View>(_ title: String, selection: Binding<T>, @ViewBuilder content: () -> Content) -> some View {
        HStack {
            Text(title)
            Spacer()
            Picker(title, selection: selection, content: content)
                .pickerStyle(.menu)
                .labelsHidden()
        }
    }

    private func caption(_ text: String) -> some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(OrbitalTheme.textSecondary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func row(_ title: String, _ value: String) -> some View {
        HStack {
            Text(title).foregroundStyle(OrbitalTheme.textSecondary)
            Spacer()
            Text(value).multilineTextAlignment(.trailing)
        }
        .font(.subheadline)
    }
}

/// Speaker-only loudness. Lives in Voice settings.
struct SpeakerVolumeControl: View {
    @ObservedObject private var speech = SpeechSettings.shared
    var showsHint: Bool = true

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Label("Speaker volume", systemImage: "speaker.wave.3.fill")
                    .font(.subheadline.weight(.semibold))
                Spacer()
                Text(speech.speakerBoostLabel)
                    .font(.subheadline.monospacedDigit().weight(.semibold))
                    .foregroundStyle(OrbitalTheme.accent)
            }
            HStack(spacing: 10) {
                stepButton("minus", -1)
                Slider(
                    value: $speech.speakerBoostDb,
                    in: SpeakerBoost.minDb ... SpeakerBoost.maxDb,
                    step: 1
                )
                .accessibilityLabel("Speaker volume")
                stepButton("plus", 1)
            }
            if showsHint {
                Text("Louder on the iPhone's own speaker, for the car or a walk. Headphones, glasses and CarPlay stay the same. The side buttons still change the phone volume.")
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
    }

    private func stepButton(_ system: String, _ delta: Double) -> some View {
        Button {
            speech.speakerBoostDb = min(SpeakerBoost.maxDb, max(SpeakerBoost.minDb, speech.speakerBoostDb + delta))
        } label: {
            Image(systemName: system)
                .font(.body.weight(.bold))
                .frame(width: 36, height: 36)
                .background(OrbitalTheme.surfaceHighlight, in: Circle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(OrbitalTheme.text)
        .accessibilityLabel(delta > 0 ? "Louder speech" : "Quieter speech")
    }
}
