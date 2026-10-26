import AVKit
import SwiftUI

// The Agent Satellite Tracker inside the app: every item any agent (or you) opened on any machine,
// with details, progress notes, screenshots, recordings, and subagents. Read-only, like the Mac panel.

/// How each agent looks everywhere: badges, live cards, job rows. Unknown agents get a stable colour.
enum AgentStyle {
    static func color(_ name: String) -> Color {
        switch key(name) {
        case "claude": return Color(red: 0.85, green: 0.47, blue: 0.34)
        case "codex", "chatgpt", "openai": return Color(red: 0.06, green: 0.64, blue: 0.50)
        case "grok": return Color(red: 0.86, green: 0.88, blue: 0.95)
        case "gemini": return Color(red: 0.26, green: 0.52, blue: 0.96)
        case "you", "user", "me": return OrbitalTheme.violet
        case "echo": return OrbitalTheme.textSecondary
        default: return OrbitalTheme.color(for: name)
        }
    }

    static func symbol(_ name: String) -> String {
        switch key(name) {
        case "claude": return "asterisk"
        case "codex", "chatgpt", "openai": return "circle.hexagongrid.fill"
        case "grok": return "bolt.fill"
        case "gemini": return "sparkle"
        case "you", "user", "me": return "person.fill"
        case "echo": return "text.bubble"
        default: return "cpu"
        }
    }

    /// "claude" reads "Claude", "codex" reads "Codex".
    static func displayName(_ name: String) -> String {
        let trimmed = name.trimmingCharacters(in: .whitespaces)
        guard let first = trimmed.first else { return "Agent" }
        if trimmed == trimmed.lowercased() { return first.uppercased() + trimmed.dropFirst() }
        return trimmed
    }

    private static func key(_ name: String) -> String {
        name.lowercased().split(separator: " ").first.map(String.init) ?? ""
    }
}

struct AgentBadge: View {
    let name: String
    var size: CGFloat = 30

    var body: some View {
        let tint = AgentStyle.color(name)
        ZStack {
            Circle().fill(tint.opacity(0.18))
            Circle().strokeBorder(tint.opacity(0.45), lineWidth: 1)
            Image(systemName: AgentStyle.symbol(name))
                .font(.system(size: size * 0.42, weight: .bold))
                .foregroundStyle(tint)
        }
        .frame(width: size, height: size)
        .accessibilityLabel(name)
    }
}

/// A tracker picture through the shared image cache, keyed so re-signed URLs still hit.
struct TrackerImage: View {
    let src: String
    var key: String?
    var contentMode: ContentMode = .fill
    @EnvironmentObject private var store: OrbitalStore
    @State private var image: UIImage?
    @State private var failed = false

    var body: some View {
        if contentMode == .fill {
            // Fill takes the frame it is given and crops, instead of growing past it.
            Color.clear
                .overlay { content }
                .clipped()
        } else {
            content
        }
    }

    private var content: some View {
        ZStack {
            if let image {
                Image(uiImage: image)
                    .resizable()
                    .aspectRatio(contentMode: contentMode)
            } else {
                LinearGradient(colors: [OrbitalTheme.surfaceRaised, OrbitalTheme.surface], startPoint: .topLeading, endPoint: .bottomTrailing)
                if failed {
                    Image(systemName: "photo")
                        .foregroundStyle(OrbitalTheme.textTertiary)
                } else {
                    ProgressView().tint(OrbitalTheme.textTertiary)
                }
            }
        }
        .task(id: key ?? src) {
            if let key, let hit = OrbitalImageLoader.cached(key) {
                image = hit
                return
            }
            image = await OrbitalImageLoader.shared.image(src: src, baseURL: store.baseURL, token: store.imageToken, key: key)
            failed = image == nil
        }
    }
}

// MARK: List

