import Foundation

/// `WS /v1/live`: the hub pushes section, alert, pulse, scheduler, desk, tracker, and satellite events.
/// Reconnects two seconds after a drop; the store catches up with one sync on the hub's `hello`.
@MainActor
final class LiveSocket: NSObject {
    private var task: URLSessionWebSocketTask?
    private var session: URLSession!
    private var onEvent: (([String: Any]) -> Void)?
    var onState: ((String) -> Void)?
    private var wantConnected = false
    private var baseURL: URL?
    private var token: String?
    private var reconnectTask: Task<Void, Never>?

    override init() {
        super.init()
        let config = URLSessionConfiguration.default
        config.waitsForConnectivity = true
        session = URLSession(configuration: config, delegate: nil, delegateQueue: nil)
    }

    func connect(baseURL: URL, token: String, onEvent: @escaping ([String: Any]) -> Void) {
        self.onEvent = onEvent
        self.baseURL = baseURL
        self.token = token
        wantConnected = true
        reconnectTask?.cancel()
        open()
    }

    func disconnect() {
        wantConnected = false
        reconnectTask?.cancel()
        task?.cancel(with: .goingAway, reason: nil)
        task = nil
    }

    private func open() {
        task?.cancel(with: .goingAway, reason: nil)
        guard wantConnected, let baseURL, let token else { return }
        guard var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else { return }
        switch components.scheme?.lowercased() {
        case "https", "wss":
            components.scheme = "wss"
        default:
            components.scheme = "ws"
        }
        let basePath = components.path.hasSuffix("/") ? String(components.path.dropLast()) : components.path
        components.path = basePath + "/v1/live"
        components.queryItems = [URLQueryItem(name: "token", value: token)]
        guard let url = components.url else { return }
        var request = URLRequest(url: url)
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        let task = session.webSocketTask(with: request)
        self.task = task
        task.resume()
        receive(on: task)
    }

    private func scheduleReconnect() {
        reconnectTask?.cancel()
        reconnectTask = Task { @MainActor in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            guard wantConnected, task == nil else { return }
            onState?("Connecting")
            open()
        }
    }

    private func receive(on task: URLSessionWebSocketTask) {
        task.receive { [weak self] result in
            Task { @MainActor in
                guard let self, self.wantConnected, self.task === task else { return }
                switch result {
                case .success(let message):
                    if case .string(let text) = message, let data = text.data(using: .utf8),
                       let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                        self.onEvent?(obj)
                    }
                    self.receive(on: task)
                case .failure:
                    self.task = nil
                    self.onState?("Offline")
                    self.scheduleReconnect()
                }
            }
        }
    }
}
