import SwiftUI
import UIKit

// The marketing block: a marketing ops ledger on the phone. The goal and scoreboard, the to-dos only you
// can do (Done posts marketing.todo_done, which the owning feed writes back to the ledger), the
// experiments, and every decision, action, result, and alert by day with its time. A second pane shows
// the block's `automation` object (MarketingAutomationView.swift).

private let marketingTint = Color(hex: "#5AC8FA") ?? OrbitalTheme.accent

// MARK: - Dashboard (the tab, and the block on its own page)

struct MarketingDashboard: View {
    let ref: MarketingRef
    @EnvironmentObject private var store: OrbitalStore
    /// "ledger" (goal, to-dos, experiments, timeline) or "automation" (schedules, runs, queues, releases).
    @AppStorage("marketing.pane") private var pane = "ledger"
    @State private var brand = ""
    @State private var kind = ""
    @State private var dayLimit = 7
    @State private var openedEntry: MarketingEntry?
    @State private var openedTodo: MarketingTodo?
    @State private var openedExperiment: MarketingExperiment?
    @State private var confirming: MarketingTodo?

    private var block: MarketingBlock { ref.block }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            MarketingPanePicker(pane: $pane, issues: automationIssues)
            if pane == "automation" {
                MarketingAutomationPane(automation: block.automation)
            } else {
                ledger
            }
        }
        .environment(\.marketingBrands, brands)
    }

    private var brands: [String: MarketingBrand] { MarketingBrand.registry(for: block, directory: store.directory) }

    /// Automations that need a look, counted the same way the pane does (live scheduler state first).
    private var automationIssues: Int {
        block.automation?.live(store.jobs).filter(\.needsALook).count ?? 0
    }

    @ViewBuilder
    private var ledger: some View {
        let entries = block.entries.filter { MarketingBrand.matches($0.brand, filter: brand) && MarketingKind.matches($0.kind, filter: kind) }
        let days = MarketingFeed.days(entries)
        VStack(alignment: .leading, spacing: 16) {
            MarketingGoalCard(goal: block.goal, boards: block.scoreboard)
            brandToggles
            MarketingTodosCard(
                open: block.openTodos.filter { MarketingBrand.matches($0.brand, filter: brand) },
                done: block.doneTodos.filter { MarketingBrand.matches($0.brand, filter: brand) },
                onOpen: { openedTodo = $0 },
                onDone: { confirming = $0 }
            )
            MarketingExperimentsCard(
                experiments: block.experiments.filter { MarketingBrand.matches($0.brand, filter: brand) },
                onOpen: { openedExperiment = $0 }
            )
            timelineHeader(count: entries.count)
            kindFilter
            if days.isEmpty {
                Text("Nothing recorded for this filter yet.")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .orbitalCard(padding: 14)
            }
            ForEach(days.prefix(dayLimit)) { day in
                MarketingDaySection(day: day, showsBrand: brand.isEmpty) { openedEntry = $0 }
            }
            if days.count > dayLimit {
                Button {
                    withAnimation { dayLimit += 14 }
                } label: {
                    Text("Show earlier days (\(days.count - dayLimit) more)")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(marketingTint)
                        .frame(maxWidth: .infinity, minHeight: 44)
                }
                .buttonStyle(OrbitalPressStyle())
                .orbitalCard(padding: 4)
            }
            footer
        }
        .sheet(item: $openedEntry) { entry in
            MarketingEntrySheet(entry: entry, block: block)
                .environment(\.marketingBrands, brands)
        }
        .sheet(item: $openedTodo) { todo in
            MarketingTodoSheet(todo: todo) { note in markDone(todo, note: note) }
                .environment(\.marketingBrands, brands)
        }
        .sheet(item: $openedExperiment) { experiment in
            MarketingExperimentSheet(experiment: experiment)
                .environment(\.marketingBrands, brands)
        }
        .confirmationDialog(
            "Mark this done?",
            isPresented: Binding(get: { confirming != nil }, set: { if !$0 { confirming = nil } }),
            titleVisibility: .visible,
            presenting: confirming
        ) { todo in
            Button("Mark done") { markDone(todo, note: "") }
            Button("Cancel", role: .cancel) {}
        } message: { todo in
            Text("\(todo.title)\n\nIt is recorded in the marketing ledger as done by you, with the time.")
        }
    }

    private func markDone(_ todo: MarketingTodo, note: String) {
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        let trimmed = note.trimmingCharacters(in: .whitespacesAndNewlines)
        Task {
            await store.perform(
                type: "marketing.todo_done",
                sectionID: ref.location.sectionID,
                itemID: todo.id,
                blockID: block.id,
                nodeID: ref.location.nodeID,
                tabID: ref.location.tabID,
                value: trimmed.isEmpty ? [:] : ["note": trimmed]
            )
        }
    }

    @ViewBuilder
    private var brandToggles: some View {
        let keys = block.brandKeys
        if keys.count > 1 {
            CircleToggleRow(
                choices: [CircleChoice.all()] + keys.map { key in
                    let style = MarketingBrand(key, in: brands)
                    return CircleChoice(id: key, label: style.name, image: style.image, symbol: style.symbol, tint: style.tint)
                },
                selection: $brand,
                size: 46
            )
        }
    }

    private func timelineHeader(count: Int) -> some View {
        HStack(alignment: .firstTextBaseline) {
            VStack(alignment: .leading, spacing: 2) {
                Text("Timeline")
                    .font(.title3.weight(.bold))
                    .foregroundStyle(OrbitalTheme.text)
                Text("Everything decided, done, tested, and found, newest first")
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            Spacer(minLength: 0)
            Text("\(count) \(count == 1 ? "entry" : "entries")")
                .font(.caption2.weight(.semibold))
                .foregroundStyle(OrbitalTheme.textTertiary)
        }
        .padding(.top, 4)
    }

    private var kindFilter: some View {
        let present = Set(block.entries.map(\.kind))
        let chips = MarketingKind.filters.filter { chip in
            chip.id.isEmpty || present.contains(chip.id) || (chip.id == "todo" && present.contains("todo_done"))
        }
        return ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(chips, id: \.id) { chip in
                    let selected = kind == chip.id
                    let tint = chip.id.isEmpty ? marketingTint : MarketingKind(chip.id).tint
                    Button {
                        UISelectionFeedbackGenerator().selectionChanged()
                        withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) { kind = chip.id }
                    } label: {
                        HStack(spacing: 6) {
                            if !chip.id.isEmpty {
                                Image(systemName: MarketingKind(chip.id).symbol)
                                    .font(.system(size: 11, weight: .bold))
                                    .foregroundStyle(tint)
                            }
                            Text(chip.label)
                                .font(.footnote.weight(.semibold))
                        }
                        .foregroundStyle(selected ? OrbitalTheme.text : OrbitalTheme.textSecondary)
                        .padding(.horizontal, 12)
                        .frame(minHeight: 36)
                        .background(tint.opacity(selected ? 0.26 : 0.08), in: Capsule())
                        .overlay(Capsule().strokeBorder(tint.opacity(selected ? 0.55 : 0.18), lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                    .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
                }
            }
        }
    }

    @ViewBuilder
    private var footer: some View {
        let stale = OrbitalDates.parse(block.asOf).map { Date().timeIntervalSince($0) > 3 * 3600 } ?? false
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: stale ? "exclamationmark.triangle.fill" : "arrow.triangle.2.circlepath")
                .font(.caption.weight(.bold))
                .foregroundStyle(stale ? OrbitalTheme.warning : OrbitalTheme.textTertiary)
            Text(stale
                 ? "Not updated since \(OrbitalDates.shortDateTime(block.asOf) ?? "a while"). Run the marketing feed under Automations."
                 : "Updated \(OrbitalDates.relative(block.asOf) ?? "recently") from the marketing ledger.")
                .font(.caption)
                .foregroundStyle(OrbitalTheme.textTertiary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}

// MARK: - Goal and scoreboard

struct MarketingGoalCard: View {
    let goal: MarketingGoal?
    let boards: [MarketingBoard]
    @Environment(\.marketingBrands) private var brands

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            if let goal {
                goalView(goal)
            }
            ForEach(boards) { board in
                if goal != nil || board.id != boards.first?.id {
                    Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                }
                boardView(board)
            }
        }
        .orbitalCard(padding: 16, tint: marketingTint)
    }

    private func goalView(_ goal: MarketingGoal) -> some View {
        let current = goal.current.map { $0.rounded() == $0 ? "\(Int($0))" : String(format: "%.1f", $0) } ?? "?"
        let target = Int(goal.target)
        let left = goal.current.map { max(0, Int(goal.target - $0)) }
        return VStack(alignment: .leading, spacing: 8) {
            OrbitalEyebrow(text: goal.brand.map { "\(MarketingBrand($0, in: brands).name) goal" } ?? "Goal", color: marketingTint)
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(current)
                    .font(.system(size: 40, weight: .bold, design: .rounded).monospacedDigit())
                    .foregroundStyle(OrbitalTheme.text)
                Text("/ \(target)")
                    .font(.title3.weight(.semibold).monospacedDigit())
                    .foregroundStyle(OrbitalTheme.textSecondary)
                Spacer(minLength: 0)
                Text(goal.progress.formatted(.percent.precision(.fractionLength(goal.progress < 0.1 ? 1 : 0))))
                    .font(.headline.monospacedDigit())
                    .foregroundStyle(marketingTint)
            }
            Text(goal.label)
                .font(.subheadline.weight(.semibold))
                .foregroundStyle(OrbitalTheme.textSecondary)
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    Capsule().fill(Color.white.opacity(0.08))
                    Capsule()
                        .fill(LinearGradient(colors: [marketingTint, OrbitalTheme.success], startPoint: .leading, endPoint: .trailing))
                        .frame(width: goal.progress > 0 ? max(8, geo.size.width * goal.progress) : 0)
                }
            }
            .frame(height: 10)
            .accessibilityElement()
            .accessibilityLabel("\(current) of \(target) \(goal.label.lowercased())")
            if let left {
                Text("\(left) to go")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
        }
    }

    private func boardView(_ board: MarketingBoard) -> some View {
        let brand = MarketingBrand(board.brand, in: brands)
        return VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                OrbitalEyebrow(text: brand.name, color: brand.tint)
                Spacer(minLength: 0)
                if let at = OrbitalDates.parse(board.at) {
                    Text("as of \(MarketingTime.dayAndTime(at))")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
            }
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 3), spacing: 8) {
                ForEach(board.values) { value in
                    VStack(alignment: .leading, spacing: 1) {
                        Text(value.text)
                            .font(.headline.monospacedDigit())
                            .foregroundStyle(OrbitalTheme.text)
                            .lineLimit(1)
                            .minimumScaleFactor(0.7)
                        Text(value.label)
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .lineLimit(2)
                    }
                    .frame(maxWidth: .infinity, minHeight: 62, alignment: .topLeading)
                    .padding(8)
                    .background(Color.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                    .accessibilityElement(children: .combine)
                }
            }
            if let source = board.source, !source.isEmpty {
                Text("From \(source)")
                    .font(.caption2)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .lineLimit(2)
            }
        }
    }
}