/// The Tracker, like the Mac panel. Live flashes only work still running. History is
/// everything that has finished, a day at a time. Search and All / AI / Mine narrow both.
struct TrackerListView: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var desk: DeskState
    @AppStorage("tracker.pane") private var paneRaw = TrackerPane.live.rawValue
    @AppStorage("tracker.scope") private var scopeRaw = TrackerScope.today.rawValue
    @AppStorage("tracker.who") private var whoRaw = TrackerWho.all.rawValue
    @State private var query = ""
    @State private var items: [TrackerSummary] = []
    /// Live items' estimated % (task list or subagents), for those the agent has not given one.
    @State private var estimates: [String: TrackerProgress] = [:]
    @State private var total = 0
    @State private var done = 0
    /// How far the server page has been read, so Show more stays aligned when a row is dropped.
    @State private var serverCount = 0
    @State private var loading = false
    @State private var error: String?
    @State private var reload: Task<Void, Never>?
    @State private var showVoiceSettings = false
    @StateObject private var blink = TrackerBlinkClock()
    @ObservedObject private var speech = SpeechSettings.shared

    private var pane: TrackerPane { TrackerPane(rawValue: paneRaw) ?? .live }
    private var scope: TrackerScope { TrackerScope(rawValue: scopeRaw) ?? .today }
    private var who: TrackerWho { TrackerWho(rawValue: whoRaw) ?? .all }
    /// History never asks for the live scope. A stored "Now" falls back to today.
    private var historyScope: TrackerScope {
        TrackerPane.historyScopes.contains(scope) ? scope : .today
    }
    private var requestScope: TrackerScope { pane == .live ? .active : historyScope }
    /// What this pane may show. Hides the other pane's rows while a switch is still loading.
    private var visibleItems: [TrackerSummary] {
        items.filter { pane == .live ? $0.isActive : !$0.isActive }
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                OrbitalScreenHeader(eyebrow: "Agent Satellite Tracker", title: "Tracker", subtitle: headline)
                    .padding(.bottom, 4)
                announceRow
                evidenceRow
                Picker("Pane", selection: $paneRaw) {
                    ForEach(TrackerPane.allCases) { Text($0.title).tag($0.rawValue) }
                }
                .pickerStyle(.segmented)
                .accessibilityLabel("Live or history")
                searchField
                if pane == .history {
                    Picker("Range", selection: $scopeRaw) {
                        ForEach(TrackerPane.historyScopes) { Text($0.title).tag($0.rawValue) }
                    }
                    .pickerStyle(.segmented)
                }
                HStack(spacing: 8) {
                    ForEach(TrackerWho.allCases) { option in
                        Button {
                            whoRaw = option.rawValue
                        } label: {
                            Text(option.title)
                                .font(.footnote.weight(.semibold))
                                .padding(.horizontal, 14)
                                .frame(minHeight: 32)
                                .foregroundStyle(who == option ? OrbitalTheme.backgroundDeep : OrbitalTheme.textSecondary)
                                .background(who == option ? OrbitalTheme.accent : OrbitalTheme.surfaceRaised, in: Capsule())
                        }
                        .buttonStyle(.plain)
                    }
                    Spacer()
                    if loading { ProgressView().controlSize(.small) }
                }
                if let error {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.warning)
                }
                if visibleItems.isEmpty, !loading, error == nil {
                    Text(emptyText)
                        .font(.callout)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .multilineTextAlignment(.center)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 40)
                }
                if pane == .live {
                    ForEach(visibleItems) { item in
                        NavigationLink(value: BrowseRoute.trackerItem(item.id)) {
                            TrackerLiveCard(item: item, estimate: estimates[item.id])
                        }
                        .buttonStyle(OrbitalPressStyle())
                    }
                } else {
                    ForEach(TrackerGroup.make(visibleItems)) { group in
                        groupHeader(group)
                        ForEach(group.items) { item in
                            NavigationLink(value: BrowseRoute.trackerItem(item.id)) {
                                TrackerRow(item: item)
                            }
                            .buttonStyle(OrbitalPressStyle())
                        }
                    }
                }
                if serverCount < total {
                    Button {
                        Task { await load(reset: false) }
                    } label: {
                        Text("Show more (\(total - serverCount))")
                            .font(.footnote.weight(.semibold))
                            .frame(maxWidth: .infinity, minHeight: 44)
                    }
                    .buttonStyle(.plain)
                    .foregroundStyle(OrbitalTheme.accent)
                }
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
        }
        .scrollDismissesKeyboard(.interactively)
        .background(OrbitalTheme.background.ignoresSafeArea())
        .environmentObject(blink)
        .sheet(isPresented: $showVoiceSettings) {
            DeskVoiceSettingsView()
                .environmentObject(desk)
        }
        .navigationTitle("Tracker")
        .toolbar(.hidden, for: .navigationBar)
        .refreshable {
            await desk.refreshTrackerSettings()
            await load(reset: true)
        }
        .task { await desk.refreshTrackerSettings() }
        .task(id: "\(paneRaw)|\(requestScope.rawValue)|\(whoRaw)|\(query)") {
            // Typing waits for a pause; a cancelled wait means another key came.
            if !query.isEmpty {
                try? await Task.sleep(nanoseconds: 300_000_000)
                guard !Task.isCancelled else { return }
            }
            await load(reset: true)
        }
        .onChange(of: paneRaw) {
            if pane == .history, scope == .active { scopeRaw = TrackerScope.today.rawValue }
        }
        .onChange(of: desk.trackerRevision) {
            reload?.cancel()
            reload = Task {
                try? await Task.sleep(nanoseconds: 600_000_000)
                guard !Task.isCancelled else { return }
                await load(reset: true, quiet: true)
            }
        }
    }

    /// The toggle that reads finished items out loud, in the Desk tab's voice.
    private var announceRow: some View {
        let on = desk.announceTrackerDone
        return HStack(spacing: 12) {
            Image(systemName: on ? "speaker.wave.2.fill" : "speaker.slash.fill")
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(on ? OrbitalTheme.success : OrbitalTheme.textTertiary)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 2) {
                Text("Say when something finishes")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
                Button {
                    showVoiceSettings = true
                } label: {
                    HStack(spacing: 3) {
                        Text("Voice: \(voiceLabel)")
                        Image(systemName: "chevron.right").font(.caption2.weight(.bold))
                    }
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.accent)
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Voice settings, \(voiceLabel)")
            }
            Spacer(minLength: 8)
            Toggle("Say when something finishes", isOn: $desk.announceTrackerDone)
                .labelsHidden()
                .tint(OrbitalTheme.success)
        }
        .orbitalCard(padding: 12, tint: on ? OrbitalTheme.success : nil)
    }

    /// Off / Min 1 / All: the screenshots and videos every AI agent attaches for UI, website and game
    /// work. The same switch as every Mac tracker's camera button; changing one changes all.
    private var evidenceRow: some View {
        let level = desk.trackerEvidence
        let on = level != .off
        return VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 12) {
                Image(systemName: level.symbol)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(on ? OrbitalTheme.accent : OrbitalTheme.textTertiary)
                    .frame(width: 24)
                    .padding(.top, 1)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Agent screenshots")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                    Text(level.blurb)
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                Spacer(minLength: 8)
                if desk.trackerEvidenceSaving { ProgressView().controlSize(.small) }
            }
            Picker("Agent screenshots", selection: Binding(get: { desk.trackerEvidence }, set: { desk.setTrackerEvidence($0) })) {
                ForEach(TrackerEvidence.allCases) { Text($0.title).tag($0) }
            }
            .pickerStyle(.segmented)
            if let error = desk.trackerEvidenceError {
                Label(error, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.warning)
            } else {
                Text(evidenceFootnote)
                    .font(.caption2)
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
        }
        .orbitalCard(padding: 12, tint: on ? OrbitalTheme.accent : nil)
    }

    private var evidenceFootnote: String {
        var text = "For UI, website and game work. Synced with every Mac running the tracker."
        switch desk.trackerEvidenceBy {
        case "tracker": text += " Last set on a Mac."
        case "cli": text += " Last set from the command line."
        default: break
        }
        return text
    }

    /// The voice set in Voice settings (shared with the Desk tab).
    private var voiceLabel: String {
        switch speech.ttsProvider {
        case .fishAudio:
            let id = speech.voiceId(for: speech.speechLanguage)
            let name = speech.voiceName(for: id)
            return id.isEmpty || name == id ? "Fish" : "Fish · \(name)"
        case .appleOnDevice: return "Built-in"
        case .azureSpeech: return "Azure"
        }
    }

    /// "2 working" on Live, "14 done today" on History.
    private var headline: String {
        if pane == .live {
            return "\(total) working"
        }
        let range: String
        switch historyScope {
        case .today: range = "today"
        case .week: range = "this week"
        case .month: range = "this month"
        default: range = "in all"
        }
        return "\(done) done \(range)"
    }

    private var emptyText: String {
        let trimmed = query.trimmingCharacters(in: .whitespaces)
        if !trimmed.isEmpty { return "Nothing matches “\(trimmed)”." }
        if pane == .live { return "Nothing being worked on right now." }
        switch historyScope {
        case .today: return "Nothing finished yet today."
        case .week: return "Nothing finished this week yet."
        case .month: return "Nothing finished this month yet."
        default: return "No finished items yet."
        }
    }

    private var searchField: some View {
        HStack(spacing: 8) {
            Image(systemName: "magnifyingglass")
                .font(.footnote.weight(.semibold))
                .foregroundStyle(OrbitalTheme.textTertiary)
            TextField("Search titles, notes, results, projects", text: $query)
                .textFieldStyle(.plain)
                .font(.subheadline)
                .foregroundStyle(OrbitalTheme.text)
                .autocorrectionDisabled()
                .textInputAutocapitalization(.never)
                .submitLabel(.search)
            if !query.isEmpty {
                Button {
                    query = ""
                } label: {
                    Image(systemName: "xmark.circle.fill")
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .frame(width: 32, height: 32)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityLabel("Clear search")
            }
        }
        .padding(.horizontal, 12)
        .frame(minHeight: 44)
        .background(OrbitalTheme.backgroundDeep.opacity(0.8), in: Capsule())
        .overlay(Capsule().strokeBorder(OrbitalTheme.hairline, lineWidth: 1))
    }

    private func groupHeader(_ group: TrackerGroup) -> some View {
        HStack(spacing: 6) {
            if group.working { TrackerLiveDot() }
            OrbitalEyebrow(text: group.title, color: group.working ? OrbitalTheme.success : OrbitalTheme.textTertiary)
            Spacer()
            Text("\(group.items.count)")
                .font(.caption2.weight(.bold).monospacedDigit())
                .foregroundStyle(OrbitalTheme.textTertiary)
        }
        .padding(.top, 10)
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }

    private func load(reset: Bool, quiet: Bool = false) async {
        let api = DeskAPI(baseURL: store.baseURL, token: store.imageToken)
        let live = pane == .live
        if !quiet { loading = true }
        defer { loading = false }
        do {
            let shown = max(items.count, serverCount)
            let offset = reset ? 0 : serverCount
            let limit = reset ? max(40, quiet ? shown : 40) : 40
            let page = try await api.trackerPage(
                scope: live ? .active : historyScope,
                who: who,
                query: query,
                limit: limit,
                offset: offset,
                finishedOnly: !live
            )
            guard !Task.isCancelled else { return }
            // A finished item never stays on Live, and a running one never lands in History,
            // even if an older server still mixes the two.
            let rows = page.items.filter { live ? $0.isActive : !$0.isActive }
            items = reset ? rows : items + rows.filter { row in !items.contains { $0.id == row.id } }
            serverCount = offset + page.items.count
            total = page.total
            done = page.done ?? rows.filter { !$0.isActive }.count
            error = nil
        } catch {
            // A newer filter or search replaced this load.
            if Task.isCancelled || (error as? URLError)?.code == .cancelled || error is CancellationError { return }
            self.error = (error as? OrbitalAPIError)?.shortReason ?? error.localizedDescription
            return
        }
        loading = false
        if live { await refreshEstimates(api) }
    }

    /// A list row carries only the agent's own %. A live item without one reads its task list and
    /// subagents for an estimate, the way the Mac tracker does. A failed read keeps the last one.
    private func refreshEstimates(_ api: DeskAPI) async {
        var next: [String: TrackerProgress] = [:]
        for item in items where item.isActive && item.progress == nil && !item.hubSentProgress {
            guard !Task.isCancelled else { return }
            if let detail = try? await api.trackerItem(item.id) {
                if let shown = detail.shownProgress { next[item.id] = shown }
            } else if let old = estimates[item.id] {
                next[item.id] = old
            }
        }
        guard !Task.isCancelled else { return }
        estimates = next
    }
}

