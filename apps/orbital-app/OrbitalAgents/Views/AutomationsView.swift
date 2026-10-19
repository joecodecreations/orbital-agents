import SwiftUI

/// Every feed: how often it runs, a live countdown to the next run, what the last run did,
/// and buttons to run one feed, one business, or everything (in stages, one account at a time).
struct AutomationsView: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @Environment(\.orbitalPushed) private var pushed
    @State private var detail: JobSheetID?

    private var snapshot: JobsSnapshot? { store.jobs }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                OrbitalScreenHeader(
                    eyebrow: store.systemName,
                    title: "Automations",
                    subtitle: subtitle
                ) {
                    SchedulerPill(snapshot: snapshot)
                }
                if let snapshot {
                    if snapshot.scheduler.up == false {
                        schedulerDown(snapshot)
                    }
                    if let disk = snapshot.disk, disk.low == true {
                        diskLow(disk)
                    }
                    overview(snapshot)
                    SubscriptionUsageCard(report: store.usageReport, jobs: snapshot.jobs) { id in detail = JobSheetID(id: id) }
                    if let batch = snapshot.activeBatch {
                        BatchProgressCard(snapshot: snapshot, batch: batch)
                    }
                    lanes(snapshot)
                    ForEach(snapshot.groups) { group in
                        let rows = snapshot.jobs(in: group.id)
                        if !rows.isEmpty {
                            groupCard(group, rows: rows)
                        }
                    }
                    let grouped = Set(snapshot.groups.map(\.id))
                    let loose = snapshot.jobs.filter { !grouped.contains($0.group) }
                    if !loose.isEmpty {
                        groupCard(JobGroup(id: "other", title: "Other", icon: "square.stack.3d.up"), rows: loose)
                    }
                } else {
                    emptyState
                }
            }
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, 36)
        }
        .scrollIndicators(.hidden)
        .orbitalScrollFade()
        .refreshable { await store.refreshJobs() }
        .orbitalBackdrop(OrbitalTheme.accent)
        .navigationTitle("Automations")
        .orbitalNavigationBar(pushed: pushed)
        .task {
            // The live socket pushes every change; this keeps the page right if the socket is down.
            while !Task.isCancelled {
                await store.refreshJobs()
                try? await Task.sleep(nanoseconds: 20_000_000_000)
            }
        }
        .onAppear(perform: takeFocus)
        .onChange(of: router.navigationToken) { takeFocus() }
        .sheet(item: $detail) { item in
            JobDetailView(jobID: item.id)
                .environmentObject(store)
                .environmentObject(router)
        }
    }

    private var subtitle: String {
        guard let snapshot else { return "Loading the schedule…" }
        let total = snapshot.jobs.count
        let bad = snapshot.failing.count + snapshot.warning.count
        return bad == 0 ? "\(total) feeds, all working" : "\(total) feeds, \(bad) need a look"
    }

    private func takeFocus() {
        guard let id = router.focusedJobID else { return }
        router.focusedJobID = nil
        detail = JobSheetID(id: id)
    }

    // MARK: Cards

    private func overview(_ snapshot: JobsSnapshot) -> some View {
        let running = snapshot.jobs.filter(\.isRunning).count
        let queued = snapshot.jobs.filter(\.isQueued).count
        let ok = snapshot.jobs.filter { $0.health == .ok }.count
        return VStack(alignment: .leading, spacing: 14) {
            HStack(spacing: 10) {
                CountTile(value: ok, label: "Working", tint: OrbitalTheme.success)
                CountTile(value: snapshot.warning.count, label: "Partly", tint: OrbitalTheme.warning)
                CountTile(value: snapshot.failing.count, label: "Failing", tint: OrbitalTheme.danger)
                CountTile(value: running + queued, label: running > 0 ? "Updating" : "Queued", tint: OrbitalTheme.accent)
            }
            TimelineView(.periodic(from: .now, by: 1)) { context in
                if let next = snapshot.nextScheduled {
                    HStack(spacing: 6) {
                        Image(systemName: "timer")
                            .font(.caption.weight(.bold))
                        Text("Next: \(next.job.name) in \(JobFormat.countdown(to: next.at, now: context.date))")
                            .font(.footnote.weight(.medium))
                            .monospacedDigit()
                            .lineLimit(1)
                    }
                    .foregroundStyle(OrbitalTheme.textSecondary)
                }
            }
            OrbitalActionButton(
                title: snapshot.activeBatch == nil ? "Refresh everything" : "Refreshing everything…",
                symbol: "arrow.triangle.2.circlepath",
                busy: snapshot.activeBatch != nil
            ) {
                Task { await store.runAll() }
            }
            .disabled(snapshot.activeBatch != nil || snapshot.scheduler.up == false)
            Text("Runs every feed now, in stages: feeds that share a browser profile or account go one at a time, the rest side by side.")
                .font(.caption)
                .foregroundStyle(OrbitalTheme.textTertiary)
        }
        .orbitalCard(padding: 16)
    }

    private func schedulerDown(_ snapshot: JobsSnapshot) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "exclamationmark.octagon.fill")
                .foregroundStyle(OrbitalTheme.danger)
            VStack(alignment: .leading, spacing: 4) {
                Text("The scheduler is not running")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
                Text("No feed is updating. Its last heartbeat was \(OrbitalDates.relative(snapshot.scheduler.heartbeatAt) ?? "never"). The watchdog restarts it and raises an alert if it stays down; `orbital services restart scheduler` on the hub starts it by hand.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .orbitalCard(padding: 14, tint: OrbitalTheme.danger)
    }

    private func diskLow(_ disk: DiskStatus) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: "externaldrive.fill.badge.exclamationmark")
                .foregroundStyle(OrbitalTheme.danger)
            VStack(alignment: .leading, spacing: 4) {
                Text("The hub's disk is almost full: \(String(format: "%.1f", disk.freeGb ?? 0)) GB free")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
                Text("At zero, the hub can't save pages and every feed fails.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textSecondary)
                ForEach(disk.bigLogs ?? [], id: \.path) { log in
                    Text("\(log.path): \(String(format: "%.1f", log.gb)) GB")
                        .font(.caption.monospaced())
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .textSelection(.enabled)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .orbitalCard(padding: 14, tint: OrbitalTheme.danger)
    }

    @ViewBuilder
    private func lanes(_ snapshot: JobsSnapshot) -> some View {
        let chrome = snapshot.lanes.filter { $0.kind == "chrome" }
        if !chrome.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                OrbitalEyebrow(text: "Machines and browsers")
                ForEach(chrome) { lane in
                    LaneRow(lane: lane, busyName: lane.busyWith.flatMap { snapshot.job($0)?.name })
                    if lane.id != chrome.last?.id {
                        Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                    }
                }
            }
            .orbitalCard(padding: 14)
        }
    }

    private func groupCard(_ group: JobGroup, rows: [FeedJob]) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 8) {
                IconTile(symbol: group.icon ?? "square.stack.3d.up", tint: OrbitalTheme.accent, size: 26)
                Text(group.title)
                    .font(.headline)
                    .foregroundStyle(OrbitalTheme.text)
                Spacer(minLength: 0)
                Button {
                    Task { await store.runAll(group: group.id) }
                } label: {
                    Label("Refresh", systemImage: "arrow.clockwise")
                        .font(.caption.weight(.semibold))
                        .padding(.horizontal, 10)
                        .frame(minHeight: 30)
                        .background(OrbitalTheme.accent.opacity(0.12), in: Capsule())
                        .overlay(Capsule().strokeBorder(OrbitalTheme.accent.opacity(0.25), lineWidth: 1))
                        .foregroundStyle(OrbitalTheme.accent)
                        .frame(minHeight: 44)
                        .contentShape(Rectangle())
                }
                .buttonStyle(OrbitalPressStyle())
                .disabled(rows.allSatisfy(\.isBusy))
                .accessibilityLabel("Refresh every \(group.title) feed")
            }
            ForEach(rows) { job in
                JobRow(job: job) {
                    detail = JobSheetID(id: job.id)
                } onRun: {
                    Task { await store.runJob(job.id) }
                }
                if job.id != rows.last?.id {
                    Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                }
            }
        }
        .orbitalCard(padding: 14)
    }

    private var emptyState: some View {
        VStack(spacing: 10) {
            ProgressView()
            Text(store.connectionLabel == "Offline" ? "Can't reach the hub. Pull to try again." : "Loading the schedule…")
                .font(.footnote)
                .foregroundStyle(OrbitalTheme.textTertiary)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 60)
    }
}

