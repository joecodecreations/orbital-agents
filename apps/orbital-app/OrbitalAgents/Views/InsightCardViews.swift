import SwiftUI
import UIKit

// Insight cards: the open ones sit at the very top of Home. A tap opens the evidence and the exact
// email or agent brief each action would send; Approve, Snooze, and Dismiss post insight.* actions
// to the card's section, and the feed that owns it writes the result back.

enum InsightStyle {
    static func color(_ severity: String) -> Color {
        switch severity {
        case "critical": return OrbitalTheme.danger
        case "high": return Color(hex: "#FF9F6B") ?? OrbitalTheme.warning
        case "medium": return OrbitalTheme.warning
        case "good": return OrbitalTheme.success
        default: return OrbitalTheme.accent
        }
    }

    static func label(_ severity: String) -> String {
        switch severity {
        case "critical": return "Urgent"
        case "high": return "High"
        case "medium": return "Medium"
        case "good": return "Good news"
        default: return "FYI"
        }
    }

    static func symbol(kind: String, severity: String) -> String {
        switch kind {
        case "stuck_user", "errors", "rage_click", "form_abandon": return "exclamationmark.triangle.fill"
        case "deletion_requested": return "trash.circle.fill"
        case "new_signup": return "person.crop.circle.badge.plus"
        case "quiet_user": return "moon.zzz.fill"
        case "trial_ending", "trial_pipeline", "trial_ended", "card_trial_ending": return "hourglass"
        case "trial_canceled", "payment_failed": return "creditcard.trianglebadge.exclamationmark"
        case "converted": return "party.popper.fill"
        case "paywall": return "lock.circle.fill"
        case "objection": return "quote.bubble.fill"
        case "funnel", "traffic", "marketing": return "megaphone.fill"
        case "support_ticket", "feedback", "ai_rating": return "bubble.left.and.exclamationmark.bubble.right.fill"
        default: return severity == "good" ? "checkmark.seal.fill" : "lightbulb.max.fill"
        }
    }

    /// Card or action state as a chip. Nil for the quiet states.
    static func state(_ state: String) -> (text: String, symbol: String, tint: Color)? {
        switch state {
        case "pending": return ("Sending to the hub", "arrow.triangle.2.circlepath", OrbitalTheme.accent)
        case "running", "in_progress": return ("Working", "gearshape.2.fill", OrbitalTheme.accent)
        case "waiting": return ("Waiting", "clock.fill", OrbitalTheme.warning)
        case "done": return ("Done", "checkmark.circle.fill", OrbitalTheme.success)
        case "failed": return ("Failed", "xmark.octagon.fill", OrbitalTheme.danger)
        case "blocked": return ("Not allowed", "nosign", OrbitalTheme.textTertiary)
        case "snoozed": return ("Snoozed", "zzz", OrbitalTheme.textSecondary)
        case "dismissed": return ("Dismissed", "xmark", OrbitalTheme.textTertiary)
        case "resolved": return ("Resolved", "checkmark", OrbitalTheme.success)
        default: return nil
        }
    }

    static func countdown(_ days: Int) -> String {
        switch days {
        case ..<(-1): return "\(-days) days overdue"
        case -1: return "1 day overdue"
        case 0: return "today"
        case 1: return "in 1 day"
        default: return "in \(days) days"
        }
    }
}

/// Which card a sheet shows. The sheet re-reads the card from the store, so it follows live updates.
private struct InsightSelection: Identifiable, Equatable {
    var id: String
}

// MARK: - Home strip

struct InsightHomeStrip: View {
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @State private var selection: InsightSelection?
    @State private var expanded = false

    private static let homeLimit = 4

