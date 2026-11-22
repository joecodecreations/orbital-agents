#if DEBUG
import SwiftUI
import UIKit

/// DEBUG-only: render the live voice call bar and its settings on the device into PNGs under
/// Documents/evidence-live-voice, for agent evidence (copied off with `xcrun devicectl device copy from`).
/// Started with ORBITAL_LIVE_VOICE_EVIDENCE=1 in the launch environment. The call states are set by
/// hand, since a real call needs the hub's OpenAI key; the settings card shows what the hub reports.
enum LiveVoiceEvidence {
    @MainActor
    static func save(desk: DeskState) async {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        let out = docs.appendingPathComponent("evidence-live-voice", isDirectory: true)
        try? FileManager.default.removeItem(at: out)
        try? FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)

        await desk.refreshRealtime()
        let hubInfo = desk.realtime
        let bar = RealtimeVoiceSession()
        let states: [(String, RealtimeVoiceSession.Phase, String, String, String?, Bool)] = [
            ("1-listening", .listening, "", "", nil, false),
            ("2-hearing", .hearing, "Look in the client folder and tell me what the latest meeting notes say", "", nil, false),
            ("3-checking", .working("Searching your files…"), "", "Checking on that, one sec.", nil, false),
            ("4-speaking", .speaking, "", "The newest notes are from Tuesday: the launch moved a week because the payment page failed review.", nil, false),
            ("5-agent-update", .listening, "", "", "Store check answered: the listing is live and the build is in review.", false),
            ("6-muted", .listening, "", "", nil, true),
        ]
        for (name, phase, user, reply, notice, muted) in states {
            bar.debugShow(phase, user: user, reply: reply, notice: notice, muted: muted)
            let view = LiveVoiceBar(live: bar)
                .environmentObject(desk)
                .padding(12)
                .frame(width: 390)
                .background(OrbitalTheme.backgroundDeep)
                .preferredColorScheme(.dark)
            write(view, size: CGSize(width: 390, height: 190), to: out.appendingPathComponent("call-bar-\(name).png"))
        }

        let settings = { () -> AnyView in
            AnyView(LiveVoiceSettingsSection()
                .environmentObject(desk)
                .padding(16)
                .frame(width: 390, alignment: .topLeading)
                .background(OrbitalTheme.surface)
                .preferredColorScheme(.dark))
        }
        write(settings(), size: CGSize(width: 390, height: 360), to: out.appendingPathComponent("settings-from-hub.png"))
        desk.debugSetRealtime(DeskRealtimeInfo(available: true, engine: "realtime", model: "gpt-realtime-2.1", voice: "marin", eagerness: "auto", note: nil))
        write(settings(), size: CGSize(width: 390, height: 360), to: out.appendingPathComponent("settings-with-key.png"))
        desk.debugSetRealtime(hubInfo)
        try? "hub available=\(hubInfo?.available ?? false)".write(to: out.appendingPathComponent("done.txt"), atomically: true, encoding: .utf8)
    }

    @MainActor
    private static func write<V: View>(_ view: V, size: CGSize, to url: URL) {
        let host = UIHostingController(rootView: view.fixedSize(horizontal: false, vertical: true))
        // Offscreen there is no window: drop the safe-area padding the controller would add.
        host.safeAreaRegions = []
        host.view.backgroundColor = UIColor(OrbitalTheme.backgroundDeep)
        host.overrideUserInterfaceStyle = .dark
        let fitted = host.sizeThatFits(in: CGSize(width: size.width, height: 2000))
        let final = CGSize(width: size.width, height: max(60, min(1200, fitted.height)))
        host.view.bounds = CGRect(origin: .zero, size: final)
        host.view.layoutIfNeeded()
        let format = UIGraphicsImageRendererFormat()
        format.scale = 3
        format.opaque = true
        let image = UIGraphicsImageRenderer(size: final, format: format).image { _ in
            host.view.drawHierarchy(in: host.view.bounds, afterScreenUpdates: true)
        }
        try? image.pngData()?.write(to: url)
    }
}
#endif