struct JobSheetID: Identifiable, Equatable {
    let id: String
}

/// Scheduler running / down, from its heartbeat.
struct SchedulerPill: View {
    let snapshot: JobsSnapshot?

    var body: some View {
        let up = snapshot?.scheduler.up
        let label = up == nil ? "…" : (up == true ? "Running" : "Stopped")
        let color = up == nil ? OrbitalTheme.textTertiary : (up == true ? OrbitalTheme.success : OrbitalTheme.danger)
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 6, height: 6).shadow(color: color.opacity(0.8), radius: 3)
            Text(label).font(.caption2.weight(.semibold)).foregroundStyle(color)
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(color.opacity(0.12), in: Capsule())
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Scheduler \(label)")
    }
}

private struct CountTile: View {
    let value: Int
    let label: String
    let tint: Color

    var body: some View {
        VStack(spacing: 2) {
            Text("\(value)")
                .font(.title3.weight(.bold))
                .monospacedDigit()
                .foregroundStyle(value > 0 ? tint : OrbitalTheme.textTertiary)
            Text(label)
                .font(.caption2.weight(.semibold))
                .foregroundStyle(OrbitalTheme.textTertiary)
                .lineLimit(1)
                .minimumScaleFactor(0.8)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 10)
        .background(Color.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

private struct LaneRow: View {
    let lane: JobLane
    let busyName: String?

    var body: some View {
        let ok = lane.ok ?? false
        let tint = lane.ok == nil ? OrbitalTheme.textTertiary : (ok ? OrbitalTheme.success : OrbitalTheme.danger)
        HStack(alignment: .top, spacing: 10) {
            IconTile(symbol: "desktopcomputer", tint: tint, size: 30)
            VStack(alignment: .leading, spacing: 2) {
                Text(lane.title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
                if let machine = lane.machine {
                    Text(machine)
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                }
                Text(ok ? (busyName.map { "Reading now: \($0)" } ?? "Connected, idle") : (lane.detail ?? "Not checked yet"))
                    .font(.caption)
                    .foregroundStyle(ok ? OrbitalTheme.textTertiary : OrbitalTheme.danger)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
            OrbitalChip(text: ok ? "Online" : "Offline", symbol: ok ? "checkmark" : "xmark", tint: tint)
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }
}

/// One feed in a list: status, name, countdown, last result, and a run button.
struct JobRow: View {
    let job: FeedJob
    let onOpen: () -> Void
    let onRun: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Button(action: onOpen) {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: job.statusSymbol)
                        .font(.system(size: 15, weight: .semibold))
                        .foregroundStyle(job.isBusy ? OrbitalTheme.accent : job.tint)
                        .symbolEffect(.pulse, isActive: job.isRunning)
                        .frame(width: 22, height: 22)
                        .padding(.top, 1)
                    VStack(alignment: .leading, spacing: 3) {
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            Text(job.name)
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(OrbitalTheme.text)
                                .lineLimit(2)
                            JobKindChip(job: job)
                            Spacer(minLength: 4)
                            JobClock(job: job)
                        }
                        Text(scheduleLine)
                            .font(.caption)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .lineLimit(1)
                        if job.isAgent, let usage = job.usageLive ?? job.last?.usage {
                            UsageLine(usage: usage)
                        }
                        if let issue = job.last?.issues?.first, job.health != .ok {
                            Text(issue)
                                .font(.caption)
                                .foregroundStyle(job.tint)
                                .lineLimit(2)
                                .fixedSize(horizontal: false, vertical: true)
                        } else if let summary = job.last?.summary, !summary.isEmpty {
                            Text(summary)
                                .font(.caption)
                                .foregroundStyle(OrbitalTheme.textSecondary)
                                .lineLimit(2)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(OrbitalPressStyle())
            RunButton(busy: job.isBusy, action: onRun)
                .accessibilityLabel("Run \(job.name) now")
        }
        .padding(.vertical, 8)
    }

    private var scheduleLine: String {
        var parts = [job.paused ? "Paused" : JobFormat.every(job.everyS)]
        if let last = OrbitalDates.relative(job.last?.finishedAt) {
            parts.append("last \(last)")
        }
        if let seconds = JobFormat.duration(job.last?.durationS) {
            parts.append(seconds)
        }
        return parts.joined(separator: " · ")
    }
}

/// "in 12:04", "updating 0:23", "queued #2", "paused", ticking every second.
struct JobClock: View {
    let job: FeedJob

    var body: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            Text(text(context.date))
                .font(.caption.weight(.semibold))
                .monospacedDigit()
                .foregroundStyle(job.isBusy ? OrbitalTheme.accent : OrbitalTheme.textSecondary)
                .lineLimit(1)
        }
    }

    private func text(_ now: Date) -> String {
        if job.isRunning { return "updating \(JobFormat.elapsed(since: job.startedDate, now: now))" }
        if job.isQueued { return job.queuePosition.map { "queued #\($0)" } ?? "queued" }
        if job.paused { return "paused" }
        return "in \(JobFormat.countdown(to: job.nextRunDate, now: now))"
    }
}

struct RunButton: View {
    let busy: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            ZStack {
                Circle()
                    .fill(OrbitalTheme.accent.opacity(busy ? 0.06 : 0.14))
                    .overlay(Circle().strokeBorder(OrbitalTheme.accent.opacity(0.28), lineWidth: 1))
                if busy {
                    ProgressView().controlSize(.small).tint(OrbitalTheme.accent)
                } else {
                    Image(systemName: "arrow.clockwise")
                        .font(.system(size: 13, weight: .bold))
                        .foregroundStyle(OrbitalTheme.accent)
                }
            }
            .frame(width: 34, height: 34)
            .frame(width: 44, height: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(OrbitalPressStyle())
        .disabled(busy)
    }
}

/// Refresh-everything progress: which stage, how many done.
struct BatchProgressCard: View {
    let snapshot: JobsSnapshot
    let batch: RefreshBatch

    var body: some View {
        let done = snapshot.doneCount(batch)
        let total = max(batch.jobs.count, 1)
        let stage = snapshot.currentStage(batch)
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("Refreshing everything")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
                Spacer()
                Text("\(done) of \(batch.jobs.count) done")
                    .font(.caption.weight(.semibold))
                    .monospacedDigit()
                    .foregroundStyle(OrbitalTheme.accent)
            }
            ProgressView(value: Double(done), total: Double(total))
                .tint(OrbitalTheme.accent)
            ForEach(Array(batch.stages.enumerated()), id: \.offset) { index, ids in
                HStack(alignment: .top, spacing: 8) {
                    Text("Stage \(index + 1)")
                        .font(.caption2.weight(.bold))
                        .foregroundStyle(index + 1 == stage ? OrbitalTheme.accent : OrbitalTheme.textTertiary)
                        .frame(width: 54, alignment: .leading)
                    FlowLayout(spacing: 6) {
                        ForEach(ids, id: \.self) { id in
                            let job = snapshot.job(id)
                            let finished = job?.finished(since: batch.requestedAt) ?? true
                            OrbitalChip(
                                text: job?.name ?? id,
                                symbol: finished ? (job?.health == .ok ? "checkmark" : "exclamationmark") : (job?.isRunning == true ? "arrow.triangle.2.circlepath" : "hourglass"),
                                tint: finished ? (job?.tint ?? OrbitalTheme.textTertiary) : (job?.isRunning == true ? OrbitalTheme.accent : OrbitalTheme.textTertiary)
                            )
                        }
                    }
                }
            }
        }
        .orbitalCard(padding: 14, tint: OrbitalTheme.accent)
    }
}

// MARK: - Detail

struct JobDetailView: View {
    let jobID: String
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @Environment(\.dismiss) private var dismiss
    @State private var log: [String]?
    @State private var loadingLog = false

