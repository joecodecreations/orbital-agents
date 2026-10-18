import SwiftUI
import UIKit

// The product block on a page, releases, ratings, and reviews.

/// Real app icon when the payload has one, otherwise the SF Symbol tile.
struct AppIcon: View {
    let image: String?
    let symbol: String
    var tint: Color = OrbitalTheme.accent
    var size: CGFloat = 40
    var filled = false
    @EnvironmentObject private var store: OrbitalStore
    @State private var loaded: UIImage?

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: size * 0.225, style: .continuous)
        Group {
            if let picture = loaded ?? cached {
                Image(uiImage: picture)
                    .resizable()
                    .scaledToFill()
                    .frame(width: size, height: size)
                    .background(OrbitalTheme.surfaceRaised)
                    .clipShape(shape)
                    .overlay(shape.strokeBorder(Color.white.opacity(0.12), lineWidth: 0.5))
            } else {
                IconTile(symbol: symbol, tint: tint, size: size, filled: filled)
            }
        }
        .frame(width: size, height: size)
        .task(id: image) {
            guard let image, !image.isEmpty else {
                loaded = nil
                return
            }
            loaded = await OrbitalImageLoader.shared.image(src: image, baseURL: store.baseURL, token: store.imageToken)
        }
        .accessibilityHidden(true)
    }

    private var cached: UIImage? {
        guard let image, !image.isEmpty else { return nil }
        return OrbitalImageLoader.cached(image)
    }
}

enum ReleaseStyle {
    static func color(_ status: String) -> Color {
        switch status {
        case "live": return OrbitalTheme.success
        case "pending": return OrbitalTheme.warning
        case "testing": return OrbitalTheme.violet
        case "rejected": return OrbitalTheme.danger
        case "removed": return OrbitalTheme.textTertiary
        default: return OrbitalTheme.textSecondary
        }
    }

    static func symbol(_ status: String) -> String {
        switch status {
        case "live": return "checkmark.circle.fill"
        case "pending": return "hourglass"
        case "testing": return "testtube.2"
        case "rejected": return "xmark.octagon.fill"
        case "removed": return "minus.circle.fill"
        default: return "pencil.circle.fill"
        }
    }

    static func label(_ status: String) -> String {
        switch status {
        case "live": return "Live"
        case "pending": return "Pending"
        case "testing": return "Testing"
        case "rejected": return "Rejected"
        case "removed": return "Removed"
        case "draft": return "Draft"
        default: return status.capitalized
        }
    }

    static func platform(_ platform: String) -> String {
        switch platform.lowercased() {
        case "ios", "ipados", "app store": return "apple.logo"
        case "android", "google play", "play": return "candybarphone"
        case "web", "site": return "globe"
        case "roblox": return "cube.fill"
        case "steam", "pc", "windows", "mac", "macos": return "desktopcomputer"
        default: return "app.dashed"
        }
    }

    static func platformName(_ platform: String) -> String {
        switch platform.lowercased() {
        case "ios": return "iOS"
        case "android": return "Android"
        case "web": return "Web"
        case "roblox": return "Roblox"
        default: return platform.capitalized
        }
    }