    var body: some View {
        let all = InsightFeed.collect(Array(store.sectionsByID.values))
        let open = InsightFeed.open(all)
        if !open.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                header(open)
                let shown = expanded ? open : Array(open.prefix(Self.homeLimit))
                ForEach(shown) { ref in
                    Button {
                        selection = InsightSelection(id: ref.id)
                    } label: {
                        InsightCardRow(ref: ref, prominent: true)
                    }
                    .buttonStyle(OrbitalPressStyle())
                }
                if open.count > Self.homeLimit {
                    Button {
                        withAnimation(.easeInOut(duration: 0.25)) { expanded.toggle() }
                    } label: {
                        HStack(spacing: 6) {
                            Text(expanded ? "Show fewer" : "Show all \(open.count)")
                            Image(systemName: expanded ? "chevron.up" : "chevron.down")
                                .font(.caption2.weight(.bold))
                        }
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.accent)
                        .frame(maxWidth: .infinity, minHeight: 36)
                    }
                    .buttonStyle(OrbitalPressStyle())
                }
            }
            .sheet(item: $selection) { picked in
                InsightDetailSheet(refID: picked.id)
                    .environmentObject(store)
                    .environmentObject(router)
            }
        }
    }

    private func header(_ open: [InsightRef]) -> some View {
        let worst = open.first?.card.severity ?? "medium"
        let tint = InsightStyle.color(worst)
        let urgent = open.filter { $0.card.severityRank <= 1 }.count
        return HStack(alignment: .center, spacing: 10) {
            ZStack(alignment: .topTrailing) {
                IconTile(symbol: "bolt.badge.clock.fill", tint: tint, size: 34, filled: true)
                Text("\(open.count)")
                    .font(.caption2.weight(.heavy))
                    .monospacedDigit()
                    .foregroundStyle(.white)
                    .padding(.horizontal, 5)
                    .frame(minWidth: 18, minHeight: 18)
                    .background(OrbitalTheme.danger, in: Capsule())
                    .overlay(Capsule().strokeBorder(OrbitalTheme.background, lineWidth: 2))
                    .offset(x: 8, y: -7)
                    .accessibilityHidden(true)
            }
            VStack(alignment: .leading, spacing: 1) {
                OrbitalEyebrow(text: urgent > 0 ? "Needs you · \(urgent) urgent" : "Needs you", color: tint)
                Text(open.count == 1 ? "1 insight to act on" : "\(open.count) insights to act on")
                    .font(.headline)
                    .foregroundStyle(OrbitalTheme.text)
            }
            Spacer(minLength: 0)
            if let section = open.first?.sectionID {
                Button {
                    router.open(section: section)
                } label: {
                    Text("All")
                        .font(.footnote.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.accent)
                        .padding(.horizontal, 10)
                        .frame(minHeight: 30)
                        .background(OrbitalTheme.accent.opacity(0.12), in: Capsule())
                }
                .buttonStyle(OrbitalPressStyle())
                .accessibilityHint("Opens every insight, including dismissed and finished ones")
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(open.count) insights to act on")
    }
}

// MARK: - Card row

struct InsightCardRow: View {
    let ref: InsightRef
    var prominent = false

    var body: some View {
        let card = ref.card
        let tint = InsightStyle.color(card.severity)
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .center, spacing: 8) {
                Image(systemName: InsightStyle.symbol(kind: card.kind, severity: card.severity))
                    .font(.system(size: 13, weight: .bold))
                    .foregroundStyle(tint)
                Text(InsightStyle.label(card.severity).uppercased())
                    .font(.caption2.weight(.heavy))
                    .tracking(0.8)
                    .foregroundStyle(tint)
                if let product = card.product {
                    Text("· \(product)")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .lineLimit(1)
                }
                Spacer(minLength: 4)
                if let count = card.count {
                    HStack(spacing: 3) {
                        Text("\(count)")
                            .font(.caption.weight(.heavy))
                            .monospacedDigit()
                        if let label = card.countLabel {
                            Text(label)
                                .font(.caption2.weight(.semibold))
                                .lineLimit(1)
                        }
                    }
                    .foregroundStyle(tint)
                    .padding(.horizontal, 8)
                    .padding(.vertical, 3)
                    .background(tint.opacity(0.16), in: Capsule())
                }
            }
            Text(card.title)
                .font(prominent ? .callout.weight(.semibold) : .subheadline.weight(.semibold))
                .foregroundStyle(OrbitalTheme.text)
                .multilineTextAlignment(.leading)
                .lineLimit(prominent ? 3 : 2)
                .frame(maxWidth: .infinity, alignment: .leading)
            if let summary = card.summary, prominent {
                Text(summary)
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .multilineTextAlignment(.leading)
                    .lineLimit(2)
            }
            chips(card, tint: tint)
            if prominent, !card.actions.isEmpty {
                HStack(spacing: 6) {
                    ForEach(card.actions) { action in
                        actionPill(action)
                    }
                    Spacer(minLength: 0)
                    Image(systemName: "chevron.right")
                        .font(.caption.weight(.bold))
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
            }
        }
        .padding(.leading, 6)
        .overlay(alignment: .leading) {
            RoundedRectangle(cornerRadius: 2)
                .fill(tint)
                .frame(width: 4)
                .padding(.vertical, 2)
                .offset(x: -8)
        }
        .orbitalCard(padding: 14, tint: tint)
        .overlay(
            RoundedRectangle(cornerRadius: OrbitalTheme.cardRadius, style: .continuous)
                .strokeBorder(tint.opacity(card.severityRank <= 1 && card.isOpen ? 0.55 : 0.0), lineWidth: 1.5)
        )
        .opacity(card.isOpen ? 1 : 0.72)
        .accessibilityElement(children: .combine)
        .accessibilityHint("Opens the evidence and what each action would do")
    }

    @ViewBuilder
    private func chips(_ card: InsightCard, tint: Color) -> some View {
        let items = chipItems(card)
        if !items.isEmpty {
            ScrollView(.horizontal, showsIndicators: false) {
                HStack(spacing: 6) {
                    ForEach(items, id: \.text) { item in
                        OrbitalChip(text: item.text, symbol: item.symbol, tint: item.tint)
                    }
                }
            }
        }
    }

    private func chipItems(_ card: InsightCard) -> [(text: String, symbol: String?, tint: Color)] {
        var out: [(text: String, symbol: String?, tint: Color)] = []
        if card.pending {
            out.append(("Syncing", "arrow.triangle.2.circlepath", OrbitalTheme.accent))
        }
        if let days = card.daysToDeadline {
            let tint = days <= 7 ? OrbitalTheme.danger : days <= 14 ? OrbitalTheme.warning : OrbitalTheme.textSecondary
            out.append(("\(card.deadlineLabel ?? "Due") \(InsightStyle.countdown(days))", "calendar.badge.clock", tint))
        }
        // Every action that has moved past "ready": Fix it: Working, Email them: Waiting, and so on.
        for action in card.actions where !["ready", "blocked"].contains(action.state) {
            if let state = InsightStyle.state(action.state) {
                out.append(("\(action.label): \(state.text)", state.symbol, state.tint))
            }
        }
        if !card.isOpen, let state = InsightStyle.state(card.state) {
            out.append((state.text, state.symbol, state.tint))
        }
        if let person = card.person?.label, !prominent {
            out.append((person, "person.fill", OrbitalTheme.textSecondary))
        }
        return out
    }

    private func actionPill(_ action: InsightAction) -> some View {
        let blocked = action.state == "blocked"
        let tint = blocked ? OrbitalTheme.textTertiary : OrbitalTheme.accent
        return HStack(spacing: 4) {
            Image(systemName: blocked ? "nosign" : action.symbol)
                .font(.system(size: 10, weight: .bold))
            Text(action.label)
                .font(.caption.weight(.semibold))
                .lineLimit(1)
        }
        .foregroundStyle(tint)
        .padding(.horizontal, 9)
        .padding(.vertical, 5)
        .background(tint.opacity(0.12), in: Capsule())
        .strikethrough(blocked, color: tint)
    }
}

