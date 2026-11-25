// swift-tools-version:6.0
import PackageDescription
let package=Package(name:"PureApp",dependencies:[.package(url:"file:///nix/store/krp7vr3lh5yv37z0s2lk8nmwjqf3ksis-purifier-alpha-git-fixture/alpha.git",revision:"6dfb4a7fdbd761e179d886f1d67f23ab89d64dad")],targets:[.executableTarget(name:"PureApp",dependencies:[.product(name:"Alpha",package:"alpha")])])
