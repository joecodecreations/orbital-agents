import AppKit
import SwiftUI
import OrbitalTrackerCore

struct ScrollHeightKey: PreferenceKey {
    static let defaultValue: CGFloat = 0
    static func reduce(value: inout CGFloat, nextValue: () -> CGFloat) { value = max(value, nextValue()) }
}

/// Scrolls only once content is taller than `maxHeight`; otherwise sizes to fit,
/// so the panel never shows empty glass.
struct CappedScroll<Content: View>: View {
    var maxHeight: CGFloat
    @ViewBuilder var content: Content
    @State private var contentHeight: CGFloat = 0

    var body: some View {
        ScrollView(.vertical, showsIndicators: contentHeight > maxHeight) {
            content
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(GeometryReader { g in Color.clear.preference(key: ScrollHeightKey.self, value: g.size.height) })
        }
        .onPreferenceChange(ScrollHeightKey.self) { height in
            MainActor.assumeIsolated { contentHeight = height }
        }
        .frame(height: min(max(contentHeight, 1), maxHeight))
    }
}

/// Everything known about an item: what it is, progress, result, media, subagents.
struct ItemDetailBody: View {
    let item: TrackedItem
    let store: TrackerStore

    var body: some View {
        VStack(alignment: .leading, spacing: 11) {
            metaRow
            if let transcript = item.transcript, transcript != item.details {
                section("You said") {
                    Text("“\(transcript)”")
                        .font(.system(size: 12, weight: .regular).italic())
                        .foregroundStyle(.white.opacity(0.75))
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if !item.details.isEmpty {
                section(item.isAI ? "Task" : "Details") { MarkdownView(text: item.details, store: store) }
            }
            if item.progress != nil || item.usage != nil {
                section("Progress and cost") { UsageSummary(progress: item.progress, usage: item.usage) }
            }
            if !item.notes.isEmpty {
                section("Progress") {
                    VStack(alignment: .leading, spacing: 5) {
                        ForEach(Array(item.notes.enumerated()), id: \.offset) { _, note in
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                Text(Fmt.clock(note.at))
                                    .font(.system(size: 10, weight: .medium).monospacedDigit())
                                    .foregroundStyle(.white.opacity(0.5))
                                    .frame(width: 62, alignment: .leading)
                                MarkdownView(text: note.text, store: store, fontSize: 11.5)
                            }
                        }
                    }
                }
            }
            if let summary = item.summary, !summary.isEmpty {
                section("Result") { MarkdownView(text: summary, store: store) }
            }
            if !item.media.isEmpty {
                section("Screenshots & media") { MediaGallery(media: item.media, store: store) }
            }
            if !item.subagents.isEmpty {
                SubagentList(subagents: item.subagents, store: store)
            }
        }
    }

    private var metaRow: some View {
        let who = item.isAI ? (item.agentLabel ?? Agents.displayName(item.agent)) : (item.source == .voice ? "You (voice)" : "You (typed)")
        var parts = [who]
        if let machine = item.machine, !machine.isEmpty { parts.append("on \(machine)") }
        parts.append("started \(Fmt.clock(item.createdAt))")
        if let done = item.completedAt {
            parts.append("done \(Fmt.clock(done)) · \(Fmt.duration(done.timeIntervalSince(item.createdAt)))")
            if let by = item.completedBy, by != "user", by != item.agent { parts.append("closed by \(Agents.displayName(by))") }
        } else {
            parts.append("running \(Fmt.duration(store.now.timeIntervalSince(item.createdAt)))")
        }
        return VStack(alignment: .leading, spacing: 3) {
            Text(parts.joined(separator: " · "))
                .font(.system(size: 10.5, weight: .medium))
                .foregroundStyle(.white.opacity(0.55))
            if let ws = item.workspace, !ws.isEmpty {
                Button {
                    NSWorkspace.shared.open(URL(fileURLWithPath: ws))
                } label: {
                    Label((ws as NSString).abbreviatingWithTildeInPath, systemImage: "folder")
                        .font(.system(size: 10.5))
                        .foregroundStyle(.white.opacity(0.55))
                        .lineLimit(1)
                        .truncationMode(.middle)
                }
                .buttonStyle(.plain)
                .help("Open in Finder")
            }
        }
    }

    private func section<C: View>(_ title: String, @ViewBuilder content: () -> C) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(title.uppercased())
                .font(.system(size: 9.5, weight: .bold, design: .rounded))
                .tracking(0.6)
                .foregroundStyle(.white.opacity(0.45))
            content()
        }
    }
}

