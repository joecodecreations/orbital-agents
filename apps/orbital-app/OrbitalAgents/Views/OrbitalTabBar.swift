import SwiftUI
import UIKit

/// The iPhone bottom bar. Drawn here rather than with TabView so each tab keeps its own stack
/// alive behind the visible one, and the bar steps aside while the keyboard is up.
struct OrbitalTabBar: View {
    let selection: RootTab
    /// Red counts: things waiting on you.
    var badges: [RootTab: Int] = [:]
    /// Green counts: things in progress, like the tracker's working items.
    var activity: [RootTab: Int] = [:]
    let onSelect: (RootTab) -> Void

    static let tabs: [(tab: RootTab, title: String, symbol: String)] = [
        (.home, "Home", "house.fill"),
        (.agents, "Agents", "cpu"),
        (.desk, "Desk", "sparkles"),
        (.tracker, "Tracker", "dot.radiowaves.left.and.right"),
        (.alerts, "Alerts", "bell.fill"),
    ]

    var body: some View {
        HStack(spacing: 0) {
            ForEach(Self.tabs, id: \.tab) { item in
                button(item.tab, title: item.title, symbol: item.symbol)
            }
        }
        .padding(.horizontal, 4)
        .padding(.top, 6)
        .padding(.bottom, 2)
        .background {
            ZStack(alignment: .top) {
                Rectangle().fill(.ultraThinMaterial)
                Rectangle().fill(OrbitalTheme.backgroundDeep.opacity(0.82))
                Rectangle().fill(Color.white.opacity(0.08)).frame(height: 1 / UIScreen.main.scale)
            }
            .ignoresSafeArea(edges: .bottom)
        }
    }

    private func button(_ tab: RootTab, title: String, symbol: String) -> some View {
        let selected = tab == selection
        let count = badges[tab] ?? 0
        let working = activity[tab] ?? 0
        let shown = count > 0 ? count : working
        return Button {
            if !selected { UISelectionFeedbackGenerator().selectionChanged() }
            onSelect(tab)
        } label: {
            VStack(spacing: 3) {
                Image(systemName: symbol)
                    .font(.system(size: 19, weight: .semibold))
                    .frame(height: 24)
                    .overlay(alignment: .topTrailing) {
                        if shown > 0 {
                            Text(shown > 99 ? "99+" : "\(shown)")
                                .font(.system(size: 10, weight: .heavy))
                                .monospacedDigit()
                                .foregroundStyle(count > 0 ? .white : OrbitalTheme.backgroundDeep)
                                .padding(.horizontal, 4)
                                .frame(minWidth: 17, minHeight: 17)
                                .background(count > 0 ? OrbitalTheme.danger : OrbitalTheme.success, in: Capsule())
                                .offset(x: 10, y: -5)
                        }
                    }
                Text(title)
                    .font(.system(size: 10, weight: .semibold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
            }
            .foregroundStyle(selected ? OrbitalTheme.accent : OrbitalTheme.textTertiary)
            .frame(maxWidth: .infinity, minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(count > 0 ? "\(title), \(count)" : working > 0 ? "\(title), \(working) in progress" : title)
        .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
    }
}
