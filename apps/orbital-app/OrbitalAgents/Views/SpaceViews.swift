import SwiftUI

// A space's page and a product's page. Everything here is read from the hub: the sections filed
// under the space, and its product, ledger, and series blocks. Nothing is entered by hand.

// MARK: - Space

/// Everything one space holds: its products, its sections, its daily numbers by kind, and its money.
struct SpaceView: View {
    /// The space this page opened on. The switcher in the header moves to another without leaving the page.
    let spaceID: String
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @Environment(\.orbitalPushed) private var pushed
    @State private var picked: String?
    /// Kept across spaces, so switching shows the same thing for the other space.
    @AppStorage("orbital.space.area") private var area = "overview"
    @AppStorage("orbital.space.platform") private var platform = "all"

    private var current: String { picked ?? spaceID }
    private var space: SpaceSummary? { store.directory.space(current) }
    private var sectionIDs: Set<String> { Set(space?.sectionIDs ?? []) }

    /// Series in this space: tagged with it, or on one of its sections. Narrowed to the platform when one is picked.
    private var refs: [SeriesRef] {
        let all = store.series.filter { store.directory.resolve($0.spaceID) == current || sectionIDs.contains($0.location.sectionID) }
        guard platform != "all", platforms.count > 1 else { return all }
        return all.filter { ref in
            let product = Spaces.key(ref.block.product)
            return !product.isEmpty && (productPlatforms[product]?.contains(platform) ?? false)
        }
    }

    /// Subscriptions blocks for this space's products, or on its sections.
    private var spaceSubscriptions: [SubscriptionsRef] {
        store.subscriptions.filter { ref in
            store.directory.resolve(ref.space) == current || sectionIDs.contains(ref.location.sectionID)
                || store.directory.product(ref.productID)?.spaceID == current
        }
    }

    private var ledgers: [LedgerRef] {
        let all = store.ledgers.filter { store.directory.resolve($0.space) == current || sectionIDs.contains($0.location.sectionID) }
        guard platform != "all", platforms.count > 1 else { return all }
        return all.filter { SpacePlatform.of($0) == platform }
    }

    /// Products by the platforms their money comes from (App Store → iOS, Google Play → Android, Robux → Roblox).
    private var productPlatforms: [String: Set<String>] {
        var out: [String: Set<String>] = [:]
        for ledger in store.ledgers where store.directory.resolve(ledger.space) == current || sectionIDs.contains(ledger.location.sectionID) {
            guard let platform = SpacePlatform.of(ledger) else { continue }
            let name = ledger.title.components(separatedBy: " · ").first ?? ledger.title
            out[Spaces.key(name), default: []].insert(platform)
            for row in ledger.block.days.compactMap(\.product) { out[Spaces.key(row), default: []].insert(platform) }
        }
        return out
    }

    private var platforms: [String] {
        let found = Set(productPlatforms.values.flatMap { $0 })
        return SpacePlatform.order.filter { found.contains($0) }
    }

    private var kinds: [String] {
        var seen: [String] = []
        for ref in refs where !seen.contains(ref.block.kind) { seen.append(ref.block.kind) }
        return seen
    }

    /// Videos blocks on this space's pages.
    private var videos: [(block: ContentBlock, location: PageLocation)] {
        var out: [(ContentBlock, PageLocation)] = []
        for id in space?.sectionIDs ?? [] {
            guard let section = store.sectionsByID[id] else { continue }
            for page in section.pages {
                for block in page.blocks {
                    if case .videos = block {
                        out.append((block, PageLocation(sectionID: section.id, nodeID: page.node?.id, tabID: page.tabID)))
                    }
                }
            }
        }
        return out.map { (block: $0.0, location: $0.1) }
    }

    /// The areas this space has data for, in a fixed order so switching spaces keeps them in place.
    private var areas: [(id: String, title: String, symbol: String)] {
        var out: [(String, String, String)] = [("overview", "Overview", "square.grid.2x2")]
        for kind in SpaceArea.order where kinds.contains(kind) { out.append((kind, SpaceArea.title(kind), SpaceArea.symbol(kind))) }
        // Money and Videos get their own tab below, so a `money` or `videos` series kind must not add a second one.
        let ownTabs: Set<String> = ["overview", "money", "videos", "pages"]
        for kind in kinds where !SpaceArea.order.contains(kind) && !ownTabs.contains(kind) {
            out.append((kind, StatStyle.kindLabel(kind), "chart.xyaxis.line"))
        }
        let hasLedger = !ledgers.isEmpty || store.ledgers.contains(where: { sectionIDs.contains($0.location.sectionID) })
        if hasLedger || kinds.contains("money") { out.append(("money", "Money", "dollarsign.circle")) }
        if !videos.isEmpty || kinds.contains("videos") { out.append(("videos", "Videos", "play.rectangle")) }
        out.append(("pages", "Pages", "doc.on.doc"))
        return out.map { (id: $0.0, title: $0.1, symbol: $0.2) }
    }