    private var job: FeedJob? { store.jobs?.job(jobID) }

    var body: some View {
        NavigationStack {
            ScrollView {
                if let job {
                    VStack(alignment: .leading, spacing: 16) {
                        header(job)
                        nextCard(job)
                        actions(job)
                        if let last = job.last {
                            lastRun(job, last)
                        }
                        if job.isAgent {
                            usageCard(job)
                        }
                        historyCard(job)
                        scheduleCard(job)
                        pagesCard(job)
                        logCard(job)
                    }
                    .frame(maxWidth: OrbitalTheme.columnMax)
                    .frame(maxWidth: .infinity)
                    .padding(16)
                } else {
                    Text("This feed is not in the schedule.")
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .padding(.top, 60)
                        .frame(maxWidth: .infinity)
                }
            }
            .scrollIndicators(.hidden)
            .background(OrbitalTheme.backgroundDeep.ignoresSafeArea())
            .navigationTitle(job?.name ?? "Feed")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .preferredColorScheme(.dark)
        .presentationDetents([.large])
    }

    private func header(_ job: FeedJob) -> some View {
        HStack(alignment: .top, spacing: 12) {
            IconTile(symbol: job.statusSymbol, tint: job.isBusy ? OrbitalTheme.accent : job.tint, size: 44, filled: true)
            VStack(alignment: .leading, spacing: 4) {
                Text(job.name)
                    .font(.title3.weight(.bold))
                    .foregroundStyle(OrbitalTheme.text)
                OrbitalChip(text: job.statusWord, tint: job.isBusy ? OrbitalTheme.accent : job.tint)
                if let reads = job.reads, !reads.isEmpty {
                    Text("Reads: \(reads)")
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 0)
        }
    }

    private func nextCard(_ job: FeedJob) -> some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            VStack(alignment: .leading, spacing: 4) {
                OrbitalEyebrow(text: job.isRunning ? "Updating now" : job.isQueued ? "Waiting its turn" : job.paused ? "Paused" : "Next update")
                Text(bigClock(job, now: context.date))
                    .font(.system(size: 34, weight: .bold, design: .rounded))
                    .monospacedDigit()
                    .foregroundStyle(job.isBusy ? OrbitalTheme.accent : OrbitalTheme.text)
                Text(clockCaption(job))
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .orbitalCard(padding: 16)
    }

    private func bigClock(_ job: FeedJob, now: Date) -> String {
        if job.isRunning { return JobFormat.elapsed(since: job.startedDate, now: now) }
        if job.isQueued { return job.queuePosition.map { "#\($0) in line" } ?? "Queued" }
        if job.paused { return "—" }
        return JobFormat.countdown(to: job.nextRunDate, now: now)
    }

    private func clockCaption(_ job: FeedJob) -> String {
        if job.isRunning { return "Started \(JobFormat.clock(job.startedDate) ?? "just now"). Time limit \(JobFormat.duration(Double(job.timeoutS ?? 0)) ?? "-")." }
        if job.isQueued { return "Another feed on the same account is running; this one starts right after." }
        if job.paused { return "Not running on its schedule. Run now still works." }
        var line = "\(job.schedule)"
        if let at = JobFormat.clock(job.nextRunDate) { line += " · next at \(at)" }
        if job.watches == true { line += " · also seconds after a change from the phone or its files" }
        return line
    }

    private func actions(_ job: FeedJob) -> some View {
        HStack(spacing: 10) {
            if job.isRunning {
                OrbitalActionButton(title: "Stop", symbol: "stop.fill", kind: .destructive) {
                    Task { await store.cancelJob(job.id) }
                }
            } else {
                OrbitalActionButton(title: job.isQueued ? "Queued…" : "Run now", symbol: "arrow.clockwise", busy: job.isQueued) {
                    Task { await store.runJob(job.id) }
                }
                .disabled(job.isQueued)
            }
            OrbitalActionButton(title: job.paused ? "Resume" : "Pause", symbol: job.paused ? "play.fill" : "pause.fill", kind: .secondary) {
                Task { await store.setPaused(job.id, paused: !job.paused) }
            }
        }
    }

    private func lastRun(_ job: FeedJob, _ last: JobRun) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            OrbitalEyebrow(text: "Last run")
            HStack(spacing: 8) {
                OrbitalChip(text: job.statusWord == "Updating" || job.statusWord == "Queued" ? (last.status ?? "-").capitalized : job.statusWord, symbol: job.statusSymbol, tint: job.tint)
                if let when = OrbitalDates.relative(last.finishedAt) {
                    Text(when).font(.caption).foregroundStyle(OrbitalTheme.textSecondary)
                }
                if let seconds = JobFormat.duration(last.durationS) {
                    Text("· \(seconds)").font(.caption).foregroundStyle(OrbitalTheme.textTertiary)
                }
                if let reason = job.reasonWord {
                    Text("· \(reason)").font(.caption).foregroundStyle(OrbitalTheme.textTertiary).lineLimit(1)
                }
            }
            if let summary = last.summary, !summary.isEmpty {
                Text(summary)
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.text)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
            if let issues = last.issues, !issues.isEmpty {
                VStack(alignment: .leading, spacing: 6) {
                    ForEach(issues, id: \.self) { issue in
                        HStack(alignment: .top, spacing: 6) {
                            Image(systemName: "exclamationmark.triangle.fill")
                                .font(.system(size: 10, weight: .bold))
                                .foregroundStyle(job.tint)
                                .padding(.top, 3)
                            Text(issue)
                                .font(.footnote)
                                .foregroundStyle(OrbitalTheme.textSecondary)
                                .fixedSize(horizontal: false, vertical: true)
                                .textSelection(.enabled)
                        }
                    }
                }
            }
            if let ok = job.lastOkAt, job.health != .ok {
                Text("Last fully working run: \(OrbitalDates.relative(ok) ?? ok)")
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            if let code = last.exitCode, code != 0 {
                Text("Exit code \(code)")
                    .font(.caption.monospaced())
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .orbitalCard(padding: 14, tint: job.health == .ok ? nil : job.tint)
    }

    @ViewBuilder
    private func usageCard(_ job: FeedJob) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                OrbitalEyebrow(text: "Subscription usage")
                Spacer(minLength: 0)
                JobKindChip(job: job)
            }
            if let total = job.usageTotal, let runs = total.runs, runs > 0 {
                HStack(spacing: 0) {
                    usageTile("Per run", AIUsage.compact(job.averageTokens ?? 0) + " tok",
                              job.averagePlanPercent.map { AIUsage.percent($0) + " of plan" })
                    Spacer(minLength: 8)
                    usageTile("Total", AIUsage.compact(total.totalTokens) + " tok",
                              total.planPercent.map { AIUsage.percent($0) + " of plan" })
                    Spacer(minLength: 8)
                    usageTile("Runs measured", "\(runs)", OrbitalDates.relative(total.since).map { "since \($0)" })
                }
                if let live = job.usageLive {
                    Text("Running now").font(.caption.weight(.semibold)).foregroundStyle(OrbitalTheme.accent)
                    UsageLine(usage: live, showsBreakdown: true)
                } else if let last = job.last?.usage {
                    Text("Last run").font(.caption.weight(.semibold)).foregroundStyle(OrbitalTheme.textSecondary)
                    UsageLine(usage: last, showsBreakdown: true)
                }
            } else {
                Text("This job starts an AI agent. Its tokens and share of the plan show here after its next run.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .orbitalCard(padding: 14)
    }

    private func usageTile(_ label: String, _ value: String, _ caption: String?) -> some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(label.uppercased())
                .font(.system(size: 9.5, weight: .bold))
                .tracking(0.4)
                .foregroundStyle(OrbitalTheme.textTertiary)
            Text(value)
                .font(.subheadline.weight(.bold).monospacedDigit())
                .foregroundStyle(OrbitalTheme.text)
            if let caption {
                Text(caption).font(.caption2).foregroundStyle(OrbitalTheme.textTertiary)
            }
        }
    }

    @ViewBuilder
    private func historyCard(_ job: FeedJob) -> some View {
        if !job.history.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                OrbitalEyebrow(text: "Recent runs")
                HStack(spacing: 3) {
                    ForEach(Array(job.history.enumerated()), id: \.offset) { _, entry in
                        RoundedRectangle(cornerRadius: 2, style: .continuous)
                            .fill(color(entry.status))
                            .frame(maxWidth: .infinity)
                            .frame(height: 22)
                    }
                }
                .accessibilityElement(children: .ignore)
                .accessibilityLabel("\(job.history.filter { $0.status == "ok" }.count) of \(job.history.count) recent runs worked")
                ForEach(Array(job.history.suffix(6).reversed().enumerated()), id: \.offset) { _, entry in
                    HStack(alignment: .top, spacing: 8) {
                        Circle().fill(color(entry.status)).frame(width: 7, height: 7).padding(.top, 5)
                        VStack(alignment: .leading, spacing: 1) {
                            Text([JobFormat.runTime(entry.at), JobFormat.duration(entry.durationS)].compactMap { $0 }.joined(separator: " · "))
                                .font(.caption.weight(.semibold))
                                .foregroundStyle(OrbitalTheme.textSecondary)
                            if let summary = entry.summary, !summary.isEmpty {
                                Text(summary)
                                    .font(.caption)
                                    .foregroundStyle(OrbitalTheme.textTertiary)
                                    .lineLimit(2)
                            }
                        }
                    }
                }
            }
            .orbitalCard(padding: 14)
        }
    }