/// One heading on the Tracker list: everything still being worked on, or one day of finished items.
struct TrackerGroup: Identifiable {
    let id: String
    let title: String
    let working: Bool
    var items: [TrackerSummary]

    /// Working items first (the server sends them first), then finished ones by the day they were last touched.
    static func make(_ items: [TrackerSummary], now: Date = Date()) -> [TrackerGroup] {
        var groups: [TrackerGroup] = []
        let calendar = Calendar.current
        for item in items {
            let key: String
            let title: String
            if item.isActive {
                key = "working"
                title = "Working now"
            } else if let date = DeskTime.parse(item.completedAt ?? item.updatedAt ?? item.createdAt) {
                let day = calendar.startOfDay(for: date)
                key = "day-\(Int(day.timeIntervalSince1970))"
                title = "Done · " + dayTitle(day, now: now)
            } else {
                key = "undated"
                title = "Done"
            }
            if let index = groups.firstIndex(where: { $0.id == key }) {
                groups[index].items.append(item)
            } else {
                groups.append(TrackerGroup(id: key, title: title, working: item.isActive, items: [item]))
            }
        }
        return groups
    }

    /// "Today", "Yesterday", "Thursday", "Sat, Oct 3", "Dec 30, 2025".
    static func dayTitle(_ day: Date, now: Date = Date()) -> String {
        let calendar = Calendar.current
        if calendar.isDateInToday(day) { return "Today" }
        if calendar.isDateInYesterday(day) { return "Yesterday" }
        let formatter = DateFormatter()
        let days = calendar.dateComponents([.day], from: day, to: calendar.startOfDay(for: now)).day ?? 0
        if days < 7 {
            formatter.dateFormat = "EEEE"
        } else if calendar.component(.year, from: day) == calendar.component(.year, from: now) {
            formatter.dateFormat = "EEE, MMM d"
        } else {
            formatter.dateFormat = "MMM d, yyyy"
        }
        return formatter.string(from: day)
    }
}

