import AppKit
import OrbitalTrackerCore
import SwiftUI

enum Metrics {
    static let barWidth: CGFloat = 492
    static let margin: CGFloat = 14
}

struct RootView: View {
    @Bindable var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            ControlBar(model: model)
            if model.isTyping { TypeField(model: model) }
            if model.isEditingWakeWord { WakeWordField(model: model) }
            if let toast = model.toast {
                Text(toast)
                    .font(.system(size: 11.5, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 12).padding(.vertical, 6)
                    .background(Glass(cornerRadius: 13))
                    .transition(.move(edge: .top).combined(with: .opacity))
            }
            switch model.viewMode {
            case .active: ActiveView(model: model)
            case .history: HistoryView(model: model)
            }
        }
        .padding(Metrics.margin)
        .frame(width: Metrics.barWidth + Metrics.margin * 2, alignment: .topLeading)
        .fixedSize(horizontal: false, vertical: true)
        .onGeometryChange(for: CGSize.self, of: { $0.size }) { size in
            model.onContentSize?(size)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .environment(\.colorScheme, .dark)
    }
}

struct ActiveView: View {
    @Bindable var model: AppModel

    var body: some View {
        let items = model.store.activeItems(model.filter)
        VStack(alignment: .leading, spacing: 9) {
            if items.isEmpty {
                Text(emptyHint)
                    .font(.system(size: 11.5, weight: .medium, design: .rounded))
                    .foregroundStyle(.white.opacity(0.75))
                    .padding(.horizontal, 12).padding(.vertical, 7)
                    .background(Glass(cornerRadius: 14))
            } else {
                FlowLayout {
                    ForEach(items) { item in
                        BubbleView(item: item, isSelected: model.selectedID == item.id,
                                   onTap: { model.select(item.id) }, onClose: { model.close(item.id) })
                            .transition(.scale(scale: 0.6).combined(with: .opacity))
                    }
                }
                .animation(.spring(response: 0.35, dampingFraction: 0.8), value: items.map(\.id))
            }
            if let id = model.selectedID, let item = model.store.item(id), model.store.isOnScreen(item) {
                DetailCard(item: item, model: model)
                    .transition(.opacity.combined(with: .move(edge: .top)))
            }
        }
    }

    private var emptyHint: String {
        switch model.filter {
        case .ai: return "No AI agents are working on anything right now."
        case .mine:
            guard model.voiceEnabled else { return "Nothing of yours yet. Press +\(model.shortcutSuffix("N")) to type what you're working on." }
            return model.micLive ? "Say what you're working on…" : "Turn the mic on\(model.shortcutSuffix("M")) and say what you're working on."
        case .all:
            guard model.voiceEnabled else { return "Nothing tracked yet. Agents appear here; press +\(model.shortcutSuffix("N")) to type your own." }
            return model.micLive ? "Say what you're working on…" : "Nothing tracked. Turn the mic on\(model.shortcutSuffix("M")) or press + to type."
        }
    }
}

struct ControlBar: View {
    @Bindable var model: AppModel

    var body: some View {
        HStack(spacing: 8) {
            if model.voiceEnabled {
                MicButton(model: model)
                MicMenu(model: model)
            } else {
                OrbitBadge()
            }
            VStack(alignment: .leading, spacing: 1) {
                Text(TrackerInfo.shortName)
                    .font(.system(size: 12, weight: .bold, design: .rounded))
                    .foregroundStyle(.white)
                    .lineLimit(1)
                    .minimumScaleFactor(0.72)
                let status = model.statusLine
                Text(status.text)
                    .font(.system(size: 10, weight: .medium))
                    .foregroundStyle(color(status.tone))
                    .lineLimit(1)
                    .truncationMode(.tail)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .allowsHitTesting(false)
            FilterToggle(selection: $model.filter)
            BarButton(systemName: model.viewMode == .active ? "clock.arrow.circlepath" : "circle.grid.2x2.fill",
                      help: model.viewMode == .active ? "Show finished history" : "Back to active items",
                      active: model.viewMode == .history) {
                withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) {
                    model.viewMode = model.viewMode == .active ? .history : .active
                }
            }
            EvidenceMenu(model: model)
            BarButton(systemName: "plus", help: "Type an item" + model.shortcutSuffix("N")) { model.startTyping() }
            SettingsMenu(model: model)
        }
        .padding(.leading, 8)
        .padding(.trailing, 8)
        .frame(width: Metrics.barWidth, height: 46)
        .background(WindowDragHandle())
        .background(Glass(cornerRadius: 23))
    }

