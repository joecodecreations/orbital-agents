import SwiftUI
import UIKit
import UserNotifications

struct AlertsInboxView: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @Environment(\.orbitalPushed) private var pushed
    @Environment(\.scenePhase) private var scenePhase
    @Environment(\.openURL) private var openURL
    @Namespace private var filterPill
    @State private var showHistory = false
    @State private var permission: UNAuthorizationStatus?
    @State private var sending = false
    @State private var scheduledFor: Date?
    @State private var lastSentAt: Date?
    @State private var showScheduled = false
    /// The type chip picked under Waiting / History: a section id, "" for the app's own, nil for all.
    @State private var selectedType: String?

    private let accent = OrbitalTheme.accent


    /// What the test notification exercises, in the order iOS shows it.
    private static let testFeatures: [(String, String, Color)] = [
        ("Image", "photo", OrbitalTheme.accent),
        ("Subtitle", "text.alignleft", OrbitalTheme.accent),
        ("Sound", "speaker.wave.2.fill", OrbitalTheme.accent),
        ("Badge", "app.badge.fill", OrbitalTheme.accent),
        ("Open", "arrow.up.forward.app", OrbitalTheme.success),
        ("Reply", "text.bubble.fill", OrbitalTheme.success),
        ("Snooze", "moon.zzz.fill", OrbitalTheme.success),
        ("Dismiss", "xmark", OrbitalTheme.danger),
        ("Time Sensitive", "bolt.fill", OrbitalTheme.warning),
        ("Grouped", "square.stack.fill", OrbitalTheme.violet),
        ("Deep link", "link", OrbitalTheme.violet),
    ]

    /// Waiting or History, every type.
    private var listed: [OrbitalAlert] {
        // Reminders still waiting for their deliver_at live in the Scheduled group, not here.
        let now = max(store.alertClock, Date())
        let fired = store.alerts.filter { $0.isAcked || !$0.isScheduled(at: now) }
        let filtered = showHistory ? fired : fired.filter { !$0.isAcked }
        return filtered.sorted { lhs, rhs in
            if lhs.isAcked != rhs.isAcked { return !lhs.isAcked && rhs.isAcked }
            return (lhs.createdAt ?? "") > (rhs.createdAt ?? "")
        }
    }

    private var rows: [OrbitalAlert] {
        guard let type = activeType else { return listed }
        return listed.filter { Self.typeKey($0) == type }
    }

    /// One chip per source in the current list, in sidebar order, the app's own last.
    private var types: [AlertType] {
        let order = Dictionary(
            store.sortedCatalog.enumerated().map { ($1.id, $0) },
            uniquingKeysWith: { first, _ in first }
        )
        return Dictionary(grouping: listed, by: Self.typeKey)
            .map { key, alerts in
                let entry = key.isEmpty ? nil : store.entry(key)
                return AlertType(
                    id: key,
                    title: entry?.title ?? (key.isEmpty ? OrbitalBrand.appName : key),
                    icon: entry?.iconImage,
                    symbol: entry?.icon ?? "bell.fill",
                    tint: OrbitalTheme.accentColor(entry?.accent),
                    count: alerts.count
                )
            }
            .sorted { lhs, rhs in
                let left = lhs.id.isEmpty ? Int.max : order[lhs.id] ?? Int.max - 1
                let right = rhs.id.isEmpty ? Int.max : order[rhs.id] ?? Int.max - 1
                if left != right { return left < right }
                return lhs.title.localizedCaseInsensitiveCompare(rhs.title) == .orderedAscending
            }
    }

    /// The picked type while it still has rows. Clearing a type drops the list back to all.
    private var activeType: String? {
        guard let selectedType, types.contains(where: { $0.id == selectedType }) else { return nil }
        return selectedType
    }

    /// Alerts with no section (the tracker, the test card, the watchdog) are the app's own.
    private static func typeKey(_ alert: OrbitalAlert) -> String {
        alert.sectionId ?? ""
    }

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 14) {
                OrbitalScreenHeader(
                    eyebrow: "Inbox",
                    title: "Alerts",
                    subtitle: store.unackedCount == 0 ? "All caught up" : "\(store.unackedCount) waiting",
                    accent: accent
                )
                // The test card leads an empty inbox (the time you want to try one) and sits below real alerts.
                if store.unackedCount == 0 {
                    testCard
                }
                filterBar
                    .padding(.top, 4)
                if !types.isEmpty {
                    typeBar
                }
                if !showHistory, let type = types.first(where: { $0.id == activeType }) {
                    clearRow(type)
                }
                if !store.scheduledAlerts.isEmpty {
                    scheduledGroup(store.scheduledAlerts)
                }
                if rows.isEmpty {
                    emptyState
                } else {
                    ForEach(rows) { alert in
                        AlertCard(
                            alert: alert,
                            entry: store.catalog.first { $0.id == alert.sectionId },
                            onOpen: { open(alert) },
                            onDone: { Task { await store.ack([alert.id]) } }
                        )
                        .transition(.opacity.combined(with: .move(edge: .top)))
                    }
                }
                if store.unackedCount > 0 {
                    testCard
                        .padding(.top, 10)
                }
            }
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, 36)
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
            .animation(.easeInOut(duration: 0.25), value: rows.map(\.id))
        }
        .scrollIndicators(.hidden)
        .orbitalScrollFade()
        .refreshable {
            await store.refresh()
            await loadPermission()
        }
        .orbitalBackdrop(accent)
        .navigationTitle("Alerts")
        .orbitalNavigationBar(pushed: pushed)
        .task { await loadPermission() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await loadPermission() } }
        }
    }

    // MARK: - Test card

    private var testCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .center, spacing: 12) {
                IconTile(symbol: "bell.badge.fill", tint: accent, size: 42, filled: true)
                    .shadow(color: accent.opacity(0.5), radius: 10)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Test notification")
                        .font(.headline)
                        .foregroundStyle(OrbitalTheme.text)
                    Text("A real notification with every feature on. It stays on this device.")
                        .font(.footnote)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            FlowLayout(spacing: 6, lineSpacing: 6) {
                ForEach(Self.testFeatures, id: \.0) { feature in
                    OrbitalChip(text: feature.0, symbol: feature.1, tint: feature.2)
                }
            }
            if permission == .denied {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: "bell.slash.fill")
                        .foregroundStyle(OrbitalTheme.warning)
                        .accessibilityLabel("Warning")
                    VStack(alignment: .leading, spacing: 6) {
                        Text("Notifications are off for \(OrbitalBrand.appName). The test still lands in this inbox, but the system will not show a banner.")
                            .font(.footnote)
                            .foregroundStyle(OrbitalTheme.text.opacity(0.9))
                            .fixedSize(horizontal: false, vertical: true)
                        Button("Open notification settings") {
                            if let url = URL(string: UIApplication.openNotificationSettingsURLString) {
                                openURL(url)
                            }
                        }
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.warning)
                        .frame(minHeight: 32)
                    }
                }
                .padding(12)
                .background(OrbitalTheme.warning.opacity(0.10), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
                .overlay(
                    RoundedRectangle(cornerRadius: 12, style: .continuous)
                        .strokeBorder(OrbitalTheme.warning.opacity(0.3), lineWidth: 1)
                )
            }
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 10) {
                    sendNowButton
                    sendLaterButton
                }
                VStack(spacing: 10) {
                    sendNowButton
                    sendLaterButton
                }
            }
            .disabled(sending)
            statusLine
        }
        .orbitalCard(padding: 16, tint: accent)
    }

    private var sendNowButton: some View {
        OrbitalActionButton(title: "Send now", symbol: "paperplane.fill", kind: .primary, accent: accent, busy: sending) {
            send(delay: 0)
        }
    }

    private var sendLaterButton: some View {
        OrbitalActionButton(title: "In 10 s", symbol: "timer", kind: .secondary, accent: accent) {
            send(delay: 10)
        }
    }

    @ViewBuilder
    private var statusLine: some View {
        if let scheduledFor {
            TimelineView(.periodic(from: .now, by: 1)) { context in
                let remaining = Int(ceil(scheduledFor.timeIntervalSince(context.date)))
                statusText(
                    symbol: remaining > 0 ? "lock.fill" : "checkmark.circle.fill",
                    text: remaining > 0
                        ? "Arrives in \(remaining) s. Lock the phone now to see it on the Lock Screen."
                        : "Delivered. Long-press it to see the image and the four action buttons.",
                    tint: remaining > 0 ? OrbitalTheme.warning : OrbitalTheme.success
                )
            }
        } else if lastSentAt != nil {
            statusText(
                symbol: "checkmark.circle.fill",
                text: "Sent. Long-press the banner (or pull it down) to see the image, Open, Reply, Snooze, and Dismiss.",
                tint: OrbitalTheme.success
            )
        } else {
            statusText(
                symbol: "hand.tap.fill",
                text: "Use In 10 s, then lock the phone, to see how it looks on the Lock Screen.",
                tint: OrbitalTheme.textTertiary
            )
        }
    }

    private func statusText(symbol: String, text: String, tint: Color) -> some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: symbol)
                .font(.caption.weight(.bold))
                .foregroundStyle(tint)
                .padding(.top, 1)
            Text(text)
                .font(.caption)
                .foregroundStyle(OrbitalTheme.textSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private func send(delay: TimeInterval) {
        guard !sending else { return }
        sending = true
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        Task {
            await store.sendTestAlert(delay: delay)
            await loadPermission()
            sending = false
            lastSentAt = Date()
            scheduledFor = delay > 0 ? Date().addingTimeInterval(delay) : nil
        }
    }

    private func loadPermission() async {
        permission = await OrbitalNotifications.authorizationStatus()
    }

    // MARK: - Filter and list

    private var filterBar: some View {
        HStack(spacing: 4) {
            filterButton("Waiting", count: store.unackedCount, selected: !showHistory) { showHistory = false }
            filterButton("History", count: store.alerts.count - store.scheduledAlerts.count, selected: showHistory) { showHistory = true }
        }
        .padding(4)
        .background(OrbitalTheme.backgroundDeep.opacity(0.75), in: Capsule())
        .overlay(Capsule().strokeBorder(Color.white.opacity(0.07), lineWidth: 1))
        .animation(.spring(response: 0.32, dampingFraction: 0.82), value: showHistory)
    }

    private func filterButton(_ title: String, count: Int, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 6) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                Text("\(count)")
                    .font(.caption.weight(.bold))
                    .monospacedDigit()
                    .padding(.horizontal, 6)
                    .padding(.vertical, 1)
                    .background(
                        (selected ? OrbitalTheme.backgroundDeep : Color.white).opacity(selected ? 0.25 : 0.08),
                        in: Capsule()
                    )
            }
            .foregroundStyle(selected ? OrbitalTheme.backgroundDeep : OrbitalTheme.textSecondary)
            .padding(.horizontal, 14)
            .frame(minHeight: 38)
            .background {
                if selected {
                    Capsule()
                        .fill(LinearGradient(colors: [accent, accent.opacity(0.82)], startPoint: .top, endPoint: .bottom))
                        .matchedGeometryEffect(id: "filter", in: filterPill)
                }
            }
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    // MARK: - Types

    /// All, then one chip per source. Picking one narrows the list and offers Clear all.
    private var typeBar: some View {
        let active = activeType
        return ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                typeChip(title: "All", count: listed.count, tint: accent, selected: active == nil) {
                    AppIcon(image: nil, symbol: "tray.full.fill", tint: accent, size: 20)
                } action: {
                    selectedType = nil
                }
                ForEach(types) { type in
                    typeChip(title: type.title, count: type.count, tint: type.tint, selected: active == type.id) {
                        AppIcon(image: type.icon, symbol: type.symbol, tint: type.tint, size: 20)
                    } action: {
                        selectedType = active == type.id ? nil : type.id
                    }
                }
            }
            .padding(.vertical, 2)
        }
        .scrollClipDisabled()
        .animation(.spring(response: 0.32, dampingFraction: 0.82), value: active)
    }

    private func typeChip<Icon: View>(
        title: String,
        count: Int,
        tint: Color,
        selected: Bool,
        @ViewBuilder icon: () -> Icon,
        action: @escaping () -> Void
    ) -> some View {
        Button(action: action) {
            HStack(spacing: 7) {
                icon()
                Text(title)
                    .font(.footnote.weight(.semibold))
                    .lineLimit(1)
                Text("\(count)")
                    .font(.caption2.weight(.bold))
                    .monospacedDigit()
                    .padding(.horizontal, 6)
                    .padding(.vertical, 1)
                    .background(tint.opacity(selected ? 0.28 : 0.12), in: Capsule())
            }
            .foregroundStyle(selected ? OrbitalTheme.text : OrbitalTheme.textSecondary)
            .padding(.leading, 6)
            .padding(.trailing, 10)
            .frame(minHeight: 36)
            .background(selected ? tint.opacity(0.18) : OrbitalTheme.surface.opacity(0.6), in: Capsule())
            .overlay(Capsule().strokeBorder(selected ? tint.opacity(0.55) : Color.white.opacity(0.07), lineWidth: 1))
            .contentShape(Capsule())
        }
        .buttonStyle(OrbitalPressStyle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(title), \(count)")
        .accessibilityAddTraits(selected ? [.isButton, .isSelected] : .isButton)
    }

    /// Clear all for the picked type.
    private func clearRow(_ type: AlertType) -> some View {
        OrbitalActionButton(
            title: "Clear all \(type.count) \(type.title)",
            symbol: "checkmark.circle.fill",
            kind: .primary,
            accent: type.tint
        ) {
            clear(type)
        }
    }

    /// Marks every waiting alert of this type done. They move to History.
    private func clear(_ type: AlertType) {
        let ids = listed.filter { !$0.isAcked && Self.typeKey($0) == type.id }.map(\.id)
        guard !ids.isEmpty else { return }
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        selectedType = nil
        Task { await store.ack(ids) }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            ZStack {
                Circle()
                    .fill(OrbitalTheme.success.opacity(0.10))
                    .frame(width: 84, height: 84)
                Image(systemName: showHistory ? "tray" : "checkmark.seal.fill")
                    .font(.system(size: 32, weight: .semibold))
                    .foregroundStyle(showHistory ? OrbitalTheme.textTertiary : OrbitalTheme.success)
            }
            Text(showHistory ? "No alerts yet." : "Nothing waiting.")
                .font(.headline)
                .foregroundStyle(OrbitalTheme.text)
            Text(showHistory
                ? "Alerts from your feeds, agents, and the watchdog collect here."
                : "New alerts from your feeds, agents, and the watchdog show up here and as notifications.")
                .font(.footnote)
                .foregroundStyle(OrbitalTheme.textTertiary)
                .multilineTextAlignment(.center)
        }
        .padding(.horizontal, 24)
        .frame(maxWidth: .infinity)
        .padding(.vertical, 36)
    }

    // MARK: - Scheduled

    /// Reminders the phone will show later. Collapsed to one line; they become normal rows when they fire.
    private func scheduledGroup(_ alerts: [OrbitalAlert]) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Button {
                withAnimation(.easeInOut(duration: 0.2)) { showScheduled.toggle() }
            } label: {
                HStack(spacing: 10) {
                    Image(systemName: "calendar.badge.clock")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .frame(width: 22)
                    Text("Scheduled")
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                    Text("\(alerts.count)")
                        .font(.caption.weight(.bold))
                        .monospacedDigit()
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .padding(.horizontal, 7)
                        .padding(.vertical, 2)
                        .background(Color.white.opacity(0.08), in: Capsule())
                    Spacer(minLength: 8)
                    if !showScheduled, let next = alerts.first?.deliverDate {
                        Text("next \(Self.fireTime(next))")
                            .font(.caption)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .lineLimit(1)
                    }
                    Image(systemName: "chevron.down")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .rotationEffect(.degrees(showScheduled ? 180 : 0))
                }
                .frame(minHeight: 44)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Scheduled reminders, \(alerts.count)")
            .accessibilityHint(showScheduled ? "Collapses the list" : "Shows reminders that have not fired yet")
            if showScheduled {
                ForEach(alerts) { alert in
                    Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                    Button {
                        // It has not fired yet, so opening the page does not mark it done.
                        navigate(to: alert)
                    } label: {
                        HStack(alignment: .top, spacing: 10) {
                            AppIcon(
                                image: store.entry(alert.sectionId)?.iconImage,
                                symbol: store.entry(alert.sectionId)?.icon ?? "bell.fill",
                                tint: OrbitalTheme.accentColor(store.entry(alert.sectionId)?.accent),
                                size: 26
                            )
                            VStack(alignment: .leading, spacing: 2) {
                                Text(alert.title)
                                    .font(.subheadline.weight(.medium))
                                    .foregroundStyle(OrbitalTheme.text.opacity(0.9))
                                    .lineLimit(2)
                                if let date = alert.deliverDate {
                                    Text("Fires \(Self.fireTime(date))")
                                        .font(.caption)
                                        .foregroundStyle(OrbitalTheme.textTertiary)
                                }
                            }
                            Spacer(minLength: 0)
                        }
                        .padding(.vertical, 9)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(OrbitalPressStyle())
                }
            }
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 4)
        .background(OrbitalTheme.surface.opacity(0.6), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .overlay(
            RoundedRectangle(cornerRadius: 14, style: .continuous)
                .strokeBorder(Color.white.opacity(0.06), lineWidth: 1)
        )
    }

    /// "7:00 AM", "Tomorrow 7:00 AM", or "Thu 7:00 AM".
    private static func fireTime(_ date: Date) -> String {
        let calendar = Calendar.current
        let time = date.formatted(date: .omitted, time: .shortened)
        if calendar.isDateInToday(date) { return time }
        if calendar.isDateInTomorrow(date) { return "tomorrow \(time)" }
        return "\(date.formatted(.dateTime.weekday(.abbreviated).month(.abbreviated).day())) \(time)"
    }

    private func open(_ alert: OrbitalAlert) {
        navigate(to: alert)
        Task { await store.ack([alert.id]) }
    }

    private func navigate(to alert: OrbitalAlert) {
        if let url = alert.resolvedLaunchURL {
            router.handle(url)
        } else {
            router.open(section: alert.sectionId, node: alert.nodeId, tab: alert.tabId)
        }
    }
}