// MARK: - Block on its own page

struct InsightCardsBlockView: View {
    let block: InsightCardsBlock
    var location: PageLocation?
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @State private var selection: InsightSelection?
    @State private var showClosed = false
    /// The product the list is narrowed to; nil shows every card.
    @State private var product: String?

    var body: some View {
        let all = InsightFeed.collect(Array(store.sectionsByID.values)).filter { ref in
            ref.blockID == block.id && (location == nil || ref.sectionID == location?.sectionID)
        }
        let products = Self.products(all)
        let picked = product.flatMap { wanted in products.contains(wanted) ? wanted : nil }
        let refs = picked.map { wanted in all.filter { $0.card.product == wanted } } ?? all
        let open = InsightFeed.open(refs)
        let closed = refs.filter { !$0.card.isOpen }.sorted { ($0.card.updatedAt ?? "") > ($1.card.updatedAt ?? "") }
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .firstTextBaseline) {
                Text(block.title ?? "Insights")
                    .font(.headline)
                    .foregroundStyle(OrbitalTheme.text)
                Spacer(minLength: 0)
                if let asOf = OrbitalDates.relative(block.asOf) {
                    Text("Checked \(asOf)")
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
            }
            if let note = block.note {
                Text(note)
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textSecondary)
            }
            if products.count > 1 {
                productBar(products, picked: picked, all: all)
            }
            if open.isEmpty {
                Label(block.emptyText ?? "Nothing needs you right now.", systemImage: "checkmark.seal.fill")
                    .font(.subheadline)
                    .foregroundStyle(OrbitalTheme.success)
                    .orbitalCard(padding: 14)
            }
            ForEach(open) { ref in
                Button { selection = InsightSelection(id: ref.id) } label: {
                    InsightCardRow(ref: ref, prominent: true)
                }
                .buttonStyle(OrbitalPressStyle())
            }
            if !closed.isEmpty {
                Button {
                    withAnimation(.easeInOut(duration: 0.25)) { showClosed.toggle() }
                } label: {
                    HStack(spacing: 6) {
                        OrbitalEyebrow(text: "Earlier · \(closed.count)")
                        Image(systemName: showClosed ? "chevron.up" : "chevron.down")
                            .font(.caption2.weight(.bold))
                            .foregroundStyle(OrbitalTheme.textTertiary)
                        Spacer(minLength: 0)
                    }
                    .frame(minHeight: 32)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                if showClosed {
                    ForEach(closed) { ref in
                        Button { selection = InsightSelection(id: ref.id) } label: {
                            InsightCardRow(ref: ref, prominent: false)
                        }
                        .buttonStyle(OrbitalPressStyle())
                    }
                }
            }
        }
        .sheet(item: $selection) { picked in
            InsightDetailSheet(refID: picked.id)
                .environmentObject(store)
                .environmentObject(router)
        }
    }

    /// Products named on these cards, the busiest first. Cards about the whole business carry none.
    static func products(_ refs: [InsightRef]) -> [String] {
        var open: [String: Int] = [:]
        var order: [String] = []
        for ref in refs {
            guard let name = ref.card.product?.trimmingCharacters(in: .whitespaces), !name.isEmpty else { continue }
            if open[name] == nil { order.append(name) }
            open[name, default: 0] += ref.card.isOpen ? 1 : 0
        }
        return order.enumerated().sorted { lhs, rhs in
            let (a, b) = (open[lhs.element] ?? 0, open[rhs.element] ?? 0)
            return a != b ? a > b : lhs.offset < rhs.offset
        }.map(\.element)
    }

    private func productBar(_ products: [String], picked: String?, all: [InsightRef]) -> some View {
        let openAll = InsightFeed.open(all)
        return ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 6) {
                productChip("All · \(openAll.count)", selected: picked == nil) { product = nil }
                ForEach(products, id: \.self) { name in
                    let count = openAll.filter { $0.card.product == name }.count
                    productChip("\(name) · \(count)", selected: picked == name) { product = name }
                }
            }
            .padding(.vertical, 1)
        }
    }

    private func productChip(_ text: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            OrbitalChip(text: text, tint: selected ? OrbitalTheme.accent : OrbitalTheme.textSecondary)
                .frame(minHeight: 32)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }
}

