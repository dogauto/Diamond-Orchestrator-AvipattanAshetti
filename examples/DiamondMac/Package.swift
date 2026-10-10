// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "DiamondMac",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "DiamondMac", targets: ["DiamondMac"])
    ],
    targets: [
        .executableTarget(name: "DiamondMac")
    ]
)
