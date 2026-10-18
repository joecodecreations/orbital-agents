import SwiftUI

// A space's page and a product's page. Everything here is read from the hub: the sections filed
// under the space, and its product, ledger, and series blocks. Nothing is entered by hand.

// MARK: - Space

/// Everything one space holds: its products, its sections, its daily numbers by kind, and its money.
struct SpaceView: View {
    let spaceID: String
    @EnvironmentObject private var store: OrbitalStore
    @EnvironmentObject private var router: AppRouter
    @Environment(\.orbitalPushed) private var pushed

    private var space: SpaceSummary? { store.directory.space(spaceID) }

    /// Series in this space: tagged with it, or on one of its sections.
    private var refs: [SeriesRef] {
        let sections = Set(space?.sectionIDs ?? [])
        return store.series.filter { $0.spaceID == spaceID || sections.contains($0.location.sectionID) }
    }

    private var kinds: [String] {
        var seen: [String] = []
        for ref in refs where !seen.contains(ref.block.kind) { seen.append(ref.block.kind) }
        return seen
    }

    private var hasMoney: Bool {
        let sections = Set(space?.sectionIDs ?? [])
        return store.ledgers.contains { Spaces.key($0.space) == spaceID || sections.contains($0.location.sectionID) }
    }

    var body: some View {
        let tint = space?.tint ?? OrbitalTheme.accent
        let products = store.directory.products(of: spaceID)
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                header(tint: tint, products: products)
                if !products.isEmpty {
                    productStrip(products)
                }
                sectionsCard(tint: tint)
                ForEach(kinds, id: \.self) { kind in
                    let mine = refs.filter { $0.block.kind == kind }
                    if mine.count == 1, let only = mine.first {
                        SeriesCard(block: only.block, accent: tint, eyebrow: Insights.nonEmpty(only.block.product) ?? StatStyle.kindLabel(kind),
                                   onOpen: { router.show(only.location, tab: only.location.tabID) })
                    } else {
                        RollupChartCard(eyebrow: StatStyle.kindLabel(kind), tint: tint, kind: kind, refs: mine,
                                        directory: store.directory, lockedSpace: spaceID)
                    }
                }
                if hasMoney {
                    MoneySummaryCard(space: spaceID)
                }
                reviews(products)
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
    }

    private func header(tint: Color, products: [ProductInfo]) -> some View {
        HStack(spacing: 14) {
            AppIcon(image: space?.iconImage, symbol: space?.symbol ?? "square.stack.3d.up.fill", tint: tint, size: 58)
                .clipShape(Circle())
                .overlay(Circle().strokeBorder(tint.opacity(0.7), lineWidth: 2))
            VStack(alignment: .leading, spacing: 3) {
                OrbitalEyebrow(text: "Space", color: tint)
                Text(space?.name ?? spaceID)
                    .font(.title2.weight(.bold))
                    .foregroundStyle(OrbitalTheme.text)
                    .lineLimit(2)
                    .minimumScaleFactor(0.8)
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