// MARK: - Needs you

struct MarketingTodosCard: View {
    let open: [MarketingTodo]
    let done: [MarketingTodo]
    let onOpen: (MarketingTodo) -> Void
    let onDone: (MarketingTodo) -> Void
    @State private var showsDone = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                OrbitalEyebrow(text: "Needs you", color: open.isEmpty ? OrbitalTheme.textTertiary : OrbitalTheme.warning)
                Spacer(minLength: 0)
                Text(open.isEmpty ? "All clear" : "\(open.count) only you can do")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            VStack(spacing: 0) {
                if open.isEmpty {
                    HStack(spacing: 10) {
                        Image(systemName: "checkmark.seal.fill")
                            .foregroundStyle(OrbitalTheme.success)
                        Text("Nothing is waiting on you.")
                            .font(.subheadline)
                            .foregroundStyle(OrbitalTheme.textSecondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.vertical, 6)
                }
                ForEach(Array(open.enumerated()), id: \.element.id) { index, todo in
                    if index > 0 {
                        Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                    }
                    openRow(todo)
                }
                if !done.isEmpty {
                    Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                    Button {
                        withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) { showsDone.toggle() }
                    } label: {
                        HStack(spacing: 6) {
                            Image(systemName: "checkmark.circle")
                            Text("\(done.count) done")
                            Spacer(minLength: 0)
                            Image(systemName: "chevron.down")
                                .font(.caption2.weight(.bold))
                                .rotationEffect(.degrees(showsDone ? 180 : 0))
                        }
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .frame(minHeight: 44)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    if showsDone {
                        ForEach(done.prefix(15)) { todo in
                            doneRow(todo)
                        }
                    }
                }
            }
            .orbitalCard(padding: 12, tint: open.isEmpty ? nil : OrbitalTheme.warning)
        }
    }

    private func openRow(_ todo: MarketingTodo) -> some View {
        HStack(alignment: .top, spacing: 12) {
            Button {
                onDone(todo)
            } label: {
                Image(systemName: "circle")
                    .font(.system(size: 24, weight: .regular))
                    .foregroundStyle(OrbitalTheme.warning)
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(OrbitalPressStyle())
            .padding(.leading, -8)
            .padding(.top, -8)
            .accessibilityLabel("Mark done: \(todo.title)")
            Button {
                onOpen(todo)
            } label: {
                VStack(alignment: .leading, spacing: 4) {
                    Text(todo.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                    if let why = todo.why, !why.isEmpty {
                        Text(why)
                            .font(.footnote)
                            .foregroundStyle(OrbitalTheme.textSecondary)
                            .multilineTextAlignment(.leading)
                            .lineLimit(3)
                    }
                    HStack(spacing: 6) {
                        MarketingBrandChip(brand: todo.brand)
                        if let age = MarketingFeed.age(todo.at) {
                            Text(age)
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(OrbitalTheme.textTertiary)
                        }
                        if let at = todo.date {
                            Text("· added \(MarketingTime.dayAndTime(at))")
                                .font(.caption2)
                                .foregroundStyle(OrbitalTheme.textTertiary)
                                .lineLimit(1)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
        }
        .padding(.vertical, 8)
    }

    private func doneRow(_ todo: MarketingTodo) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: todo.pending ? "arrow.triangle.2.circlepath" : "checkmark.circle.fill")
                .font(.system(size: 16, weight: .semibold))
                .foregroundStyle(todo.pending ? OrbitalTheme.accent : OrbitalTheme.success)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 3) {
                Text(todo.title)
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .strikethrough(!todo.pending, color: OrbitalTheme.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
                Text(doneLine(todo))
                    .font(.caption2)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
        }
        .padding(.vertical, 6)
        .contentShape(Rectangle())
        .onTapGesture { onOpen(todo) }
    }

    private func doneLine(_ todo: MarketingTodo) -> String {
        if todo.pending { return "Recording in the ledger…" }
        var parts: [String] = []
        if let at = todo.doneDate { parts.append("Done \(MarketingTime.dayAndTime(at))") }
        if let by = todo.doneBy { parts.append("by \(by)") }
        if let note = todo.doneDetails, !note.isEmpty { parts.append(note) }
        return parts.joined(separator: " · ")
    }
}

// MARK: - Experiments

struct MarketingExperimentsCard: View {
    let experiments: [MarketingExperiment]
    let onOpen: (MarketingExperiment) -> Void

    var body: some View {
        let running = experiments.filter(\.isRunning).sorted { ($0.due ? 0 : 1, $0.check ?? "9999") < ($1.due ? 0 : 1, $1.check ?? "9999") }
        let finished = experiments.filter { !$0.isRunning }
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                OrbitalEyebrow(text: "Experiments")
                Spacer(minLength: 0)
                Text("\(running.count) running · \(finished.count) finished")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            VStack(spacing: 0) {
                if experiments.isEmpty {
                    Text("No experiments yet. Every change meant to move a number gets one, with a baseline and a check date.")
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 6)
                }
                ForEach(Array((running + finished).enumerated()), id: \.element.id) { index, experiment in
                    if index > 0 {
                        Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                    }
                    row(experiment)
                }
            }
            .orbitalCard(padding: 12)
        }
    }

    private func row(_ experiment: MarketingExperiment) -> some View {
        let status = MarketingStatus(experiment.status)
        return Button {
            onOpen(experiment)
        } label: {
            HStack(alignment: .top, spacing: 12) {
                IconTile(symbol: status.symbol, tint: status.tint, size: 34)
                VStack(alignment: .leading, spacing: 4) {
                    Text(experiment.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                    if experiment.isRunning {
                        if let metric = experiment.metric {
                            Text(metric)
                                .font(.caption)
                                .foregroundStyle(OrbitalTheme.textSecondary)
                                .lineLimit(2)
                        }
                        Text(runningLine(experiment))
                            .font(.caption2.weight(.semibold))
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .lineLimit(2)
                    } else if let result = experiment.latestResult?.details {
                        Text(result)
                            .font(.caption)
                            .foregroundStyle(OrbitalTheme.textSecondary)
                            .lineLimit(3)
                    }
                    HStack(spacing: 6) {
                        MarketingBrandChip(brand: experiment.brand)
                        OrbitalChip(text: status.name, symbol: status.symbol, tint: status.tint)
                        if experiment.due {
                            OrbitalChip(text: "Due for a read", symbol: "clock.badge.exclamationmark", tint: OrbitalTheme.warning)
                        }
                    }
                }
                Spacer(minLength: 0)
                Image(systemName: "chevron.right")
                    .font(.caption.weight(.bold))
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .padding(.top, 10)
            }
            .padding(.vertical, 8)
            .contentShape(Rectangle())
        }
        .buttonStyle(OrbitalPressStyle())
    }

    private func runningLine(_ experiment: MarketingExperiment) -> String {
        var parts: [String] = []
        if let baseline = experiment.baseline { parts.append("Baseline \(baseline)") }
        if let target = experiment.target { parts.append("target \(target)") }
        if let check = MarketingFeed.checkDay(experiment.check) { parts.append("check \(check)") }
        return parts.joined(separator: " · ")
    }
}

// MARK: - Timeline

struct MarketingDaySection: View {
    let day: MarketingDay
    var showsBrand = true
    let onOpen: (MarketingEntry) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                OrbitalEyebrow(text: MarketingTime.dayTitle(day.day))
                Spacer(minLength: 0)
                Text("\(day.entries.count) \(day.entries.count == 1 ? "entry" : "entries")")
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            VStack(spacing: 0) {
                ForEach(Array(day.entries.enumerated()), id: \.element.id) { index, entry in
                    if index > 0 {
                        Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                    }
                    MarketingEntryRow(entry: entry, showsBrand: showsBrand) { onOpen(entry) }
                }
            }
            .orbitalCard(padding: 12)
        }
    }
}

