import SwiftUI
import UIKit

// The Agents screen: every machine that joined the hub (`GET /v1/satellites`), the agent jobs they run
// (`GET /v1/agents/jobs`), one machine, one job with its log and Stop, and Run agent
// (`POST /v1/satellites/{id}/agents/run`). Live socket events keep it current; it also polls while open.

// MARK: - Agents

struct AgentsView: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @Environment(\.orbitalPushed) private var pushed
    @AppStorage("orbital.agents.filter") private var filterRaw = JobFilter.all.rawValue

    enum JobFilter: String, CaseIterable, Identifiable {
        case all, running, failed
        var id: String { rawValue }
        var title: String {
            switch self {
            case .all: return "All"
            case .running: return "Running"
            case .failed: return "Failed"
            }
        }
    }

    private var filter: JobFilter { JobFilter(rawValue: filterRaw) ?? .all }

    private var jobs: [AgentJob] {
        switch filter {
        case .all: return store.agentJobs
        case .running: return store.agentJobs.filter(\.isActive)
        case .failed: return store.agentJobs.filter(\.isFailed)
        }
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                OrbitalScreenHeader(eyebrow: store.systemName, title: "Agents", subtitle: subtitle) {
                    ShellHeaderButtons()
                }
                OrbitalActionButton(title: "Run an agent", symbol: "play.fill", kind: .primary) {
                    router.runAgent()
                }
                .disabled(store.onlineSatellites.isEmpty)
                .opacity(store.onlineSatellites.isEmpty ? 0.55 : 1)
                if let error = store.agentsError {
                    Label(error, systemImage: "exclamationmark.triangle.fill")
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.warning)
                        .fixedSize(horizontal: false, vertical: true)
                        .orbitalCard(padding: 12, tint: OrbitalTheme.warning)
                }
                machines
                jobsSection
            }
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, 36)
        }
        .scrollIndicators(.hidden)
        .orbitalScrollFade()
        .refreshable { await store.refreshAgents() }
        .orbitalBackdrop(OrbitalTheme.accent)
        .navigationTitle("Agents")
        .orbitalNavigationBar(pushed: pushed)
        .task {
            // The live socket pushes changes; this keeps the screen right if the socket is down.
            while !Task.isCancelled {
                await store.refreshAgents()
                try? await Task.sleep(nanoseconds: (store.activeAgentJobs.isEmpty ? 30 : 8) * 1_000_000_000)
            }
        }
    }

    private var subtitle: String {
        guard store.agentsLoaded || !store.satellites.isEmpty else { return "Looking for machines…" }
        var parts = ["\(store.onlineSatellites.count) of \(store.satellites.count) \(store.satellites.count == 1 ? "machine" : "machines") online"]
        let running = store.activeAgentJobs.count
        if running > 0 { parts.append("\(running) running") }
        return parts.joined(separator: " · ")
    }

    @ViewBuilder
    private var machines: some View {
        VStack(alignment: .leading, spacing: 10) {
            OrbitalEyebrow(text: "Machines")
            if store.satellites.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    Text(store.agentsLoaded ? "No machines yet" : "Looking for machines…")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                    Text("The hub runs a satellite of its own. To add another computer, run ./setup there and choose satellite; it finds the hub over Tailscale. `orbital satellites invite` on the hub prints the join token.")
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .orbitalCard(padding: 14)
            } else {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 300), spacing: 10)], spacing: 10) {
                    ForEach(store.satellites) { machine in
                        Button {
                            router.push(.satellite(machine.id))
                        } label: {
                            SatelliteCard(machine: machine, running: store.agentJobs.filter { $0.isActive && store.satellite(for: $0)?.id == machine.id }.count)
                        }
                        .buttonStyle(OrbitalPressStyle())
                    }
                }
            }
        }
    }

    private var jobsSection: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                OrbitalEyebrow(text: "Jobs")
                Spacer(minLength: 0)
            }
            Picker("Jobs", selection: $filterRaw) {
                ForEach(JobFilter.allCases) { Text($0.title).tag($0.rawValue) }
            }
            .pickerStyle(.segmented)
            if jobs.isEmpty {
                Text(emptyText)
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .orbitalCard(padding: 14)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(jobs.prefix(60).enumerated()), id: \.element.id) { index, job in
                        if index > 0 {
                            Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                        }
                        Button {
                            router.push(.agentJob(job.id))
                        } label: {
                            AgentJobRow(job: job)
                        }
                        .buttonStyle(OrbitalPressStyle())
                    }
                }
                .orbitalCard(padding: 14)
            }
        }
    }

    private var emptyText: String {
        switch filter {
        case .all: return "No agent jobs yet. Run one with the button above, from the desk, or with `orbital agents run`."
        case .running: return "Nothing running right now."
        case .failed: return "No failed jobs."
        }
    }
}

