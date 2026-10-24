import SwiftUI
import UIKit

/// Resigns the first responder so the iPhone tab bar comes back after typing.
enum OrbitalKeyboard {
    static func dismiss() {
        UIApplication.shared.sendAction(#selector(UIResponder.resignFirstResponder), to: nil, from: nil, for: nil)
    }
}

/// The iPhone bottom bar. Drawn here rather than with TabView so each tab keeps its own stack
/// alive behind the visible one. While the keyboard is up a slim Done strip replaces it so you
/// can leave the field without force-closing the app.
struct OrbitalTabBar: View {
    let selection: RootTab
    /// Red counts: things waiting on you.
    var badges: [RootTab: Int] = [:]
    /// Green counts: things in progress, like the tracker's working items.
    var activity: [RootTab: Int] = [:]
    /// The hub's extra items, after the fixed six and Sections.
    var extra: [NavItem] = []
    let onSelect: (RootTab) -> Void
    /// Opens the spaces-and-pages drawer (sidebar on a wide window).
    var onOpenSections: (() -> Void)? = nil

    static let tabs: [(tab: RootTab, title: String, symbol: String)] = [
        (.home, "Home", "house.fill"),
        (.money, "Money", "dollarsign.circle.fill"),
        (.agents, "System", "cpu"),
        (.desk, "Agent", "sparkles"),
        (.tracker, "Tracker", "dot.radiowaves.left.and.right"),
        (.alerts, "Alerts", "bell.fill"),
    ]

    /// Fixed six + Sections + hub extras. Scroll when more than seven.
    private var itemCount: Int { Self.tabs.count + 1 + extra.count }

    var body: some View {
        Group {
            if itemCount <= 7 {
                HStack(spacing: 0) { items(width: nil) }
            } else {
                // Crowded bar: scroll sideways, each item keeps a comfortable width.
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 0) { items(width: 68) }
                }
            }
        }
        .padding(.horizontal, 4)
        .padding(.top, 6)
        .padding(.bottom, 2)
        .background { barBackground }
    }

    @ViewBuilder
    private func items(width: CGFloat?) -> some View {
        ForEach(Self.tabs, id: \.tab) { item in
            button(item.tab, title: item.title, symbol: item.symbol, width: width)
        }
        sectionsButton(width: width)
        ForEach(extra) { item in
            button(.custom(item.id), title: item.title, symbol: item.symbol, width: width, navItem: item)
        }
    }

    private func sectionsButton(width: CGFloat?) -> some View {
        Button {
            UISelectionFeedbackGenerator().selectionChanged()
            onOpenSections?()
        } label: {
            VStack(spacing: 3) {
                Image(systemName: "square.grid.2x2.fill")
                    .font(.system(size: 19, weight: .semibold))
                    .frame(height: 24)
                Text("Sections")
                    .font(.system(size: 10, weight: .semibold))
                    .lineLimit(1)
                    .minimumScaleFactor(0.8)
            }
            .foregroundStyle(OrbitalTheme.textTertiary)
            .frame(minWidth: width, maxWidth: width ?? .infinity, minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel("Sections")
        .accessibilityHint("Opens spaces and pages")
    }

    private func button(_ tab: RootTab, title: String, symbol: String, width: CGFloat?, navItem: NavItem? = nil) -> some View {
        let selected = tab == selection
        let count = badges[tab] ?? 0
        let working = activity[tab] ?? 0
        let shown = count > 0 ? count : working
        return Button {
            if !selected { UISelectionFeedbackGenerator().selectionChanged() }
            onSelect(tab)
        } label: {
            VStack(spacing: 3) {
                Group {
                    if let item = navItem {
                        NavIcon(item: item, size: 22)
                    } else {
                        Image(systemName: symbol)
                            .font(.system(size: 19, weight: .semibold))
                    }
                }
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
            .frame(minWidth: width, maxWidth: width ?? .infinity, minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityLabel(count > 0 ? "\(title), \(count)" : working > 0 ? "\(title), \(working) in progress" : title)
        .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
    }

    private var barBackground: some View {
        ZStack(alignment: .top) {
            Rectangle().fill(.ultraThinMaterial)
            Rectangle().fill(OrbitalTheme.backgroundDeep.opacity(0.82))
            Rectangle().fill(Color.white.opacity(0.08)).frame(height: 1 / UIScreen.main.scale)
        }
        .ignoresSafeArea(edges: .bottom)
    }
}

/// Slim strip shown while the keyboard covers the tab bar: Done restores the bar, Sections opens
/// spaces and pages without force-closing the app.
struct OrbitalKeyboardBar: View {
    var onDone: () -> Void
    var onOpenSections: () -> Void

    var body: some View {
        HStack(spacing: 12) {
            Button(action: onOpenSections) {
                Label("Sections", systemImage: "square.grid.2x2.fill")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .frame(minHeight: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityHint("Opens spaces and pages")

            Spacer(minLength: 0)

            Button(action: onDone) {
                Text("Done")
                    .font(.body.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.accent)
                    .padding(.horizontal, 14)
                    .frame(minHeight: 44)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Done")
            .accessibilityHint("Dismisses the keyboard and shows the tab bar")
        }
        .padding(.horizontal, 16)
        .padding(.top, 4)
        .padding(.bottom, 2)
        .background {
            ZStack(alignment: .top) {
                Rectangle().fill(.ultraThinMaterial)
                Rectangle().fill(OrbitalTheme.backgroundDeep.opacity(0.92))
                Rectangle().fill(Color.white.opacity(0.08)).frame(height: 1 / UIScreen.main.scale)
            }
            .ignoresSafeArea(edges: .bottom)
        }
    }
}