struct MarketingEntryRow: View {
    let entry: MarketingEntry
    var showsBrand = true
    let onOpen: () -> Void

    var body: some View {
        let kind = MarketingKind(entry.kind)
        Button(action: onOpen) {
            HStack(alignment: .top, spacing: 10) {
                Text(entry.date.map { $0.formatted(date: .omitted, time: .shortened) } ?? "")
                    .font(.caption.weight(.semibold).monospacedDigit())
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .frame(width: 62, alignment: .leading)
                    .padding(.top, 7)
                IconTile(symbol: kind.symbol, tint: kind.tint, size: 30)
                VStack(alignment: .leading, spacing: 4) {
                    Text(entry.title)
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                        .multilineTextAlignment(.leading)
                        .lineLimit(3)
                    HStack(spacing: 5) {
                        if showsBrand { MarketingBrandChip(brand: entry.brand) }
                        Text(kind.name)
                            .foregroundStyle(kind.tint)
                            .layoutPriority(1)
                        if let status = entry.status, entry.kind == "result" {
                            Text("· \(MarketingStatus(status).name)")
                                .foregroundStyle(MarketingStatus(status).tint)
                                .layoutPriority(1)
                        }
                        if let actor = entry.actor {
                            Text("· \(actor)")
                                .lineLimit(1)
                        }
                    }
                    .font(.caption2.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .lineLimit(1)
                }
                Spacer(minLength: 0)
            }
            .padding(.vertical, 8)
            .contentShape(Rectangle())
        }
        .buttonStyle(OrbitalPressStyle())
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens why and the details")
    }
}

struct MarketingBrandChip: View {
    let brand: String?
    @Environment(\.marketingBrands) private var brands