/// The green dot on a working item. It breathes, like the Mac tracker's blinking bubbles.
struct TrackerLiveDot: View {
    var size: CGFloat = 7
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var dim = false

    var body: some View {
        Circle()
            .fill(OrbitalTheme.success)
            .frame(width: size, height: size)
            .opacity(dim ? 0.3 : 1)
            .onAppear {
                guard !reduceMotion else { return }
                withAnimation(.easeInOut(duration: 0.9).repeatForever(autoreverses: true)) { dim = true }
            }
            .accessibilityHidden(true)
    }
}

/// The Mac bubble colors, in the same order, so a phone card matches the panel.
enum TrackerPalette {
    static let bubbles: [Color] = [
        Color(red: 1.00, green: 0.42, blue: 0.42),
        Color(red: 0.20, green: 0.74, blue: 0.80),
        Color(red: 1.00, green: 0.65, blue: 0.20),
        Color(red: 0.55, green: 0.42, blue: 1.00),
        Color(red: 0.36, green: 0.80, blue: 0.45),
        Color(red: 0.95, green: 0.40, blue: 0.78),
        Color(red: 0.30, green: 0.58, blue: 1.00),
        Color(red: 0.88, green: 0.76, blue: 0.24),
    ]

    static func color(_ index: Int) -> Color {
        let count = bubbles.count
        return bubbles[((index % count) + count) % count]
    }
}

/// One clock for every live card, so they dim together the way the Mac bubbles do.
@MainActor
final class TrackerBlinkClock: ObservableObject {
    @Published var dim = false
    private var timer: Timer?

    init() {
        let timer = Timer(timeInterval: 0.8, repeats: true) { [weak self] _ in
            Task { @MainActor in self?.dim.toggle() }
        }
        RunLoop.main.add(timer, forMode: .common)
        self.timer = timer
    }
}

/// Dims every live card together, the way the Mac bubbles blink while work is running.
struct TrackerBlinkOverlay: View {
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @EnvironmentObject private var clock: TrackerBlinkClock

