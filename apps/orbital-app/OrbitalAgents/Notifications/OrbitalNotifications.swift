import BackgroundTasks
import Foundation
import UserNotifications

extension Notification.Name {
    /// Posted when background refresh fires. The store should sync.
    static let orbitalRefresh = Notification.Name("orbital.refresh")
    /// Posted when the user acts on a notification. userInfo: action, alert_id, launch_url, response_text.
    static let orbitalAlertAction = Notification.Name("orbital.alert.action")
    /// The system gave this device an Apple push token. userInfo: token (hex).
    static let orbitalPushToken = Notification.Name("orbital.push.token")
}

/// Local notifications for hub alerts. Every alert that reaches the app (sync or live socket) is
/// scheduled here, so reminders fire on time even while the hub is asleep.
enum OrbitalNotifications {
    private static let delegate = OrbitalNotificationDelegate()
    private static let deliveredKey = "orbital.deliveredAlertIDs"
    /// Matches BGTaskSchedulerPermittedIdentifiers in Info.plist: `$(PRODUCT_BUNDLE_IDENTIFIER).refresh`.
    private static var taskID: String { "\(OrbitalBrand.bundleID).refresh" }
    private static var extraCategories: [String: UNNotificationCategory] = [:]

    static func configure() {
        let center = UNUserNotificationCenter.current()
        center.delegate = delegate
        registerCategories(on: center)
        registerBackgroundTask()
    }

    /// One full sync, set by the store. Background refresh waits for it before telling iOS it is done,
    /// so an alert cleared on another device also leaves this one's Notification Center while the app is closed.
    @MainActor static var backgroundSync: (() async -> Void)?

    static func registerBackgroundTask() {
        BGTaskScheduler.shared.register(forTaskWithIdentifier: taskID, using: nil) { task in
            scheduleRefresh()
            let work = Task { @MainActor in
                if let sync = backgroundSync {
                    await sync()
                } else {
                    NotificationCenter.default.post(name: .orbitalRefresh, object: nil)
                }
                guard !Task.isCancelled else { return }
                task.setTaskCompleted(success: true)
            }
            task.expirationHandler = {
                work.cancel()
                task.setTaskCompleted(success: false)
            }
        }
        scheduleRefresh()
    }