    var body: some View {
        let tint = space?.tint ?? OrbitalTheme.accent
        let products = store.directory.products(of: current)
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                header(tint: tint, products: products)
                areaBar(tint: tint)
                if platforms.count > 1 && ["overview", "money", "players", "site", "social", "usage", "search"].contains(area) {
                    platformBar(tint: tint)
                }
                switch area {
                case "overview":
                    if !products.isEmpty { productStrip(products) }
                    ForEach(kinds, id: \.self) { kind in kindCard(kind, tint: tint) }
                    if !ledgers.isEmpty { moneyCards(tint: tint, compact: true) }
                    reviews(products)
                    sectionsCard(tint: tint)
                case "money":
                    if ledgers.isEmpty { empty("No money for \(platformName) in \(space?.name ?? "this space").") } else { moneyCards(tint: tint, compact: false) }
                case "videos":
                    if videos.isEmpty { empty("No videos in \(space?.name ?? "this space").") }
                    ForEach(Array(videos.enumerated()), id: \.offset) { _, row in
                        BlockView(block: row.block, accent: tint, location: row.location)
                    }
                case "pages":
                    sectionsCard(tint: tint)
                default:
                    if kinds.contains(area) { kindCard(area, tint: tint) } else { empty("No \(SpaceArea.title(area).lowercased()) data in \(space?.name ?? "this space")\(platform == "all" ? "" : " for \(platformName)").") }
                }
            }
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, 36)
        }
        .scrollIndicators(.hidden)
        .refreshable { await store.refresh() }
        .orbitalBackdrop(tint)
        .navigationTitle(space?.name ?? "Space")
        .orbitalNavigationBar(pushed: pushed)
        .onChange(of: current) {
            // The other space may not have this platform; keep the area, reset the platform.
            if !platforms.contains(platform) { platform = "all" }
        }
    }

    private var platformName: String { platform == "all" ? "all platforms" : SpacePlatform.title(platform) }

    @ViewBuilder
    private func kindCard(_ kind: String, tint: Color) -> some View {
        let mine = refs.filter { $0.block.kind == kind }
        if mine.count == 1, let only = mine.first {
            SeriesCard(block: only.block, accent: tint, eyebrow: Insights.nonEmpty(only.block.product) ?? SpaceArea.title(kind),
                       onOpen: { router.show(only.location, tab: only.location.tabID) })
        } else if !mine.isEmpty {
            RollupChartCard(eyebrow: SpaceArea.title(kind), tint: tint, kind: kind, refs: mine,
                            directory: store.directory, lockedSpace: current)
        }
    }

    @ViewBuilder
    private func moneyCards(tint: Color, compact: Bool) -> some View {
        if compact || platform == "all" {
            MoneySummaryCard(space: current)
        }
        ForEach(spaceSubscriptions) { ref in
            SubscriptionsSummaryCard(ref: ref, compact: compact)
        }
        if !compact {
            ForEach(ledgers) { ledger in
                LedgerCard(block: ledger.block, accent: tint, location: ledger.location)
            }
        }
    }

    private func empty(_ text: String) -> some View {
        Text(text)
            .font(.footnote)
            .foregroundStyle(OrbitalTheme.textTertiary)
            .frame(maxWidth: .infinity, alignment: .leading)
            .orbitalCard(padding: 14)
    }

    private func areaBar(tint: Color) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(areas, id: \.id) { item in
                    chip(item.title, symbol: item.symbol, selected: area == item.id, tint: tint) { area = item.id }
                }
                if !areas.contains(where: { $0.id == area }) {
                    chip(SpaceArea.title(area), symbol: "questionmark.circle", selected: true, tint: OrbitalTheme.textTertiary) {}
                }
            }
            .padding(.vertical, 1)
        }
    }

    private func platformBar(tint: Color) -> some View {
        HStack(spacing: 8) {
            chip("All", symbol: "square.stack", selected: platform == "all", tint: tint) { platform = "all" }
            ForEach(platforms, id: \.self) { item in
                chip(SpacePlatform.title(item), symbol: SpacePlatform.symbol(item), selected: platform == item, tint: tint) { platform = item }
            }
        }
    }

    private func chip(_ title: String, symbol: String, selected: Bool, tint: Color, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Label(title, systemImage: symbol)
                .font(.caption.weight(.semibold))
                .foregroundStyle(selected ? OrbitalTheme.backgroundDeep : OrbitalTheme.textSecondary)
                .padding(.horizontal, 12)
                .frame(minHeight: 34)
                .background(selected ? tint : Color.white.opacity(0.06), in: Capsule())
        }
        .buttonStyle(.plain)
        .accessibilityAddTraits(selected ? .isSelected : [])
    }

    private func header(tint: Color, products: [ProductInfo]) -> some View {
        HStack(spacing: 14) {
            AppIcon(image: space?.iconImage, symbol: space?.symbol ?? "square.stack.3d.up.fill", tint: tint, size: 58)
                .clipShape(Circle())
                .overlay(Circle().strokeBorder(tint.opacity(0.7), lineWidth: 2))
            VStack(alignment: .leading, spacing: 3) {
                OrbitalEyebrow(text: "Space", color: tint)
                Menu {
                    ForEach(store.directory.spaces) { other in
                        Button {
                            picked = other.id
                        } label: {
                            Label(other.name, systemImage: other.id == current ? "checkmark" : other.symbol)
                        }
                    }
                } label: {
                    HStack(spacing: 6) {
                        Text(space?.name ?? current)
                            .font(.title2.weight(.bold))
                            .foregroundStyle(OrbitalTheme.text)
                            .lineLimit(2)
                            .minimumScaleFactor(0.8)
                        Image(systemName: "chevron.up.chevron.down")
                            .font(.caption.weight(.bold))
                            .foregroundStyle(tint)
                    }
                }
                .accessibilityLabel("Switch space, now \(space?.name ?? current)")
                if let subtitle = space?.subtitle ?? (products.isEmpty ? nil : products.map(\.name).joined(separator: " · ")) {
                    Text(subtitle)
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .lineLimit(2)
                }
            }
            Spacer(minLength: 0)
        }
    }

    /// One round button per product. Tap opens the product page.
    private func productStrip(_ products: [ProductInfo]) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(alignment: .top, spacing: 12) {
                ForEach(products) { product in
                    Button {
                        router.openProduct(product.id)
                    } label: {
                        VStack(spacing: 6) {
                            AppIcon(image: product.iconImage, symbol: product.symbol, tint: product.tint, size: 54)
                                .overlay(RoundedRectangle(cornerRadius: 54 * 0.225, style: .continuous).strokeBorder(product.tint.opacity(0.5), lineWidth: 1))
                            Text(product.name)
                                .font(.caption2.weight(.semibold))
                                .foregroundStyle(OrbitalTheme.textSecondary)
                                .lineLimit(2)
                                .multilineTextAlignment(.center)
                                .frame(width: 76)
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(OrbitalPressStyle())
                    .accessibilityLabel("Open \(product.name)")
                }
            }
            .padding(.vertical, 2)
        }
    }

    @ViewBuilder
    private func sectionsCard(tint: Color) -> some View {
        let entries = (space?.sectionIDs ?? []).compactMap { store.entry($0) }
        if !entries.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                OrbitalEyebrow(text: "Sections")
                VStack(spacing: 0) {
                    ForEach(Array(entries.enumerated()), id: \.element.id) { index, entry in
                        if index > 0 {
                            Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                        }
                        Button {
                            router.show(PageLocation(sectionID: entry.id))
                        } label: {
                            ReportLinkRow(
                                title: entry.title,
                                detail: Insights.nonEmpty(entry.summary) ?? Insights.nonEmpty(entry.subtitle) ?? OrbitalDates.relative(entry.updatedAt).map { "Updated \($0)" },
                                image: entry.iconImage,
                                symbol: entry.icon,
                                tint: OrbitalTheme.accentColor(entry.accent)
                            )
                        }
                        .buttonStyle(OrbitalPressStyle())
                    }
                }
                .orbitalCard(padding: 14)
            }
        }
    }

    @ViewBuilder
    private func reviews(_ products: [ProductInfo]) -> some View {
        let ids = Set(products.map(\.id))
        let rows = store.products
            .filter { ids.contains(Spaces.key($0.title)) }
            .flatMap { product in product.block.reviews.map { (review: $0, product: product) } }
            .sorted { (OrbitalDates.parseFlexible($0.review.at) ?? .distantPast) > (OrbitalDates.parseFlexible($1.review.at) ?? .distantPast) }
        if !rows.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                OrbitalEyebrow(text: "Latest reviews")
                VStack(spacing: 0) {
                    ForEach(Array(rows.prefix(5).enumerated()), id: \.element.review.id) { index, row in
                        if index > 0 {
                            Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                        }
                        ReviewRow(review: row.review, product: row.product, showsProduct: true)
                            .padding(.vertical, 10)
                    }
                }
                .orbitalCard(padding: 14)
            }
        }
    }
}

