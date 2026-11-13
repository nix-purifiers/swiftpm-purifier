// swift-tools-version:4.2
import PackageDescription
let package = Package(name: "Root", products: [], dependencies: [.package(url: "https://example.invalid/Alpha.git", from: "1.0.0"), .package(path: "LocalPkg")], targets: [])
