// swift-tools-version: 6.0
import PackageDescription
let package = Package(
    name: "native-proof",
    products: [.library(name: "Proof", type: .dynamic, targets: ["Proof"]),
               .library(name: "BaseStatic", type: .static, targets: ["Base"])],
    targets: [.target(name: "Base"), .target(name: "Proof", dependencies: ["Base"])]
)