    private func color(_ status: String?) -> Color {
        switch status {
        case "ok": return OrbitalTheme.success
        case "warn": return OrbitalTheme.warning
        case "error": return OrbitalTheme.danger
        default: return OrbitalTheme.textTertiary
        }
    }

    private func scheduleCard(_ job: FeedJob) -> some View {
        let choices = store.jobs?.scheduler.everyChoices ?? [300, 900, 1800, 3600]
        return VStack(alignment: .leading, spacing: 10) {
            OrbitalEyebrow(text: "How often")
            Menu {
                ForEach(choices, id: \.self) { seconds in
                    Button {
                        Task { await store.setEvery(job.id, seconds: seconds) }
                    } label: {
                        if seconds == job.everyS {
                            Label(label(seconds, job), systemImage: "checkmark")
                        } else {
                            Text(label(seconds, job))
                        }
                    }
                }
            } label: {
                HStack {
                    Text(job.schedule)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                        .multilineTextAlignment(.leading)
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.up.chevron.down")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(OrbitalTheme.accent)
                }
                .padding(.horizontal, 12)
                .frame(minHeight: 44)
                .background(OrbitalTheme.surfaceRaised, in: RoundedRectangle(cornerRadius: OrbitalTheme.controlRadius, style: .continuous))
            }
            Text("Feeds on the same account wait for each other, so a shorter interval can queue behind them. Pages say when they were last checked.")
                .font(.caption)
                .foregroundStyle(OrbitalTheme.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
            if let lane = store.jobs?.lanes.first(where: { $0.id == job.lane }) {
                Text("Account lane: \(lane.title)\(lane.machine.map { " · \($0)" } ?? "")")
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textSecondary)
            }
        }
        .orbitalCard(padding: 14)
    }

