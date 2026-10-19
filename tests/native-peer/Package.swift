// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AudaySyntheticPeer",
    platforms: [.macOS(.v13)],
    products: [.executable(name: "auday-test-peer", targets: ["auday-test-peer"])],
    dependencies: [.package(path: "../../scripts/link")],
    targets: [
        .target(name: "FixtureLibrary", dependencies: [.product(name: "AudayLink", package: "link")]),
        .executableTarget(name: "auday-test-peer", dependencies: ["FixtureLibrary", .product(name: "AudayLink", package: "link")]),
        .testTarget(name: "FixtureLibraryTests", dependencies: ["FixtureLibrary"]),
    ]
)
