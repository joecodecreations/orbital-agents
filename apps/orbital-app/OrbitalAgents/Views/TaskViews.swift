import SwiftUI
import UIKit

// The tasks block: a checklist the app can tick (task.set_done) and add to (task.add). The feed that
// owns the list writes each tap back to its source and pushes the page again, which clears `pending`.

/// "Syncing" with a spinner, while the server holds a tap the owner has not written back yet.
struct SyncingChip: View {
    var body: some View {
        HStack(spacing: 5) {
            ProgressView()
                .controlSize(.mini)
                .tint(OrbitalTheme.textSecondary)
            Text("Syncing")
                .font(.caption2.weight(.semibold))
                .lineLimit(1)
        }
        .foregroundStyle(OrbitalTheme.textSecondary)
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(Color.white.opacity(0.06), in: Capsule())
        .overlay(Capsule().strokeBorder(Color.white.opacity(0.10), lineWidth: 1))
        .accessibilityLabel("Syncing")
    }
}

// MARK: - Tasks

struct TasksCard: View {
    let block: TasksBlock
    let accent: Color
    var location: PageLocation?
    @EnvironmentObject private var store: OrbitalStore
    @State private var expanded = false
    @State private var showAll = false
    @State private var openRows: Set<String> = []
    @State private var showAdd = false

    private var isCollapsed: Bool { block.collapsed && !expanded }

    private var visible: [TaskItem] {
        guard block.limit > 0, !showAll else { return block.items }
        return Array(block.items.prefix(block.limit))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            header
            if !isCollapsed {
                if block.items.isEmpty {
                    Text(Insights.nonEmpty(block.emptyText) ?? "Nothing here.")
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .padding(.vertical, 4)
                } else {
                    VStack(spacing: 0) {
                        ForEach(Array(visible.enumerated()), id: \.element.id) { index, item in
                            if index > 0 {
                                Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                            }
                            TaskRow(
                                item: item,
                                accent: accent,
                                expanded: openRows.contains(item.id),
                                canPost: location != nil,
                                onToggle: { toggle(item) },
                                onExpand: { expand(item) }
                            )
                        }
                    }
                    if block.limit > 0, block.items.count > block.limit {
                        Button {
                            withAnimation(.easeInOut(duration: 0.2)) { showAll.toggle() }
                        } label: {
                            HStack(spacing: 5) {
                                Text(showAll ? "Show fewer" : "Show all (\(block.items.count))")
                                Image(systemName: "chevron.down")
                                    .font(.caption2.weight(.bold))
                                    .rotationEffect(.degrees(showAll ? 180 : 0))
                            }
                            .font(.subheadline.weight(.semibold))
                            .foregroundStyle(accent)
                            .frame(maxWidth: .infinity, minHeight: 44)
                            .contentShape(Rectangle())
                        }
                        .buttonStyle(.plain)
                    }
                }
            }
        }
        .orbitalCard(padding: 16)
        .sheet(isPresented: $showAdd) {
            if let add = block.add, let location {
                AddTaskSheet(add: add, blockID: block.id, location: location, accent: accent)
                    .environmentObject(store)
            }
        }
    }

    @ViewBuilder
    private var header: some View {
        HStack(alignment: .center, spacing: 10) {
            if block.collapsed {
                Button {
                    withAnimation(.easeInOut(duration: 0.22)) { expanded.toggle() }
                } label: {
                    HStack(spacing: 10) {
                        titles
                        Spacer(minLength: 8)
                        Text("\(block.items.count)")
                            .font(.caption.weight(.bold))
                            .monospacedDigit()
                            .foregroundStyle(OrbitalTheme.textSecondary)
                            .padding(.horizontal, 8)
                            .padding(.vertical, 3)
                            .background(Color.white.opacity(0.08), in: Capsule())
                        Image(systemName: "chevron.down")
                            .font(.caption.weight(.bold))
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .rotationEffect(.degrees(expanded ? 180 : 0))
                    }
                    .frame(minHeight: 36)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .accessibilityHint(expanded ? "Collapses the list" : "Shows the list")
            } else {
                titles
                Spacer(minLength: 8)
            }
            if let add = block.add, location != nil {
                Button {
                    showAdd = true
                } label: {
                    HStack(spacing: 4) {
                        Image(systemName: "plus")
                            .font(.caption.weight(.heavy))
                        Text(Insights.nonEmpty(add.label) ?? "Add to-do")
                            .lineLimit(1)
                    }
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(accent)
                    .padding(.horizontal, 12)
                    .frame(minHeight: 34)
                    .background(accent.opacity(0.12), in: Capsule())
                    .overlay(Capsule().strokeBorder(accent.opacity(0.28), lineWidth: 1))
                    .frame(minHeight: 44)
                    .contentShape(Rectangle())
                }
                .buttonStyle(OrbitalPressStyle())
            }
        }
    }

    private var titles: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(Insights.nonEmpty(block.title) ?? "To-dos")
                .font(.headline)
                .foregroundStyle(OrbitalTheme.text)
                .multilineTextAlignment(.leading)
            if let subtitle = Insights.nonEmpty(block.subtitle) {
                Text(subtitle)
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .multilineTextAlignment(.leading)
            }
        }
    }

    private func expand(_ item: TaskItem) {
        withAnimation(.easeInOut(duration: 0.2)) {
            if openRows.contains(item.id) {
                openRows.remove(item.id)
            } else {
                openRows.insert(item.id)
            }
        }
    }

    private func toggle(_ item: TaskItem) {
        guard let location else { return }
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        Task {
            await store.perform(
                type: "task.set_done",
                sectionID: location.sectionID,
                itemID: item.id,
                blockID: block.id,
                nodeID: location.nodeID,
                tabID: location.tabID,
                value: ["done": !item.done]
            )
        }
    }
}

