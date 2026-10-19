import SwiftUI

// Rows and cards for the desk thread.

struct DeskMessageRow: View {
    let message: DeskMessage
    @EnvironmentObject private var desk: DeskState

    var body: some View {
        switch message.role {
        case .user: userBubble
        case .assistant: assistantText
        case .event: DeskEventRow(message: message)
        }
    }

    private var userBubble: some View {
        HStack {
            Spacer(minLength: 56)
            VStack(alignment: .trailing, spacing: 4) {
                Text(message.text)
                    .font(.body)
                    .foregroundStyle(OrbitalTheme.text)
                    .textSelection(.enabled)
                    .padding(.horizontal, 14)
                    .padding(.vertical, 10)
                    .background(
                        LinearGradient(colors: [OrbitalTheme.accent.opacity(0.32), OrbitalTheme.accent.opacity(0.2)], startPoint: .top, endPoint: .bottom),
                        in: RoundedRectangle(cornerRadius: 18, style: .continuous)
                    )
                HStack(spacing: 6) {
                    if message.source == "voice" {
                        Image(systemName: "mic.fill")
                    }
                    if let error = message.error {
                        Text("Not sent · \(error)")
                            .foregroundStyle(OrbitalTheme.danger)
                        Button("Retry") { desk.retry(message) }
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(OrbitalTheme.accent)
                    } else if message.pending {
                        Text("Sending…")
                    } else {
                        Text(DeskTime.clock(message.at))
                    }
                }
                .font(.caption2)
                .foregroundStyle(OrbitalTheme.textTertiary)
            }
        }
    }

    private var assistantText: some View {
        HStack(alignment: .top, spacing: 10) {
            AgentMark()
            VStack(alignment: .leading, spacing: 4) {
                // Replies are meant to be plain words; a stray **bold** or `code` still reads cleanly.
                Text(DeskMarkdown.inline(message.text))
                    .font(.body)
                    .foregroundStyle(message.error == nil ? OrbitalTheme.text : OrbitalTheme.warning)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 6) {
                    // Which model answered, when the hub picks per turn.
                    if let model = message.model?.components(separatedBy: " · ").first, !model.isEmpty {
                        Text(model)
                    }
                    if let ms = message.latencyMs {
                        Text(ms < 1000 ? "\(ms) ms" : String(format: "%.1f s", Double(ms) / 1000))
                    }
                    Text(DeskTime.clock(message.at))
                }
                .font(.caption2.monospacedDigit())
                .foregroundStyle(OrbitalTheme.textTertiary)
            }
            Spacer(minLength: 32)
        }
    }
}

struct AgentMark: View {
    var size: CGFloat = 26

    var body: some View {
        ZStack {
            Circle().fill(LinearGradient(colors: [OrbitalTheme.accent, OrbitalTheme.violet], startPoint: .topLeading, endPoint: .bottomTrailing))
            Image(systemName: "sparkles")
                .font(.system(size: size * 0.46, weight: .bold))
                .foregroundStyle(OrbitalTheme.backgroundDeep)
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}

struct DeskStreamingRow: View {
    let text: String

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            AgentMark()
            if text.isEmpty {
                TypingDots().padding(.top, 8)
            } else {
                Text(text + " ▍")
                    .font(.body)
                    .foregroundStyle(OrbitalTheme.text)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 32)
        }
    }
}

struct TypingDots: View {
    @State private var phase = 0.0

    var body: some View {
        TimelineView(.animation(minimumInterval: 0.15)) { context in
            let t = context.date.timeIntervalSinceReferenceDate
            HStack(spacing: 5) {
                ForEach(0 ..< 3) { i in
                    Circle()
                        .fill(OrbitalTheme.textSecondary)
                        .frame(width: 7, height: 7)
                        .opacity(0.35 + 0.65 * max(0, sin((t * 5) - Double(i) * 0.7)))
                }
            }
        }
        .accessibilityLabel("The desk is answering")
    }
}

