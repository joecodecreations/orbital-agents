import SwiftUI
import OrbitalTrackerCore

/// Wraps bubbles onto as many rows as needed.
struct FlowLayout: Layout {
    var spacing: CGFloat = 7
    var lineSpacing: CGFloat = 7

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let rows = arrange(width: proposal.width ?? .infinity, subviews: subviews)
        let height = rows.reduce(0) { $0 + $1.height } + CGFloat(max(rows.count - 1, 0)) * lineSpacing
        let width = rows.map(\.width).max() ?? 0
        return CGSize(width: proposal.width ?? width, height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in arrange(width: bounds.width, subviews: subviews) {
            var x = bounds.minX
            for index in row.indices {
                let size = subviews[index].sizeThatFits(.unspecified)
                subviews[index].place(at: CGPoint(x: x, y: y + (row.height - size.height) / 2), proposal: ProposedViewSize(size))
                x += size.width + spacing
            }
            y += row.height + lineSpacing
        }
    }

    private struct Row {
        var indices: [Int] = []
        var width: CGFloat = 0
        var height: CGFloat = 0
    }

    private func arrange(width maxWidth: CGFloat, subviews: Subviews) -> [Row] {
        var rows: [Row] = []
        var row = Row()
        for (i, view) in subviews.enumerated() {
            var size = view.sizeThatFits(.unspecified)
            size.width = min(size.width, maxWidth)
            let needed = row.indices.isEmpty ? size.width : row.width + spacing + size.width
            if needed > maxWidth, !row.indices.isEmpty {
                rows.append(row)
                row = Row()
            }
            row.width = row.indices.isEmpty ? size.width : row.width + spacing + size.width
            row.height = max(row.height, size.height)
            row.indices.append(i)
        }
        if !row.indices.isEmpty { rows.append(row) }
        return rows
    }
}

struct BubbleView: View {
    let item: TrackedItem
    let isSelected: Bool
    var onTap: () -> Void
    var onClose: () -> Void
    @State private var hover = false
    @State private var hoverX = false

    private var color: Color { Palette.color(item.colorIndex) }
    private var done: Bool { item.status == .done }
    private var runningSubs: Int { item.subagents.filter { $0.status == .active }.count }

    var body: some View {
        HStack(spacing: 6) {
            SourceIcon(item: item, size: 17)
            if done {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 12, weight: .bold))
                    .foregroundStyle(.white, Palette.ok)
            }
            Text(item.title)
                .font(.system(size: 12.5, weight: .semibold, design: .rounded))
                .foregroundStyle(.white)
                .lineLimit(1)
                .truncationMode(.tail)
                .frame(maxWidth: 230, alignment: .leading)
                .fixedSize(horizontal: true, vertical: false)
            if item.processing {
                ProgressView().controlSize(.mini).tint(.white)
            }
            if !item.subagents.isEmpty {
                HStack(spacing: 2) {
                    Image(systemName: "person.2.fill").font(.system(size: 8, weight: .bold))
                    Text(runningSubs > 0 ? "\(runningSubs)" : "\(item.subagents.count)")
                        .font(.system(size: 9.5, weight: .bold, design: .rounded))
                }
                .foregroundStyle(.white.opacity(0.9))
                .padding(.horizontal, 5).padding(.vertical, 2)
                .background(Capsule().fill(Color.black.opacity(0.28)))
                .help(runningSubs > 0 ? "\(runningSubs) subagent(s) running" : "\(item.subagents.count) subagent(s) finished")
            }
            Button(action: onClose) {
                Image(systemName: "xmark")
                    .font(.system(size: 8.5, weight: .heavy))
                    .foregroundStyle(.white.opacity(hoverX ? 1 : 0.8))
                    .frame(width: 17, height: 17)
                    .background(Circle().fill(Color.black.opacity(hoverX ? 0.45 : 0.25)))
            }
            .buttonStyle(.plain)
            .onHover { hoverX = $0 }
            .help(done ? "Clear from the floater" : "Mark done and clear")
        }
        .padding(.leading, 6)
        .padding(.trailing, 5)
        .frame(height: 30)
        .background(background)
        .overlay { if !done { BlinkOverlay() } }
        .scaleEffect(hover ? 1.025 : 1)
        .animation(.easeOut(duration: 0.12), value: hover)
        .contentShape(Capsule())
        .onHover { hover = $0 }
        .onTapGesture(perform: onTap)
        .help(item.details.isEmpty ? item.title : String(item.details.prefix(200)))
    }

    private var background: some View {
        ZStack {
            Capsule().fill(Color.black.opacity(0.3)).shadow(color: .black.opacity(0.3), radius: 6, y: 3)
            VisualEffectBlur(material: .hudWindow, cornerRadius: 15).clipShape(Capsule())
            Capsule().fill(LinearGradient(colors: [color.opacity(0.88), color.opacity(0.66)],
                                          startPoint: .topLeading, endPoint: .bottomTrailing))
            Capsule().strokeBorder(isSelected ? Color.white.opacity(0.95) : color.opacity(0.95),
                                   lineWidth: isSelected ? 1.6 : 1)
        }
    }
}

/// Still-being-worked-on blink: dims and lifts every 0.8 s. Driven by wall-clock
/// ticks so every bubble blinks in step, and only this overlay redraws.
struct BlinkOverlay: View {
    static let interval: TimeInterval = 0.8

    var body: some View {
        TimelineView(.periodic(from: Date(timeIntervalSinceReferenceDate: 0), by: Self.interval)) { context in
            let lit = Int(context.date.timeIntervalSinceReferenceDate / Self.interval) % 2 == 0
            Capsule()
                .fill(Color.black.opacity(lit ? 0 : 0.5))
                .animation(.easeInOut(duration: 0.2), value: lit)
        }
        .allowsHitTesting(false)
    }
}