// MARK: - Detail sheet

struct InsightDetailSheet: View {
    let refID: String
    @EnvironmentObject private var store: OrbitalStore
    @Environment(\.dismiss) private var dismiss
    @State private var confirmDismiss = false

    private var ref: InsightRef? {
        InsightFeed.collect(Array(store.sectionsByID.values)).first { $0.id == refID }
    }

    var body: some View {
        NavigationStack {
            Group {
                if let ref {
                    content(ref)
                } else {
                    VStack(spacing: 10) {
                        Image(systemName: "checkmark.seal")
                            .font(.largeTitle)
                            .foregroundStyle(OrbitalTheme.success)
                        Text("This card is gone. The feed that owns it closed it.")
                            .foregroundStyle(OrbitalTheme.textSecondary)
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                }
            }
            .background(OrbitalTheme.background.ignoresSafeArea())
            .navigationTitle("Insight")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    private func content(_ ref: InsightRef) -> some View {
        let card = ref.card
        let tint = InsightStyle.color(card.severity)
        return ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 6) {
                        OrbitalChip(text: InsightStyle.label(card.severity), symbol: InsightStyle.symbol(kind: card.kind, severity: card.severity), tint: tint)
                        if let product = card.product {
                            OrbitalChip(text: product, tint: OrbitalTheme.textSecondary)
                        }
                        if let state = InsightStyle.state(card.state) {
                            OrbitalChip(text: state.text, symbol: state.symbol, tint: state.tint)
                        }
                        if card.pending {
                            OrbitalChip(text: "Syncing", symbol: "arrow.triangle.2.circlepath", tint: OrbitalTheme.accent)
                        }
                    }
                    Text(card.title)
                        .font(.title3.weight(.bold))
                        .foregroundStyle(OrbitalTheme.text)
                        .fixedSize(horizontal: false, vertical: true)
                    if let summary = card.summary {
                        Text(summary)
                            .font(.subheadline)
                            .foregroundStyle(OrbitalTheme.textSecondary)
                            .fixedSize(horizontal: false, vertical: true)
                    }
                    if let status = card.statusText {
                        Label(status, systemImage: "info.circle")
                            .font(.footnote)
                            .foregroundStyle(OrbitalTheme.textSecondary)
                    }
                }

                if let days = card.daysToDeadline, let deadline = card.deadline {
                    deadlineBanner(days: days, deadline: deadline, label: card.deadlineLabel)
                }

                if let person = card.person, person.label != nil || person.email != nil {
                    personRow(person)
                }

                if !card.metrics.isEmpty {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 110), spacing: 10)], spacing: 10) {
                        ForEach(card.metrics) { tile in
                            MetricTileView(tile: tile, accent: tint)
                        }
                    }
                }

                if !card.evidence.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        OrbitalEyebrow(text: "Evidence")
                        ForEach(Array(card.evidence.enumerated()), id: \.offset) { _, line in
                            HStack(alignment: .firstTextBaseline, spacing: 8) {
                                Circle().fill(tint).frame(width: 5, height: 5)
                                Text(line)
                                    .font(.footnote)
                                    .foregroundStyle(OrbitalTheme.text.opacity(0.9))
                                    .fixedSize(horizontal: false, vertical: true)
                                    .textSelection(.enabled)
                            }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .orbitalCard(padding: 14)
                }

                if !card.links.isEmpty {
                    VStack(alignment: .leading, spacing: 6) {
                        ForEach(card.links) { link in
                            if let url = URL(string: link.url) {
                                Link(destination: url) {
                                    Label(link.label, systemImage: "arrow.up.right.square")
                                        .font(.footnote.weight(.semibold))
                                }
                            }
                        }
                    }
                }

                ForEach(card.actions) { action in
                    InsightActionPanel(ref: ref, action: action)
                }

                if card.isOpen {
                    HStack(spacing: 10) {
                        Menu {
                            Button("1 day") { post(ref, type: "insight.snooze", value: ["days": 1]) }
                            Button("1 week") { post(ref, type: "insight.snooze", value: ["days": 7]) }
                        } label: {
                            Label("Snooze", systemImage: "zzz")
                                .font(.subheadline.weight(.semibold))
                                .frame(maxWidth: .infinity, minHeight: 44)
                                .background(OrbitalTheme.surfaceRaised, in: RoundedRectangle(cornerRadius: OrbitalTheme.controlRadius))
                        }
                        Button(role: .destructive) {
                            confirmDismiss = true
                        } label: {
                            Label("Dismiss", systemImage: "xmark")
                                .font(.subheadline.weight(.semibold))
                                .frame(maxWidth: .infinity, minHeight: 44)
                                .background(OrbitalTheme.danger.opacity(0.14), in: RoundedRectangle(cornerRadius: OrbitalTheme.controlRadius))
                                .foregroundStyle(OrbitalTheme.danger)
                        }
                        .buttonStyle(.plain)
                    }
                    .confirmationDialog("Dismiss this card?", isPresented: $confirmDismiss, titleVisibility: .visible) {
                        Button("Dismiss", role: .destructive) {
                            post(ref, type: "insight.dismiss", value: [:])
                            dismiss()
                        }
                    } message: {
                        Text("It comes back only if something new happens.")
                    }
                }

                if let seen = OrbitalDates.shortDateTime(card.firstSeen) {
                    Text("First seen \(seen) · \(ref.sectionTitle)")
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
            }
            .padding(16)
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
        }
    }

    private func deadlineBanner(days: Int, deadline: String, label: String?) -> some View {
        let tint = days <= 7 ? OrbitalTheme.danger : days <= 14 ? OrbitalTheme.warning : OrbitalTheme.accent
        return HStack(spacing: 14) {
            VStack(spacing: 0) {
                Text("\(max(days, 0))")
                    .font(.system(size: 34, weight: .heavy, design: .rounded))
                    .monospacedDigit()
                Text(abs(days) == 1 ? "day" : "days")
                    .font(.caption.weight(.semibold))
            }
            .foregroundStyle(tint)
            .frame(minWidth: 64)
            VStack(alignment: .leading, spacing: 2) {
                Text(label ?? "Deadline")
                    .font(.headline)
                    .foregroundStyle(OrbitalTheme.text)
                Text("\(OrbitalDates.shortDay(deadline) ?? deadline) · \(InsightStyle.countdown(days))")
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textSecondary)
            }
            Spacer(minLength: 0)
        }
        .orbitalCard(padding: 14, tint: tint)
    }

    private func personRow(_ person: InsightPerson) -> some View {
        HStack(spacing: 10) {
            IconTile(symbol: "person.fill", tint: OrbitalTheme.accent, size: 32)
            VStack(alignment: .leading, spacing: 1) {
                Text(person.label ?? person.email ?? "")
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
                    .textSelection(.enabled)
                if let brand = person.brand {
                    Text(brand)
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                }
            }
            Spacer(minLength: 0)
            if let email = person.email {
                Button {
                    UIPasteboard.general.string = email
                    store.showToast("Email copied")
                } label: {
                    Image(systemName: "doc.on.doc")
                        .font(.footnote.weight(.semibold))
                        .frame(width: 36, height: 36)
                }
                .accessibilityLabel("Copy email address")
            }
        }
        .orbitalCard(padding: 12)
    }

    private func post(_ ref: InsightRef, type: String, value: [String: Any]) {
        Task {
            await store.perform(
                type: type, sectionID: ref.sectionID, itemID: ref.card.id, blockID: ref.blockID,
                nodeID: ref.nodeID, tabID: ref.tabID, value: value
            )
        }
    }
}