struct DeskEventRow: View {
    let message: DeskMessage
    @EnvironmentObject private var desk: DeskState

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if let id = message.taskId, let task = desk.tasks[id], showsCard {
                DeskTaskCard(task: task, headline: message.text, kind: message.kind)
            } else {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: symbol)
                        .font(.footnote.weight(.bold))
                        .foregroundStyle(tint)
                        .frame(width: 20)
                    Text(message.text)
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 0)
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 9)
                .background(tint.opacity(0.08), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            }
        }
        .padding(.leading, 36)
    }

    /// The start card is the live one; the done card shows the result.
    private var showsCard: Bool {
        ["task_started", "task_done", "task_failed"].contains(message.kind ?? "")
    }

    private var symbol: String {
        switch message.kind {
        case "todo": return "checklist"
        case "reminder": return "bell.fill"
        case "task_followup": return "arrow.turn.down.right"
        case "task_stopped": return "stop.circle.fill"
        case "engine": return "lifepreserver.fill"
        case "context": return "eraser"
        case "error", "task_failed": return "exclamationmark.triangle.fill"
        default: return "sparkles"
        }
    }

    private var tint: Color {
        switch message.kind {
        case "error", "task_failed": return OrbitalTheme.warning
        case "todo", "reminder": return OrbitalTheme.success
        default: return OrbitalTheme.accent
        }
    }
}

struct DeskTaskCard: View {
    let task: DeskTask
    var headline: String?
    var kind: String?
    @EnvironmentObject private var desk: DeskState
    @EnvironmentObject private var router: AppRouter

    var body: some View {
        let isDoneCard = kind == "task_done" || kind == "task_failed"
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                statusIcon
                VStack(alignment: .leading, spacing: 1) {
                    Text(task.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                    Text("\(task.projectName) · \(statusLine)")
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
                Spacer(minLength: 0)
            }
            if isDoneCard, let headline {
                Text(headline)
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            } else if task.isActive, let note = task.lastNote {
                Text(note)
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .lineLimit(3)
            }
            HStack(spacing: 14) {
                if let tracker = task.trackerId {
                    Button {
                        router.push(.trackerItem(tracker))
                    } label: {
                        Label(isDoneCard ? "Full result" : "Details", systemImage: "list.bullet.rectangle")
                    }
                }
                if task.isActive, !isDoneCard {
                    Button(role: .destructive) {
                        desk.cancel(task: task.id)
                    } label: {
                        Label("Stop", systemImage: "stop.fill")
                    }
                    .foregroundStyle(OrbitalTheme.danger)
                }
            }
            .font(.caption.weight(.semibold))
            .foregroundStyle(OrbitalTheme.accent)
            .buttonStyle(.plain)
        }
        .orbitalCard(padding: 12, radius: 14, tint: tint)
        .id("task-card-\(task.id)-\(kind ?? "")")
    }

    private var tint: Color {
        switch task.status {
        case "done": return OrbitalTheme.success
        case "failed", "stopped": return OrbitalTheme.warning
        default: return OrbitalTheme.accent
        }
    }

    @ViewBuilder
    private var statusIcon: some View {
        switch task.status {
        case "running":
            ProgressView().controlSize(.small).tint(OrbitalTheme.accent).frame(width: 22)
        case "queued":
            Image(systemName: "hourglass").foregroundStyle(OrbitalTheme.textSecondary).frame(width: 22)
        case "done":
            Image(systemName: "checkmark.seal.fill").foregroundStyle(OrbitalTheme.success).frame(width: 22)
        default:
            Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(OrbitalTheme.warning).frame(width: 22)
        }
    }

    private var statusLine: String {
        switch task.status {
        case "running": return "working \(DeskTime.elapsed(since: task.startedAt))"
        case "queued": return (task.pendingFollowups ?? 0) > 0 ? "follow-up queued" : "queued"
        case "done": return "done \(DeskTime.short(task.finishedAt))"
        case "stopped": return "stopped"
        default: return task.error ?? "failed"
        }
    }
}