    private func color(_ tone: AppModel.Tone) -> Color {
        switch tone {
        case .muted: return .white.opacity(0.55)
        case .live: return Palette.ok.opacity(0.95)
        case .warn: return Palette.warn
        }
    }
}

/// Left end of the bar when voice is off: a static mark instead of the mic.
struct OrbitBadge: View {
    var body: some View {
        Image(systemName: "antenna.radiowaves.left.and.right")
            .font(.system(size: 12, weight: .bold))
            .foregroundStyle(.white.opacity(0.85))
            .frame(width: 30, height: 30)
            .background(Circle().fill(Color.white.opacity(0.12)))
            .help("\(TrackerInfo.productName): what every agent is working on. Voice is off (tracker.voice).")
    }
}

struct MicButton: View {
    @Bindable var model: AppModel
    @State private var hover = false

    var body: some View {
        Button(action: model.toggleMic) {
            ZStack {
                if model.micLive {
                    Circle()
                        .stroke(Palette.live.opacity(0.55), lineWidth: 2)
                        .scaleEffect(1 + CGFloat(model.level) * 0.45)
                        .opacity(0.4 + Double(model.level) * 0.6)
                        .animation(.easeOut(duration: 0.12), value: model.level)
                }
                Circle().fill(model.micLive ? Palette.live : Color.white.opacity(hover ? 0.2 : 0.12))
                if model.transcribing > 0 {
                    ProgressView().controlSize(.small).tint(.white)
                } else {
                    Image(systemName: model.micLive ? "mic.fill" : "mic.slash.fill")
                        .font(.system(size: 13, weight: .bold))
                        .foregroundStyle(.white.opacity(model.micLive ? 1 : 0.75))
                }
            }
            .frame(width: 30, height: 30)
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .help((model.micLive ? "Mute microphone" : "Unmute microphone") + model.shortcutSuffix("M"))
    }
}

/// Picks which microphone feeds the tracker.
struct MicMenu: View {
    @Bindable var model: AppModel

    var body: some View {
        Menu {
            Section("Microphone") {
                Button {
                    model.selectedDeviceID = nil
                } label: {
                    Label("System default (\(AudioCapture.defaultDeviceName() ?? "none"))",
                          systemImage: model.selectedDeviceID == nil ? "checkmark" : "")
                }
                ForEach(model.devices) { device in
                    Button {
                        model.selectedDeviceID = device.id
                    } label: {
                        Label(device.name, systemImage: model.selectedDeviceID == device.id ? "checkmark" : "")
                    }
                }
            }
            Divider()
            Button("Refresh microphones") { model.refreshDevices() }
        } label: {
            Image(systemName: "chevron.down")
                .font(.system(size: 9, weight: .bold))
                .foregroundStyle(.white.opacity(0.75))
                .frame(width: 16, height: 26)
                .contentShape(Rectangle())
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .help("Choose microphone · \(model.currentDeviceName)")
    }
}

struct FilterToggle: View {
    @Binding var selection: ItemFilter
    @Namespace private var ns