    var body: some View {
        Rectangle()
            .fill(Color.black.opacity(!reduceMotion && clock.dim ? 0.45 : 0))
            .animation(reduceMotion ? nil : .easeInOut(duration: 0.2), value: clock.dim)
            .allowsHitTesting(false)
            .accessibilityHidden(true)
    }
}

/// A tracker progress bar: a thin rounded track, a fill that slides to each new figure, and the
/// percent at its right ("~40%" for an estimate). `onColor` draws it in white for a coloured card.
struct TrackerProgressBar: View {
    let progress: TrackerProgress
    var onColor = false
    var tint: Color = OrbitalTheme.accent
    var height: CGFloat = 5
    var showsLabel = true
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private var track: Color { onColor ? .white.opacity(0.25) : .white.opacity(0.08) }
    private var fill: Color {
        if onColor { return .white.opacity(0.9) }
        return progress.percent >= 100 ? OrbitalTheme.success : tint
    }

    var body: some View {
        HStack(spacing: 8) {
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(track)
                    Capsule().fill(fill)
                        .frame(width: max(height, geo.size.width * CGFloat(min(max(progress.percent, 0), 100)) / 100))
                }
            }
            .frame(height: height)
            .animation(reduceMotion ? nil : .spring(response: 0.5, dampingFraction: 0.85), value: progress.percent)
            if showsLabel {
                Text(progress.label)
                    .font(.caption2.weight(.bold).monospacedDigit())
                    .foregroundStyle(onColor ? .white : OrbitalTheme.textSecondary)
                    .contentTransition(.numericText())
                    .animation(reduceMotion ? nil : .default, value: progress.percent)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Progress")
        .accessibilityValue(progress.spoken)
    }
}

/// One running item: who, title, and the latest note, on the same color as its Mac bubble.
struct TrackerLiveCard: View {
    let item: TrackerSummary
    /// The task list or subagents' estimate, when the agent has not sent its own % yet.
    var estimate: TrackerProgress?

    private var color: Color { TrackerPalette.color(item.colorIndex ?? 0) }
    private var progress: TrackerProgress? { item.shownProgress ?? estimate }

    private var who: String {
        var parts = [item.agentLabel ?? item.agentName]
        if let origin = item.shownOrigin { parts.append(origin) }
        if let model = item.shownModel { parts.append(model) }
        if let machine = item.machine, !machine.isEmpty { parts.append(machine) }
        if let project = item.project, !project.isEmpty { parts.append(project) }
        return parts.joined(separator: " · ")
    }

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            AgentBadge(name: item.agentName, size: 34)
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(who)
                        .font(.caption.weight(.semibold))
                        .lineLimit(1)
                    Spacer(minLength: 4)
                    if item.processing == true {
                        ProgressView().controlSize(.small).tint(.white)
                    }
                    Text(DeskTime.short(item.updatedAt))
                        .font(.caption2.monospacedDigit())
                }
                .foregroundStyle(.white.opacity(0.82))
                Text(item.title)
                    .font(.subheadline.weight(.bold))
                    .foregroundStyle(.white)
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                Text(item.lastNote ?? "Just started")
                    .font(.footnote)
                    .foregroundStyle(.white.opacity(item.lastNote == nil ? 0.65 : 0.92))
                    .lineLimit(2)
                    .multilineTextAlignment(.leading)
                if let progress {
                    TrackerProgressBar(progress: progress, onColor: true)
                        .padding(.top, 2)
                } else {
                    Text("No % yet")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.white.opacity(0.6))
                }
                if let usage = item.usage {
                    Text(usage.short)
                        .font(.caption2.weight(.semibold).monospacedDigit())
                        .foregroundStyle(.white.opacity(0.8))
                }
                if item.mediaCount > 0 || (item.subagents?.total ?? 0) > 0 {
                    HStack(spacing: 8) {
                        if let subs = item.subagents, subs.total > 0 {
                            meta(subs.active > 0 ? "\(subs.active)" : "\(subs.total)", system: "person.2.fill")
                        }
                        if item.mediaCount > 0 {
                            meta("\(item.mediaCount)", system: item.videoCount > 0 ? "play.rectangle.fill" : "photo.fill")
                        }
                    }
                }
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            LinearGradient(colors: [color.opacity(0.95), color.opacity(0.72)], startPoint: .topLeading, endPoint: .bottomTrailing)
        )
        .overlay { TrackerBlinkOverlay() }
        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 18, style: .continuous)
                .strokeBorder(Color.white.opacity(0.35), lineWidth: 1)
        )
        .shadow(color: color.opacity(0.35), radius: 8, y: 4)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(who). \(item.title). \(item.lastNote ?? "Just started"). Working. \(progress.map { "Progress \($0.spoken)." } ?? "No percent yet.")")
    }

    private func meta(_ text: String, system: String) -> some View {
        Label(text, systemImage: system)
            .font(.caption2.weight(.bold))
            .foregroundStyle(.white)
            .padding(.horizontal, 7)
            .padding(.vertical, 3)
            .background(Capsule().fill(Color.black.opacity(0.28)))
            .labelStyle(.titleAndIcon)
    }
}