/// "Subagents being worked on" — small icons, small text, live status.
struct SubagentList: View {
    let subagents: [SubTask]
    let store: TrackerStore
    @State private var expanded: Set<String> = []

    var body: some View {
        let running = subagents.filter { $0.status == .active }.count
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Text("SUBAGENTS BEING WORKED ON")
                    .font(.system(size: 9.5, weight: .bold, design: .rounded))
                    .tracking(0.6)
                    .foregroundStyle(.white.opacity(0.45))
                Text(running > 0 ? "\(running) running · \(subagents.count - running) done" : "all \(subagents.count) done")
                    .font(.system(size: 9.5, weight: .medium))
                    .foregroundStyle(.white.opacity(0.4))
            }
            VStack(alignment: .leading, spacing: 4) {
                ForEach(subagents) { sub in row(sub) }
            }
            .padding(7)
            .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Color.white.opacity(0.05)))
        }
    }

    private func row(_ sub: SubTask) -> some View {
        let isOpen = expanded.contains(sub.id)
        return VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .center, spacing: 6) {
                AgentIconView(agent: sub.agent, size: 12)
                statusDot(sub)
                Text(sub.title)
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.white.opacity(sub.status == .done ? 0.6 : 0.92))
                    .strikethrough(sub.status == .done, color: .white.opacity(0.4))
                    .lineLimit(1)
                Spacer(minLength: 4)
                if let machine = sub.machine, machine != store.machineName {
                    Text(machine)
                        .font(.system(size: 9, weight: .medium))
                        .foregroundStyle(.white.opacity(0.4))
                        .lineLimit(1)
                        .frame(maxWidth: 80)
                        .help("Ran on \(machine)")
                }
                Text(sub.status == .done
                     ? Fmt.duration((sub.completedAt ?? sub.updatedAt).timeIntervalSince(sub.createdAt))
                     : Fmt.ago(sub.createdAt, now: store.now))
                    .font(.system(size: 9.5).monospacedDigit())
                    .foregroundStyle(.white.opacity(0.45))
                Image(systemName: isOpen ? "chevron.up" : "chevron.down")
                    .font(.system(size: 8, weight: .bold))
                    .foregroundStyle(.white.opacity(0.4))
            }
            .contentShape(Rectangle())
            .onTapGesture {
                withAnimation(.easeOut(duration: 0.15)) {
                    if isOpen { expanded.remove(sub.id) } else { expanded.insert(sub.id) }
                }
            }
            if !isOpen, !sub.details.isEmpty {
                Text(sub.details)
                    .font(.system(size: 10))
                    .foregroundStyle(.white.opacity(0.5))
                    .lineLimit(2)
                    .padding(.leading, 26)
            }
            if isOpen {
                VStack(alignment: .leading, spacing: 6) {
                    if !sub.details.isEmpty { MarkdownView(text: sub.details, store: store, fontSize: 10.5) }
                    ForEach(Array(sub.notes.enumerated()), id: \.offset) { _, note in
                        Text("\(Fmt.clock(note.at)) — \(note.text)")
                            .font(.system(size: 10)).foregroundStyle(.white.opacity(0.6))
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let summary = sub.summary, !summary.isEmpty {
                        MarkdownView(text: summary, store: store, fontSize: 10.5, color: .white.opacity(0.8))
                    }
                    if !sub.media.isEmpty { MediaGallery(media: sub.media, store: store, height: 54) }
                }
                .padding(.leading, 26)
            }
        }
        .padding(.vertical, 2)
    }

    private func statusDot(_ sub: SubTask) -> some View {
        Group {
            if sub.status == .done {
                Image(systemName: "checkmark.circle.fill").font(.system(size: 9)).foregroundStyle(Palette.ok)
            } else {
                PulsingDot()
            }
        }
        .frame(width: 9, height: 9)
    }
}