// MARK: - Machine pieces

/// A small online/offline chip for a machine, used on Home.
struct SatelliteChip: View {
    let machine: Satellite
    var running = 0

    var body: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(machine.isOnline ? OrbitalTheme.success : OrbitalTheme.textTertiary)
                .frame(width: 7, height: 7)
            Image(systemName: machine.symbol)
                .font(.caption2.weight(.bold))
                .foregroundStyle(OrbitalTheme.textSecondary)
            Text(machine.name)
                .font(.caption.weight(.semibold))
                .foregroundStyle(machine.isOnline ? OrbitalTheme.text : OrbitalTheme.textTertiary)
                .lineLimit(1)
            if running > 0 {
                Text("\(running)")
                    .font(.caption2.weight(.heavy).monospacedDigit())
                    .foregroundStyle(OrbitalTheme.backgroundDeep)
                    .padding(.horizontal, 5)
                    .frame(minHeight: 16)
                    .background(OrbitalTheme.accent, in: Capsule())
            }
        }
        .padding(.horizontal, 10)
        .frame(minHeight: 32)
        .background(Color.white.opacity(0.05), in: Capsule())
        .overlay(Capsule().strokeBorder(Color.white.opacity(0.08), lineWidth: 1))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(machine.name), \(machine.isOnline ? "online" : "offline")\(running > 0 ? ", \(running) running" : "")")
    }
}

struct SatelliteCard: View {
    let machine: Satellite
    var running = 0

    var body: some View {
        let tint = machine.isOnline ? OrbitalTheme.success : OrbitalTheme.textTertiary
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                IconTile(symbol: machine.symbol, tint: machine.isOnline ? OrbitalTheme.accent : OrbitalTheme.textTertiary, size: 38)
                VStack(alignment: .leading, spacing: 2) {
                    HStack(spacing: 6) {
                        Text(machine.name)
                            .font(.headline)
                            .foregroundStyle(OrbitalTheme.text)
                            .lineLimit(1)
                        if machine.isHub {
                            OrbitalChip(text: "Hub", tint: OrbitalTheme.violet)
                        }
                    }
                    Text(detailLine)
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
                VStack(alignment: .trailing, spacing: 2) {
                    HStack(spacing: 5) {
                        Circle().fill(tint).frame(width: 7, height: 7)
                        Text(machine.isOnline ? "Online" : "Offline")
                            .font(.caption.weight(.bold))
                            .foregroundStyle(tint)
                    }
                    if running > 0 {
                        Text("\(running) running")
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(OrbitalTheme.accent)
                    }
                }
            }
            FlowLayout(spacing: 6, lineSpacing: 6) {
                ForEach(machine.capabilities.agents) { agent in
                    OrbitalChip(text: agent.title, symbol: AgentStyle.symbol(agent.name), tint: agent.available ? AgentStyle.color(agent.name) : OrbitalTheme.textTertiary)
                        .opacity(agent.available ? 1 : 0.6)
                }
                if machine.capabilities.browser {
                    OrbitalChip(text: "Browser", symbol: "globe", tint: OrbitalTheme.textSecondary)
                }
                if machine.capabilities.tracker {
                    OrbitalChip(text: "Tracker", symbol: "dot.radiowaves.left.and.right", tint: OrbitalTheme.textSecondary)
                }
                if machine.capabilities.agents.isEmpty {
                    OrbitalChip(text: "No agents found", symbol: "exclamationmark.circle", tint: OrbitalTheme.warning)
                }
            }
        }
        .orbitalCard(padding: 14, tint: machine.isOnline ? nil : OrbitalTheme.textTertiary)
        .opacity(machine.isOnline ? 1 : 0.8)
    }

    private var detailLine: String {
        var parts: [String] = []
        if let os = machine.osLabel { parts.append(os) }
        if machine.isOnline {
            if let seen = OrbitalDates.relative(machine.lastSeen) { parts.append("seen \(seen)") }
        } else {
            parts.append("last seen \(OrbitalDates.relative(machine.lastSeen) ?? "never")")
        }
        return parts.joined(separator: " · ")
    }
}

