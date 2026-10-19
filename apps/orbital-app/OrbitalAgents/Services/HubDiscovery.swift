import Foundation
import Network

/// Finds Orbital hubs on the local network via Bonjour (`_orbital-hub._tcp`).
/// The hub puts its Tailscale address in the TXT record (`url=`); that is the address the app uses,
/// because the hub only accepts Tailscale and loopback clients, never a LAN address.
@MainActor
final class HubDiscovery: ObservableObject {
    @Published var hubs: [DiscoveredHub] = []
    @Published var isScanning = false

    struct DiscoveredHub: Identifiable, Equatable {
        let id: String
        let name: String
        let url: String
    }

    private var browser: NWBrowser?
    private var stopTimer: DispatchWorkItem?

    func scan(timeout: TimeInterval = 6) {
        stop()
        hubs = []
        isScanning = true

        let params = NWParameters()
        params.includePeerToPeer = true
        let browser = NWBrowser(for: .bonjourWithTXTRecord(type: "_orbital-hub._tcp", domain: nil), using: params)
        self.browser = browser

        browser.browseResultsChangedHandler = { [weak self] results, _ in
            let found = results.compactMap(Self.hub(from:))
            Task { @MainActor in
                guard let self else { return }
                for hub in found where !self.hubs.contains(where: { $0.id == hub.id }) {
                    self.hubs.append(hub)
                }
            }
        }
        browser.stateUpdateHandler = { [weak self] state in
            if case .failed = state {
                Task { @MainActor in self?.stop() }
            }
        }
        browser.start(queue: .main)

        let timer = DispatchWorkItem { [weak self] in
            Task { @MainActor in self?.stop() }
        }
        stopTimer = timer
        DispatchQueue.main.asyncAfter(deadline: .now() + timeout, execute: timer)
    }

    func stop() {
        stopTimer?.cancel()
        stopTimer = nil
        browser?.cancel()
        browser = nil
        isScanning = false
    }

    /// Only hubs that advertise a usable (Tailscale) URL; a hub without one can't be signed in to from here.
    nonisolated static func hub(from result: NWBrowser.Result) -> DiscoveredHub? {
        guard case .service(let name, _, _, _) = result.endpoint,
              case .bonjour(let txt) = result.metadata,
              let url = txt["url"], !url.isEmpty else { return nil }
        return DiscoveredHub(id: txt["hub_id"] ?? url, name: name, url: url)
    }
}