    private func label(_ seconds: Int, _ job: FeedJob) -> String {
        JobFormat.every(seconds) + (seconds == job.defaultEveryS ? " (default)" : "")
    }

    @ViewBuilder
    private func pagesCard(_ job: FeedJob) -> some View {
        let targets = job.targets
        if !targets.isEmpty {
            VStack(alignment: .leading, spacing: 6) {
                OrbitalEyebrow(text: "Pages it updates")
                ForEach(targets, id: \.self) { target in
                    Button {
                        dismiss()
                        router.open(section: target.section, node: target.node, tab: target.tab)
                    } label: {
                        HStack {
                            Text(pageName(target))
                                .font(.subheadline)
                                .foregroundStyle(OrbitalTheme.text)
                            Spacer()
                            Image(systemName: "chevron.right")
                                .font(.caption.weight(.bold))
                                .foregroundStyle(OrbitalTheme.textTertiary)
                        }
                        .frame(minHeight: 40)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(OrbitalPressStyle())
                }
            }
            .orbitalCard(padding: 14)
        }
    }

    private func pageName(_ target: JobTarget) -> String {
        let section = store.sectionsByID[target.section]
        let sectionTitle = section?.title ?? store.catalog.first { $0.id == target.section }?.title ?? target.section
        guard let nodeID = target.node else { return sectionTitle }
        let node = section?.nodes.first { $0.id == nodeID }
        var name = "\(sectionTitle) › \(node?.title ?? nodeID)"
        if let tab = target.tab {
            name += " › \(node?.tabs.first { $0.id == tab }?.title ?? tab)"
        }
        return name
    }

    private func logCard(_ job: FeedJob) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                OrbitalEyebrow(text: "Last run output")
                Spacer()
                Button(log == nil ? "Show" : "Reload") {
                    Task {
                        loadingLog = true
                        log = await store.jobLog(job.id) ?? ["Could not load the log."]
                        loadingLog = false
                    }
                }
                .font(.caption.weight(.semibold))
                .frame(minHeight: 44)
            }
            if loadingLog {
                ProgressView().frame(maxWidth: .infinity)
            } else if let log {
                ScrollView(.horizontal, showsIndicators: false) {
                    Text(log.suffix(120).joined(separator: "\n"))
                        .font(.system(size: 11, design: .monospaced))
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .textSelection(.enabled)
                        .padding(10)
                }
                .frame(maxHeight: 360)
                .background(Color.black.opacity(0.35), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            }
            if let paths = job.logs, !paths.isEmpty {
                Text("Logs on the hub: " + paths.joined(separator: ", "))
                    .font(.caption2.monospaced())
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
        }
        .orbitalCard(padding: 14)
    }
}