/// The standard areas a space page offers, in the order they appear.
enum SpaceArea {
    static let order = ["site", "social", "players", "usage", "search"]

    static func title(_ kind: String) -> String {
        switch kind {
        case "overview": return "Overview"
        case "site": return "Site traffic"
        case "social": return "Social media"
        case "players": return "Players"
        case "usage": return "Usage"
        case "search": return "Search"
        case "money": return "Money"
        case "videos": return "Videos"
        case "pages": return "Pages"
        default: return StatStyle.kindLabel(kind)
        }
    }

    static func symbol(_ kind: String) -> String {
        switch kind {
        case "site": return "globe"
        case "social": return "bubble.left.and.bubble.right"
        case "players": return "gamecontroller"
        case "usage": return "person.2"
        case "search": return "magnifyingglass"
        default: return "chart.xyaxis.line"
        }
    }
}

/// Where money comes from, read off a ledger's title, source and rows.
enum SpacePlatform {
    static let order = ["ios", "android", "roblox", "web"]

    static func of(_ ledger: LedgerRef) -> String? {
        let text = [ledger.title, ledger.source, ledger.block.source ?? "", ledger.block.title ?? ""].joined(separator: " ").lowercased()
        if text.contains("app store") || text.contains("ios") || text.contains("apple") { return "ios" }
        if text.contains("google play") || text.contains("android") || text.contains("play store") { return "android" }
        if text.contains("robux") || text.contains("roblox") { return "roblox" }
        if text.contains("stripe") || text.contains("web") { return "web" }
        return nil
    }

