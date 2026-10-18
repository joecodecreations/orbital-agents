import SwiftUI

struct SectionEditorView: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @State private var showCreate = false
    @State private var editing: CatalogEntry?
    @State private var pendingDelete: CatalogEntry?
    @Environment(\.orbitalPushed) private var pushed

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 12) {
                OrbitalScreenHeader(
                    eyebrow: "Left bar",
                    title: "Manage sections",
                    subtitle: "\(store.sortedCatalog.count) sections. Feeds own what is inside each one."
                )
                OrbitalActionButton(title: "New section", symbol: "plus", kind: .primary) {
                    showCreate = true
                }
                .padding(.bottom, 4)
                ForEach(Array(store.sortedCatalog.enumerated()), id: \.element.id) { index, entry in
                    sectionRow(entry, index: index, count: store.sortedCatalog.count)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, 36)
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
        }
        .scrollIndicators(.hidden)
        .orbitalScrollFade()
        .refreshable { await store.refresh() }
        .orbitalBackdrop(OrbitalTheme.accent)
        .navigationTitle("Manage sections")
        .orbitalNavigationBar(pushed: pushed)
        .sheet(isPresented: $showCreate) {
            SectionForm(mode: .create) { draft in
                Task {
                    await store.createSection(
                        id: draft.id,
                        title: draft.title,
                        subtitle: draft.subtitle,
                        icon: draft.icon,
                        accent: draft.accent,
                        order: draft.order,
                        layout: draft.layout,
                        space: draft.space
                    )
                }
            }
        }
        .sheet(item: $editing) { entry in
            SectionForm(mode: .edit(entry)) { draft in
                Task {
                    await store.updateSectionMeta(
                        id: entry.id,
                        title: draft.title,
                        subtitle: draft.subtitle,
                        icon: draft.icon,
                        accent: draft.accent,
                        order: draft.order,
                        layout: draft.layout,
                        space: draft.space
                    )
                }
            }
        }
        .alert(
            "Delete this section?",
            isPresented: Binding(
                get: { pendingDelete != nil },
                set: { if !$0 { pendingDelete = nil } }
            ),
            presenting: pendingDelete
        ) { entry in
            Button("Delete", role: .destructive) {
                Task { await store.deleteSection(entry.id) }
            }
            Button("Cancel", role: .cancel) {}
        } message: { entry in
            Text("\(entry.title) and its pages are removed from the hub. Its feed creates the section again on its next push.")
        }
        .safeAreaInset(edge: .bottom) {
            if let error = store.editorError {
                HStack(alignment: .top, spacing: 8) {
                    Image(systemName: "exclamationmark.triangle.fill")
                    Text(error)
                        .fixedSize(horizontal: false, vertical: true)
                }
                .font(.footnote)
                .foregroundStyle(OrbitalTheme.danger)
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .padding(.horizontal, 16)
                .padding(.bottom, 8)
            }
        }
    }

    private func sectionRow(_ entry: CatalogEntry, index: Int, count: Int) -> some View {
        let tint = OrbitalTheme.accentColor(entry.accent)
        return HStack(alignment: .center, spacing: 12) {
            Button {
                editing = entry
            } label: {
                HStack(alignment: .center, spacing: 12) {
                    AppIcon(image: entry.iconImage, symbol: entry.icon, tint: tint, size: 40, filled: true)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(entry.title)
                            .font(.headline)
                            .foregroundStyle(OrbitalTheme.text)
                            .lineLimit(2)
                            .multilineTextAlignment(.leading)
                        Text(entry.id)
                            .font(.caption.monospaced())
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .lineLimit(1)
                        FlowLayout(spacing: 6, lineSpacing: 6) {
                            OrbitalChip(text: layoutName(entry.layout), symbol: layoutSymbol(entry.layout), tint: tint)
                            if entry.layout == .sidebar {
                                OrbitalChip(text: entry.nodeCount == 1 ? "1 page" : "\(entry.nodeCount) pages", symbol: "doc.on.doc", tint: OrbitalTheme.textSecondary)
                            }
                            if let space = store.directory.space(forSection: entry.id) {
                                OrbitalChip(text: space.name, symbol: "square.stack.3d.up.fill", tint: space.tint)
                            }
                            if store.isPinned(entry.id) {
                                OrbitalChip(text: "On Home", symbol: "pin.fill", tint: OrbitalTheme.accent)
                            }
                        }
                        .padding(.top, 2)
                    }
                    Spacer(minLength: 0)
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityHint("Edit section")
            Menu {
                Button {
                    router.open(section: entry.id)
                } label: {
                    Label("Open", systemImage: "arrow.up.forward.app")
                }
                Button {
                    editing = entry
                } label: {
                    Label("Edit", systemImage: "pencil")
                }
                Button {
                    store.togglePin(entry.id)
                } label: {
                    Label(store.isPinned(entry.id) ? "Unpin from Home" : "Pin to Home", systemImage: store.isPinned(entry.id) ? "pin.slash" : "pin")
                }
                Button {
                    Task { await move(entry, delta: -1) }
                } label: {
                    Label("Move up", systemImage: "arrow.up")
                }
                .disabled(index == 0)
                Button {
                    Task { await move(entry, delta: 1) }
                } label: {
                    Label("Move down", systemImage: "arrow.down")
                }
                .disabled(index == count - 1)
                Divider()
                Button(role: .destructive) {
                    pendingDelete = entry
                } label: {
                    Label("Delete", systemImage: "trash")
                }
            } label: {
                Image(systemName: "ellipsis")
                    .font(.body.weight(.bold))
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .frame(width: 44, height: 44)
                    .background(Color.white.opacity(0.06), in: Circle())
            }
            .accessibilityLabel("Actions for \(entry.title)")
        }
        .orbitalCard(padding: 14)
    }

    private func layoutName(_ layout: SectionLayout) -> String {
        switch layout {
        case .page: return "Single page"
        case .tabs: return "Tabs"
        case .sidebar: return "Sidebar"
        }
    }

    private func layoutSymbol(_ layout: SectionLayout) -> String {
        switch layout {
        case .page: return "doc.text"
        case .tabs: return "rectangle.split.3x1"
        case .sidebar: return "sidebar.left"
        }
    }

    private func move(_ entry: CatalogEntry, delta: Int) async {
        let rows = store.sortedCatalog
        guard let index = rows.firstIndex(where: { $0.id == entry.id }) else { return }
        let next = index + delta
        guard rows.indices.contains(next) else { return }
        let neighbor = rows[next]
        await store.updateSectionMeta(
            id: entry.id,
            title: entry.title,
            subtitle: entry.subtitle ?? "",
            icon: entry.icon,
            accent: entry.accent ?? "",
            order: neighbor.order,
            layout: entry.layout,
            space: entry.space ?? ""
        )
        await store.updateSectionMeta(
            id: neighbor.id,
            title: neighbor.title,
            subtitle: neighbor.subtitle ?? "",
            icon: neighbor.icon,
            accent: neighbor.accent ?? "",
            order: entry.order,
            layout: neighbor.layout,
            space: neighbor.space ?? ""
        )
    }
}