    var body: some View {
        let style = MarketingBrand(brand, in: brands)
        Text(style.short)
            .font(.system(size: 10, weight: .heavy))
            .foregroundStyle(style.tint)
            .padding(.horizontal, 6)
            .padding(.vertical, 2)
            .background(style.tint.opacity(0.14), in: Capsule())
            .overlay(Capsule().strokeBorder(style.tint.opacity(0.3), lineWidth: 1))
            .accessibilityLabel(style.name)
    }
}

enum MarketingTime {
    /// "Today 11:32 AM", "Yesterday 9:05 PM", "Mon 12 Oct 4:10 PM".
    static func dayAndTime(_ date: Date) -> String {
        let calendar = Calendar.current
        let time = date.formatted(date: .omitted, time: .shortened)
        if calendar.isDateInToday(date) { return "today \(time)" }
        if calendar.isDateInYesterday(date) { return "yesterday \(time)" }
        return "\(date.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated))) \(time)"
    }

    static func dayTitle(_ day: Date) -> String {
        let calendar = Calendar.current
        let full = day.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated))
        if calendar.isDateInToday(day) { return "Today · \(full)" }
        if calendar.isDateInYesterday(day) { return "Yesterday · \(full)" }
        let sameYear = calendar.isDate(day, equalTo: Date(), toGranularity: .year)
        return sameYear ? day.formatted(.dateTime.weekday(.wide).day().month(.abbreviated)) : day.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated).year())
    }
}