    static func title(_ id: String) -> String {
        switch id {
        case "ios": return "iOS"
        case "android": return "Android"
        case "roblox": return "Roblox"
        default: return "Web"
        }
    }

    static func symbol(_ id: String) -> String {
        switch id {
        case "ios": return "apple.logo"
        case "android": return "smartphone"
        case "roblox": return "cube.fill"
        default: return "globe"
        }
    }
}

/// A row that opens a page.
struct ReportLinkRow: View {
    let title: String
    var detail: String?
    var image: String?
    var symbol: String
    var tint: Color

    var body: some View {
        HStack(spacing: 12) {
            AppIcon(image: image, symbol: symbol, tint: tint, size: 32)
            VStack(alignment: .leading, spacing: 2) {
                Text(title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(OrbitalTheme.text)
                    .lineLimit(1)
                if let detail {
                    Text(detail)
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textTertiary)
                        .lineLimit(1)
                }
            }
            Spacer(minLength: 0)
            Image(systemName: "chevron.right")
                .font(.caption.weight(.bold))
                .foregroundStyle(OrbitalTheme.textTertiary)
        }
        .padding(.vertical, 8)
        .contentShape(Rectangle())
    }
}

// MARK: - Product

/// One product: its daily charts, its money, its status card, and every page behind it.
struct ProductView: View {
    let productID: String
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @Environment(\.orbitalPushed) private var pushed

    private var product: ProductInfo? { store.directory.product(productID) }
    private var ref: ProductRef? { store.products.first { Spaces.key($0.title) == productID } }
    private var refs: [SeriesRef] { store.series.filter { $0.productID == productID } }

    /// Usage first (the product's own numbers), then the other kinds in the order they arrived.
    private var orderedRefs: [SeriesRef] {
        refs.sorted { lhs, rhs in
            if (lhs.block.kind == "usage") != (rhs.block.kind == "usage") { return lhs.block.kind == "usage" }
            return false
        }
    }

    private var hasMoney: Bool {
        store.ledgers.contains { ledger in ledger.block.days.contains { Spaces.key($0.product ?? "") == productID } }
    }

