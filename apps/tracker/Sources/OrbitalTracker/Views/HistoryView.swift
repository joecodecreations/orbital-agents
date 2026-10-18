import SwiftUI
import OrbitalTrackerCore

struct HistoryView: View {
    @Bindable var model: AppModel

    var body: some View {
        let all = model.store.history(model.filter, range: model.historyRange)
        let size = model.historyPageSize
        let pages = max(1, Int(ceil(Double(all.count) / Double(size))))
        let page = min(model.historyPage, pages - 1)
        let slice = Array(all.dropFirst(page * size).prefix(size))

        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                ForEach(HistoryRange.allCases) { range in
                    Button {
                        model.historyRange = range
                    } label: {
                        Text(range.label)
                            .font(.system(size: 11, weight: .semibold, design: .rounded))
                            .foregroundStyle(model.historyRange == range ? .black : .white.opacity(0.8))
                            .padding(.horizontal, 9).padding(.vertical, 4)
                            .background(Capsule().fill(model.historyRange == range ? Color.white.opacity(0.92) : Color.white.opacity(0.08)))
                    }
                    .buttonStyle(.plain)
                }
                Spacer()
                Text("\(all.count) done")
                    .font(.system(size: 10.5, weight: .medium))
                    .foregroundStyle(.white.opacity(0.5))
            }

            if slice.isEmpty {
                Text(emptyText)
                    .font(.system(size: 12))
                    .foregroundStyle(.white.opacity(0.55))
                    .frame(maxWidth: .infinity, alignment: .center)
                    .padding(.vertical, 18)
            } else {
                CappedScroll(maxHeight: 470) {
                    VStack(alignment: .leading, spacing: 2) {
                        ForEach(slice) { item in row(item) }
                    }
                }
            }

            if !all.isEmpty {
                HStack(spacing: 8) {
                    if pages > 1 {
                        pageButton("chevron.left", enabled: page > 0) { model.historyPage = page - 1 }
                        Text("Page \(page + 1) of \(pages)")
                            .font(.system(size: 10.5, weight: .medium).monospacedDigit())
                            .foregroundStyle(.white.opacity(0.6))
                        pageButton("chevron.right", enabled: page < pages - 1) { model.historyPage = page + 1 }
                        Text("\(page * size + 1)–\(page * size + slice.count) of \(all.count)")
                            .font(.system(size: 10, weight: .medium).monospacedDigit())
                            .foregroundStyle(.white.opacity(0.4))
                    } else {
                        Text(all.count == 1 ? "1 item" : "All \(all.count) on one page")
                            .font(.system(size: 10.5, weight: .medium).monospacedDigit())
                            .foregroundStyle(.white.opacity(0.5))
                    }
                    Spacer(minLength: 6)
                    PageSizeControl(model: model)
                }
            }
        }
        .padding(13)
        .background(Glass(cornerRadius: 16))
    }

    private var emptyText: String {
        switch model.historyRange {
        case .today: return "Nothing finished yet today."
        case .week: return "Nothing finished this week yet."
        case .month: return "Nothing finished this month yet."
        case .all: return "No finished items yet."
        }
    }

    private func row(_ item: TrackedItem) -> some View {
        let open = model.expandedHistoryID == item.id
        let done = item.completedAt ?? item.updatedAt
        return VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 8) {
                Circle().fill(Palette.color(item.colorIndex)).frame(width: 6, height: 6)
                SourceIcon(item: item, size: 16)
                Button {
                    model.toggleHistoryDetail(item.id)
                } label: {
                    Text(item.title)
                        .font(.system(size: 12.5, weight: .semibold, design: .rounded))
                        .foregroundStyle(.white)
                        .lineLimit(1)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help(model.detailStyle == .window ? "Show details in a window" : "Show details")
                if !item.subagents.isEmpty {
                    Label("\(item.subagents.count)", systemImage: "person.2.fill")
                        .font(.system(size: 9.5, weight: .semibold))
                        .foregroundStyle(.white.opacity(0.5))
                }
                if !item.media.isEmpty {
                    Image(systemName: "photo.on.rectangle").font(.system(size: 10)).foregroundStyle(.white.opacity(0.5))
                }
                if let machine = item.machine, !machine.isEmpty {
                    Text(machine)
                        .font(.system(size: 9.5, weight: .medium))
                        .foregroundStyle(.white.opacity(machine == model.config.machineName ? 0.3 : 0.5))
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .frame(maxWidth: 76, alignment: .trailing)
                        .help("Worked on \(machine)")
                }
                Text(Fmt.clock(done))
                    .font(.system(size: 10.5).monospacedDigit())
                    .foregroundStyle(.white.opacity(0.55))
                Text(Fmt.duration(done.timeIntervalSince(item.createdAt)))
                    .font(.system(size: 9.5, weight: .semibold, design: .rounded).monospacedDigit())
                    .foregroundStyle(.white.opacity(0.7))
                    .padding(.horizontal, 6).padding(.vertical, 2)
                    .background(Capsule().fill(Color.white.opacity(0.08)))
            }
            if open {
                ItemDetailBody(item: item, store: model.store)
                    .padding(10)
                    .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(Color.white.opacity(0.05)))
            }
        }
        .padding(.vertical, 5)
        .padding(.horizontal, 4)
        .background(RoundedRectangle(cornerRadius: 8).fill(open ? Color.white.opacity(0.04) : .clear))
    }

    private func pageButton(_ symbol: String, enabled: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 10, weight: .bold))
                .foregroundStyle(.white.opacity(enabled ? 0.9 : 0.25))
                .frame(width: 24, height: 22)
                .background(Capsule().fill(Color.white.opacity(enabled ? 0.1 : 0.03)))
        }
        .buttonStyle(.plain)
        .disabled(!enabled)
    }
}