// MARK: - Sheets

/// One ledger entry: when, who, why, the details, and its links.
struct MarketingEntrySheet: View {
    let entry: MarketingEntry
    let block: MarketingBlock
    @Environment(\.marketingBrands) private var brands
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        let kind = MarketingKind(entry.kind)
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(alignment: .top, spacing: 12) {
                        IconTile(symbol: kind.symbol, tint: kind.tint, size: 40, filled: true)
                        VStack(alignment: .leading, spacing: 6) {
                            Text(entry.title)
                                .font(.headline)
                                .foregroundStyle(OrbitalTheme.text)
                                .fixedSize(horizontal: false, vertical: true)
                            FlowLayout(spacing: 6) {
                                OrbitalChip(text: kind.name, symbol: kind.symbol, tint: kind.tint)
                                OrbitalChip(text: MarketingBrand(entry.brand, in: brands).name, tint: MarketingBrand(entry.brand, in: brands).tint)
                                if let status = entry.status {
                                    let style = MarketingStatus(status)
                                    OrbitalChip(text: style.name, symbol: style.symbol, tint: style.tint)
                                }
                            }
                        }
                    }
                    MarketingFacts(rows: whenRows)
                    MarketingTextCard(title: "Why", text: entry.why)
                    MarketingTextCard(title: "Details", text: entry.details)
                    MarketingFacts(title: "Experiment", rows: [
                        ("Hypothesis", entry.hypothesis), ("Metric", entry.metric), ("Baseline", entry.baseline),
                        ("Target", entry.target), ("Check", MarketingFeed.checkDay(entry.check)),
                    ])
                    if !entry.values.isEmpty {
                        MarketingFacts(title: "Scoreboard", rows: entry.values.map { ($0.label, $0.text) })
                    }
                    if let ref = entry.ref {
                        MarketingFacts(title: "About", rows: [(refKind(ref), refTitle(ref) ?? ref)])
                    }
                    MarketingLinks(links: entry.links)
                    if !entry.tags.isEmpty {
                        FlowLayout(spacing: 6) {
                            ForEach(entry.tags, id: \.self) { OrbitalChip(text: $0) }
                        }
                    }
                    Text(entry.id)
                        .font(.caption2.monospaced())
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .textSelection(.enabled)
                }
                .padding(16)
            }
            .background(OrbitalTheme.background.ignoresSafeArea())
            .navigationTitle(kind.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .preferredColorScheme(.dark)
    }

    private var whenRows: [(String, String?)] {
        let date = entry.date
        let when = date.map { $0.formatted(.dateTime.weekday(.abbreviated).day().month(.abbreviated).hour().minute()) }
        return [
            ("When", [when, OrbitalDates.relative(entry.at)].compactMap { $0 }.joined(separator: " · ")),
            ("Ledger time", entry.local),
            ("By", entry.actor),
        ]
    }

    private func refKind(_ ref: String) -> String {
        ref.hasPrefix("EXP-") ? "Experiment" : ref.hasPrefix("TODO-") ? "To-do" : "Entry"
    }

    private func refTitle(_ ref: String) -> String? {
        block.experiments.first { $0.id == ref }?.title ?? block.todos.first { $0.id == ref }?.title
    }
}

