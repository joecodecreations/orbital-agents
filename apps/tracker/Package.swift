// swift-tools-version:6.0
import PackageDescription

// Orbital Agents - Agent Satellite Tracker: the floating macOS panel (OrbitalTracker),
// the agent CLI (orbital-track) and the code they share (OrbitalTrackerCore).
let package = Package(
    name: "OrbitalTracker",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "OrbitalTracker", targets: ["OrbitalTracker"]),
        .executable(name: "orbital-track", targets: ["orbital-track"]),
        .library(name: "OrbitalTrackerCore", targets: ["OrbitalTrackerCore"]),
    ],
    targets: [
        .target(name: "OrbitalTrackerCore"),
        .executableTarget(name: "OrbitalTracker", dependencies: ["OrbitalTrackerCore"]),
        .executableTarget(name: "orbital-track", dependencies: ["OrbitalTrackerCore"]),
        .testTarget(name: "OrbitalTrackerCoreTests", dependencies: ["OrbitalTrackerCore"]),
    ],
    swiftLanguageModes: [.v5]
)