/// "[10] per page ⌄": click the number and type any page size, or pick a preset from the menu.
struct PageSizeControl: View {
    @Bindable var model: AppModel
    @State private var draft = ""
    @FocusState private var focused: Bool
    static let presets = [5, 10, 15, 20, 25, 40, 50, 100]

    var body: some View {
        HStack(spacing: 4) {
            if model.isEditingPageSize {
                TextField("", text: $draft)
                    .textFieldStyle(.plain)
                    .font(.system(size: 11, weight: .bold).monospacedDigit())
                    .foregroundStyle(.white)
                    .multilineTextAlignment(.center)
                    .frame(width: 34)
                    .padding(.vertical, 2)
                    .background(RoundedRectangle(cornerRadius: 5).fill(Color.white.opacity(0.16)))
                    .overlay(RoundedRectangle(cornerRadius: 5).strokeBorder(Palette.ok.opacity(0.7), lineWidth: 1))
                    .focused($focused)
                    .onChange(of: draft) { _, text in
                        let digits = String(text.filter(\.isNumber).prefix(3))
                        if digits != text { draft = digits }
                    }
                    .onSubmit(commit)
                    .onExitCommand { model.isEditingPageSize = false }
                    .onChange(of: focused) { _, isFocused in
                        if !isFocused, model.isEditingPageSize { commit() }
                    }
                    .onAppear {
                        draft = "\(model.historyPageSize)"
                        DispatchQueue.main.async { focused = true }
                    }
                    .help("Type a number, then Enter (Esc cancels)")
            } else {
                Button { model.editPageSize() } label: {
                    Text("\(model.historyPageSize)")
                        .font(.system(size: 11, weight: .bold).monospacedDigit())
                        .foregroundStyle(.white)
                        .frame(minWidth: 22)
                        .padding(.horizontal, 5).padding(.vertical, 2)
                        .background(RoundedRectangle(cornerRadius: 5).fill(Color.white.opacity(0.1)))
                        .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .help("Click to type how many items to show per page")
            }
            Text("per page")
                .font(.system(size: 10.5, weight: .medium))
                .foregroundStyle(.white.opacity(0.6))
            Menu {
                ForEach(Self.presets, id: \.self) { n in
                    Toggle("\(n) per page", isOn: Binding(
                        get: { model.historyPageSize == n },
                        set: { if $0 { model.setHistoryPageSize(n) } }))
                }
                Divider()
                Button("Custom number…") { model.editPageSize() }
            } label: {
                Image(systemName: "chevron.down")
                    .font(.system(size: 8.5, weight: .bold))
                    .foregroundStyle(.white.opacity(0.7))
                    .frame(width: 18, height: 18)
                    .contentShape(Rectangle())
            }
            .menuStyle(.borderlessButton)
            .menuIndicator(.hidden)
            .fixedSize()
            .help("Items per page")
        }
        .padding(.leading, 4).padding(.trailing, 2).padding(.vertical, 2)
        .background(Capsule().fill(Color.white.opacity(0.06)))
    }

    private func commit() {
        model.isEditingPageSize = false
        if let n = Int(draft), n > 0 { model.setHistoryPageSize(n) }
    }
}