    /// The one-word state of a whole product: rejected beats pending beats testing beats live.
    static func overall(_ block: ProductBlock) -> (text: String, status: String)? {
        if let rejected = block.releases.first(where: { $0.status == "rejected" }) {
            return ("\(rejected.version.map { "\($0) " } ?? "")rejected", "rejected")
        }
        let pending = block.releases.filter { $0.status == "pending" }
        let live = block.releases.filter { $0.status == "live" }
        // On sale somewhere: say so first, so a Play review never reads as the App Store one.
        if !live.isEmpty, !pending.isEmpty {
            var stores: [String] = []
            for name in live.map({ platformName($0.platform) }) where !stores.contains(name) { stores.append(name) }
            let waiting = pending.count == 1
                ? "\(platformName(pending[0].platform)) \((pending[0].state ?? "pending").lowercased())"
                : "\(pending.count) pending"
            return ("Live on \(stores.joined(separator: " & ")) · \(waiting)", "live")
        }
        if pending.count == 1, let one = pending.first {
            let prefix = block.releases.contains(where: { $0.platform != one.platform }) ? "\(platformName(one.platform)) " : ""
            return ("\(prefix)\(one.version.map { "\($0) " } ?? "")\((one.state ?? "pending").lowercased())", "pending")
        }
        if pending.count > 1 {
            return ("\(pending.count) releases pending", "pending")
        }
        if block.releases.contains(where: { $0.status == "live" }) {
            return ("Live", "live")
        }
        if block.releases.contains(where: { $0.status == "testing" }) {
            return ("Testing", "testing")
        }
        return nil
    }
}

struct StatusPill: View {
    let text: String
    let status: String

    var body: some View {
        let color = ReleaseStyle.color(status)
        HStack(spacing: 4) {
            Image(systemName: ReleaseStyle.symbol(status))
                .font(.system(size: 10, weight: .bold))
            Text(text)
                .font(.caption2.weight(.bold))
                .lineLimit(1)
        }
        .foregroundStyle(color)
        .padding(.horizontal, 8)
        .padding(.vertical, 4)
        .background(color.opacity(0.14), in: Capsule())
        .overlay(Capsule().strokeBorder(color.opacity(0.28), lineWidth: 1))
        .accessibilityElement(children: .combine)
    }
}

struct StarRow: View {
    let value: Double
    var maximum: Double = 5
    var size: CGFloat = 11

    var body: some View {
        let scaled = maximum > 0 ? value / maximum * 5 : 0
        HStack(spacing: 1.5) {
            ForEach(0 ..< 5, id: \.self) { index in
                let fill = scaled - Double(index)
                Image(systemName: fill >= 0.75 ? "star.fill" : fill >= 0.25 ? "star.leadinghalf.filled" : "star")
                    .font(.system(size: size, weight: .semibold))
                    .foregroundStyle(fill >= 0.25 ? OrbitalTheme.warning : OrbitalTheme.textTertiary)
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(String(format: "%.1f out of %.0f stars", value, maximum))
    }
}

/// Compact release marker for a Home tile: platform glyph, version, status colour.
struct ReleaseChip: View {
    let release: ProductRelease

    var body: some View {
        let color = ReleaseStyle.color(release.status)
        HStack(spacing: 4) {
            Image(systemName: orbitalSymbol(ReleaseStyle.platform(release.platform)))
                .font(.system(size: 9, weight: .bold))
            // Name the platform in words; the glyph alone is too small to tell iOS from Android.
            Text(ReleaseStyle.platformName(release.platform))
                .font(.caption2.weight(.semibold))
                .lineLimit(1)
            if release.status == "live" {
                if let version = release.version, !version.isEmpty {
                    Text(version)
                        .font(.caption2.weight(.semibold))
                        .monospacedDigit()
                        .lineLimit(1)
                }
            } else {
                Text(release.status == "pending" ? (release.state ?? "Pending") : ReleaseStyle.label(release.status))
                    .font(.caption2.weight(.semibold))
                    .lineLimit(1)
            }
            Circle()
                .fill(color)
                .frame(width: 5, height: 5)
        }
        .foregroundStyle(release.status == "live" ? OrbitalTheme.textSecondary : color)
        .padding(.horizontal, 7)
        .padding(.vertical, 3)
        .background(color.opacity(release.status == "live" ? 0.08 : 0.14), in: Capsule())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(ReleaseStyle.platformName(release.platform)) \(release.version ?? "") \(release.state ?? ReleaseStyle.label(release.status))")
    }
}

struct ReleaseRow: View {
    let release: ProductRelease
    @Environment(\.openURL) private var openURL

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: orbitalSymbol(ReleaseStyle.platform(release.platform)))
                .font(.system(size: 15, weight: .semibold))
                .foregroundStyle(OrbitalTheme.textSecondary)
                .frame(width: 30, height: 30)
                .background(Color.white.opacity(0.05), in: RoundedRectangle(cornerRadius: 8, style: .continuous))
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(release.store ?? ReleaseStyle.platformName(release.platform))
                        .font(.subheadline.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.text)
                    if let version = release.version, !version.isEmpty {
                        Text(version)
                            .font(.subheadline.weight(.semibold).monospacedDigit())
                            .foregroundStyle(OrbitalTheme.text.opacity(0.85))
                    }
                    if let build = release.build, !build.isEmpty {
                        Text("(\(build))")
                            .font(.caption.monospacedDigit())
                            .foregroundStyle(OrbitalTheme.textTertiary)
                    }
                }
                Text(detail)
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .fixedSize(horizontal: false, vertical: true)
                if let note = release.note, !note.isEmpty {
                    Text(note)
                        .font(.caption2)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            Spacer(minLength: 8)
            StatusPill(text: ReleaseStyle.label(release.status), status: release.status)
        }
        .padding(.vertical, 10)
        .contentShape(Rectangle())
        .onTapGesture {
            if let url = release.url.flatMap(URL.init(string:)) { openURL(url) }
        }
        .accessibilityElement(children: .combine)
    }

    private var detail: String {
        var parts: [String] = []
        if let state = release.state, !state.isEmpty { parts.append(state) }
        if let since = OrbitalDates.shortDay(release.since) { parts.append("since \(since)") }
        return parts.isEmpty ? ReleaseStyle.label(release.status) : parts.joined(separator: " · ")
    }
}

