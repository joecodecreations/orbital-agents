import Foundation
import SwiftUI

// Spaces are the home tiles: a project, a team, a household, a server fleet. Nothing about them is
// configured in the app. They come from the hub's catalog (`spaces`, and `space` on each section) and
// from the `space` tag on product, ledger, and series blocks. Products are the things inside a space
// that have their own numbers: an app, a website, a server.

struct SpaceSummary: Identifiable, Hashable {
    var id: String
    var name: String
    var subtitle: String?
    var iconImage: String?
    var symbol: String
    var tint: Color
    var order: Int
    /// Sections in this space, in left-bar order.
    var sectionIDs: [String]
}

struct ProductInfo: Identifiable, Hashable {
    var id: String
    var name: String
    var spaceID: String
    var iconImage: String?
    var symbol: String
    var accent: String?
    /// The product's own page: its node, opened on the tab its product block sits on.
    var location: PageLocation?
    var kind: String?

    var tint: Color { OrbitalTheme.color(for: id, hex: accent) }
}

enum Spaces {
    /// "Home Lab", "home-lab", and "home_lab" are one space. Letters and digits only, lowercased.
    static func key(_ name: String) -> String {
        name.lowercased().filter { $0.isLetter || $0.isNumber }
    }

    /// A slug like "home-lab" shown as "Home Lab"; a real name is kept as written.
    static func displayName(_ raw: String) -> String {
        let looksLikeSlug = raw == raw.lowercased() && !raw.contains(" ") && (raw.contains("-") || raw.contains("_"))
        guard looksLikeSlug else { return raw }
        return raw.replacingOccurrences(of: "-", with: " ").replacingOccurrences(of: "_", with: " ").capitalized
    }
}

/// Spaces, their sections and products, and where each product's page is.
struct SpaceDirectory: Equatable {
    var spaces: [SpaceSummary] = []
    var products: [ProductInfo] = []
    /// Other names (a title, an old company name) → the space id they belong to.
    var aliases: [String: String] = [:]

    /// The space id a raw name on a block belongs to.
    func resolve(_ raw: String?) -> String {
        let key = Spaces.key(raw ?? "")
        return aliases[key] ?? key
    }

    func space(_ id: String?) -> SpaceSummary? {
        guard let id else { return nil }
        let key = Spaces.key(id)
        return spaces.first { $0.id == key }
    }

    func product(_ id: String?) -> ProductInfo? {
        guard let id else { return nil }
        return products.first { $0.id == id }
    }

    func products(of spaceID: String) -> [ProductInfo] {
        products.filter { $0.spaceID == spaceID }
    }

    /// The space a section is filed under, if any.
    func space(forSection id: String) -> SpaceSummary? {
        spaces.first { $0.sectionIDs.contains(id) }
    }