struct MarketingTodoSheet: View {
    let todo: MarketingTodo
    let onDone: (String) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var note = ""

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(alignment: .top, spacing: 12) {
                        IconTile(symbol: todo.isDone ? "checkmark.circle.fill" : "person.fill.questionmark",
                                 tint: todo.isDone ? OrbitalTheme.success : OrbitalTheme.warning, size: 40, filled: true)
                        VStack(alignment: .leading, spacing: 6) {
                            Text(todo.title)
                                .font(.headline)
                                .foregroundStyle(OrbitalTheme.text)
                                .fixedSize(horizontal: false, vertical: true)
                            MarketingBrandChip(brand: todo.brand)
                        }
                    }
                    MarketingTextCard(title: "Why it needs you", text: todo.why)
                    MarketingTextCard(title: "Details", text: todo.details)
                    MarketingFacts(rows: [
                        ("Added", todo.date.map { $0.formatted(.dateTime.weekday(.wide).day().month(.wide).hour().minute()) }),
                        ("Waiting", todo.isDone ? nil : MarketingFeed.age(todo.at)),
                        ("Asked by", todo.actor),
                        ("Done", todo.doneDate.map { $0.formatted(.dateTime.weekday(.wide).day().month(.wide).hour().minute()) }),
                        ("Done by", todo.doneBy),
                        ("Note", todo.doneDetails),
                    ])
                    if !todo.isDone {
                        VStack(alignment: .leading, spacing: 8) {
                            OrbitalEyebrow(text: "Note (optional)")
                            TextField("What you did, for the ledger", text: $note, axis: .vertical)
                                .lineLimit(1...4)
                                .font(.subheadline)
                                .foregroundStyle(OrbitalTheme.text)
                                .padding(12)
                                .background(Color.white.opacity(0.05), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                            OrbitalActionButton(title: "Mark done", symbol: "checkmark.circle.fill") {
                                onDone(note)
                                dismiss()
                            }
                            Text("Recorded in the marketing ledger as done by you, with the time. The feed picks it up on its next run.")
                                .font(.caption)
                                .foregroundStyle(OrbitalTheme.textTertiary)
                        }
                    }
                    Text(todo.id)
                        .font(.caption2.monospaced())
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .textSelection(.enabled)
                }
                .padding(16)
            }
            .scrollDismissesKeyboard(.interactively)
            .background(OrbitalTheme.background.ignoresSafeArea())
            .navigationTitle(todo.isDone ? "Done" : "Needs you")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Close") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .preferredColorScheme(.dark)
    }
}