    static func requestAuthorization() async {
        // Time Sensitive is the entitlement plus interruptionLevel. The prompt is alert, sound, and badge.
        _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge])
    }

    static func authorizationStatus() async -> UNAuthorizationStatus {
        await UNUserNotificationCenter.current().notificationSettings().authorizationStatus
    }

    /// App icon badge follows the unacked count.
    static func setBadge(_ count: Int) {
        Task { try? await UNUserNotificationCenter.current().setBadgeCount(max(0, count)) }
    }

    /// Pulls scheduled and delivered notifications with these ids, and every copy made from them
    /// ("<id>#snooze…" from Snooze, "<id>#failed" from a notification button that could not post).
    /// Used when a test alert is replaced and when the server retracts a reminder.
    static func withdraw(_ ids: [String]) {
        guard !ids.isEmpty else { return }
        let center = UNUserNotificationCenter.current()
        center.removePendingNotificationRequests(withIdentifiers: ids)
        center.removeDeliveredNotifications(withIdentifiers: ids)
        let prefixes = ids.map { "\($0)#" }
        func isCopy(_ identifier: String) -> Bool {
            prefixes.contains { identifier.hasPrefix($0) }
        }
        center.getPendingNotificationRequests { requests in
            let copies = requests.map(\.identifier).filter(isCopy)
            if !copies.isEmpty { center.removePendingNotificationRequests(withIdentifiers: copies) }
        }
        center.getDeliveredNotifications { notifications in
            let copies = notifications.map(\.request.identifier).filter(isCopy)
            if !copies.isEmpty { center.removeDeliveredNotifications(withIdentifiers: copies) }
        }
    }

    /// Lets these ids be scheduled again. A retracted alert that the server sends again is new again.
    static func forgetDelivered(_ ids: [String]) {
        guard !ids.isEmpty else { return }
        let drop = Set(ids)
        let kept = deliveredIDs().filter { !drop.contains($0) }
        UserDefaults.standard.set(kept, forKey: deliveredKey)
    }

    static func deliver(_ alerts: [OrbitalAlert], baseURL: URL, authToken: String?) async {
        let center = UNUserNotificationCenter.current()
        var delivered = deliveredIDs()
        // An alert the hub already pushed through Apple is on screen: don't show it a second time.
        let pushed = Set(await center.deliveredNotifications().compactMap { pushAlertID($0.request.content.userInfo) })
        for alert in alerts {
            if delivered.contains(alert.id) { continue }
            if pushed.contains(alert.id) {
                delivered.append(alert.id)
                continue
            }
            let content = await content(for: alert, baseURL: baseURL, authToken: authToken, center: center)
            let trigger = trigger(for: alert)
            let request = UNNotificationRequest(identifier: alert.id, content: content, trigger: trigger)
            center.removePendingNotificationRequests(withIdentifiers: [alert.id])
            do {
                try await center.add(request)
                delivered.append(alert.id)
            } catch {
                continue
            }
        }
        if delivered.count > 500 {
            delivered = Array(delivered.suffix(500))
        }
        UserDefaults.standard.set(delivered, forKey: deliveredKey)
    }

    /// The alert id an Apple push carries in its `orbital` dictionary, if this is one.
    static func pushAlertID(_ userInfo: [AnyHashable: Any]) -> String? {
        guard let orbital = userInfo["orbital"] as? [String: Any] else { return nil }
        return orbital["alert_id"] as? String ?? orbital["id"] as? String
    }

    /// Remembers that an alert was shown by an Apple push, so the local path does not repeat it.
    static func markPushed(_ userInfo: [AnyHashable: Any]) {
        guard let id = pushAlertID(userInfo) else { return }
        var delivered = deliveredIDs()
        if !delivered.contains(id) {
            delivered.append(id)
            UserDefaults.standard.set(Array(delivered.suffix(500)), forKey: deliveredKey)
        }
    }

    /// An APNs device token as the hex string the hub stores.
    static func hex(_ token: Data) -> String {
        token.map { String(format: "%02x", $0) }.joined()
    }

    private static func deliveredIDs() -> [String] {
        UserDefaults.standard.stringArray(forKey: deliveredKey) ?? []
    }

    private static func trigger(for alert: OrbitalAlert) -> UNNotificationTrigger {
        if let repeats = alert.repeats, alert.deliverAt == nil {
            var components = DateComponents()
            components.hour = repeats.hour
            components.minute = repeats.minute
            components.second = repeats.second ?? 0
            if let weekday = repeats.weekday {
                components.weekday = weekday
            }
            return UNCalendarNotificationTrigger(dateMatching: components, repeats: true)
        }
        if let deliverAt = alert.deliverAt, let date = parseDate(deliverAt), date.timeIntervalSinceNow > 2 {
            return UNTimeIntervalNotificationTrigger(timeInterval: date.timeIntervalSinceNow, repeats: false)
        }
        return UNTimeIntervalNotificationTrigger(timeInterval: 1, repeats: false)
    }

    private static func content(
        for alert: OrbitalAlert,
        baseURL: URL,
        authToken: String?,
        center: UNUserNotificationCenter
    ) async -> UNMutableNotificationContent {
        let content = UNMutableNotificationContent()
        content.title = alert.title
        if let subtitle = alert.subtitle, !subtitle.isEmpty {
            content.subtitle = subtitle
        }
        content.body = alert.body
        switch alert.sound {
        case "none":
            content.sound = nil
        case nil, "default":
            content.sound = .default
        case let name?:
            content.sound = UNNotificationSound(named: UNNotificationSoundName(name))
        }
        if let level = alert.interruptionLevel {
            content.interruptionLevel = interruption(level)
        } else {
            content.interruptionLevel = alert.kind == "reminder" ? .active : .timeSensitive
        }
        if let score = alert.relevanceScore {
            content.relevanceScore = score
        }
        content.threadIdentifier = alert.threadId ?? alert.sectionId ?? "orbital"
        if let badge = alert.badge {
            content.badge = NSNumber(value: badge)
        }
        if let target = alert.targetContentId {
            content.targetContentIdentifier = target
        }
        if let argument = alert.summaryArgument {
            content.summaryArgument = argument
        }
        if let count = alert.summaryArgumentCount {
            content.summaryArgumentCount = count
        }
        content.categoryIdentifier = categoryIdentifier(for: alert, center: center)
        var info: [String: Any] = ["alert_id": alert.id]
        if let launch = alert.launchURL { info["launch_url"] = launch }
        if let section = alert.sectionId { info["section_id"] = section }
        if let node = alert.nodeId { info["node_id"] = node }
        if let tab = alert.tabId { info["tab_id"] = tab }
        if let extra = alert.userInfo {
            for (key, value) in extra {
                switch value {
                case .string(let string): info[key] = string
                case .number(let number): info[key] = number
                case .bool(let flag): info[key] = flag
                }
            }
        }
        content.userInfo = info
        if let attachment = alert.attachments?.first,
           let file = await downloadAttachment(attachment, alertID: alert.id, baseURL: baseURL, authToken: authToken),
           let attached = try? UNNotificationAttachment(identifier: attachment.stableId, url: file) {
            content.attachments = [attached]
        }
        return content
    }

    private static func categoryIdentifier(for alert: OrbitalAlert, center: UNUserNotificationCenter) -> String {
        let actions = alert.actions ?? []
        if !actions.isEmpty {
            let raw = alert.categoryId ?? alert.id
            let safe = raw.map { character -> Character in
                character.isLetter || character.isNumber ? character : "-"
            }
            let identifier = "orbital.custom.\(String(safe))"
            let built: [UNNotificationAction] = actions.prefix(4).map { action in
                var options: UNNotificationActionOptions = []
                if action.foreground != false { options.insert(.foreground) }
                if action.style == "destructive" { options.insert(.destructive) }
                if let input = action.textInput {
                    return UNTextInputNotificationAction(
                        identifier: action.id,
                        title: action.title,
                        options: options,
                        textInputButtonTitle: input.buttonTitle,
                        textInputPlaceholder: input.placeholder ?? ""
                    )
                }
                return UNNotificationAction(identifier: action.id, title: action.title, options: options)
            }
            let category = UNNotificationCategory(
                identifier: identifier,
                actions: built,
                intentIdentifiers: [],
                hiddenPreviewsBodyPlaceholder: nil,
                categorySummaryFormat: "%@",
                options: []
            )
            extraCategories[identifier] = category
            center.setNotificationCategories(Set(builtinCategories() + Array(extraCategories.values)))
            return identifier
        }
        if let category = alert.categoryId, !category.isEmpty {
            return category
        }
        return alert.kind == "reminder" ? "orbital.reminder" : "orbital.default"
    }

    private static func builtinCategories() -> [UNNotificationCategory] {
        let open = UNNotificationAction(identifier: "open", title: "Open", options: [.foreground])
        let dismiss = UNNotificationAction(identifier: "dismiss", title: "Dismiss", options: [.destructive])
        let snooze = UNNotificationAction(identifier: "snooze", title: "Snooze 1 hour", options: [])
        let reply = UNTextInputNotificationAction(
            identifier: "reply",
            title: "Reply",
            options: [.foreground],
            textInputButtonTitle: "Send",
            textInputPlaceholder: "Note"
        )
        let summary = "%@"
        return [
            UNNotificationCategory(identifier: "orbital.default", actions: [open, dismiss], intentIdentifiers: [], hiddenPreviewsBodyPlaceholder: nil, categorySummaryFormat: summary, options: []),
            UNNotificationCategory(identifier: "orbital.reminder", actions: [open, snooze, dismiss], intentIdentifiers: [], hiddenPreviewsBodyPlaceholder: nil, categorySummaryFormat: summary, options: []),
            UNNotificationCategory(identifier: "orbital.input", actions: [reply, dismiss], intentIdentifiers: [], hiddenPreviewsBodyPlaceholder: nil, categorySummaryFormat: summary, options: []),
        ]
    }

    private static func registerCategories(on center: UNUserNotificationCenter) {
        center.setNotificationCategories(Set(builtinCategories() + Array(extraCategories.values)))
    }

    private static func interruption(_ level: String) -> UNNotificationInterruptionLevel {
        switch level {
        case "passive": return .passive
        case "active": return .active
        case "critical": return .critical
        default: return .timeSensitive
        }
    }

    private static func parseDate(_ value: String) -> Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let date = formatter.date(from: value) { return date }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: value)
    }

    private static func downloadAttachment(
        _ attachment: AlertAttachment,
        alertID: String,
        baseURL: URL,
        authToken: String?
    ) async -> URL? {
        guard let remote = resolve(attachment.url, baseURL: baseURL) else { return nil }
        if remote.isFileURL {
            return copyOwnedFile(remote, alertID: alertID)
        }
        var request = URLRequest(url: remote)
        if let authToken, !authToken.isEmpty, remote.host == baseURL.host || attachment.url.hasPrefix("/") {
            request.setValue("Bearer \(authToken)", forHTTPHeaderField: "Authorization")
        }
        guard let (data, response) = try? await URLSession.shared.data(for: request),
              let http = response as? HTTPURLResponse,
              (200 ... 299).contains(http.statusCode),
              data.count <= 8 * 1024 * 1024
        else { return nil }
        let ext = fileExtension(for: attachment, response: http, url: remote)
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("orbital-alert-\(alertID)", isDirectory: true)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let file = directory.appendingPathComponent("attachment.\(ext)")
        do {
            try data.write(to: file)
            return file
        } catch {
            return nil
        }
    }

    /// A file this app wrote for the Alerts test card. iOS moves an attached file, so attach a copy.
    private static func copyOwnedFile(_ source: URL, alertID: String) -> URL? {
        guard OrbitalTestAlert.isOwnedFile(source), let local = OrbitalTestAlert.localFile(for: source) else { return nil }
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("orbital-alert-\(alertID)", isDirectory: true)
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let ext = local.pathExtension.isEmpty ? "png" : local.pathExtension.lowercased()
        let file = directory.appendingPathComponent("attachment.\(ext)")
        try? FileManager.default.removeItem(at: file)
        do {
            try FileManager.default.copyItem(at: local, to: file)
            return file
        } catch {
            return nil
        }
    }

    private static func resolve(_ urlString: String, baseURL: URL) -> URL? {
        if urlString.hasPrefix("/") {
            return URL(string: urlString, relativeTo: baseURL)?.absoluteURL
        }
        return URL(string: urlString)
    }

    private static func fileExtension(for attachment: AlertAttachment, response: HTTPURLResponse, url: URL) -> String {
        let pathExt = url.pathExtension.lowercased()
        if ["jpg", "jpeg", "png", "gif", "webp", "mp4", "mp3", "wav", "aiff"].contains(pathExt) {
            return pathExt == "jpeg" ? "jpg" : pathExt
        }
        switch attachment.typeHint {
        case "audio": return "mp3"
        case "video": return "mp4"
        default: break
        }
        let mime = response.mimeType ?? ""
        switch mime {
        case "image/png": return "png"
        case "image/gif": return "gif"
        case "image/webp": return "webp"
        case "video/mp4": return "mp4"
        default: return "jpg"
        }
    }

    private static func scheduleRefresh() {
        let request = BGAppRefreshTaskRequest(identifier: taskID)
        request.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)
        try? BGTaskScheduler.shared.submit(request)
    }
}