// MARK: - Job row

struct AgentJobRow: View {
    let job: AgentJob
    var compact = false
    @EnvironmentObject private var store: OrbitalStore

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            AgentBadge(name: job.agent, size: compact ? 28 : 32)
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(job.shownTitle)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                        .lineLimit(compact ? 1 : 2)
                        .multilineTextAlignment(.leading)
                    Spacer(minLength: 4)
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        Text(clock(context.date))
                            .font(.caption2.weight(.semibold).monospacedDigit())
                            .foregroundStyle(job.isActive ? OrbitalTheme.accent : OrbitalTheme.textTertiary)
                    }
                }
                HStack(spacing: 6) {
                    Image(systemName: job.statusSymbol)
                        .font(.system(size: 10, weight: .bold))
                        .foregroundStyle(job.tint)
                        .symbolEffect(.pulse, isActive: job.isRunning)
                    Text(metaLine)
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .lineLimit(1)
                }
                if !compact, let tail = lastLine {
                    Text(tail)
                        .font(.caption2.monospaced())
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .lineLimit(2)
                        .multilineTextAlignment(.leading)
                }
            }
        }
        .padding(.vertical, compact ? 6 : 9)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
    }

    private var metaLine: String {
        var parts = [job.statusWord, AgentStyle.displayName(job.agent)]
        if let machine = store.satellite(for: job)?.name ?? job.machine { parts.append(machine) }
        return parts.joined(separator: " · ")
    }

    private var lastLine: String? {
        guard let tail = job.outputTail else { return nil }
        return tail.split(whereSeparator: \.isNewline).last.map(String.init).flatMap(Insights.nonEmpty)
    }

    private func clock(_ now: Date) -> String {
        if job.isActive, let duration = job.duration(now: now) { return JobFormat.elapsed(since: now.addingTimeInterval(-duration), now: now) }
        if let finished = job.finishedAt { return OrbitalDates.relative(finished) ?? "" }
        return OrbitalDates.relative(job.createdAt) ?? ""
    }
}

// MARK: - One machine

struct SatelliteView: View {
    let satelliteID: String
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @Environment(\.dismiss) private var dismiss
    @State private var confirmRemove = false

    private var machine: Satellite? { store.satellite(satelliteID) }