struct TrackerRow: View {
    let item: TrackerSummary

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            AgentBadge(name: item.agentName, size: 34)
            VStack(alignment: .leading, spacing: 4) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(item.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                        .lineLimit(2)
                    Spacer(minLength: 4)
                    Text(DeskTime.short(item.updatedAt))
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .lineLimit(1)
                if let line = item.isActive ? item.lastNote : (item.summary ?? item.lastNote) {
                    Text(line)
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .lineLimit(2)
                }
                if item.isActive, let progress = item.shownProgress {
                    TrackerProgressBar(progress: progress, tint: AgentStyle.color(item.agentName))
                }
                if let usage = item.usage {
                    UsageLine(usage: usage)
                }
                HStack(spacing: 10) {
                    status
                    if item.mediaCount > 0 {
                        Label("\(item.mediaCount)", systemImage: item.videoCount > 0 ? "play.rectangle.fill" : "photo.fill")
                    }
                    if let subs = item.subagents, subs.total > 0 {
                        Label("\(subs.total)", systemImage: "person.2.fill")
                    }
                }
                .font(.caption2.weight(.semibold))
                .foregroundStyle(OrbitalTheme.textTertiary)
                .labelStyle(.titleAndIcon)
            }
            if let cover = item.cover, let thumb = cover.thumbURL {
                TrackerImage(src: thumb, key: "tracker-\(cover.id)-thumb")
                    .frame(width: 56, height: 56)
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .overlay(alignment: .center) {
                        if cover.isVideo {
                            Image(systemName: "play.circle.fill")
                                .font(.title3)
                                .foregroundStyle(.white.opacity(0.9))
                                .shadow(radius: 3)
                        }
                    }
            }
        }
        .orbitalCard(padding: 14, tint: item.isActive ? AgentStyle.color(item.agentName) : nil)
    }

    private var subtitle: String {
        var parts = [item.agentLabel ?? item.agentName]
        if let origin = item.shownOrigin { parts.append(origin) }
        if let model = item.shownModel { parts.append(model) }
        if let machine = item.machine, !machine.isEmpty { parts.append(machine) }
        if let project = item.project { parts.append(project) }
        return parts.joined(separator: " · ")
    }

    @ViewBuilder
    private var status: some View {
        if item.isActive {
            HStack(spacing: 4) {
                TrackerLiveDot(size: 6)
                Text(item.processing == true ? "Naming…" : "Working")
            }
            .foregroundStyle(OrbitalTheme.success)
        } else {
            Label("Done", systemImage: "checkmark")
        }
    }
}

// MARK: Item

struct TrackerItemView: View {
    let itemID: String
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var desk: DeskState
    @State private var item: TrackerDetail?
    @State private var error: String?
    @State private var viewer: MediaSelection?