struct ReviewRow: View {
    let review: ProductReview
    var product: ProductRef?
    var showsProduct = false

    @State private var expanded = false

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 8) {
                if showsProduct, let product {
                    AppIcon(image: product.iconImage, symbol: product.symbol, tint: OrbitalTheme.accentColor(product.accent), size: 22)
                    Text(product.title)
                        .font(.caption.weight(.semibold))
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .lineLimit(1)
                }
                if let rating = review.rating, rating > 0 {
                    StarRow(value: Double(rating))
                }
                Spacer(minLength: 0)
                Text(OrbitalDates.relativeDay(review.at) ?? "")
                    .font(.caption2)
                    .foregroundStyle(OrbitalTheme.textTertiary)
            }
            if let title = review.title, !title.isEmpty {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
            }
            if let body = review.body, !body.isEmpty {
                Text(body)
                    .font(.subheadline)
                    .foregroundStyle(OrbitalTheme.text.opacity(0.85))
                    .lineLimit(expanded ? nil : 3)
                    .onTapGesture { withAnimation(.easeInOut(duration: 0.2)) { expanded.toggle() } }
            }
            Text(byline)
                .font(.caption2)
                .foregroundStyle(OrbitalTheme.textTertiary)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }

    private var byline: String {
        [review.author, review.store, review.territory]
            .compactMap { ($0?.isEmpty == false) ? $0 : nil }
            .joined(separator: " · ")
    }
}