struct PulsingDot: View {
    @State private var on = false
    var body: some View {
        Circle().fill(Palette.warn)
            .frame(width: 7, height: 7)
            .opacity(on ? 1 : 0.35)
            .animation(.easeInOut(duration: 0.8).repeatForever(autoreverses: true), value: on)
            .onAppear { on = true }
            .help("Running")
    }
}

/// The card under the bubbles when one is clicked.
struct DetailCard: View {
    let item: TrackedItem
    let model: AppModel

    var body: some View {
        let color = Palette.color(item.colorIndex)
        VStack(alignment: .leading, spacing: 10) {
            DetailHeader(item: item) {
                BarButton(systemName: "macwindow.on.rectangle", help: "Open in a floating window") { model.popOut(item.id) }
                BarButton(systemName: "xmark", help: "Close details") { model.selectedID = nil }
            }
            CappedScroll(maxHeight: 380) {
                ItemDetailBody(item: item, store: model.store)
                    .padding(.trailing, 4)
            }
            if item.status == .active { MarkDoneRow(item: item, model: model) }
        }
        .padding(14)
        .background(Glass(cornerRadius: 16, tint: color, tintOpacity: 0.10))
        .overlay(alignment: .top) {
            Capsule().fill(color).frame(width: 46, height: 3).padding(.top, 1)
        }
    }
}

/// Icon, title and status line shared by the inline card and the floating window.
struct DetailHeader<Trailing: View>: View {
    let item: TrackedItem
    @ViewBuilder var trailing: Trailing

    var body: some View {
        let color = Palette.color(item.colorIndex)
        HStack(alignment: .top, spacing: 9) {
            SourceIcon(item: item, size: 22)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.title)
                    .font(.system(size: 15, weight: .bold, design: .rounded))
                    .foregroundStyle(.white)
                    .fixedSize(horizontal: false, vertical: true)
                Text(item.status == .done ? "Done" : (item.processing ? "Picking a title…" : "In progress"))
                    .font(.system(size: 10.5, weight: .semibold))
                    .foregroundStyle(item.status == .done ? Palette.ok : color)
            }
            Spacer(minLength: 6)
            trailing
        }
    }
}

extension DetailHeader where Trailing == EmptyView {
    init(item: TrackedItem) { self.init(item: item) { EmptyView() } }
}

struct MarkDoneRow: View {
    let item: TrackedItem
    let model: AppModel

    var body: some View {
        HStack {
            Spacer()
            Button {
                model.markDone(item.id)
            } label: {
                Label("Mark done", systemImage: "checkmark")
                    .font(.system(size: 11.5, weight: .semibold))
                    .foregroundStyle(.black)
                    .padding(.horizontal, 12).padding(.vertical, 5)
                    .background(Capsule().fill(Palette.ok))
            }
            .buttonStyle(.plain)
        }
    }
}

/// Content of a floating detail window: clear black glass like the panel, but more see-through.
/// Reads the item live, so new notes, results and subagents show up while it's open.
struct DetailWindowView: View {
    let id: String
    let model: AppModel
    var onClose: () -> Void
    /// Transparent edge around the glass so its shadow isn't clipped.
    static let margin: CGFloat = 10