// MARK: - Page strip

/// Under a page's title: when its feed updates next, whether the last run worked, and Refresh.
struct PageFeedStrip: View {
    let jobs: [FeedJob]
    let accent: Color
    let onRefresh: () -> Void
    let onOpen: () -> Void

    private var worst: FeedJob? {
        jobs.first { $0.health == .error } ?? jobs.first { $0.health == .warn }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                Button(action: onOpen) {
                    TimelineView(.periodic(from: .now, by: 1)) { context in
                        HStack(spacing: 5) {
                            Image(systemName: busy ? "arrow.triangle.2.circlepath" : "timer")
                                .font(.system(size: 10, weight: .bold))
                                .symbolEffect(.pulse, isActive: busy)
                            Text(line(context.date))
                                .font(.caption2.weight(.semibold))
                                .monospacedDigit()
                                .lineLimit(1)
                        }
                        .foregroundStyle(busy ? accent : OrbitalTheme.textTertiary)
                    }
                    .frame(minHeight: 30)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityHint("Opens this feed's schedule")
                Button(action: onRefresh) {
                    HStack(spacing: 4) {
                        Image(systemName: "arrow.clockwise")
                            .font(.system(size: 10, weight: .bold))
                        Text("Refresh")
                            .font(.caption2.weight(.bold))
                    }
                    .foregroundStyle(accent)
                    .padding(.horizontal, 10)
                    .frame(minHeight: 26)
                    .background(accent.opacity(0.12), in: Capsule())
                    .overlay(Capsule().strokeBorder(accent.opacity(0.25), lineWidth: 1))
                    .frame(minHeight: 44)
                    .contentShape(Rectangle())
                }
                .buttonStyle(OrbitalPressStyle())
                .disabled(busy)
                .accessibilityLabel("Refresh this page now")
            }
            if let worst, let issue = worst.last?.issues?.first ?? worst.last?.summary {
                Button(action: onOpen) {
                    HStack(alignment: .top, spacing: 5) {
                        Image(systemName: worst.statusSymbol)
                            .font(.system(size: 10, weight: .bold))
                            .padding(.top, 2)
                        Text(issue)
                            .font(.caption2.weight(.medium))
                            .lineLimit(2)
                            .multilineTextAlignment(.leading)
                    }
                    .foregroundStyle(worst.tint)
                }
                .buttonStyle(.plain)
            }
        }
    }

    private var busy: Bool { jobs.contains(where: \.isBusy) }

    private func line(_ now: Date) -> String {
        if let running = jobs.first(where: \.isRunning) {
            return "Updating now · \(JobFormat.elapsed(since: running.startedDate, now: now))"
        }
        if jobs.contains(where: \.isQueued) { return "Update queued" }
        // A page with a page feed and a live counter counts down to the page feed.
        let paced = jobs.filter { $0.everyS >= 300 }
        let main = (paced.isEmpty ? jobs : paced).filter { !$0.paused }
        let next = main.compactMap(\.nextRunDate).min()
        let every = main.map(\.everyS).min().map { JobFormat.every($0) } ?? ""
        guard let next else { return "Paused" }
        return "Next in \(JobFormat.countdown(to: next, now: now)) · \(every.lowercased())"
    }
}


