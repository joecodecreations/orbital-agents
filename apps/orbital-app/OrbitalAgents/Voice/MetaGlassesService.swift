import AVFoundation
import Foundation
import MediaPlayer
import UIKit

/// Meta Ray-Ban / AI glasses via system Bluetooth.
///
/// Audio: glasses as HFP mic / A2DP speakers (`AVAudioSession`).
/// Temple tap: Meta does not expose a remappable capture button. While we own
/// Now Playing, a capacitive temple tap arrives as AVRCP play/pause.
///
/// You start the back-and-forth with the on-screen button, then lock the
/// phone. After that, a temple tap toggles your mic. The app keeps Now
/// Playing for the life of that conversation, including on the lock screen,
/// so the tap still arrives and the desk's voice still plays. A tap while the
/// phone is locked and conversation is off does not start one. Fully quitting
/// the app gives the tap back.
@MainActor
final class MetaGlassesService: ObservableObject {
    @Published var bluetoothLabel: String = "Checking…"
    @Published var isGlassesAudioConnected = false
    @Published var isCarPlayConnected = false
    @Published var activeOutputName: String = "—"
    @Published var remoteControlsEnabled = false
    @Published var lastMessage: String?

    /// Temple tap / headset play-pause. Conversation session owns pause/resume.
    var onTempleToggle: (() -> Void)?

    private var routeObserver: NSObjectProtocol?
    private var ttsPlayObserver: NSObjectProtocol?
    private var ttsEndObserver: NSObjectProtocol?
    private var keepAlivePlayer: AVAudioPlayer?
    private var remoteTargetsInstalled = false
    private var keepAliveHeldForTTS = false