    static func build(
        catalog: [CatalogEntry],
        declared: [SpaceInfo]?,
        sections: [String: SectionDocument],
        collected: Insights.Collected
    ) -> SpaceDirectory {
        struct Draft {
            var id: String
            var name: String
            var subtitle: String?
            var icon: String?
            var iconImage: String?
            var accent: String?
            var order: Int?
            var sectionIDs: [String] = []
            var firstSeen: Int
            var titled = false
        }
        var drafts: [String: Draft] = [:]
        var seen = 0
        /// A declared space's title used as a tag points at the declared id.
        var aliases: [String: String] = [:]

        @discardableResult
        func note(_ raw: String) -> String? {
            let key = Spaces.key(raw)
            guard !key.isEmpty else { return nil }
            // Another name for a declared space: never a space of its own.
            if let target = aliases[key], drafts[target] != nil { return target }
            if drafts[key] == nil {
                drafts[key] = Draft(id: key, name: Spaces.displayName(raw), firstSeen: seen)
                seen += 1
            }
            return key
        }

        // 1. What the hub declares.
        for info in declared ?? [] {
            let key = Spaces.key(info.id)
            guard !key.isEmpty else { continue }
            var draft = drafts[key] ?? Draft(id: key, name: info.title, firstSeen: seen)
            seen += 1
            draft.name = info.title
            draft.titled = true
            draft.subtitle = info.subtitle
            draft.icon = info.icon
            draft.iconImage = info.iconImage
            draft.accent = info.accent
            draft.order = info.order
            draft.sectionIDs.append(contentsOf: info.sections ?? [])
            drafts[key] = draft
            // A section may name the space by its title rather than its id, or by an alias (an old company name).
            if Spaces.key(info.title) != key, drafts[Spaces.key(info.title)] == nil {
                aliases[Spaces.key(info.title)] = key
            }
            for alias in info.aliases ?? [] where Spaces.key(alias) != key {
                aliases[Spaces.key(alias)] = key
            }
        }
        /// Sections a declared space lists stay there, whatever their own tag says.
        let claimed = Set((declared ?? []).flatMap { $0.sections ?? [] })

        // 2. Sections tagged with a space, in left-bar order.
        let orderedCatalog = catalog.sorted { ($0.order, $0.title) < ($1.order, $1.title) }
        for entry in orderedCatalog where !claimed.contains(entry.id) {
            let document = sections[entry.id]
            let tag = document?.space ?? entry.space
            guard let tag, let raw = note(tag) else { continue }
            let key = aliases[raw] ?? raw
            if !(drafts[key]?.sectionIDs.contains(entry.id) ?? true) {
                drafts[key]?.sectionIDs.append(entry.id)
            }
            // The first section that names the space titles it, unless the hub declared it.
            if declared?.contains(where: { Spaces.key($0.id) == key }) != true {
                if let title = document?.spaceTitle, drafts[key]?.titled != true {
                    drafts[key]?.name = title
                    drafts[key]?.titled = true
                }
                if let icon = document?.spaceIcon, drafts[key]?.icon == nil {
                    drafts[key]?.icon = icon
                }
            }
        }

        // 3. Blocks tagged with a space. A section with no tag joins the one space its blocks name.
        var blockSpaces: [String: Set<String>] = [:]
        func tagged(_ raw: String, section: String) {
            guard let noted = note(raw) else { return }
            blockSpaces[section, default: []].insert(aliases[noted] ?? noted)
        }
        for product in collected.products where !product.space.isEmpty { tagged(product.space, section: product.location.sectionID) }
        for ledger in collected.ledgers where !ledger.space.isEmpty { tagged(ledger.space, section: ledger.location.sectionID) }
        for series in collected.series where !series.block.space.isEmpty { tagged(series.block.space, section: series.location.sectionID) }
        let filed = Set(drafts.values.flatMap(\.sectionIDs))
        for entry in orderedCatalog where !filed.contains(entry.id) {
            if let keys = blockSpaces[entry.id], keys.count == 1, let key = keys.first {
                drafts[key]?.sectionIDs.append(entry.id)
            }
        }
        // Aliased drafts (a title used as a tag) fold into the declared space.
        for (alias, target) in aliases {
            guard let extra = drafts.removeValue(forKey: alias) else { continue }
            for id in extra.sectionIDs where !(drafts[target]?.sectionIDs.contains(id) ?? true) {
                drafts[target]?.sectionIDs.append(id)
            }
        }

        // Products: product blocks first (they have a page), then names on series and ledger rows.
        var found: [String: ProductInfo] = [:]
        var productOrder: [String] = []
        func add(_ name: String, space: String, image: String? = nil, symbol: String? = nil, accent: String? = nil,
                 location: PageLocation? = nil, kind: String? = nil) {
            let id = Spaces.key(name)
            guard !id.isEmpty, let rawSpace = note(space) else { return }
            let spaceID = aliases[rawSpace] ?? rawSpace
            if var existing = found[id] {
                existing.iconImage = existing.iconImage ?? image
                if existing.location == nil { existing.location = location }
                existing.kind = existing.kind ?? kind
                if existing.symbol == "app.fill", let symbol { existing.symbol = symbol }
                found[id] = existing
            } else {
                found[id] = ProductInfo(id: id, name: name, spaceID: spaceID, iconImage: image, symbol: symbol ?? "app.fill",
                                        accent: accent, location: location, kind: kind)
                productOrder.append(id)
            }
        }
        func spaceOf(section id: String) -> String? {
            drafts.values.first { $0.sectionIDs.contains(id) }.map(\.name)
        }
        for product in collected.products {
            let space = product.space.isEmpty ? (spaceOf(section: product.location.sectionID) ?? "") : product.space
            add(product.title, space: space, image: product.iconImage, symbol: product.symbol, accent: product.accent,
                location: product.location, kind: product.block.kind)
        }
        for ref in collected.series where !ref.block.product.isEmpty {
            let node = sections[ref.location.sectionID]?.nodes.first { $0.id == ref.location.nodeID }
            let space = ref.block.space.isEmpty ? (spaceOf(section: ref.location.sectionID) ?? "") : ref.block.space
            add(ref.block.product, space: space, image: node?.iconImage, accent: ref.accent)
        }
        for ledger in collected.ledgers {
            for day in ledger.block.days {
                guard let name = Insights.nonEmpty(day.product) else { continue }
                let space = Insights.nonEmpty(day.space) ?? (ledger.space.isEmpty ? (spaceOf(section: ledger.location.sectionID) ?? "") : ledger.space)
                add(name, space: space, accent: ledger.accent)
            }
        }
        let productList = productOrder.compactMap { found[$0] }

        var spaces: [SpaceSummary] = []
        for draft in drafts.values {
            let firstSection = draft.sectionIDs.compactMap { id in catalog.first { $0.id == id } }.first
            let namesake = productList.first { $0.id == draft.id }
            let sectionOrder = draft.sectionIDs.compactMap { id in catalog.first { $0.id == id }?.order }.min()
            spaces.append(SpaceSummary(
                id: draft.id,
                name: draft.name,
                subtitle: draft.subtitle,
                iconImage: draft.iconImage ?? namesake?.iconImage ?? firstSection?.iconImage,
                symbol: draft.icon ?? firstSection?.icon ?? "square.stack.3d.up.fill",
                tint: OrbitalTheme.color(for: draft.id, hex: draft.accent ?? firstSection?.accent),
                order: draft.order ?? sectionOrder ?? (1000 + draft.firstSeen),
                sectionIDs: draft.sectionIDs.sorted { lhs, rhs in
                    let left = catalog.first { $0.id == lhs }?.order ?? Int.max
                    let right = catalog.first { $0.id == rhs }?.order ?? Int.max
                    return left == right ? lhs < rhs : left < right
                }
            ))
        }
        spaces.sort { lhs, rhs in
            if lhs.order != rhs.order { return lhs.order < rhs.order }
            return lhs.name.localizedCaseInsensitiveCompare(rhs.name) == .orderedAscending
        }
        return SpaceDirectory(spaces: spaces, products: productList, aliases: aliases)
    }
}