/// The product block drawn on its own page.
struct ProductCard: View {
    let block: ProductBlock
    let accent: Color
    var location: PageLocation?
    @EnvironmentObject private var store: OrbitalStore
    @Environment(\.openURL) private var openURL
    @State private var showReviews = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .center, spacing: 8) {
                if let overall = ReleaseStyle.overall(block) {
                    StatusPill(text: overall.text, status: overall.status)
                }
                Spacer(minLength: 0)
                Text(kicker)
                    .font(.caption2.weight(.bold))
                    .tracking(0.6)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .lineLimit(1)
            }
            if let headline = block.headline, !headline.isEmpty {
                Text(headline)
                    .font(.subheadline)
                    .foregroundStyle(OrbitalTheme.text.opacity(0.92))
                    .fixedSize(horizontal: false, vertical: true)
            }
            if let location, case let live = store.pulses(for: location), !live.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    OrbitalEyebrow(text: "Live", color: OrbitalTheme.success)
                    PulseGrid(items: live)
                }
            }
            if !block.releases.isEmpty {
                VStack(spacing: 0) {
                    ForEach(Array(block.releases.enumerated()), id: \.offset) { index, release in
                        if index > 0 {
                            Rectangle().fill(OrbitalTheme.hairline.opacity(0.7)).frame(height: 1)
                        }
                        ReleaseRow(release: release)
                    }
                }
            }
            if !block.ratings.isEmpty {
                FlowLayout(spacing: 8, lineSpacing: 8) {
                    ForEach(Array(block.ratings.enumerated()), id: \.offset) { _, rating in
                        RatingBadge(rating: rating)
                    }
                }
            }
            if !block.metrics.isEmpty {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 104), spacing: 8)], alignment: .leading, spacing: 8) {
                    ForEach(block.metrics) { metric in
                        MiniMetric(tile: metric)
                    }
                }
            }
            if let latest = block.reviews.first {
                VStack(alignment: .leading, spacing: 10) {
                    OrbitalEyebrow(text: block.reviews.count == 1 ? "Latest review" : "Latest of \(block.reviews.count) reviews", color: accent)
                    ReviewRow(review: latest)
                    if block.reviews.count > 1 {
                        Button {
                            showReviews = true
                        } label: {
                            Label("All reviews", systemImage: "text.bubble")
                                .font(.subheadline.weight(.semibold))
                                .foregroundStyle(accent)
                                .frame(minHeight: 36)
                        }
                        .buttonStyle(.plain)
                    }
                }
                .padding(12)
                .background(Color.white.opacity(0.03), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
            }
            if !block.links.isEmpty {
                FlowLayout(spacing: 8, lineSpacing: 8) {
                    ForEach(block.links) { link in
                        Button {
                            if let url = URL(string: link.url) { openURL(url) }
                        } label: {
                            HStack(spacing: 5) {
                                Text(link.label)
                                Image(systemName: "arrow.up.right")
                                    .font(.caption2.weight(.bold))
                            }
                            .font(.footnote.weight(.semibold))
                            .foregroundStyle(accent)
                            .padding(.horizontal, 12)
                            .frame(minHeight: 34)
                            .background(accent.opacity(0.12), in: Capsule())
                        }
                        .buttonStyle(OrbitalPressStyle())
                    }
                }
            }
            CheckedLine(raw: block.checkedAt)
        }
        .orbitalCard(padding: 16, tint: tone)
        .sheet(isPresented: $showReviews) {
            ReviewsView(onlyProductID: productID)
                .environmentObject(store)
                .presentationDetents([.large])
        }
    }

    private var productID: String? {
        guard let location else { return nil }
        return store.products.first { $0.location.sectionID == location.sectionID && $0.location.nodeID == location.nodeID && $0.block.id == block.id }?.id
    }

    private var tone: Color? {
        switch block.tone {
        case "danger": return OrbitalTheme.danger
        case "warning": return OrbitalTheme.warning
        case "success": return OrbitalTheme.success
        default: return accent
        }
    }

    private var kicker: String {
        [block.space, block.kind?.capitalized].compactMap { ($0?.isEmpty == false) ? $0 : nil }.joined(separator: " · ").uppercased()
    }
}

struct RatingBadge: View {
    let rating: ProductRating