/// One source of alerts in the inbox: a section, or the app's own when `id` is "".
private struct AlertType: Identifiable {
    let id: String
    let title: String
    let icon: String?
    let symbol: String
    let tint: Color
    let count: Int
}

/// One inbox row: source, time, text, image attachment, and what the notification carried.
struct AlertCard: View {
    let alert: OrbitalAlert
    let entry: CatalogEntry?
    let onOpen: () -> Void
    let onDone: () -> Void

    private var isTest: Bool { OrbitalTestAlert.isTest(alert.id) }

    private var tint: Color {
        OrbitalTheme.accentColor(entry?.accent)
    }

    private var source: String {
        entry?.title ?? alert.sectionId ?? OrbitalBrand.appName
    }

    private var imageAttachment: AlertAttachment? {
        alert.attachments?.first { $0.typeHint == nil || $0.typeHint == "image" }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Button(action: onOpen) {
                VStack(alignment: .leading, spacing: 10) {
                    HStack(alignment: .top, spacing: 12) {
                        AppIcon(
                            image: entry?.iconImage,
                            symbol: entry?.icon ?? (isTest ? "bell.badge.fill" : "bell.fill"),
                            tint: tint,
                            size: 36,
                            filled: !alert.isAcked
                        )
                        VStack(alignment: .leading, spacing: 3) {
                            HStack(spacing: 6) {
                                Text(source.uppercased())
                                    .font(.caption2.weight(.bold))
                                    .tracking(0.6)
                                    .foregroundStyle(tint)
                                    .lineLimit(1)
                                Spacer(minLength: 4)
                                if let when = OrbitalDates.relative(alert.createdAt) {
                                    Text(when)
                                        .font(.caption2)
                                        .foregroundStyle(OrbitalTheme.textTertiary)
                                }
                                if !alert.isAcked {
                                    Circle()
                                        .fill(tint)
                                        .frame(width: 8, height: 8)
                                        .shadow(color: tint.opacity(0.8), radius: 3)
                                        .accessibilityLabel("Unread")
                                }
                            }
                            Text(alert.title)
                                .font(.headline)
                                .foregroundStyle(OrbitalTheme.text)
                                .multilineTextAlignment(.leading)
                            if let subtitle = alert.subtitle, !subtitle.isEmpty {
                                Text(subtitle)
                                    .font(.subheadline.weight(.medium))
                                    .foregroundStyle(OrbitalTheme.text.opacity(0.85))
                                    .multilineTextAlignment(.leading)
                            }
                            Text(alert.body)
                                .font(.subheadline)
                                .foregroundStyle(OrbitalTheme.textSecondary)
                                .multilineTextAlignment(.leading)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                    if let imageAttachment {
                        RemoteImage(src: imageAttachment.url, alt: "Alert image", aspect: "16:9", caption: nil, corner: 12)
                    }
                    if let reply = alert.responseText, !reply.isEmpty {
                        HStack(alignment: .top, spacing: 8) {
                            Image(systemName: "arrowshape.turn.up.left.fill")
                                .font(.caption)
                                .foregroundStyle(tint)
                            Text("You replied: \(reply)")
                                .font(.footnote)
                                .foregroundStyle(OrbitalTheme.text.opacity(0.9))
                        }
                        .padding(10)
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .background(tint.opacity(0.10), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
                    }
                    let chips = featureChips
                    if !chips.isEmpty {
                        FlowLayout(spacing: 6, lineSpacing: 6) {
                            ForEach(chips, id: \.text) { chip in
                                OrbitalChip(text: chip.text, symbol: chip.symbol, tint: chip.tint)
                            }
                        }
                    }
                }
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if !alert.isAcked {
                HStack(spacing: 8) {
                    Button(action: onOpen) {
                        Label("Open", systemImage: "arrow.up.forward")
                            .font(.footnote.weight(.semibold))
                            .lineLimit(1)
                            .foregroundStyle(tint)
                            .padding(.horizontal, 12)
                            .frame(minHeight: 34)
                            .background(tint.opacity(0.12), in: Capsule())
                    }
                    .buttonStyle(OrbitalPressStyle())
                    Spacer(minLength: 0)
                    Button(action: onDone) {
                        Label("Done", systemImage: "checkmark")
                            .font(.footnote.weight(.semibold))
                            .lineLimit(1)
                            .foregroundStyle(OrbitalTheme.textSecondary)
                            .padding(.horizontal, 12)
                            .frame(minHeight: 34)
                            .background(Color.white.opacity(0.06), in: Capsule())
                    }
                    .buttonStyle(OrbitalPressStyle())
                }
                .frame(minHeight: 44)
            }
        }
        .orbitalCard(padding: 14, tint: alert.isAcked ? nil : tint)
        .opacity(alert.isAcked ? 0.7 : 1)
        .contextMenu {
            Button(action: onOpen) {
                Label("Open", systemImage: "arrow.up.forward.app")
            }
            if !alert.isAcked {
                Button(action: onDone) {
                    Label("Mark done", systemImage: "checkmark.circle")
                }
            }
        }
    }

    private struct Chip {
        let text: String
        let symbol: String
        let tint: Color
    }

    private var featureChips: [Chip] {
        var chips: [Chip] = []
        if isTest {
            chips.append(Chip(text: "Test", symbol: "wand.and.stars", tint: OrbitalTheme.violet))
        }
        switch alert.interruptionLevel {
        case "timeSensitive":
            chips.append(Chip(text: "Time Sensitive", symbol: "bolt.fill", tint: OrbitalTheme.warning))
        case "critical":
            chips.append(Chip(text: "Critical", symbol: "exclamationmark.octagon.fill", tint: OrbitalTheme.danger))
        case "passive":
            chips.append(Chip(text: "Quiet", symbol: "moon.fill", tint: OrbitalTheme.textSecondary))
        default:
            break
        }
        if alert.kind == "reminder" {
            let when = OrbitalDates.parse(alert.deliverAt)?
                .formatted(.dateTime.month(.abbreviated).day().hour().minute())
            chips.append(Chip(text: when.map { "Reminder · \($0)" } ?? "Reminder", symbol: "alarm.fill", tint: OrbitalTheme.accent))
        }
        if let repeats = alert.repeats {
            chips.append(Chip(text: "Repeats \(repeatText(repeats))", symbol: "repeat", tint: OrbitalTheme.accent))
        }
        if let other = alert.attachments?.first(where: { $0.typeHint == "audio" || $0.typeHint == "video" }) {
            chips.append(Chip(
                text: other.typeHint == "audio" ? "Audio" : "Video",
                symbol: other.typeHint == "audio" ? "waveform" : "play.rectangle.fill",
                tint: OrbitalTheme.textSecondary
            ))
        }
        if let actions = alert.actions, !actions.isEmpty {
            chips.append(Chip(
                text: actions.count == 1 ? "1 action" : "\(actions.count) actions",
                symbol: "hand.tap.fill",
                tint: OrbitalTheme.textSecondary
            ))
        }
        return chips
    }

    private func repeatText(_ repeats: AlertRepeat) -> String {
        var components = DateComponents()
        components.hour = repeats.hour
        components.minute = repeats.minute
        let time = Calendar.current.date(from: components)?.formatted(date: .omitted, time: .shortened)
            ?? String(format: "%02d:%02d", repeats.hour, repeats.minute)
        if let weekday = repeats.weekday, (1 ... 7).contains(weekday) {
            return "\(Calendar.current.shortWeekdaySymbols[weekday - 1]) \(time)"
        }
        return "daily \(time)"
    }
}
