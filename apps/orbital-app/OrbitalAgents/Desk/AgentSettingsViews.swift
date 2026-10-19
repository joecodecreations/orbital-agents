import SwiftUI

// System → Settings → Agent: which AI chats, its model, how work is handed out, and routing rules in plain
// words ("Always send iOS builds to studio with claude"). Read and saved through /v1/desk/settings; every
// change saves at once.

struct AgentRule: Codable, Equatable, Identifiable {
    var id: String
    var text: String
    var enabled: Bool
}

struct AgentSettings: Codable, Equatable {
    var chat: String
    var model: String
    var dispatch: String
    var rules: [AgentRule]
}

struct AgentChoice: Codable, Equatable, Identifiable {
    var id: String
    var title: String
    var detail: String?
}

struct AgentMachineOption: Codable, Equatable, Identifiable {
    var id: String
    var name: String
    var status: String?
    var agents: [String]
}

struct AgentSettingsOptions: Codable, Equatable {
    var chats: [AgentChoice]
    var models: [String: [String]]
    var dispatch: [AgentChoice]
    var machines: [AgentMachineOption]
    var agents: [String]
    var xaiKey: Bool?

    enum CodingKeys: String, CodingKey {
        case chats, models, dispatch, machines, agents
        case xaiKey = "xai_key"
    }
}

struct AgentSettingsResponse: Codable, Equatable {
    var settings: AgentSettings
    var options: AgentSettingsOptions?
    var model: AgentModelInfo?
}

struct AgentModelInfo: Codable, Equatable {
    var configured: Bool?
    var model: String?
}

/// The Agent and Routing rules cards at the top of Settings.
struct AgentSettingsCards: View {
    @EnvironmentObject private var desk: DeskState
    @State private var settings: AgentSettings?
    @State private var options: AgentSettingsOptions?
    @State private var answering: String?
    @State private var error: String?
    @State private var draft = ""
    @State private var saving = false
    @FocusState private var draftFocused: Bool