    var body: some View {
        let tint = product?.tint ?? OrbitalTheme.accent
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                header(tint: tint)
                if let location = product?.location, case let live = store.pulses(for: location), !live.isEmpty {
                    VStack(alignment: .leading, spacing: 8) {
                        OrbitalEyebrow(text: "Live", color: OrbitalTheme.success)
                        PulseGrid(items: live)
                    }
                    .orbitalCard(padding: 14, tint: OrbitalTheme.success)
                }
                ForEach(orderedRefs) { series in
                    SeriesCard(
                        block: series.block,
                        accent: tint,
                        eyebrow: series.block.title == nil ? StatStyle.kindLabel(series.block.kind) : nil
                    )
                }
                if hasMoney {
                    MoneySummaryCard(space: product?.spaceID, product: productID)
                }
                ForEach(store.subscriptions.filter { $0.productID == productID }) { ref in
                    SubscriptionsSummaryCard(ref: ref)
                }
                if let ref {
                    ProductCard(block: ref.block, accent: tint, location: ref.location)
                }
                reports(tint: tint)
            }
            .frame(maxWidth: OrbitalTheme.columnMax)
            .frame(maxWidth: .infinity)
            .padding(.horizontal, 16)
            .padding(.top, 12)
            .padding(.bottom, 36)
        }
        .scrollIndicators(.hidden)
        .refreshable { await store.refresh() }
        .orbitalBackdrop(tint)
        .navigationTitle(product?.name ?? "Product")
        .orbitalNavigationBar(pushed: pushed)
    }

    private func header(tint: Color) -> some View {
        let space = store.directory.space(product?.spaceID)
        return HStack(spacing: 14) {
            AppIcon(image: product?.iconImage, symbol: product?.symbol ?? "app.fill", tint: tint, size: 60)
            VStack(alignment: .leading, spacing: 4) {
                if let space {
                    Button {
                        router.openSpace(space.id)
                    } label: {
                        HStack(spacing: 4) {
                            OrbitalEyebrow(text: space.name, color: space.tint)
                            Image(systemName: "chevron.right")
                                .font(.system(size: 8, weight: .heavy))
                                .foregroundStyle(space.tint)
                        }
                        .frame(minHeight: 24)
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                }
                Text(product?.name ?? productID)
                    .font(.title2.weight(.bold))
                    .foregroundStyle(OrbitalTheme.text)
                    .lineLimit(2)
                    .minimumScaleFactor(0.8)
                if let headline = ref?.block.headline ?? ref?.subtitle {
                    Text(headline)
                        .font(.caption)
                        .foregroundStyle(OrbitalTheme.textSecondary)
                        .lineLimit(3)
                }
            }
            Spacer(minLength: 0)
        }
    }

    /// Every tab of the product's own page, then the other pages its daily numbers come from.
    @ViewBuilder
    private func reports(tint: Color) -> some View {
        let links = reportLinks
        if !links.isEmpty {
            VStack(alignment: .leading, spacing: 10) {
                OrbitalEyebrow(text: "Pages")
                VStack(spacing: 0) {
                    ForEach(Array(links.enumerated()), id: \.element.id) { index, link in
                        if index > 0 {
                            Rectangle().fill(OrbitalTheme.hairline.opacity(0.6)).frame(height: 1)
                        }
                        Button {
                            router.show(link.location, tab: link.location.tabID)
                        } label: {
                            ReportLinkRow(title: link.title, detail: link.detail, image: nil, symbol: link.symbol, tint: tint)
                        }
                        .buttonStyle(OrbitalPressStyle())
                    }
                }
                .orbitalCard(padding: 14)
            }
        }
    }

    private struct ReportLink: Identifiable {
        var id: String
        var title: String
        var detail: String?
        var symbol: String
        var location: PageLocation
    }

    private var reportLinks: [ReportLink] {
        var pages: [PageLocation] = []
        if let location = product?.location {
            pages.append(PageLocation(sectionID: location.sectionID, nodeID: location.nodeID))
        }
        for series in refs {
            let page = PageLocation(sectionID: series.location.sectionID, nodeID: series.location.nodeID)
            if !pages.contains(page) { pages.append(page) }
        }
        var out: [ReportLink] = []
        for page in pages {
            guard let section = store.sectionsByID[page.sectionID] else { continue }
            guard let nodeID = page.nodeID, let node = section.nodes.first(where: { $0.id == nodeID }) else {
                out.append(ReportLink(id: page.sectionID, title: section.title, detail: section.subtitle, symbol: section.icon, location: page))
                continue
            }
            if node.layout == .tabs, !node.tabs.isEmpty {
                for tab in node.tabs {
                    out.append(ReportLink(
                        id: "\(page.sectionID)/\(nodeID)/\(tab.id)",
                        title: pages.count > 1 ? "\(node.title) · \(tab.title)" : tab.title,
                        detail: nil,
                        symbol: tab.icon ?? node.icon ?? "doc.text",
                        location: PageLocation(sectionID: page.sectionID, nodeID: nodeID, tabID: tab.id)
                    ))
                }
            } else {
                out.append(ReportLink(id: "\(page.sectionID)/\(nodeID)", title: node.title, detail: node.subtitle, symbol: node.icon ?? "doc.text", location: page))
            }
        }
        return out
    }
}