    var body: some View {
        HStack(spacing: 0) {
            ForEach(ItemFilter.allCases) { filter in
                Button {
                    withAnimation(.spring(response: 0.25, dampingFraction: 0.85)) { selection = filter }
                } label: {
                    Text(filter.label)
                        .font(.system(size: 10.5, weight: .bold, design: .rounded))
                        .foregroundStyle(selection == filter ? .black : .white.opacity(0.75))
                        .padding(.horizontal, 8)
                        .frame(height: 22)
                        .background {
                            if selection == filter {
                                Capsule().fill(Color.white.opacity(0.92)).matchedGeometryEffect(id: "pill", in: ns)
                            }
                        }
                        .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .help(help(filter))
            }
        }
        .padding(2)
        .background(Capsule().fill(Color.white.opacity(0.08)))
    }

    private func help(_ f: ItemFilter) -> String {
        switch f {
        case .all: return "Everything going on"
        case .ai: return "Only what AI agents are working on"
        case .mine: return "Only items you said or typed"
        }
    }
}

struct SettingsMenu: View {
    @Bindable var model: AppModel

    var body: some View {
        Menu {
            if model.voiceEnabled {
                Section("Speech (CrisperWhisper 2.0 on Metal)") {
                    Text(model.stt.statusText + (model.stt.lastLatency.map { String(format: " · last %.2fs", $0) } ?? ""))
                    Toggle("Require wake word: \(model.wakeWordsDisplay)", isOn: $model.requireWakeWord)
                    Button("Edit wake words…") { model.editWakeWords() }
                    Picker("Pause before a phrase ends", selection: $model.pauseSeconds) {
                        Text("0.8 s").tag(0.8)
                        Text("1.1 s").tag(1.1)
                        Text("1.5 s").tag(1.5)
                        Text("2.0 s").tag(2.0)
                    }
                }
            } else {
                Section("Speech") {
                    Text("Voice is off (tracker.voice = false in config.toml)")
                }
            }
            Section("Item details") {
                Picker("Open item details", selection: $model.detailStyle) {
                    ForEach(DetailStyle.allCases) { Text($0.label).tag($0) }
                }
            }
            Section("Screenshots from AI agents (synced with Orbital Agents)") {
                EvidencePicker(model: model)
            }
            Section("Titles") {
                switch model.titleMode {
                case .none:
                    Text("Local titles only (tracker.titles = \"none\")")
                default:
                    Toggle("Use Grok to title & close items", isOn: $model.useGrok)
                        .disabled(!model.grokAvailable)
                    if !model.grokAvailable { Text("grok CLI not found: using local titles") }
                }
            }
            Section("This machine") {
                Text("\(model.config.machineName) · \(model.config.role)\(model.config.relaysToHub ? " · reports to the hub" : "")")
                Text("API 127.0.0.1:\(String(model.config.trackerPort)) · login item: \(LoginItem.installed ? "on" : "off") (orbital services)")
                Text(model.hotkeysEnabled
                     ? "Shortcuts: \(model.config.hotkeyModifiers.symbols) + T show/hide · N type\(model.voiceEnabled ? " · M mic" : "") (tracker.hotkey_modifiers)"
                     : "Shortcuts off (tracker.hotkeys = false)")
            }
            Divider()
            Button("Open data folder") { NSWorkspace.shared.open(model.paths.root) }
            Button("Open log") {
                let log = model.paths.logFile
                NSWorkspace.shared.open(FileManager.default.fileExists(atPath: log.path) ? log : model.paths.logsDir)
            }
            Button("Open settings folder") { NSWorkspace.shared.open(model.config.home) }
            Divider()
            Button("Quit \(TrackerInfo.shortName)") { NSApp.terminate(nil) }
        } label: {
            Image(systemName: "ellipsis")
                .font(.system(size: 12, weight: .bold))
                .foregroundStyle(.white.opacity(0.85))
                .frame(width: 26, height: 26)
                .background(Circle().fill(Color.white.opacity(0.08)))
                .contentShape(Circle())
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        .help("Settings")
    }
}

/// The LaunchAgent the services installer writes (`orbital services install`).
enum LoginItem {
    static var plist: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/LaunchAgents/\(TrackerInfo.launchAgentLabel).plist")
    }
    static var installed: Bool { FileManager.default.fileExists(atPath: plist.path) }
}

/// Off / Min 1 / All: how much screenshot and video proof agents attach for UI, website and
/// game work. Lives in settings.json, so the Orbital Agents app's toggle and this one stay in step.
struct EvidencePicker: View {
    @Bindable var model: AppModel