struct MarketingExperimentSheet: View {
    let experiment: MarketingExperiment
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        let status = MarketingStatus(experiment.status)
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    HStack(alignment: .top, spacing: 12) {
                        IconTile(symbol: status.symbol, tint: status.tint, size: 40, filled: true)
                        VStack(alignment: .leading, spacing: 6) {
                            Text(experiment.title)
                                .font(.headline)
                                .foregroundStyle(OrbitalTheme.text)
                                .fixedSize(horizontal: false, vertical: true)
                            FlowLayout(spacing: 6) {
                                MarketingBrandChip(brand: experiment.brand)
                                OrbitalChip(text: status.name, symbol: status.symbol, tint: status.tint)
                                if experiment.due {
                                    OrbitalChip(text: "Due for a read", symbol: "clock.badge.exclamationmark", tint: OrbitalTheme.warning)
                                }
                            }
                        }
                    }
                    MarketingTextCard(title: "Hypothesis", text: experiment.hypothesis)
                    MarketingFacts(rows: [
                        ("Metric", experiment.metric), ("Baseline", experiment.baseline), ("Target", experiment.target),
                        ("Check on", MarketingFeed.checkDay(experiment.check)),
                        ("Started", OrbitalDates.parse(experiment.at).map { $0.formatted(.dateTime.weekday(.wide).day().month(.wide).hour().minute()) }),
                    ])
                    MarketingTextCard(title: "Why", text: experiment.why)
                    MarketingTextCard(title: "Details", text: experiment.details)
                    if !experiment.results.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            OrbitalEyebrow(text: "Reads")
                            VStack(alignment: .leading, spacing: 0) {
                                ForEach(Array(experiment.results.reversed().enumerated()), id: \.offset) { index, result in
                                    if index > 0 {
                                        Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                                    }
                                    let style = MarketingStatus(result.status)
                                    VStack(alignment: .leading, spacing: 4) {
                                        HStack(spacing: 6) {
                                            OrbitalChip(text: style.name, symbol: style.symbol, tint: style.tint)
                                            Spacer(minLength: 0)
                                            if let at = OrbitalDates.parse(result.at) {
                                                Text(MarketingTime.dayAndTime(at))
                                                    .font(.caption2)
                                                    .foregroundStyle(OrbitalTheme.textTertiary)
                                            }
                                        }
                                        if let details = result.details {
                                            Text(details)
                                                .font(.footnote)
                                                .foregroundStyle(OrbitalTheme.textSecondary)
                                                .textSelection(.enabled)
                                                .fixedSize(horizontal: false, vertical: true)
                                        }
                                    }
                                    .padding(.vertical, 8)
                                }
                            }
                            .orbitalCard(padding: 12)
                        }
                    }
                    MarketingLinks(links: experiment.links)
                    Text(experiment.id)
                        .font(.caption2.monospaced())
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .textSelection(.enabled)
                }
                .padding(16)
            }
            .background(OrbitalTheme.background.ignoresSafeArea())
            .navigationTitle("Experiment")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
        .preferredColorScheme(.dark)
    }
}