    var body: some View {
        HStack(spacing: 6) {
            Text(String(format: "%.1f", rating.value))
                .font(.subheadline.weight(.bold).monospacedDigit())
                .foregroundStyle(OrbitalTheme.text)
            StarRow(value: rating.value, maximum: rating.max ?? 5)
            Text(caption)
                .font(.caption2)
                .foregroundStyle(OrbitalTheme.textTertiary)
                .lineLimit(1)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(Color.white.opacity(0.04), in: Capsule())
        .accessibilityElement(children: .combine)
    }

    private var caption: String {
        var parts: [String] = []
        if let label = rating.label, !label.isEmpty { parts.append(label) }
        if let count = rating.count { parts.append("\(count) \(count == 1 ? "rating" : "ratings")") }
        if let store = rating.store { parts.append(store) }
        return parts.joined(separator: " · ")
    }
}

struct MiniMetric: View {
    let tile: MetricTile

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(tile.label.uppercased())
                .font(.system(size: 9.5, weight: .bold))
                .tracking(0.5)
                .foregroundStyle(OrbitalTheme.textTertiary)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
            Text(tile.value)
                .font(.system(.headline, design: .rounded).weight(.bold))
                .monospacedDigit()
                .foregroundStyle(OrbitalTheme.text)
                .lineLimit(1)
                .minimumScaleFactor(0.6)
            if let delta = tile.delta {
                DeltaPill(delta: delta, direction: tile.direction)
            } else if let caption = tile.caption, !caption.isEmpty {
                Text(caption)
                    .font(.caption2)
                    .foregroundStyle(OrbitalTheme.textTertiary)
                    .lineLimit(1)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(10)
        .background(Color.white.opacity(0.04), in: RoundedRectangle(cornerRadius: 12, style: .continuous))
        .accessibilityElement(children: .combine)
    }
}

struct CheckedLine: View {
    let raw: String?

    var body: some View {
        if let relative = OrbitalDates.relative(raw) {
            let stale = (OrbitalDates.parse(raw).map { Date().timeIntervalSince($0) > 3 * 3600 }) ?? false
            HStack(spacing: 5) {
                Image(systemName: stale ? "exclamationmark.arrow.circlepath" : "clock.arrow.circlepath")
                    .font(.system(size: 10, weight: .bold))
                Text("Checked \(relative)")
                    .font(.caption2.weight(.medium))
            }
            .foregroundStyle(stale ? OrbitalTheme.warning : OrbitalTheme.textTertiary)
        }
    }
}

/// One product on Home.
struct ProductTile: View {
    let product: ProductRef
    @EnvironmentObject private var store: OrbitalStore

    var body: some View {
        let tint = OrbitalTheme.accentColor(product.accent)
        VStack(alignment: .leading, spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                AppIcon(image: product.iconImage, symbol: product.symbol, tint: tint, size: 44)
                VStack(alignment: .leading, spacing: 2) {
                    Text(product.title)
                        .font(.subheadline.weight(.bold))
                        .foregroundStyle(OrbitalTheme.text)
                        .lineLimit(2)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(Spaces.displayName(product.space))
                        .font(.caption2)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .lineLimit(1)
                }
                Spacer(minLength: 0)
            }
            if let live = store.pulses(for: product.location).first {
                PulseBadge(item: live)
            }
            if !product.block.releases.isEmpty {
                FlowLayout(spacing: 5, lineSpacing: 5) {
                    ForEach(Array(product.block.releases.filter { $0.status != "removed" }.prefix(4).enumerated()), id: \.offset) { _, release in
                        ReleaseChip(release: release)
                    }
                }
            }
            if let rating = product.block.ratings.first {
                HStack(spacing: 5) {
                    StarRow(value: rating.value, maximum: rating.max ?? 5, size: 10)
                    Text(String(format: "%.1f", rating.value))
                        .font(.caption.weight(.bold).monospacedDigit())
                        .foregroundStyle(OrbitalTheme.text)
                    if let count = rating.count {
                        Text("(\(count))")
                            .font(.caption2)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                    }
                }
            }
            if product.block.metrics.isEmpty && product.block.releases.isEmpty, let headline = product.block.headline {
                Text(headline)
                    .font(.caption)
                    .foregroundStyle(OrbitalTheme.textSecondary)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !product.block.metrics.isEmpty {
                HStack(alignment: .top, spacing: 12) {
                    ForEach(product.block.metrics.prefix(2)) { metric in
                        VStack(alignment: .leading, spacing: 1) {
                            Text(metric.value)
                                .font(.system(.subheadline, design: .rounded).weight(.bold))
                                .monospacedDigit()
                                .foregroundStyle(OrbitalTheme.text)
                                .lineLimit(1)
                                .minimumScaleFactor(0.6)
                            Text(metric.label)
                                .font(.caption2)
                                .foregroundStyle(OrbitalTheme.textTertiary)
                                .lineLimit(1)
                        }
                    }
                }
            }
            Spacer(minLength: 0)
            CheckedLine(raw: product.block.checkedAt)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .orbitalCard(padding: 12, radius: 16, tint: tileTone)
        .contentShape(Rectangle())
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isButton)
    }