    private var jobs: [AgentJob] {
        guard let machine else { return [] }
        return store.agentJobs.filter { store.satellite(for: $0)?.id == machine.id }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let machine {
                    header(machine)
                    OrbitalActionButton(title: "Run an agent here", symbol: "play.fill", kind: .primary) {
                        router.runAgent(AgentRunDraft(machine: machine.id))
                    }
                    .disabled(!machine.isOnline || machine.availableAgents.isEmpty)
                    .opacity(!machine.isOnline || machine.availableAgents.isEmpty ? 0.55 : 1)
                    facts(machine)
                    agents(machine)
                    jobsCard
                    OrbitalActionButton(title: "Remove from the hub", symbol: "trash", kind: .destructive) {
                        confirmRemove = true
                    }
                    Text("Removing only forgets the machine here. Its satellite joins again on its next start unless you stop it there (`orbital services uninstall satellite`).")
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                } else {
                    Text("This machine is no longer joined to the hub.")
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .frame(maxWidth: .infinity)
                        .padding(.top, 60)
                }
            }
            .padding(16)
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
        }
        .scrollIndicators(.hidden)
        .refreshable { await store.refreshAgents() }
        .orbitalBackdrop(machine?.isOnline == true ? OrbitalTheme.accent : OrbitalTheme.textTertiary)
        .navigationTitle(machine?.name ?? "Machine")
        .orbitalNavigationBar(pushed: true)
        .confirmationDialog("Remove \(machine?.name ?? "this machine")?", isPresented: $confirmRemove, titleVisibility: .visible) {
            Button("Remove", role: .destructive) {
                guard let machine else { return }
                Task {
                    await store.removeSatellite(machine)
                    dismiss()
                }
            }
            Button("Cancel", role: .cancel) {}
        }
    }

    private func header(_ machine: Satellite) -> some View {
        HStack(spacing: 14) {
            IconTile(symbol: machine.symbol, tint: machine.isOnline ? OrbitalTheme.accent : OrbitalTheme.textTertiary, size: 56, filled: machine.isOnline)
            VStack(alignment: .leading, spacing: 4) {
                OrbitalEyebrow(text: machine.isHub ? "Hub machine" : "Satellite", color: machine.isOnline ? OrbitalTheme.success : OrbitalTheme.textTertiary)
                Text(machine.name)
                    .font(.title2.weight(.bold))
                    .foregroundStyle(OrbitalTheme.text)
                HStack(spacing: 6) {
                    Circle().fill(machine.isOnline ? OrbitalTheme.success : OrbitalTheme.textTertiary).frame(width: 7, height: 7)
                    Text(machine.isOnline ? "Online" : "Offline · last seen \(OrbitalDates.relative(machine.lastSeen) ?? "never")")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(machine.isOnline ? OrbitalTheme.success : OrbitalTheme.textSecondary)
                }
            }
            Spacer(minLength: 0)
        }
    }

    private func facts(_ machine: Satellite) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            ForEach(Array(rows(machine).enumerated()), id: \.offset) { index, row in
                if index > 0 {
                    Rectangle().fill(OrbitalTheme.hairline.opacity(0.7)).frame(height: 1)
                }
                HStack(alignment: .firstTextBaseline) {
                    Text(row.0)
                        .font(.subheadline)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                    Spacer(minLength: 12)
                    Text(row.1)
                        .font(.subheadline.weight(.semibold).monospacedDigit())
                        .foregroundStyle(OrbitalTheme.text)
                        .multilineTextAlignment(.trailing)
                        .textSelection(.enabled)
                }
                .padding(.vertical, 10)
            }
        }
        .orbitalCard(padding: 16)
    }

    private func rows(_ machine: Satellite) -> [(String, String)] {
        var out: [(String, String)] = []
        if let value = machine.machine, value != machine.name { out.append(("Machine", value)) }
        if let os = machine.osLabel { out.append(("System", os)) }
        if let host = machine.host { out.append(("Host", host)) }
        if let ip = machine.ip { out.append(("Address", machine.port.map { "\(ip):\($0)" } ?? ip)) }
        if let version = machine.version { out.append(("Version", version)) }
        if let joined = OrbitalDates.shortDateTime(machine.joinedAt) { out.append(("Joined", joined)) }
        if let seen = OrbitalDates.relative(machine.lastSeen) { out.append(("Last heartbeat", seen)) }
        out.append(("Browser control", machine.capabilities.browser ? "Yes" : "No"))
        out.append(("Tracker", machine.capabilities.tracker ? "Yes" : "No"))
        return out
    }

    private func agents(_ machine: Satellite) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            OrbitalEyebrow(text: "Agents on this machine")
            if machine.capabilities.agents.isEmpty {
                Text("None found. An agent shows here once its command (agents.<name>.command in config.toml) is on that machine's PATH.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            } else {
                ForEach(machine.capabilities.agents) { agent in
                    HStack(spacing: 10) {
                        AgentBadge(name: agent.name, size: 28)
                        Text(agent.title)
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(OrbitalTheme.text)
                        Spacer(minLength: 0)
                        OrbitalChip(text: agent.available ? "Ready" : "Not installed", symbol: agent.available ? "checkmark" : "xmark",
                                    tint: agent.available ? OrbitalTheme.success : OrbitalTheme.textTertiary)
                    }
                }
            }
        }
        .orbitalCard(padding: 14)
    }

    @ViewBuilder
    private var jobsCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            OrbitalEyebrow(text: "Jobs here")
            if jobs.isEmpty {
                Text("No jobs on this machine yet.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
            } else {
                VStack(spacing: 0) {
                    ForEach(Array(jobs.prefix(20).enumerated()), id: \.element.id) { index, job in
                        if index > 0 {
                            Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                        }
                        Button {
                            router.push(.agentJob(job.id))
                        } label: {
                            AgentJobRow(job: job)
                        }
                        .buttonStyle(OrbitalPressStyle())
                    }
                }
            }
        }
        .orbitalCard(padding: 14)
    }
}