// MARK: - Sheet parts

/// A titled paragraph on a card. Nothing when the text is empty.
struct MarketingTextCard: View {
    let title: String
    let text: String?

    var body: some View {
        if let text, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                OrbitalEyebrow(text: title)
                Text(text)
                    .font(.subheadline)
                    .foregroundStyle(OrbitalTheme.text.opacity(0.9))
                    .lineSpacing(2)
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .orbitalCard(padding: 12)
            }
        }
    }
}

/// Label and value rows. Rows with no value are left out; nothing at all when every row is empty.
struct MarketingFacts: View {
    var title: String?
    let rows: [(String, String?)]

    var body: some View {
        let shown = rows.compactMap { row -> (String, String)? in
            guard let value = row.1, !value.isEmpty else { return nil }
            return (row.0, value)
        }
        if !shown.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                if let title { OrbitalEyebrow(text: title) }
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(shown.enumerated()), id: \.offset) { index, row in
                        if index > 0 {
                            Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                        }
                        VStack(alignment: .leading, spacing: 2) {
                            Text(row.0)
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(OrbitalTheme.textTertiary)
                            Text(row.1)
                                .font(.subheadline)
                                .foregroundStyle(OrbitalTheme.text)
                                .textSelection(.enabled)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .padding(.vertical, 7)
                    }
                }
                .orbitalCard(padding: 12)
            }
        }
    }
}

/// https links open; file paths and commit ids are shown so they can be copied.
struct MarketingLinks: View {
    let links: [String]
    @Environment(\.openURL) private var openURL

    var body: some View {
        if !links.isEmpty {
            VStack(alignment: .leading, spacing: 8) {
                OrbitalEyebrow(text: "Links")
                VStack(alignment: .leading, spacing: 0) {
                    ForEach(Array(links.enumerated()), id: \.offset) { index, link in
                        if index > 0 {
                            Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                        }
                        if let url = URL(string: link), url.scheme == "https" || url.scheme == "http" {
                            Button {
                                openURL(url)
                            } label: {
                                HStack(spacing: 8) {
                                    Image(systemName: "link")
                                        .foregroundStyle(marketingTint)
                                    Text(link)
                                        .font(.footnote)
                                        .foregroundStyle(OrbitalTheme.text)
                                        .lineLimit(2)
                                        .truncationMode(.middle)
                                        .multilineTextAlignment(.leading)
                                    Spacer(minLength: 0)
                                    Image(systemName: "arrow.up.right")
                                        .font(.caption2.weight(.bold))
                                        .foregroundStyle(OrbitalTheme.textTertiary)
                                }
                                .padding(.vertical, 9)
                                .contentShape(Rectangle())
                            }
                            .buttonStyle(OrbitalPressStyle())
                        } else {
                            HStack(alignment: .top, spacing: 8) {
                                Image(systemName: "doc.text")
                                    .foregroundStyle(OrbitalTheme.textTertiary)
                                Text(link)
                                    .font(.caption.monospaced())
                                    .foregroundStyle(OrbitalTheme.textSecondary)
                                    .textSelection(.enabled)
                                    .fixedSize(horizontal: false, vertical: true)
                            }
                            .padding(.vertical, 9)
                        }
                    }
                }
                .orbitalCard(padding: 12)
            }
        }
    }
}