// MARK: - AI usage

/// "Agent" for a job that spins up an AI agent, "Automation" for a plain script.
struct JobKindChip: View {
    let job: FeedJob

    var body: some View {
        if job.isAgent {
            OrbitalChip(text: "Agent", symbol: "sparkles", tint: OrbitalTheme.violet)
        } else {
            OrbitalChip(text: "Automation", symbol: "gearshape.2", tint: OrbitalTheme.textTertiary)
        }
    }
}

/// The subscription plans, then the work that used the most of them: is anything eating the plan?
struct SubscriptionUsageCard: View {
    let report: UsageReport?
    let jobs: [FeedJob]
    let openJob: (String) -> Void
    @EnvironmentObject private var router: AppRouter

    private var agentJobs: [FeedJob] { jobs.filter(\.isAgent) }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                OrbitalEyebrow(text: "Subscription usage")
                Spacer(minLength: 0)
                Text("\(agentJobs.count) of \(jobs.count) jobs use an agent")
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            if let report {
                ForEach(report.plans) { plan in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack {
                            Text([AgentStyle.displayName(plan.engine), plan.tier].compactMap { $0 }.joined(separator: " · "))
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(OrbitalTheme.text)
                            Spacer(minLength: 0)
                            Text(AIUsage.percent(plan.usedPercent) + " used")
                                .font(.subheadline.weight(.bold).monospacedDigit())
                                .foregroundStyle(plan.usedPercent >= 80 ? OrbitalTheme.danger : plan.usedPercent >= 60 ? OrbitalTheme.warning : OrbitalTheme.text)
                        }
                        ProgressStrip(percent: Int(plan.usedPercent.rounded()), tint: plan.usedPercent >= 80 ? OrbitalTheme.danger : OrbitalTheme.accent)
                        Text([plan.period.map { $0 == "weekly" ? "This week" : $0.capitalized }, OrbitalDates.relative(plan.resetsAt).map { "resets \($0)" }].compactMap { $0 }.joined(separator: ", "))
                            .font(.caption2)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                    }
                }
                if let totals = report.totals {
                    Text("\(AIUsage.compact(totals.tokensToday)) tokens today · \(AIUsage.compact(totals.tokensWeek)) this week" + (totals.planPercentWeek > 0 ? " · \(AIUsage.percent(totals.planPercentWeek)) of the plan" : ""))
                        .font(.caption.monospacedDigit())
                        .foregroundStyle(OrbitalTheme.textSecondary)
                }
                if !report.top.isEmpty {
                    Text("Used the most")
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .padding(.top, 2)
                    VStack(spacing: 0) {
                        ForEach(Array(report.top.prefix(5).enumerated()), id: \.element.id) { index, row in
                            if index > 0 { Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1) }
                            Button {
                                if row.kind == "automation" { openJob(row.id) } else { router.openTracker(item: row.id) }
                            } label: { topRow(row) }
                                .buttonStyle(OrbitalPressStyle())
                        }
                    }
                }
            } else {
                Text("Loading usage…").font(.footnote).foregroundStyle(OrbitalTheme.textTertiary)
            }
        }
        .orbitalCard(padding: 14)
    }

    private func topRow(_ row: UsageTop) -> some View {
        HStack(spacing: 10) {
            Image(systemName: row.kind == "automation" ? "clock.arrow.2.circlepath" : AgentStyle.symbol(row.agent ?? "agent"))
                .font(.caption.weight(.bold))
                .foregroundStyle(row.kind == "automation" ? OrbitalTheme.violet : AgentStyle.color(row.agent ?? "agent"))
                .frame(width: 22)
            VStack(alignment: .leading, spacing: 2) {
                Text(row.title ?? row.id)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
                    .lineLimit(1)
                Text([row.kind == "automation" ? row.runs.map { "\($0) runs" } : row.machine, row.progress.map { "\($0)%" }].compactMap { $0 }.joined(separator: " · "))
                    .font(.caption2)
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            Spacer(minLength: 8)
            VStack(alignment: .trailing, spacing: 2) {
                Text(AIUsage.compact(row.tokens) + " tok")
                    .font(.caption.weight(.bold).monospacedDigit())
                    .foregroundStyle(OrbitalTheme.text)
                if let plan = row.planPercent, plan > 0 {
                    Text(AIUsage.percent(plan) + " of plan")
                        .font(.caption2.monospacedDigit())
                        .foregroundStyle(plan >= 5 ? OrbitalTheme.danger : plan >= 2 ? OrbitalTheme.warning : OrbitalTheme.textTertiary)
                }
            }
        }
        .padding(.vertical, 8)
        .contentShape(Rectangle())
    }
}