// MARK: - One action with its full preview

struct InsightActionPanel: View {
    let ref: InsightRef
    let action: InsightAction
    @EnvironmentObject private var store: OrbitalStore
    @State private var confirm = false
    @State private var fullBrief = false

    var body: some View {
        let state = InsightStyle.state(action.state)
        VStack(alignment: .leading, spacing: 12) {
            HStack(spacing: 8) {
                IconTile(symbol: action.state == "blocked" ? "nosign" : action.symbol, tint: state?.tint ?? OrbitalTheme.accent, size: 30)
                Text(action.label)
                    .font(.headline)
                    .foregroundStyle(OrbitalTheme.text)
                Spacer(minLength: 0)
                if let state {
                    OrbitalChip(text: state.text, symbol: state.symbol, tint: state.tint)
                }
            }
            if let reason = action.blockedReason {
                Label(reason, systemImage: "hand.raised.fill")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.danger)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let status = action.statusText {
                Text(status)
                    .font(.footnote)
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
            if let note = action.note {
                Label(note, systemImage: "exclamationmark.circle.fill")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.warning)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let email = action.email {
                emailPreview(email)
            }
            if let brief = action.brief {
                briefPreview(brief)
            }
            if action.canApprove, let type = action.actionType {
                OrbitalActionButton(
                    title: action.state == "failed" ? "Try again: \(action.approveTitle)" : action.approveTitle,
                    symbol: action.kind == "email" ? "paperplane.fill" : "play.fill",
                    kind: .primary,
                    busy: ref.card.pending
                ) {
                    confirm = true
                }
                .disabled(ref.card.pending)
                .confirmationDialog(confirmTitle, isPresented: $confirm, titleVisibility: .visible) {
                    Button(action.approveTitle) {
                        Task {
                            await store.perform(
                                type: type, sectionID: ref.sectionID, itemID: ref.card.id, blockID: ref.blockID,
                                nodeID: ref.nodeID, tabID: ref.tabID,
                                value: ["action_id": action.id, "digest": action.digest ?? ""]
                            )
                        }
                    }
                } message: {
                    Text(confirmMessage)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .orbitalCard(padding: 14, tint: state?.tint)
    }

    private var confirmTitle: String {
        action.kind == "email" ? "Send this email?" : "Start this agent?"
    }

    private var confirmMessage: String {
        if action.kind == "email", let email = action.email {
            return "To \(email.to): \"\(email.subject)\". It goes out exactly as shown."
        }
        return "The feed that owns this card starts the agent with exactly this brief."
    }

    private func emailPreview(_ email: InsightEmail) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            OrbitalEyebrow(text: "Exactly what is sent")
            VStack(alignment: .leading, spacing: 4) {
                previewRow("To", email.to)
                if let from = email.from { previewRow("From", from) }
                if let reply = email.replyTo { previewRow("Reply-To", reply) }
                previewRow("Subject", email.subject)
            }
            Text(email.body)
                .font(.callout)
                .foregroundStyle(OrbitalTheme.text)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(OrbitalTheme.surfaceRaised, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        }
    }

    private func briefPreview(_ brief: InsightBrief) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            OrbitalEyebrow(text: "Agent brief")
            VStack(alignment: .leading, spacing: 4) {
                if let title = brief.title { previewRow("Task", title) }
                if let repo = brief.repo { previewRow("Repo", repo) }
                if let branch = brief.branch { previewRow("Branch", branch) }
                if let commits = brief.commits { previewRow("Commits", commits) }
                if let log = brief.log { previewRow("Log", log) }
            }
            Text(brief.prompt)
                .font(.system(.caption, design: .monospaced))
                .foregroundStyle(OrbitalTheme.text.opacity(0.92))
                .lineLimit(fullBrief ? nil : 14)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
                .padding(12)
                .frame(maxWidth: .infinity, alignment: .leading)
                .background(OrbitalTheme.surfaceRaised, in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            Button(fullBrief ? "Show less" : "Show the whole brief") {
                withAnimation(.easeInOut(duration: 0.2)) { fullBrief.toggle() }
            }
            .font(.footnote.weight(.semibold))
            if let resume = brief.resume {
                HStack(spacing: 8) {
                    Text(resume)
                        .font(.system(.caption, design: .monospaced))
                        .foregroundStyle(OrbitalTheme.text)
                        .lineLimit(2)
                        .textSelection(.enabled)
                    Spacer(minLength: 0)
                    Button {
                        UIPasteboard.general.string = resume
                        store.showToast("Resume command copied")
                    } label: {
                        Image(systemName: "doc.on.doc")
                            .font(.footnote.weight(.semibold))
                            .frame(width: 34, height: 34)
                    }
                    .accessibilityLabel("Copy the resume command")
                }
                .padding(10)
                .background(OrbitalTheme.accent.opacity(0.1), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
            }
        }
    }

    private func previewRow(_ label: String, _ value: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Text(label)
                .font(.caption.weight(.semibold))
                .foregroundStyle(OrbitalTheme.textTertiary)
                .frame(width: 64, alignment: .leading)
            Text(value)
                .font(.footnote)
                .foregroundStyle(OrbitalTheme.text)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
        }
    }
}