private struct SectionDraft {
    var id: String
    var title: String
    var subtitle: String
    var icon: String
    var accent: String
    var order: Int
    var layout: SectionLayout
    var space: String
}

private struct SectionForm: View {
    enum Mode {
        case create
        case edit(CatalogEntry)
    }

    let mode: Mode
    var onSave: (SectionDraft) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var id = ""
    @State private var title = ""
    @State private var subtitle = ""
    @State private var icon = "square.grid.2x2"
    @State private var accent = ""
    @State private var order = 50
    @State private var layout: SectionLayout = .page
    @State private var space = ""
    @State private var localError: String?

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    HStack(spacing: 14) {
                        IconTile(symbol: icon, tint: OrbitalTheme.accentColor(accent.isEmpty ? nil : accent), size: 48, filled: true)
                        VStack(alignment: .leading, spacing: 3) {
                            Text(title.isEmpty ? "Section title" : title)
                                .font(.headline)
                                .foregroundStyle(title.isEmpty ? OrbitalTheme.textTertiary : OrbitalTheme.text)
                            Text(subtitle.isEmpty ? "Preview in the left bar" : subtitle)
                                .font(.caption)
                                .foregroundStyle(OrbitalTheme.textSecondary)
                        }
                    }
                    .padding(.vertical, 6)
                }
                .listRowBackground(OrbitalTheme.surfaceRaised)
                Section("Identity") {
                    if case .create = mode {
                        TextField("id", text: $id)
                            .font(.body.monospaced())
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                    }
                    TextField("Title", text: $title)
                    TextField("Subtitle", text: $subtitle)
                    TextField("Space (optional)", text: $space)
                        .autocorrectionDisabled()
                }
                .listRowBackground(OrbitalTheme.surface)
                Section("Look") {
                    TextField("SF Symbol", text: $icon)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                    TextField("Accent #RRGGBB", text: $accent)
                        .font(.body.monospaced())
                        .textInputAutocapitalization(.never)
                    ScrollView(.horizontal, showsIndicators: false) {
                        HStack(spacing: 10) {
                            ForEach(OrbitalTheme.accentSwatches, id: \.self) { hex in
                                let selected = accent.uppercased() == hex
                                Button {
                                    accent = hex
                                } label: {
                                    Circle()
                                        .fill(OrbitalTheme.accentColor(hex))
                                        .frame(width: 30, height: 30)
                                        .overlay(Circle().strokeBorder(Color.white.opacity(selected ? 0.9 : 0), lineWidth: 2))
                                        .padding(3)
                                        .overlay(Circle().strokeBorder(OrbitalTheme.accentColor(hex).opacity(selected ? 0.6 : 0), lineWidth: 2))
                                        .frame(width: 44, height: 44)
                                }
                                .buttonStyle(.plain)
                                .accessibilityLabel("Accent \(hex)")
                                .accessibilityAddTraits(selected ? .isSelected : [])
                            }
                        }
                    }
                }
                .listRowBackground(OrbitalTheme.surface)
                Section("Layout") {
                    Picker("Layout", selection: $layout) {
                        Text("Single page").tag(SectionLayout.page)
                        Text("Tabs").tag(SectionLayout.tabs)
                        Text("Sidebar").tag(SectionLayout.sidebar)
                    }
                    .pickerStyle(.segmented)
                    Stepper("Order \(order)", value: $order, in: -999 ... 9999)
                }
                .listRowBackground(OrbitalTheme.surface)
                if let localError {
                    Section {
                        Label(localError, systemImage: "exclamationmark.triangle.fill")
                            .foregroundStyle(OrbitalTheme.danger)
                    }
                    .listRowBackground(OrbitalTheme.danger.opacity(0.12))
                }
            }
            .scrollContentBackground(.hidden)
            .background(OrbitalBackdrop(accent: OrbitalTheme.accentColor(accent.isEmpty ? nil : accent)))
            .navigationTitle(titleText)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Save") { save() }
                        .fontWeight(.semibold)
                }
            }
            .onAppear(perform: load)
        }
    }

    private var titleText: String {
        if case .create = mode { return "New section" }
        return "Edit section"
    }

    private func load() {
        if case .edit(let entry) = mode {
            id = entry.id
            title = entry.title
            subtitle = entry.subtitle ?? ""
            icon = entry.icon
            accent = entry.accent ?? ""
            order = entry.order
            layout = entry.layout
            space = entry.space ?? ""
        }
    }

    private func save() {
        let slug = id.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        let pattern = #"^[a-z0-9][a-z0-9-]{0,63}$"#
        if slug.range(of: pattern, options: .regularExpression) == nil {
            localError = "Id must be a lowercase slug."
            return
        }
        if title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            localError = "Title is required."
            return
        }
        if !accent.isEmpty && accent.range(of: #"^#[0-9A-Fa-f]{6}$"#, options: .regularExpression) == nil {
            localError = "Accent must look like #C084FC."
            return
        }
        onSave(SectionDraft(id: slug, title: title, subtitle: subtitle, icon: icon, accent: accent, order: order, layout: layout,
                            space: space.trimmingCharacters(in: .whitespacesAndNewlines)))
        dismiss()
    }
}