    struct MediaSelection: Identifiable {
        let id: String
        let media: [TrackerMedia]
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let item {
                    header(item)
                    if let task = item.deskTask { DeskTaskPanel(task: task) }
                    if let summary = item.summary, !summary.isEmpty {
                        section("Result") { DeskMarkdown(text: summary, onImage: openImage) }
                    }
                    if !item.media.isEmpty {
                        section("Screenshots & video") { MediaGrid(media: item.media) { open($0, in: item.media) } }
                    }
                    if !item.details.isEmpty {
                        section("Details") { DeskMarkdown(text: item.details, onImage: openImage) }
                    }
                    if !item.notes.isEmpty {
                        section("Progress notes") { NotesTimeline(notes: item.notes) }
                    }
                    if !item.subagents.isEmpty {
                        section("Subagents") {
                            VStack(spacing: 10) {
                                ForEach(item.subagents) { sub in
                                    SubagentCard(sub: sub) { open($0, in: sub.media) }
                                }
                            }
                        }
                    }
                    if let transcript = item.transcript, !transcript.isEmpty {
                        section("What you said") {
                            Text(transcript)
                                .font(.callout.italic())
                                .foregroundStyle(OrbitalTheme.textSecondary)
                        }
                    }
                } else if let error {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(OrbitalTheme.warning)
                        .padding(.top, 40)
                } else {
                    ProgressView().frame(maxWidth: .infinity).padding(.top, 60)
                }
            }
            .padding(16)
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
        }
        .background(OrbitalTheme.background.ignoresSafeArea())
        .navigationTitle(item?.title ?? "Tracker")
        .navigationBarTitleDisplayMode(.inline)
        .refreshable { await load() }
        .task(id: itemID) { await load() }
        .onChange(of: desk.trackerRevision) { Task { await load() } }
        .onChange(of: desk.tasks[item?.deskTask?.id ?? ""]) { Task { await load() } }
        .fullScreenCover(item: $viewer) { selection in
            TrackerMediaViewer(media: selection.media, selection: selection.id)
                .environmentObject(store)
        }
    }

    private func load() async {
        do {
            item = try await DeskAPI(baseURL: store.baseURL, token: store.imageToken).trackerItem(itemID)
            error = nil
        } catch {
            if item == nil { self.error = (error as? OrbitalAPIError)?.shortReason ?? error.localizedDescription }
        }
    }

    private func open(_ media: TrackerMedia, in list: [TrackerMedia]) {
        viewer = MediaSelection(id: media.id, media: list)
    }

    private func openImage(_ url: String) {
        let media = TrackerMedia(id: url, kind: "image", fullURL: url)
        viewer = MediaSelection(id: url, media: [media])
    }

    private func header(_ item: TrackerDetail) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                AgentBadge(name: item.agentName, size: 40)
                VStack(alignment: .leading, spacing: 2) {
                    Text(item.agentLabel ?? item.agentName)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                    Text([item.machine, item.project, DeskTime.clock(item.createdAt)].compactMap { $0 }.filter { !$0.isEmpty }.joined(separator: " · "))
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
                Spacer()
                if item.isActive {
                    OrbitalChip(text: "Working", tint: OrbitalTheme.success)
                } else {
                    OrbitalChip(text: "Done", tint: OrbitalTheme.textSecondary)
                }
            }
            Text(item.title)
                .font(.title2.weight(.bold))
                .foregroundStyle(OrbitalTheme.text)
            if item.shownOrigin != nil || item.shownModel != nil {
                HStack(spacing: 8) {
                    if let origin = item.shownOrigin {
                        OrbitalChip(text: origin, tint: OrbitalTheme.accent)
                    }
                    if let model = item.shownModel {
                        Text(model)
                            .font(.caption.weight(.semibold).monospaced())
                            .foregroundStyle(OrbitalTheme.textSecondary)
                            .lineLimit(1)
                    }
                }
            }
            if let done = item.completedAt {
                Text("Finished \(DeskTime.clock(done))")
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            progressRow(item)
            if let usage = item.usage {
                UsageLine(usage: usage, showsBreakdown: true)
            }
        }
    }

    /// The % in full: a wide bar, the figure, and where it comes from.
    private func progressRow(_ item: TrackerDetail) -> some View {
        let shown = item.shownProgress
        let caption = shown?.caption ?? TrackerProgress.waitingCaption
        return VStack(alignment: .leading, spacing: 6) {
            HStack(alignment: .firstTextBaseline) {
                Text("Progress")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.textSecondary)
                Spacer()
                Text(shown?.label ?? "No % yet")
                    .font((shown == nil ? Font.caption : .subheadline).weight(.bold).monospacedDigit())
                    .foregroundStyle(shown == nil ? OrbitalTheme.textTertiary : OrbitalTheme.text)
            }
            if let shown {
                TrackerProgressBar(progress: shown, tint: TrackerPalette.color(item.colorIndex ?? 0), height: 8, showsLabel: false)
            } else {
                Capsule().fill(Color.white.opacity(0.08)).frame(height: 8)
            }
            Text(caption.prefix(1).uppercased() + caption.dropFirst())
                .font(.caption2)
                .foregroundStyle(OrbitalTheme.textTertiary)
        }
        .padding(.top, 2)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Progress")
        .accessibilityValue(shown.map { $0.isEstimate ? $0.spoken : "\($0.spoken), \($0.caption)" } ?? "No percent yet, \(caption)")
    }

    private func section<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            OrbitalEyebrow(text: title)
            content()
        }
        .orbitalCard(padding: 14)
    }
}

struct NotesTimeline: View {
    let notes: [TrackerNote]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(notes.enumerated().reversed()), id: \.offset) { index, note in
                HStack(alignment: .top, spacing: 10) {
                    Circle()
                        .fill(index == notes.count - 1 ? OrbitalTheme.accent : OrbitalTheme.hairline)
                        .frame(width: 8, height: 8)
                        .padding(.top, 5)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(note.text)
                            .font(.callout)
                            .foregroundStyle(OrbitalTheme.text)
                            .textSelection(.enabled)
                        Text(DeskTime.clock(note.at))
                            .font(.caption2)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                    }
                }
            }
        }
    }
}

struct SubagentCard: View {
    let sub: TrackerSub
    let onMedia: (TrackerMedia) -> Void
    @State private var open = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                withAnimation(.easeInOut(duration: 0.2)) { open.toggle() }
            } label: {
                HStack(spacing: 10) {
                    AgentBadge(name: sub.agentName, size: 26)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(sub.title)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(OrbitalTheme.text)
                        Text(sub.status == "active" ? "Working · \(sub.notes.last?.text ?? "started")" : "Done")
                            .font(.caption)
                            .foregroundStyle(sub.status == "active" ? OrbitalTheme.success : OrbitalTheme.textTertiary)
                            .lineLimit(1)
                        if let progress = sub.shownProgress {
                            TrackerProgressBar(progress: progress, tint: AgentStyle.color(sub.agentName), height: 4)
                                .padding(.top, 2)
                        }
                        if let usage = sub.usage {
                            UsageLine(usage: usage)
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    Image(systemName: "chevron.down")
                        .font(.caption.weight(.bold))
                        .rotationEffect(.degrees(open ? 180 : 0))
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if open {
                if let summary = sub.summary, !summary.isEmpty { DeskMarkdown(text: summary) }
                if let details = sub.details, !details.isEmpty { DeskMarkdown(text: details, color: OrbitalTheme.textSecondary) }
                if !sub.media.isEmpty { MediaGrid(media: sub.media, onTap: onMedia) }
                if !sub.notes.isEmpty { NotesTimeline(notes: sub.notes) }
            }
        }
        .padding(12)
        .background(OrbitalTheme.backgroundDeep.opacity(0.5), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
    }
}

struct MediaGrid: View {
    let media: [TrackerMedia]
    let onTap: (TrackerMedia) -> Void

    var body: some View {
        LazyVGrid(columns: [GridItem(.adaptive(minimum: 100), spacing: 8)], spacing: 8) {
            ForEach(media) { item in
                Button {
                    onTap(item)
                } label: {
                    ZStack(alignment: .bottomLeading) {
                        if let thumb = item.thumbURL {
                            TrackerImage(src: thumb, key: "tracker-\(item.id)-thumb")
                        } else {
                            OrbitalTheme.surfaceRaised
                            Image(systemName: item.isVideo ? "film" : "photo")
                                .foregroundStyle(OrbitalTheme.textTertiary)
                                .frame(maxWidth: .infinity, maxHeight: .infinity)
                        }
                        if item.isVideo {
                            HStack(spacing: 4) {
                                Image(systemName: item.streamURL == nil ? "film.stack" : "play.fill")
                                if let duration = item.duration {
                                    Text(Self.duration(duration))
                                }
                            }
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(.white)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 3)
                            .background(.black.opacity(0.6), in: Capsule())
                            .padding(6)
                        }
                    }
                    .frame(height: 100)
                    .frame(maxWidth: .infinity)
                    .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(Color.white.opacity(0.08)))
                }
                .buttonStyle(OrbitalPressStyle())
                .accessibilityLabel(item.caption ?? item.fileName ?? (item.isVideo ? "Video" : "Image"))
            }
        }
    }

    static func duration(_ seconds: Double) -> String {
        let total = Int(seconds.rounded())
        return String(format: "%d:%02d", total / 60, total % 60)
    }
}