    private var tileTone: Color? {
        let releases = product.block.releases
        if releases.contains(where: { $0.status == "rejected" }) { return OrbitalTheme.danger }
        // A live app with a release in store review is not a warning tile; its chip says which store.
        if releases.contains(where: { $0.status == "pending" }), !releases.contains(where: { $0.status == "live" }) { return OrbitalTheme.warning }
        return nil
    }
}

/// Every review across products, newest first.
struct ReviewsView: View {
    var onlyProductID: String?
    @EnvironmentObject private var store: OrbitalStore
    @Environment(\.dismiss) private var dismiss
    @State private var productFilter: String?
    @State private var lowOnly = false

    private var rows: [(review: ProductReview, product: ProductRef)] {
        let wanted = productFilter ?? onlyProductID
        let all = store.products.flatMap { product in product.block.reviews.map { (review: $0, product: product) } }
        return all
            .filter { wanted == nil || $0.product.id == wanted }
            .filter { !lowOnly || ($0.review.rating ?? 5) <= 3 }
            .sorted { (OrbitalDates.parse($0.review.at) ?? .distantPast) > (OrbitalDates.parse($1.review.at) ?? .distantPast) }
    }

    private var reviewed: [ProductRef] { store.products.filter { !$0.block.reviews.isEmpty } }

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if onlyProductID == nil, reviewed.count > 1 {
                        ScrollView(.horizontal, showsIndicators: false) {
                            HStack(spacing: 8) {
                                filterChip("All", selected: productFilter == nil) { productFilter = nil }
                                ForEach(reviewed) { product in
                                    filterChip(product.title, selected: productFilter == product.id) { productFilter = product.id }
                                }
                            }
                        }
                    }
                    Toggle(isOn: $lowOnly) {
                        Text("Three stars or fewer")
                            .font(.subheadline)
                            .foregroundStyle(OrbitalTheme.textSecondary)
                    }
                    .tint(OrbitalTheme.warning)
                    if rows.isEmpty {
                        Text("No reviews match.")
                            .font(.footnote)
                            .foregroundStyle(OrbitalTheme.textTertiary)
                            .frame(maxWidth: .infinity)
                            .padding(.top, 40)
                    }
                    ForEach(rows, id: \.review.id) { row in
                        ReviewRow(review: row.review, product: row.product, showsProduct: onlyProductID == nil)
                            .orbitalCard(padding: 14, radius: 14)
                    }
                }
                .padding(16)
            }
            .orbitalBackdrop(OrbitalTheme.accent)
            .navigationTitle("Reviews")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .preferredColorScheme(.dark)
    }

    private func filterChip(_ title: String, selected: Bool, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.footnote.weight(.semibold))
                .lineLimit(1)
                .padding(.horizontal, 12)
                .frame(minHeight: 32)
                .foregroundStyle(selected ? OrbitalTheme.backgroundDeep : OrbitalTheme.textSecondary)
                .background(selected ? AnyShapeStyle(OrbitalTheme.accent) : AnyShapeStyle(Color.white.opacity(0.06)), in: Capsule())
        }
        .buttonStyle(.plain)
    }
}
