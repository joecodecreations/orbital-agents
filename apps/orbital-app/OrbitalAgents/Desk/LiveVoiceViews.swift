import SwiftUI

/// The live voice call above the composer: what it's doing, what you said, what it's saying,
/// the latest agent result, and mute / stop / end.
struct LiveVoiceBar: View {
    @EnvironmentObject private var desk: DeskState
    @ObservedObject var live: RealtimeVoiceSession

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                LiveVoiceOrb(level: live.level, phase: live.phase, muted: live.isMuted)
                    .frame(width: 34, height: 34)
                VStack(alignment: .leading, spacing: 2) {
                    Text(live.statusLine)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(statusColor)
                        .lineLimit(1)
                    if live.phase == .speaking {
                        Text("Talk over it to cut in")
                            .font(.caption2)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .lineLimit(1)
                    } else if !live.voiceName.isEmpty {
                        Text("Live voice · \(live.voiceName.capitalized)")
                            .font(.caption2)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .lineLimit(1)
                    }
                }
                Spacer(minLength: 4)
                if live.phase == .speaking {
                    roundButton("stop.fill", label: "Stop talking", tint: OrbitalTheme.textSecondary) { live.cutIn() }
                }
                roundButton(live.isMuted ? "mic.slash.fill" : "mic.fill", label: live.isMuted ? "Unmute" : "Mute",
                            tint: live.isMuted ? OrbitalTheme.danger : OrbitalTheme.textSecondary) { live.toggleMute() }
                roundButton("phone.down.fill", label: "End call", tint: .white, fill: OrbitalTheme.danger) { desk.toggleLiveVoice() }
            }
            if let line = transcript, !line.isEmpty {
                Text(line)
                    .font(.callout)
                    .foregroundStyle(transcriptIsYou ? OrbitalTheme.text : OrbitalTheme.textSecondary)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .animation(.easeOut(duration: 0.15), value: line)
            }
            if let notice = live.notice {
                Label(notice, systemImage: "sparkles")
                    .font(.caption.weight(.medium))
                    .foregroundStyle(OrbitalTheme.accent)
                    .lineLimit(2)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 6)
                    .background(OrbitalTheme.accent.opacity(0.12), in: Capsule())
                    .transition(.opacity)
            }
        }
        .padding(12)
        .background(OrbitalTheme.surfaceRaised, in: RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).strokeBorder(OrbitalTheme.success.opacity(0.35)))
        .accessibilityElement(children: .contain)
    }

    private var transcriptIsYou: Bool {
        switch live.phase {
        case .hearing, .thinking: return true
        default: return false
        }
    }

    private var transcript: String? {
        switch live.phase {
        case .hearing, .thinking:
            return live.userText.isEmpty ? nil : live.userText
        case .speaking, .working:
            return live.replyText.isEmpty ? nil : live.replyText
        default:
            return live.replyText.isEmpty ? nil : live.replyText
        }
    }

    private var statusColor: Color {
        switch live.phase {
        case .failed: return OrbitalTheme.danger
        case .speaking: return OrbitalTheme.accent
        case .working: return OrbitalTheme.warning
        default: return OrbitalTheme.success
        }
    }

    private func roundButton(_ symbol: String, label: String, tint: Color, fill: Color? = nil, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(tint)
                .frame(width: 40, height: 40)
                .background(fill ?? OrbitalTheme.backgroundDeep.opacity(0.8), in: Circle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
    }
}

/// A circle that breathes with your voice while listening and pulses while it talks.
struct LiveVoiceOrb: View {
    var level: Float
    var phase: RealtimeVoiceSession.Phase
    var muted: Bool