    init() {
        refreshBluetoothStatus()
        routeObserver = NotificationCenter.default.addObserver(
            forName: AVAudioSession.routeChangeNotification,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.refreshBluetoothStatus() }
        }
        ttsPlayObserver = NotificationCenter.default.addObserver(
            forName: .agentTTSWillPlay,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.pauseKeepAliveForTTS() }
        }
        ttsEndObserver = NotificationCenter.default.addObserver(
            forName: .agentTTSDidFinish,
            object: nil,
            queue: .main
        ) { [weak self] _ in
            Task { @MainActor in self?.resumeKeepAliveAfterTTS() }
        }
    }

    deinit {
        if let routeObserver { NotificationCenter.default.removeObserver(routeObserver) }
        if let ttsPlayObserver { NotificationCenter.default.removeObserver(ttsPlayObserver) }
        if let ttsEndObserver { NotificationCenter.default.removeObserver(ttsEndObserver) }
    }

    func bootstrap() {
        refreshBluetoothStatus()
    }

    func refreshBluetoothStatus() {
        let session = AVAudioSession.sharedInstance()
        let outs = session.currentRoute.outputs
        let ins = session.currentRoute.inputs

        isGlassesAudioConnected = AudioCaptureService.glassesOnCurrentRoute()
            || AudioCaptureService.glassesAvailable()
        isCarPlayConnected = AudioCaptureService.carPlayOutputConnected()
        activeOutputName = outs.first?.portName ?? "—"

        let outNames = outs.map(\.portName).joined(separator: ", ")
        let inNames = ins.map(\.portName).joined(separator: ", ")
        bluetoothLabel = "In: \(inNames.isEmpty ? "—" : inNames) · Out: \(outNames.isEmpty ? "—" : outNames)"
    }

    /// Become the Now Playing app so a glasses temple tap reaches us.
    /// `idleSession` switches to playback (not a call) when nobody is listening yet.
    func enableRemoteControls(idleSession: Bool = false) {
        installRemoteCommandTargetsIfNeeded()
        UIApplication.shared.beginReceivingRemoteControlEvents()
        remoteControlsEnabled = true
        if idleSession {
            try? AudioCaptureService.applyIdleRemoteSession()
        }
        if MPNowPlayingInfoCenter.default().nowPlayingInfo == nil {
            updateNowPlaying(live: false, micPaused: false, ttsMuted: false)
        }
        startKeepAlive()
    }

    /// Release remotes so the next temple tap belongs to another app.
    func disableRemoteControls() {
        stopKeepAlive()
        let center = MPRemoteCommandCenter.shared()
        center.playCommand.removeTarget(nil)
        center.pauseCommand.removeTarget(nil)
        center.togglePlayPauseCommand.removeTarget(nil)
        center.stopCommand.removeTarget(nil)
        remoteTargetsInstalled = false
        MPNowPlayingInfoCenter.default().nowPlayingInfo = nil
        remoteControlsEnabled = false
    }

    func updateNowPlaying(live: Bool, micPaused: Bool, ttsMuted: Bool) {
        let title: String
        if !live {
            title = "Tap the waveform to talk"
        } else if micPaused {
            title = "Mic off — tap to talk"
        } else if ttsMuted {
            title = "Listening · voice muted"
        } else {
            title = "Listening — tap to mute your mic"
        }
        // Rate stays 1 for the whole conversation, even with your mic muted.
        // A rate of 0 makes iOS treat the session as paused and suspend the
        // app on the lock screen, which drops its voice and the next tap.
        let info: [String: Any] = [
            MPMediaItemPropertyTitle: title,
            MPMediaItemPropertyArtist: "\(OrbitalBrand.appName) · chime means talk, low tone means mic off",
            MPMediaItemPropertyAlbumTitle: OrbitalBrand.appName,
            MPNowPlayingInfoPropertyIsLiveStream: true,
            MPNowPlayingInfoPropertyPlaybackRate: live ? 1.0 : 0.0,
            MPNowPlayingInfoPropertyElapsedPlaybackTime: 0,
        ]
        MPNowPlayingInfoCenter.default().nowPlayingInfo = info
    }

    /// Silent loop keeps the audio session / Now Playing alive after TTS stops.
    func startKeepAlive() {
        if keepAlivePlayer?.isPlaying == true { return }
        do {
            let player = try AVAudioPlayer(data: Self.silentWavData())
            player.numberOfLoops = -1
            player.volume = 0.01
            player.prepareToPlay()
            player.play()
            keepAlivePlayer = player
        } catch {
            lastMessage = "Keep-alive audio failed: \(error.localizedDescription)"
        }
    }

    func stopKeepAlive() {
        keepAlivePlayer?.stop()
        keepAlivePlayer = nil
        keepAliveHeldForTTS = false
    }

    private func pauseKeepAliveForTTS() {
        guard keepAlivePlayer != nil else { return }
        keepAliveHeldForTTS = true
        keepAlivePlayer?.pause()
    }

    private func resumeKeepAliveAfterTTS() {
        guard keepAliveHeldForTTS else { return }
        keepAliveHeldForTTS = false
        if remoteControlsEnabled {
            if keepAlivePlayer == nil {
                startKeepAlive()
            } else {
                keepAlivePlayer?.play()
            }
        }
    }

    private func installRemoteCommandTargetsIfNeeded() {
        guard !remoteTargetsInstalled else { return }
        remoteTargetsInstalled = true
        let center = MPRemoteCommandCenter.shared()
        center.playCommand.isEnabled = true
        center.pauseCommand.isEnabled = true
        center.togglePlayPauseCommand.isEnabled = true
        center.stopCommand.isEnabled = true
        center.nextTrackCommand.isEnabled = false
        center.previousTrackCommand.isEnabled = false

        let toggle: (MPRemoteCommandEvent) -> MPRemoteCommandHandlerStatus = { [weak self] _ in
            Task { @MainActor in
                self?.onTempleToggle?()
            }
            return .success
        }
        center.playCommand.addTarget(handler: toggle)
        center.pauseCommand.addTarget(handler: toggle)
        center.togglePlayPauseCommand.addTarget(handler: toggle)
        center.stopCommand.addTarget(handler: toggle)
    }

    private static func silentWavData(seconds: Double = 1.0, sampleRate: Int = 8_000) -> Data {
        let frames = Int(seconds * Double(sampleRate))
        let dataSize = frames * 2
        var data = Data()
        func ascii(_ s: String) { data.append(contentsOf: s.utf8) }
        func u32(_ v: UInt32) {
            var le = v.littleEndian
            withUnsafeBytes(of: &le) { data.append(contentsOf: $0) }
        }
        func u16(_ v: UInt16) {
            var le = v.littleEndian
            withUnsafeBytes(of: &le) { data.append(contentsOf: $0) }
        }
        ascii("RIFF")
        u32(UInt32(36 + dataSize))
        ascii("WAVE")
        ascii("fmt ")
        u32(16)
        u16(1)
        u16(1)
        u32(UInt32(sampleRate))
        u32(UInt32(sampleRate * 2))
        u16(2)
        u16(16)
        ascii("data")
        u32(UInt32(dataSize))
        data.append(Data(count: dataSize))
        return data
    }
}