// MARK: Viewer

struct TrackerMediaViewer: View {
    let media: [TrackerMedia]
    @State var selection: String
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        ZStack(alignment: .topLeading) {
            Color.black.ignoresSafeArea()
            TabView(selection: $selection) {
                ForEach(media) { item in
                    page(item).tag(item.id)
                }
            }
            .tabViewStyle(.page(indexDisplayMode: media.count > 1 ? .automatic : .never))
            Button {
                dismiss()
            } label: {
                Image(systemName: "xmark")
                    .font(.headline.weight(.bold))
                    .foregroundStyle(.white)
                    .frame(width: 44, height: 44)
                    .background(.black.opacity(0.5), in: Circle())
            }
            .padding(16)
            .accessibilityLabel("Close")
        }
    }

    @ViewBuilder
    private func page(_ item: TrackerMedia) -> some View {
        if item.isVideo, let stream = item.streamURL {
            TrackerVideoPage(path: stream, active: selection == item.id)
        } else if item.isVideo {
            VStack(spacing: 12) {
                if let thumb = item.thumbURL {
                    TrackerImage(src: thumb, key: "tracker-\(item.id)-thumb", contentMode: .fit)
                }
                Text("The recording itself is gone from the machine. This is the contact sheet the tracker kept.")
                    .font(.footnote)
                    .foregroundStyle(.white.opacity(0.7))
                    .multilineTextAlignment(.center)
                    .padding(.horizontal, 32)
            }
        } else if let src = item.fullURL ?? item.thumbURL {
            ZoomableTrackerImage(src: src, key: item.fullURL == nil ? "tracker-\(item.id)-thumb" : "tracker-\(item.id)-full")
        }
    }
}

/// Streams the original recording through the hub over Tailscale; the hub answers Range requests.
struct TrackerVideoPage: View {
    let path: String
    let active: Bool
    @EnvironmentObject private var store: OrbitalStore
    @State private var player: AVPlayer?

    var body: some View {
        VideoPlayer(player: player)
            .onAppear {
                guard player == nil, let url = DeskAPI(baseURL: store.baseURL, token: nil).url(path) else { return }
                let next = AVPlayer(url: url)
                player = next
                if active { next.play() }
            }
            .onChange(of: active) { _, isActive in
                if isActive { player?.play() } else { player?.pause() }
            }
            .onDisappear { player?.pause() }
    }
}

struct ZoomableTrackerImage: View {
    let src: String
    let key: String
    @State private var scale: CGFloat = 1
    @State private var base: CGFloat = 1
    @State private var offset: CGSize = .zero
    @State private var dragBase: CGSize = .zero

    var body: some View {
        TrackerImage(src: src, key: key, contentMode: .fit)
            .scaleEffect(scale)
            .offset(offset)
            .gesture(
                MagnifyGesture()
                    .onChanged { scale = max(1, min(base * $0.magnification, 6)) }
                    .onEnded { _ in
                        base = scale
                        if scale == 1 { offset = .zero; dragBase = .zero }
                    }
                    .simultaneously(
                        with: DragGesture()
                            .onChanged { value in
                                guard scale > 1 else { return }
                                offset = CGSize(width: dragBase.width + value.translation.width, height: dragBase.height + value.translation.height)
                            }
                            .onEnded { _ in dragBase = offset }
                    )
            )
            .onTapGesture(count: 2) {
                withAnimation(.spring(response: 0.3)) {
                    if scale > 1 {
                        scale = 1; base = 1; offset = .zero; dragBase = .zero
                    } else {
                        scale = 2.5; base = 2.5
                    }
                }
            }
    }
}