    var body: some View {
        TimelineView(.animation(minimumInterval: 1 / 30)) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            let pulse = phase == .speaking ? 0.12 * sin(t * 6) + 0.12 : 0
            let busy: Bool = {
                switch phase {
                case .thinking, .working, .connecting: return true
                default: return false
                }
            }()
            let scale = 0.72 + (muted ? 0 : Double(level) * 0.35) + pulse
            ZStack {
                Circle().fill(color.opacity(0.18))
                Circle()
                    .fill(color)
                    .scaleEffect(scale)
                if busy {
                    Circle()
                        .trim(from: 0, to: 0.3)
                        .stroke(color, style: StrokeStyle(lineWidth: 2.5, lineCap: .round))
                        .rotationEffect(.radians(t * 4))
                }
            }
        }
        .accessibilityHidden(true)
    }

    private var color: Color {
        if muted { return OrbitalTheme.textTertiary }
        switch phase {
        case .speaking: return OrbitalTheme.accent
        case .working: return OrbitalTheme.warning
        case .failed: return OrbitalTheme.danger
        default: return OrbitalTheme.success
        }
    }
}

/// Live voice choices, kept on the hub: on or classic, which voice, which model, how quickly it answers.
struct LiveVoiceSettingsSection: View {
    @EnvironmentObject private var desk: DeskState
    @ObservedObject private var speech = SpeechSettings.shared

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let info = desk.realtime {
                if !info.available {
                    Text(info.note ?? "Add OPENAI_API_KEY to ~/.orbital-agents/secrets.env on the hub to turn on live voice.")
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.warning)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    Text("Talk to the desk like a phone call: interrupt it any time, it looks things up in your files and pages itself, says \"checking on that\" before anything slow, and sends agents for real work while it keeps talking. Agent results are read to you as they land.")
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Picker("Voice engine", selection: binding(\.engine, key: "engine", info)) {
                    Text("Live voice (OpenAI)").tag("realtime")
                    Text("Classic").tag("classic")
                }
                .pickerStyle(.segmented)
                if info.engine != "classic" {
                    row("Voice") {
                        Picker("Voice", selection: binding(\.voice, key: "voice", info)) {
                            ForEach(DeskRealtimeInfo.voices, id: \.self) { Text($0.capitalized).tag($0) }
                        }
                    }
                    row("Model") {
                        Picker("Model", selection: binding(\.model, key: "model", info)) {
                            ForEach(Array(Set(DeskRealtimeInfo.models + [info.model])).sorted(), id: \.self) { Text($0).tag($0) }
                        }
                    }
                    row("Talking over it") {
                        Picker("Talking over it", selection: $speech.liveTalkOver) {
                            Text("Easy").tag("easy")
                            Text("Normal").tag("normal")
                            Text("Firm (loud speaker)").tag("firm")
                        }
                    }
                    Text("To cut in, speak up over its voice for a moment. Firm stops it cutting itself off on a loud speakerphone or in the car; Easy lets a quiet word stop it.")
                        .font(.caption2)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    row("Turn-taking") {
                        Picker("Turn-taking", selection: binding(\.eagerness, key: "eagerness", info)) {
                            ForEach(DeskRealtimeInfo.eagernessChoices, id: \.id) { Text($0.title).tag($0.id) }
                        }
                    }
                    Text("Mini costs less per minute; the full model is better at using tools. A new voice applies to the next call.")
                        .font(.caption2)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            } else {
                Text("Sign in to a hub to set up live voice.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textSecondary)
            }
        }
        .task { await desk.refreshRealtime() }
    }

    private func row<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        // Same tight menu-picker hit target as DeskVoiceSettingsView — enlarge the row.
        HStack {
            Text(title)
            Spacer(minLength: 8)
            content()
                .labelsHidden()
                .frame(minWidth: 120, minHeight: 44, alignment: .trailing)
                .contentShape(Rectangle())
        }
        .frame(minHeight: 44)
        .contentShape(Rectangle())
    }

    private func binding(_ path: KeyPath<DeskRealtimeInfo, String>, key: String, _ info: DeskRealtimeInfo) -> Binding<String> {
        Binding(
            get: { desk.realtime?[keyPath: path] ?? info[keyPath: path] },
            set: { value in Task { await desk.setVoice([key: value]) } }
        )
    }
}
