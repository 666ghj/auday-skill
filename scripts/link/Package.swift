// swift-tools-version: 5.9
import PackageDescription
let package = Package(name: "AudayLink", platforms: [.macOS(.v13), .iOS(.v16)],
    products: [.library(name: "AudayLink", targets: ["AudayLink"]), .executable(name: "auday-link", targets: ["auday-link"])],
    targets: [.target(name: "AudayLink"), .executableTarget(name: "auday-link", dependencies: ["AudayLink"]),
              .testTarget(name: "AudayLinkTests", dependencies: ["AudayLink"])])