    var body: some View {
        Picker("Screenshots from AI agents", selection: $model.evidence) {
            ForEach(EvidenceLevel.allCases, id: \.self) { level in
                Text("\(level.label) · \(level.blurb)").tag(level)
            }
        }
        .pickerStyle(.inline)
        .labelsHidden()
    }
}

/// The bar's camera: shows the evidence level at a glance and changes it.
struct EvidenceMenu: View {
    @Bindable var model: AppModel

    var body: some View {
        let level = model.evidence
        Menu {
            Section("Screenshots from AI agents") {
                EvidencePicker(model: model)
            }
            Text("Applies to UI, website and game work. Synced with Orbital Agents.")
        } label: {
            Image(systemName: level == .off ? "camera" : (level == .all ? "photo.stack.fill" : "camera.fill"))
                .font(.system(size: 11.5, weight: .semibold))
                .frame(width: 26, height: 26)
                .contentShape(Circle())
        }
        .menuStyle(.borderlessButton)
        .menuIndicator(.hidden)
        .fixedSize()
        // A menu label draws as a template (white), so the colour lives outside it.
        .background(Circle().fill(level == .off ? Color.white.opacity(0.08) : Palette.evidence))
        .overlay(alignment: .topTrailing) {
            if level != .off {
                Text(level == .all ? "ALL" : "1")
                    .font(.system(size: 7.5, weight: .heavy, design: .rounded))
                    .foregroundStyle(.black)
                    .padding(.horizontal, 3)
                    .frame(minWidth: 11, minHeight: 11)
                    .background(Capsule().fill(Palette.ok))
                    .offset(x: 5, y: -4)
                    .allowsHitTesting(false)
            }
        }
        .help("Agent screenshots: \(level.label) · \(level.blurb)")
    }
}

struct TypeField: View {
    @Bindable var model: AppModel
    @FocusState private var focused: Bool

    var body: some View {
        HStack(spacing: 8) {
            Image(systemName: "pencil").font(.system(size: 11, weight: .bold)).foregroundStyle(.white.opacity(0.7))
            TextField("What are you working on? (Enter to add, Esc to cancel)", text: $model.typedText)
                .textFieldStyle(.plain)
                .font(.system(size: 12.5))
                .foregroundStyle(.white)
                .focused($focused)
                .onSubmit { model.submitTyped() }
                .onExitCommand { model.isTyping = false; model.typedText = "" }
        }
        .padding(.horizontal, 12)
        .frame(width: Metrics.barWidth, height: 34)
        .background(Glass(cornerRadius: 17))
        .onAppear { DispatchQueue.main.async { focused = true } }
    }
}

/// Inline editor for the wake words (Enter saves and turns wake-word mode on).
struct WakeWordField: View {
    @Bindable var model: AppModel
    @State private var draft = ""
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Image(systemName: "waveform.badge.mic").font(.system(size: 11, weight: .bold)).foregroundStyle(.white.opacity(0.7))
                TextField("hey tracker, track", text: $draft)
                    .textFieldStyle(.plain)
                    .font(.system(size: 12.5))
                    .foregroundStyle(.white)
                    .focused($focused)
                    .onSubmit { model.saveWakeWords(draft) }
                    .onExitCommand { model.isEditingWakeWord = false }
                Button("Save") { model.saveWakeWords(draft) }
                    .buttonStyle(.plain)
                    .font(.system(size: 11, weight: .bold))
                    .foregroundStyle(.black)
                    .padding(.horizontal, 10).padding(.vertical, 4)
                    .background(Capsule().fill(Palette.ok))
            }
            Text("Comma-separated; each can be several words. Start a phrase with one, or say it alone, then your item within 8 s.")
                .font(.system(size: 10))
                .foregroundStyle(.white.opacity(0.65))
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 9)
        .frame(width: Metrics.barWidth, alignment: .leading)
        .background(Glass(cornerRadius: 14))
        .onAppear {
            draft = model.wakeWordsText
            DispatchQueue.main.async { focused = true }
        }
    }
}