// MARK: - One job

struct AgentJobView: View {
    let jobID: String
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @State private var log: [String]?
    @State private var logError: String?
    @State private var loadingLog = false
    @State private var showPrompt = false
    @State private var confirmStop = false
    @State private var followTail = true

    private var job: AgentJob? { store.agentJobs.first { $0.id == jobID } }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                if let job {
                    header(job)
                    actions(job)
                    if let prompt = Insights.nonEmpty(job.prompt) {
                        promptCard(prompt)
                    }
                    outputCard(job)
                } else {
                    VStack(spacing: 10) {
                        ProgressView()
                        Text("Looking for this job on the hub…")
                            .font(.footnote)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                    }
                    .frame(maxWidth: .infinity)
                    .padding(.top, 60)
                }
            }
            .padding(16)
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
        }
        .scrollIndicators(.hidden)
        .refreshable {
            await store.refreshAgents()
            await loadLog()
        }
        .orbitalBackdrop(job.map { AgentStyle.color($0.agent) } ?? OrbitalTheme.accent)
        .navigationTitle(job?.shownTitle ?? "Job")
        .orbitalNavigationBar(pushed: true)
        .task(id: jobID) {
            if job == nil { await store.refreshAgents() }
            // Follow a running job: its status and output every few seconds until it finishes.
            while !Task.isCancelled {
                if let job {
                    _ = await store.agentJobDetail(job)
                    await loadLog()
                }
                guard self.job?.isActive == true else { break }
                try? await Task.sleep(nanoseconds: 3_000_000_000)
            }
        }
        .confirmationDialog("Stop this job?", isPresented: $confirmStop, titleVisibility: .visible) {
            Button("Stop", role: .destructive) {
                guard let job else { return }
                Task { await store.cancelAgentJob(job) }
            }
            Button("Keep running", role: .cancel) {}
        } message: {
            Text("The agent is stopped on its machine. Work it already did stays.")
        }
    }

    private func header(_ job: AgentJob) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 12) {
                AgentBadge(name: job.agent, size: 44)
                VStack(alignment: .leading, spacing: 3) {
                    Text(AgentStyle.displayName(job.agent))
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                    Text([store.satellite(for: job)?.name ?? job.machine, job.cwd].compactMap { $0 }.joined(separator: " · "))
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .lineLimit(2)
                        .textSelection(.enabled)
                }
                Spacer(minLength: 0)
                OrbitalChip(text: job.statusWord, symbol: job.statusSymbol, tint: job.tint)
            }
            Text(job.shownTitle)
                .font(.title3.weight(.bold))
                .foregroundStyle(OrbitalTheme.text)
                .fixedSize(horizontal: false, vertical: true)
            TimelineView(.periodic(from: .now, by: 1)) { context in
                HStack(spacing: 14) {
                    if let started = job.startedDate {
                        fact("Started", started.formatted(date: .omitted, time: .shortened))
                    }
                    if let duration = job.duration(now: context.date) {
                        fact(job.isActive ? "Running for" : "Took", JobFormat.duration(duration) ?? "—")
                    }
                    if let code = job.exitCode {
                        fact("Exit code", "\(code)")
                    }
                }
            }
            if let error = Insights.nonEmpty(job.error) {
                Label(error, systemImage: "exclamationmark.triangle.fill")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.danger)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .orbitalCard(padding: 16, tint: job.tint)
    }

    private func fact(_ label: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label.uppercased())
                .font(.system(size: 9.5, weight: .bold))
                .tracking(0.5)
                .foregroundStyle(OrbitalTheme.textTertiary)
            Text(value)
                .font(.subheadline.weight(.bold).monospacedDigit())
                .foregroundStyle(OrbitalTheme.text)
        }
    }

    private func actions(_ job: AgentJob) -> some View {
        HStack(spacing: 10) {
            if job.isActive {
                OrbitalActionButton(title: "Stop", symbol: "stop.fill", kind: .destructive) {
                    confirmStop = true
                }
            } else {
                OrbitalActionButton(title: "Run again", symbol: "arrow.clockwise", kind: .secondary) {
                    router.runAgent(AgentRunDraft(machine: job.satelliteID ?? store.satellite(for: job)?.id, agent: job.agent,
                                                  prompt: job.prompt ?? "", cwd: job.cwd ?? "", title: job.title ?? ""))
                }
            }
            if let tracker = job.trackerID {
                OrbitalActionButton(title: "Tracker", symbol: "dot.radiowaves.left.and.right", kind: .secondary) {
                    router.openTracker(item: tracker)
                }
            }
        }
    }

    private func promptCard(_ prompt: String) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                OrbitalEyebrow(text: "Prompt")
                Spacer(minLength: 0)
                Button {
                    UIPasteboard.general.string = prompt
                    store.showToast("Prompt copied")
                } label: {
                    Image(systemName: "doc.on.doc")
                        .font(.footnote.weight(.semibold))
                        .frame(width: 36, height: 36)
                }
                .accessibilityLabel("Copy the prompt")
            }
            Text(prompt)
                .font(.callout)
                .foregroundStyle(OrbitalTheme.text.opacity(0.92))
                .lineLimit(showPrompt ? nil : 6)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            if prompt.count > 300 || prompt.filter(\.isNewline).count > 5 {
                Button(showPrompt ? "Show less" : "Show the whole prompt") {
                    withAnimation(.easeInOut(duration: 0.2)) { showPrompt.toggle() }
                }
                .font(.footnote.weight(.semibold))
            }
        }
        .orbitalCard(padding: 14)
    }

    private func outputCard(_ job: AgentJob) -> some View {
        let lines = log ?? (job.outputTail ?? "").components(separatedBy: "\n")
        let shown = Array(lines.suffix(400))
        return VStack(alignment: .leading, spacing: 10) {
            HStack {
                OrbitalEyebrow(text: log == nil ? "Output (last lines)" : "Output")
                Spacer(minLength: 0)
                if loadingLog { ProgressView().controlSize(.small) }
                Button("Reload") { Task { await loadLog() } }
                    .font(.caption.weight(.semibold))
                    .frame(minHeight: 44)
            }
            if let logError {
                Text(logError)
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.warning)
            }
            if shown.allSatisfy({ $0.trimmingCharacters(in: .whitespaces).isEmpty }) {
                Text(job.isActive ? "Waiting for output…" : "No output.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
            } else {
                ScrollViewReader { proxy in
                    // Lines wrap: a phone is narrow, and a sideways-scrolled log hides the start of every line.
                    ScrollView(.vertical, showsIndicators: true) {
                        VStack(alignment: .leading, spacing: 0) {
                            Text(shown.joined(separator: "\n"))
                                .font(.system(size: 11, design: .monospaced))
                                .foregroundStyle(OrbitalTheme.textSecondary)
                                .textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(10)
                            Color.clear.frame(height: 1).id("end")
                        }
                    }
                    .frame(maxHeight: 420)
                    .background(Color.black.opacity(0.35), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    .onChange(of: shown.count) {
                        if followTail { proxy.scrollTo("end", anchor: .bottomLeading) }
                    }
                    .onAppear { proxy.scrollTo("end", anchor: .bottomLeading) }
                }
                Toggle("Follow new output", isOn: $followTail)
                    .font(.caption)
                    .tint(OrbitalTheme.accent)
            }
        }
        .orbitalCard(padding: 14)
    }

    private func loadLog() async {
        guard let job else { return }
        loadingLog = true
        defer { loadingLog = false }
        switch await store.agentJobLog(job) {
        case .success(let lines):
            log = lines
            logError = nil
        case .failure(let error):
            if log == nil { logError = "The full log isn't available: \(OrbitalStore.reason(error)). Showing the last lines the hub has." }
        }
    }
}