    var body: some View {
        let item = model.store.item(id)
        let color = item.map { Palette.color($0.colorIndex) } ?? .white
        VStack(alignment: .leading, spacing: 10) {
            if let item {
                DetailHeader(item: item) {
                    BarButton(systemName: "xmark", help: "Close (Esc)", action: onClose)
                }
                .background(WindowDragHandle())
                ScrollView(.vertical) {
                    ItemDetailBody(item: item, store: model.store)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.trailing, 6)
                }
                if item.status == .active { MarkDoneRow(item: item, model: model) }
            } else {
                HStack {
                    Text("This item is no longer in the tracker.")
                        .font(.system(size: 12))
                        .foregroundStyle(.white.opacity(0.6))
                    Spacer()
                    BarButton(systemName: "xmark", help: "Close (Esc)", action: onClose)
                }
                .background(WindowDragHandle())
                Spacer(minLength: 0)
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(Glass(cornerRadius: 16, tint: color, tintOpacity: 0.06, clarity: 0.55))
        .overlay(alignment: .top) { Capsule().fill(color).frame(width: 46, height: 3).padding(.top, 1) }
        .overlay(alignment: .bottomTrailing) {
            ZStack {
                ResizeGrip()
                Image(systemName: "arrow.down.right")
                    .font(.system(size: 7.5, weight: .bold))
                    .foregroundStyle(.white.opacity(0.3))
                    .allowsHitTesting(false)
            }
            .frame(width: 16, height: 16)
            .padding(3)
            .help("Drag to resize")
        }
        .padding(Self.margin)
        .environment(\.colorScheme, .dark)
    }
}


/// Percent complete as a bar, then tokens and the share of the subscription the work used.
struct UsageSummary: View {
    let progress: Int?
    let usage: TokenUsage?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let progress {
                HStack(spacing: 8) {
                    ProgressView(value: Double(progress), total: 100)
                        .tint(progress >= 100 ? Palette.ok : .white.opacity(0.85))
                    Text("\(progress)%")
                        .font(.system(size: 11, weight: .bold).monospacedDigit())
                        .foregroundStyle(.white.opacity(0.85))
                }
            }
            if let usage {
                Text(Self.tokensLine(usage))
                    .font(.system(size: 11, weight: .medium).monospacedDigit())
                    .foregroundStyle(.white.opacity(0.8))
                    .fixedSize(horizontal: false, vertical: true)
                if let plan = Self.planLine(usage) {
                    Text(plan)
                        .font(.system(size: 11, weight: .semibold))
                        .foregroundStyle((usage.planPercent ?? 0) >= 5 ? Palette.warn : .white.opacity(0.7))
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
    }

    static func compact(_ n: Int) -> String {
        n >= 1_000_000 ? String(format: "%.1fM", Double(n) / 1_000_000) : n >= 1000 ? String(format: "%.1fk", Double(n) / 1000) : "\(n)"
    }

    static func tokensLine(_ u: TokenUsage) -> String {
        var parts = ["\(compact(u.totalTokens)) tokens"]
        var detail: [String] = []
        if let i = u.inputTokens { detail.append("\(compact(i)) in") }
        if let c = u.cachedTokens, c > 0 { detail.append("\(compact(c)) cached") }
        if let o = u.outputTokens { detail.append("\(compact(o)) out") }
        if let r = u.reasoningTokens, r > 0 { detail.append("\(compact(r)) reasoning") }
        if !detail.isEmpty { parts.append("(" + detail.joined(separator: ", ") + ")") }
        if let engine = u.engine { parts.append("· " + Agents.displayName(engine) + (u.model.map { " " + $0 } ?? "")) }
        return parts.joined(separator: " ")
    }

    static func planLine(_ u: TokenUsage) -> String? {
        var parts: [String] = []
        if let share = u.planPercent { parts.append(String(format: "%.1f%% of the %@plan", share, u.planPeriod.map { "\($0) " } ?? "")) }
        if let used = u.planUsedPercent { parts.append(String(format: "plan %.0f%% used", used)) }
        if let reset = u.planResetsAt { parts.append("resets " + Fmt.clock(reset)) }
        if let by = u.measuredBy { parts.append(by == "satellite" ? "measured" : "reported") }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
}