final class OrbitalNotificationDelegate: NSObject, UNUserNotificationCenterDelegate {
    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification
    ) async -> UNNotificationPresentationOptions {
        let info = notification.request.content.userInfo
        if OrbitalNotifications.pushAlertID(info) != nil {
            // An Apple push while the app is open: the sync it triggers shows the in-app banner, so the
            // system one would be a second copy. Keep it in Notification Center only.
            OrbitalNotifications.markPushed(info)
            NotificationCenter.default.post(name: .orbitalRefresh, object: nil)
            return [.list, .badge]
        }
        return [.banner, .sound, .badge, .list]
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse
    ) async {
        var userInfo = response.notification.request.content.userInfo
        // An Apple push carries its fields in `orbital`: alert_id, section_id, node_id, tab_id, launch_url.
        if let orbital = userInfo["orbital"] as? [String: Any] {
            OrbitalNotifications.markPushed(userInfo)
            for (key, value) in orbital where userInfo[key] == nil { userInfo[key] = value }
            if userInfo["alert_id"] == nil, let id = orbital["id"] { userInfo["alert_id"] = id }
        }
        var payload: [String: Any] = [:]
        if let alertID = userInfo["alert_id"] as? String {
            payload["alert_id"] = alertID
        }
        if let launch = userInfo["launch_url"] as? String {
            payload["launch_url"] = launch
        } else if let section = userInfo["section_id"] as? String {
            var components = URLComponents()
            components.scheme = OrbitalBrand.urlScheme
            components.host = "open"
            var items = [URLQueryItem(name: "section", value: section)]
            if let node = userInfo["node_id"] as? String { items.append(URLQueryItem(name: "node", value: node)) }
            if let tab = userInfo["tab_id"] as? String { items.append(URLQueryItem(name: "tab", value: tab)) }
            components.queryItems = items
            payload["launch_url"] = components.url?.absoluteString
        } else if userInfo["alert_id"] != nil {
            payload["launch_url"] = "\(OrbitalBrand.urlScheme)://alerts"
        }
        if response.actionIdentifier.hasPrefix("act.") {
            // A button that writes back. Background buttons get no app UI, so post it here
            // and finish before returning; iOS keeps the app alive until this handler completes.
            let type = String(response.actionIdentifier.dropFirst("act.".count))
            let outcome = await OrbitalNotificationActions.perform(
                type: type,
                userInfo: userInfo,
                alertID: payload["alert_id"] as? String,
                content: response.notification.request.content
            )
            payload["action"] = response.actionIdentifier
            payload["action_type"] = type
            payload["action_value"] = outcome.value
            if let error = outcome.error {
                payload["action_error"] = error
            }
            NotificationCenter.default.post(name: .orbitalAlertAction, object: nil, userInfo: payload)
            return
        }
        switch response.actionIdentifier {
        case UNNotificationDismissActionIdentifier, "dismiss":
            payload["action"] = "dismiss"
        case "snooze":
            payload["action"] = "snooze"
            let content = response.notification.request.content.mutableCopy() as? UNMutableNotificationContent
                ?? UNMutableNotificationContent()
            let trigger = UNTimeIntervalNotificationTrigger(timeInterval: 3600, repeats: false)
            let stamp = Int(Date().timeIntervalSince1970)
            let identifier = "\(payload["alert_id"] as? String ?? "alert")#snooze\(stamp)"
            let request = UNNotificationRequest(identifier: identifier, content: content, trigger: trigger)
            try? await center.add(request)
        default:
            payload["action"] = response.actionIdentifier == UNNotificationDefaultActionIdentifier
                ? "open"
                : response.actionIdentifier
            if let textResponse = response as? UNTextInputNotificationResponse {
                payload["response_text"] = textResponse.userText
            }
        }
        NotificationCenter.default.post(name: .orbitalAlertAction, object: nil, userInfo: payload)
    }
}