// MARK: - Run agent

/// Pick a machine and one of its agents, write the prompt, optionally a working folder, and run.
struct RunAgentSheet: View {
    let draft: AgentRunDraft
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @Environment(\.dismiss) private var dismiss
    @State private var machineID = ""
    @State private var agent = ""
    @State private var prompt = ""
    @State private var cwd = ""
    @State private var title = ""
    @State private var running = false
    @FocusState private var promptFocused: Bool

    private var machine: Satellite? { store.satellite(machineID) }
    private var agents: [SatelliteAgent] { machine?.availableAgents ?? [] }

    private var canRun: Bool {
        !running && machine?.isOnline == true && agents.contains { $0.name == agent }
            && !prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Picker("Machine", selection: $machineID) {
                        if machineID.isEmpty { Text("Choose").tag("") }
                        ForEach(store.satellites) { machine in
                            Text(machine.isOnline ? machine.name : "\(machine.name) (offline)")
                                .tag(machine.id)
                        }
                    }
                    Picker("Agent", selection: $agent) {
                        if agent.isEmpty || !agents.contains(where: { $0.name == agent }) { Text("Choose").tag(agent) }
                        ForEach(agents) { agent in
                            Text(agent.title).tag(agent.name)
                        }
                    }
                    .disabled(agents.isEmpty)
                } footer: {
                    if let machine, !machine.isOnline {
                        Text("\(machine.name) is offline. Pick a machine that is online.")
                    } else if machine != nil, agents.isEmpty {
                        Text("No agent CLI is installed on this machine yet.")
                    } else {
                        Text("Only machines that are online and agents installed on them can run.")
                    }
                }
                .listRowBackground(rowBackground)

