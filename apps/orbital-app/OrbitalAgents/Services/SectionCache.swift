import Foundation

/// The last catalog, sections, alerts, and pulses on disk, so the app opens with data while offline.
enum SectionCache {
    private static let encoder = JSONEncoder()
    private static let decoder = JSONDecoder()

    static var directory: URL {
        let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        let dir = base.appendingPathComponent("OrbitalCache", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        try? FileManager.default.createDirectory(at: dir.appendingPathComponent("sections", isDirectory: true), withIntermediateDirectories: true)
        return dir
    }

    static func loadCatalog() -> CatalogResponse {
        let url = directory.appendingPathComponent("catalog.json")
        guard let data = try? Data(contentsOf: url) else { return CatalogResponse(sections: []) }
        return (try? decoder.decode(CatalogResponse.self, from: data)) ?? CatalogResponse(sections: [])
    }

    static func saveCatalog(_ catalog: CatalogResponse) {
        guard let data = try? encoder.encode(catalog) else { return }
        try? data.write(to: directory.appendingPathComponent("catalog.json"), options: .atomic)
    }

    static func loadSections() -> [String: SectionDocument] {
        let folder = directory.appendingPathComponent("sections", isDirectory: true)
        guard let urls = try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil) else {
            return [:]
        }
        var result: [String: SectionDocument] = [:]
        for url in urls where url.pathExtension == "json" {
            guard let data = try? Data(contentsOf: url),
                  let doc = try? decoder.decode(SectionDocument.self, from: data) else { continue }
            result[doc.id] = doc
        }
        return result
    }

    static func save(_ document: SectionDocument) {
        guard let data = try? encoder.encode(document) else { return }
        let url = directory.appendingPathComponent("sections/\(document.id).json")
        try? data.write(to: url)
    }

    static func deleteSection(_ id: String) {
        let url = directory.appendingPathComponent("sections/\(id).json")
        try? FileManager.default.removeItem(at: url)
    }

    static func loadPulses() -> [PulseItem] {
        let url = directory.appendingPathComponent("pulse.json")
        guard let data = try? Data(contentsOf: url) else { return [] }
        return (try? decoder.decode([PulseItem].self, from: data)) ?? []
    }

    static func savePulses(_ items: [PulseItem]) {
        guard let data = try? encoder.encode(items) else { return }
        try? data.write(to: directory.appendingPathComponent("pulse.json"))
    }

    static func loadAlerts() -> [OrbitalAlert] {
        let url = directory.appendingPathComponent("alerts.json")
        guard let data = try? Data(contentsOf: url) else { return [] }
        return (try? decoder.decode([OrbitalAlert].self, from: data)) ?? []
    }

    static func saveAlerts(_ alerts: [OrbitalAlert]) {
        guard let data = try? encoder.encode(alerts) else { return }
        try? data.write(to: directory.appendingPathComponent("alerts.json"))
    }
}