struct TaskRow: View {
    let item: TaskItem
    let accent: Color
    let expanded: Bool
    let canPost: Bool
    let onToggle: () -> Void
    let onExpand: () -> Void

    var body: some View {
        HStack(alignment: .top, spacing: 6) {
            checkbox
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .top, spacing: 8) {
                    Text(item.title)
                        .font(.subheadline.weight(.medium))
                        .strikethrough(item.done, color: OrbitalTheme.textTertiary)
                        .foregroundStyle(item.done ? OrbitalTheme.textSecondary : OrbitalTheme.text)
                        .multilineTextAlignment(.leading)
                        .fixedSize(horizontal: false, vertical: true)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    // Sized to the first line so the spinner never pushes the chips down.
                    Group {
                        if item.pending {
                            ProgressView()
                                .controlSize(.mini)
                                .tint(OrbitalTheme.textSecondary)
                                .accessibilityLabel("Syncing")
                        } else if hasMore {
                            Image(systemName: "chevron.down")
                                .font(.caption2.weight(.bold))
                                .foregroundStyle(OrbitalTheme.textTertiary)
                                .rotationEffect(.degrees(expanded ? 180 : 0))
                        }
                    }
                    .frame(width: 16, height: 20)
                }
                if hasMeta {
                    FlowLayout(spacing: 6, lineSpacing: 6) {
                        meta
                    }
                }
                if expanded {
                    if let note = Insights.nonEmpty(item.note) {
                        Text(note)
                            .font(.footnote)
                            .foregroundStyle(OrbitalTheme.text.opacity(0.85))
                            .fixedSize(horizontal: false, vertical: true)
                            .padding(10)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .background(Color.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    }
                    if item.done, let doneAt = OrbitalDates.shortDateTime(item.doneAt) ?? OrbitalDates.shortDay(item.doneAt) {
                        Label("Done \(doneAt)", systemImage: "checkmark.circle")
                            .font(.caption)
                            .foregroundStyle(OrbitalTheme.success.opacity(0.9))
                    }
                }
            }
            .padding(.top, 11)
            .padding(.bottom, 10)
            .contentShape(Rectangle())
            .onTapGesture(perform: onExpand)
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(hasMore ? .isButton : [])
            .accessibilityHint(hasMore ? (expanded ? "Hides the note" : "Shows the note") : "")
        }
    }

    private var hasMore: Bool {
        Insights.nonEmpty(item.note) != nil || (item.done && item.doneAt != nil)
    }

    @ViewBuilder
    private var checkbox: some View {
        if item.canToggle, canPost {
            Button(action: onToggle) {
                checkGlyph
                    .frame(width: 44, height: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(OrbitalPressStyle())
            .accessibilityLabel(item.done ? "Done" : "Not done")
            .accessibilityValue(item.title)
            .accessibilityHint(item.done ? "Marks it not done" : "Marks it done")
        } else {
            // The feed closes this row itself, or it is still being added.
            Image(systemName: "circle.dashed")
                .font(.system(size: 20, weight: .regular))
                .foregroundStyle(OrbitalTheme.textTertiary)
                .frame(width: 44, height: 44)
                .accessibilityHidden(true)
        }
    }

    private var checkGlyph: some View {
        ZStack {
            Circle()
                .strokeBorder(item.done ? accent : OrbitalTheme.textTertiary, lineWidth: 1.8)
                .background(Circle().fill(item.done ? accent : Color.clear))
                .frame(width: 24, height: 24)
            if item.done {
                Image(systemName: "checkmark")
                    .font(.system(size: 11, weight: .heavy))
                    .foregroundStyle(OrbitalTheme.backgroundDeep)
            }
        }
        .animation(.spring(response: 0.25, dampingFraction: 0.7), value: item.done)
    }

    private var hasMeta: Bool {
        item.group != nil || DayMath.due(item) != nil || shownStatus != nil || !item.tags.isEmpty
    }

    private var shownStatus: String? {
        guard let status = Insights.nonEmpty(item.status) else { return nil }
        return item.done && status.lowercased() == "done" ? nil : status
    }

    @ViewBuilder
    private var meta: some View {
        if let group = item.group {
            OrbitalChip(text: group, symbol: "folder.fill", tint: accent)
        }
        if let due = DayMath.due(item) {
            HStack(spacing: 4) {
                Image(systemName: "calendar")
                    .font(.system(size: 10, weight: .bold))
                Text(due.text)
                    .font(.caption.weight(.medium))
                    .lineLimit(1)
            }
            .foregroundStyle(due.tint)
            .padding(.vertical, 4)
        }
        if let status = shownStatus {
            OrbitalChip(text: status.capitalized, symbol: DayMath.statusSymbol(status), tint: DayMath.statusColor(status))
        }
        ForEach(item.tags, id: \.self) { tag in
            OrbitalChip(text: tag, tint: tag == "new" ? accent : OrbitalTheme.textSecondary)
        }
    }
}

// MARK: - Add form

struct AddTaskSheet: View {
    let add: TasksAdd
    let blockID: String
    let location: PageLocation
    let accent: Color
    @EnvironmentObject private var store: OrbitalStore
    @Environment(\.dismiss) private var dismiss
    @State private var title = ""
    @State private var hasDue = false
    @State private var due = Calendar.current.date(byAdding: .day, value: 7, to: Date()) ?? Date()
    @State private var remindKey: String
    @State private var group: String?
    @State private var note = ""
    @FocusState private var titleFocused: Bool

    init(add: TasksAdd, blockID: String, location: PageLocation, accent: Color) {
        self.add = add
        self.blockID = blockID
        self.location = location
        self.accent = accent
        let choices = add.reminderChoices
        let preferred = choices.first { $0.days == [1, 0] } ?? choices.first
        _remindKey = State(initialValue: preferred?.id ?? "")
        _group = State(initialValue: add.groups.contains { $0.id == add.group } ? add.group : add.groups.first?.id)
    }

    private var trimmedTitle: String { title.trimmingCharacters(in: .whitespacesAndNewlines) }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("What needs doing", text: $title, axis: .vertical)
                        .lineLimit(1 ... 4)
                        .focused($titleFocused)
                        .submitLabel(.done)
                } header: {
                    Text("Title")
                }
                .listRowBackground(rowBackground)

                if !add.groups.isEmpty {
                    Section {
                        Picker("Group", selection: $group) {
                            ForEach(add.groups) { choice in
                                Text(choice.label).tag(String?.some(choice.id))
                            }
                        }
                    }
                    .listRowBackground(rowBackground)
                }

                Section {
                    Toggle("Due date", isOn: $hasDue.animation(.easeInOut(duration: 0.2)))
                        .tint(accent)
                    if hasDue {
                        DatePicker("Due", selection: $due, displayedComponents: .date)
                    }
                    Picker("Reminder", selection: $remindKey) {
                        ForEach(add.reminderChoices) { option in
                            Text(option.label).tag(option.id)
                        }
                    }
                    .disabled(!hasDue)
                } footer: {
                    Text(hasDue ? "Reminders arrive in the morning on those days." : "Reminders need a due date.")
                }
                .listRowBackground(rowBackground)

                Section {
                    TextEditor(text: $note)
                        .frame(minHeight: 96)
                        .scrollContentBackground(.hidden)
                } header: {
                    Text("Note")
                }
                .listRowBackground(rowBackground)
            }
            .scrollContentBackground(.hidden)
            .orbitalBackdrop(accent)
            .navigationTitle(Insights.nonEmpty(add.label) ?? "Add to-do")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save", action: save)
                        .fontWeight(.semibold)
                        .disabled(trimmedTitle.isEmpty)
                }
            }
            .onAppear { titleFocused = true }
        }
        .preferredColorScheme(.dark)
        .tint(accent)
        .presentationBackground(OrbitalTheme.background)
    }

    private var rowBackground: Color { OrbitalTheme.surfaceRaised.opacity(0.75) }

    private func save() {
        guard !trimmedTitle.isEmpty else { return }
        var value: [String: Any] = ["title": String(trimmedTitle.prefix(200))]
        if let group = group ?? add.group { value["group"] = group }
        if hasDue {
            value["due"] = DayMath.isoDay(due)
            if let option = add.reminderChoices.first(where: { $0.id == remindKey }) {
                value["remind_days"] = option.days
            }
        }
        let trimmedNote = note.trimmingCharacters(in: .whitespacesAndNewlines)
        if !trimmedNote.isEmpty { value["note"] = String(trimmedNote.prefix(2000)) }
        UIImpactFeedbackGenerator(style: .light).impactOccurred()
        let store = store
        let location = location
        let blockID = blockID
        Task {
            await store.perform(
                type: "task.add",
                sectionID: location.sectionID,
                blockID: blockID,
                nodeID: location.nodeID,
                tabID: location.tabID,
                value: value,
                source: "form"
            )
        }
        dismiss()
    }
}