                Section {
                    TextEditor(text: $prompt)
                        .frame(minHeight: 140)
                        .scrollContentBackground(.hidden)
                        .focused($promptFocused)
                } header: {
                    Text("Prompt")
                }
                .listRowBackground(rowBackground)

                Section {
                    TextField("Working folder (optional), e.g. ~/code/my-app", text: $cwd)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .font(.body.monospaced())
                    TextField("Title (optional)", text: $title)
                } footer: {
                    Text("The folder must be inside one of the machine's allowed workdirs (satellite.workdirs). Empty uses its default. The title is what the tracker shows.")
                }
                .listRowBackground(rowBackground)
            }
            .scrollContentBackground(.hidden)
            .orbitalBackdrop(OrbitalTheme.accent)
            .navigationTitle("Run an agent")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    if running {
                        ProgressView()
                    } else {
                        Button("Run", action: run)
                            .fontWeight(.semibold)
                            .disabled(!canRun)
                    }
                }
            }
            .onAppear(perform: fill)
            .onChange(of: machineID) {
                if !agents.contains(where: { $0.name == agent }) {
                    agent = preferredAgent(on: machine) ?? ""
                }
            }
        }
        .preferredColorScheme(.dark)
        .presentationBackground(OrbitalTheme.background)
    }

    private var rowBackground: Color { OrbitalTheme.surfaceRaised.opacity(0.75) }

    private func fill() {
        prompt = draft.prompt
        cwd = draft.cwd
        title = draft.title
        let wanted = draft.machine.flatMap { store.satellite($0) }
        let pick = wanted ?? store.onlineSatellites.first { machine in
            draft.agent.map { name in machine.availableAgents.contains { $0.name == name } } ?? !machine.availableAgents.isEmpty
        } ?? store.onlineSatellites.first
        machineID = pick?.id ?? ""
        if let wantedAgent = draft.agent, pick?.availableAgents.contains(where: { $0.name == wantedAgent }) == true {
            agent = wantedAgent
        } else {
            agent = preferredAgent(on: pick) ?? ""
        }
        if prompt.isEmpty { promptFocused = true }
    }

    /// Claude when it is there (the hub's default worker), else the first agent the machine has.
    private func preferredAgent(on machine: Satellite?) -> String? {
        let names = machine?.availableAgents.map(\.name) ?? []
        return names.first { $0 == "claude" } ?? names.first { $0 != "echo" } ?? names.first
    }

    private func run() {
        guard canRun, let machine else { return }
        running = true
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        let request = AgentRunRequest(
            satelliteID: machine.id,
            agent: agent,
            prompt: prompt.trimmingCharacters(in: .whitespacesAndNewlines),
            cwd: cwd.trimmingCharacters(in: .whitespacesAndNewlines),
            title: title.trimmingCharacters(in: .whitespacesAndNewlines)
        )
        Task {
            let id = await store.runAgent(request)
            running = false
            guard let id else { return }
            dismiss()
            router.openAgents(route: .agentJob(id))
        }
    }
}