    private let accent = OrbitalTheme.accent

    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            if let settings, let options {
                agentCard(settings, options)
                rulesCard(settings, options)
            } else {
                HStack(spacing: 8) {
                    if error == nil { ProgressView().controlSize(.small) }
                    Text(error ?? "Loading the Agent's settings…")
                        .font(.footnote)
                        .foregroundStyle(error == nil ? OrbitalTheme.textTertiary : OrbitalTheme.warning)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .orbitalCard(padding: 14)
            }
        }
        .task { await load() }
    }

    // MARK: Agent

    private func agentCard(_ settings: AgentSettings, _ options: AgentSettingsOptions) -> some View {
        let models = options.models[effectiveChat(settings.chat, options)] ?? []
        return VStack(alignment: .leading, spacing: 12) {
            header("Agent", symbol: "sparkles")
            row("Chat AI") {
                Menu {
                    ForEach(options.chats) { choice in
                        Button {
                            save(["chat": choice.id, "model": ""])
                        } label: {
                            Label(choice.title, systemImage: choice.id == settings.chat ? "checkmark" : "circle")
                        }
                        .disabled(choice.id == "xai" && options.xaiKey != true)
                    }
                } label: {
                    menuLabel(options.chats.first { $0.id == settings.chat }?.title ?? settings.chat)
                }
            }
            if let detail = options.chats.first(where: { $0.id == settings.chat })?.detail {
                caption(detail)
            }
            if !models.isEmpty {
                row("Model") {
                    Menu {
                        Button {
                            save(["model": ""])
                        } label: {
                            Label("Default", systemImage: settings.model.isEmpty ? "checkmark" : "circle")
                        }
                        ForEach(models, id: \.self) { model in
                            Button {
                                save(["model": model])
                            } label: {
                                Label(modelTitle(model), systemImage: settings.model == model ? "checkmark" : "circle")
                            }
                        }
                    } label: {
                        menuLabel(settings.model.isEmpty ? "Default" : modelTitle(settings.model))
                    }
                }
            }
            VStack(alignment: .leading, spacing: 8) {
                Text("Handing out work")
                    .font(.subheadline)
                    .foregroundStyle(OrbitalTheme.textSecondary)
                Picker("Handing out work", selection: Binding(get: { settings.dispatch }, set: { save(["dispatch": $0]) })) {
                    ForEach(options.dispatch) { Text($0.title).tag($0.id) }
                }
                .pickerStyle(.segmented)
                if let detail = options.dispatch.first(where: { $0.id == settings.dispatch })?.detail {
                    caption(detail)
                }
            }
            if let answering {
                Label("Answering now: \(answering)", systemImage: "bolt.fill")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.success)
            }
        }
        .orbitalCard(padding: 16)
    }

    // MARK: Rules

    private func rulesCard(_ settings: AgentSettings, _ options: AgentSettingsOptions) -> some View {
        VStack(alignment: .leading, spacing: 12) {
            header("Routing rules", symbol: "arrow.triangle.branch")
            caption("Plain words the Agent follows when it picks an agent and a machine. Tap a name to drop it in.")
            if settings.rules.isEmpty {
                Text("No rules yet. Work goes to Grok first, then the next agent that can run, on this machine.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            ForEach(settings.rules) { rule in
                HStack(alignment: .top, spacing: 10) {
                    Button {
                        var rules = settings.rules
                        if let i = rules.firstIndex(of: rule) { rules[i].enabled.toggle() }
                        save(["rules": rules.map(\.json)])
                    } label: {
                        Image(systemName: rule.enabled ? "checkmark.circle.fill" : "circle")
                            .font(.title3)
                            .foregroundStyle(rule.enabled ? accent : OrbitalTheme.textTertiary)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(rule.enabled ? "Turn this rule off" : "Turn this rule on")
                    Text(rule.text)
                        .font(.subheadline)
                        .foregroundStyle(rule.enabled ? OrbitalTheme.text : OrbitalTheme.textTertiary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .fixedSize(horizontal: false, vertical: true)
                    Button {
                        save(["rules": settings.rules.filter { $0 != rule }.map(\.json)])
                    } label: {
                        Image(systemName: "trash")
                            .font(.footnote)
                            .foregroundStyle(OrbitalTheme.danger.opacity(0.8))
                            .frame(width: 32, height: 32)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel("Delete this rule")
                }
                .padding(.vertical, 2)
            }
            OrbitalField(placeholder: "e.g. Always route iOS builds to studio with claude", text: $draft, symbol: "text.bubble")
                .focused($draftFocused)
                .submitLabel(.done)
                .onSubmit { addRule(settings) }
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(["Always route", "to", "with", "Use", "for"], id: \.self) { word in
                        chip(word, symbol: nil, tint: OrbitalTheme.textSecondary)
                    }
                    ForEach(options.machines) { machine in
                        chip(machine.name, symbol: "desktopcomputer", tint: machine.status == "online" ? OrbitalTheme.success : OrbitalTheme.textTertiary)
                    }
                    ForEach(options.agents, id: \.self) { agent in
                        chip(agent, symbol: AgentStyle.symbol(agent), tint: AgentStyle.color(agent))
                    }
                }
                .padding(.vertical, 1)
            }
            OrbitalActionButton(title: saving ? "Saving…" : "Add rule", symbol: "plus", kind: .secondary, busy: saving) {
                addRule(settings)
            }
            .disabled(draft.trimmingCharacters(in: .whitespaces).isEmpty)
            .opacity(draft.trimmingCharacters(in: .whitespaces).isEmpty ? 0.55 : 1)
            if let error {
                Text(error).font(.caption).foregroundStyle(OrbitalTheme.danger)
            }
        }
        .orbitalCard(padding: 16)
    }

    private func chip(_ text: String, symbol: String?, tint: Color) -> some View {
        Button {
            let trimmed = draft.trimmingCharacters(in: .whitespaces)
            draft = trimmed.isEmpty ? text : trimmed + " " + text
            draftFocused = true
        } label: {
            HStack(spacing: 4) {
                if let symbol { Image(systemName: symbol).font(.caption2.weight(.bold)) }
                Text(text).font(.caption.weight(.semibold))
            }
            .foregroundStyle(tint)
            .padding(.horizontal, 10)
            .frame(minHeight: 32)
            .background(tint.opacity(0.12), in: Capsule())
        }
        .buttonStyle(.plain)
    }

    // MARK: Pieces

    private func header(_ title: String, symbol: String) -> some View {
        HStack(spacing: 8) {
            Image(systemName: symbol)
                .font(.caption.weight(.bold))
                .foregroundStyle(accent)
            OrbitalEyebrow(text: title, color: OrbitalTheme.textSecondary)
        }
    }

    private func row<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        HStack {
            Text(title).font(.subheadline).foregroundStyle(OrbitalTheme.textSecondary)
            Spacer()
            content()
        }
    }

    private func menuLabel(_ text: String) -> some View {
        HStack(spacing: 4) {
            Text(text).font(.subheadline.weight(.semibold))
            Image(systemName: "chevron.up.chevron.down").font(.caption2.weight(.bold))
        }
        .foregroundStyle(accent)
        .frame(minHeight: 36)
    }

    private func caption(_ text: String) -> some View {
        Text(text)
            .font(.caption)
            .foregroundStyle(OrbitalTheme.textTertiary)
            .fixedSize(horizontal: false, vertical: true)
    }

    private func modelTitle(_ model: String) -> String {
        switch model {
        case "auto": return "Auto (Haiku, Sonnet or Opus per turn)"
        case "haiku": return "Haiku (fastest)"
        case "sonnet": return "Sonnet"
        case "opus": return "Opus (strongest)"
        default: return model
        }
    }

    /// "Automatic" picks Grok unless the hub has an xAI key.
    private func effectiveChat(_ chat: String, _ options: AgentSettingsOptions) -> String {
        chat == "auto" ? (options.xaiKey == true ? "xai" : "grok") : chat
    }

    private func addRule(_ settings: AgentSettings) {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        let rule = AgentRule(id: "r\(Int(Date().timeIntervalSince1970))", text: text, enabled: true)
        draft = ""
        draftFocused = false
        save(["rules": (settings.rules + [rule]).map(\.json)])
    }

    private func load() async {
        guard let client = desk.client else {
            error = "Sign in to the hub to change the Agent."
            return
        }
        do {
            apply(try await client.agentSettings())
        } catch {
            self.error = OrbitalAPIError.friendly(error)
        }
    }

    private func save(_ changes: [String: Any]) {
        guard let client = desk.client else { return }
        saving = true
        Task {
            defer { saving = false }
            do {
                apply(try await client.saveAgentSettings(changes))
                error = nil
            } catch {
                self.error = OrbitalAPIError.friendly(error)
            }
        }
    }

    private func apply(_ response: AgentSettingsResponse) {
        settings = response.settings
        if let options = response.options { self.options = options }
        if let model = response.model?.model { answering = model }
    }
}

private extension AgentRule {
    var json: [String: Any] { ["id": id, "text": text, "enabled": enabled] }
}
