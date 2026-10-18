import Foundation
import UserNotifications

/// Notification buttons whose id is `act.<type>` post a write-back action. This runs from the
/// notification delegate, often with the app in the background and no store loaded, so it reads the
/// hub address and session token directly. `user_info` carries `action_section`, `action_item`, and
/// `action_value` (a JSON string).
enum OrbitalNotificationActions {
    struct Outcome {
        /// The `value` that was sent, for the in-app toast.
        var value: [String: Any]
        /// Nil when the hub accepted the action.
        var error: String?
    }

    static func perform(
        type: String,
        userInfo: [AnyHashable: Any],
        alertID: String?,
        content: UNNotificationContent
    ) async -> Outcome {
        let value = actionValue(type: type, raw: userInfo["action_value"])
        let outcome = await post(type: type, value: value, userInfo: userInfo, alertID: alertID)
        if let error = outcome.error {
            await reportFailure(error, content: content, alertID: alertID)
        }
        return outcome
    }

    private static func post(type: String, value: [String: Any], userInfo: [AnyHashable: Any], alertID: String?) async -> Outcome {
        guard let section = (userInfo["action_section"] as? String).flatMap(nonEmpty) ?? (userInfo["section_id"] as? String).flatMap(nonEmpty) else {
            return Outcome(value: value, error: "the notification did not say which section")
        }
        guard let token = OrbitalKeychain.storedSessionToken(), !token.isEmpty else {
            return Outcome(value: value, error: "signed out")
        }
        let stored = UserDefaults.standard.string(forKey: OrbitalStore.Keys.hubURL) ?? ""
        guard let base = OrbitalStore.parse(stored) else {
            return Outcome(value: value, error: "the hub address is not set")
        }
        var body: [String: Any] = [
            "schema": OrbitalSchema.action,
            "type": type,
            "value": value,
            "source": "notification",
        ]
        if let item = (userInfo["action_item"] as? String).flatMap(nonEmpty) {
            body["item_id"] = item
        }
        if let error = await send(base: base, path: "/v1/sections/\(section)/actions", token: token, body: body) {
            return Outcome(value: value, error: error)
        }
        // The alert did its job. A failed post leaves it unread so the inbox still shows it.
        if let alertID, !OrbitalTestAlert.isTest(alertID) {
            _ = await send(base: base, path: "/v1/alerts/ack", token: token, body: ["ids": [alertID]])
        }
        return Outcome(value: value, error: nil)
    }

    /// `action_value` is a JSON string. A set_* button without one means "yes".
    static func actionValue(type: String, raw: Any?) -> [String: Any] {
        if let dictionary = raw as? [String: Any] { return dictionary }
        if let text = raw as? String, let data = text.data(using: .utf8),
           let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
            return object
        }
        switch type {
        case "task.set_done": return ["done": true]
        default: return [:]
        }
    }

    /// Nil on success, otherwise a short reason.
    private static func send(base: URL, path: String, token: String, body: [String: Any]) async -> String? {
        guard let url = URL(string: base.absoluteString + path),
              let data = try? JSONSerialization.data(withJSONObject: body)
        else { return "the request could not be built" }
        var request = URLRequest(url: url, timeoutInterval: 15)
        request.httpMethod = "POST"
        request.httpBody = data
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let device = UserDefaults.standard.string(forKey: OrbitalStore.Keys.deviceID) {
            request.setValue(device, forHTTPHeaderField: "X-Orbital-Device")
        }
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard let http = response as? HTTPURLResponse else { return "no response" }
            if (200 ... 299).contains(http.statusCode) { return nil }
            return OrbitalAPIError.http(http.statusCode, String(data: data, encoding: .utf8) ?? "").shortReason
        } catch {
            return error.localizedDescription
        }
    }

    /// A background tap that did not land must not look like it did. Say so with a notification that opens the page.
    private static func reportFailure(_ reason: String, content original: UNNotificationContent, alertID: String?) async {
        let content = UNMutableNotificationContent()
        content.title = "Not sent"
        content.body = "\(original.title): \(reason). Open \(OrbitalBrand.appName) to try again."
        content.sound = .default
        content.threadIdentifier = original.threadIdentifier
        content.categoryIdentifier = "orbital.default"
        var info: [String: Any] = [:]
        for key in ["launch_url", "section_id", "node_id", "tab_id"] {
            if let value = original.userInfo[key] { info[key] = value }
        }
        content.userInfo = info
        let identifier = "\(alertID ?? "action")#failed"
        let request = UNNotificationRequest(identifier: identifier, content: content, trigger: nil)
        try? await UNUserNotificationCenter.current().add(request)
    }

    private static func nonEmpty(_ text: String) -> String? {
        text.trimmingCharacters(in: .whitespaces).isEmpty ? nil : text
    }
}