/// Task facts on a tracker item the desk started: the brief it was given, and Stop.
struct DeskTaskPanel: View {
    let task: DeskTask
    @EnvironmentObject private var desk: DeskState
    @State private var showBrief = false

    var body: some View {
        let live = desk.tasks[task.id] ?? task
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                OrbitalEyebrow(text: "Started from the desk · \(live.id)")
                Spacer()
                if live.isActive {
                    Button(role: .destructive) {
                        desk.cancel(task: live.id)
                    } label: {
                        Label("Stop", systemImage: "stop.fill")
                            .font(.caption.weight(.semibold))
                    }
                    .foregroundStyle(OrbitalTheme.danger)
                    .buttonStyle(.plain)
                }
            }
            Text([live.model, live.effort.map { "effort \($0)" }, live.status].compactMap { $0 }.joined(separator: " · "))
                .font(.caption)
                .foregroundStyle(OrbitalTheme.textTertiary)
            if let brief = live.brief, !brief.isEmpty {
                Button {
                    withAnimation(.easeInOut(duration: 0.2)) { showBrief.toggle() }
                } label: {
                    Label(showBrief ? "Hide the brief" : "Show the brief it got", systemImage: "doc.text")
                        .font(.caption.weight(.semibold))
                }
                .buttonStyle(.plain)
                .foregroundStyle(OrbitalTheme.accent)
                if showBrief {
                    Text(brief)
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .textSelection(.enabled)
                }
            }
            if let error = live.error, live.status == "failed" {
                Text(error)
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.warning)
            }
        }
        .orbitalCard(padding: 14, tint: OrbitalTheme.accent)
    }
}

/// What is moving right now: desk tasks and every other agent in the tracker.
struct DeskNowStrip: View {
    @EnvironmentObject private var desk: DeskState
    @EnvironmentObject private var router: AppRouter

    var body: some View {
        let running = desk.tasks.values.filter(\.isActive).sorted { ($0.createdAt ?? "") > ($1.createdAt ?? "") }
        let deskTrackers = Set(desk.tasks.values.compactMap(\.trackerId))
        let others = desk.trackerNow.filter { !deskTrackers.contains($0.id) }
        if !running.isEmpty || !others.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 8) {
                    ForEach(running) { task in
                        nowCard(agent: task.model ?? "agent", title: task.title, line: task.lastNote ?? "\(task.projectName) · \(task.status == "queued" ? "queued" : DeskTime.elapsed(since: task.startedAt))", spinning: task.status == "running", progress: desk.trackerNow.first { $0.id == task.trackerId }?.shownProgress) {
                            if let tracker = task.trackerId { router.push(.trackerItem(tracker)) }
                        }
                    }
                    ForEach(others) { item in
                        nowCard(agent: item.agentName, title: item.title, line: item.lastNote ?? item.project ?? DeskTime.short(item.updatedAt), spinning: false, progress: item.shownProgress) {
                            router.push(.trackerItem(item.id))
                        }
                    }
                }
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
            }
        }
    }

    private func nowCard(agent: String, title: String, line: String, spinning: Bool, progress: TrackerProgress?, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(alignment: .top, spacing: 8) {
                AgentBadge(name: agent, size: 24)
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 4) {
                        Text(title)
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(OrbitalTheme.text)
                            .lineLimit(1)
                        if spinning { ProgressView().controlSize(.mini) }
                    }
                    Text(line)
                        .font(.caption2)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .lineLimit(2)
                    if let progress {
                        TrackerProgressBar(progress: progress, tint: AgentStyle.color(agent), height: 4)
                            .padding(.top, 3)
                    }
                }
            }
            .frame(width: 190, alignment: .leading)
            .padding(10)
            .background(OrbitalTheme.surfaceRaised.opacity(0.8), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(AgentStyle.color(agent).opacity(0.3), lineWidth: 1))
        }
        .buttonStyle(OrbitalPressStyle())
    }
}
