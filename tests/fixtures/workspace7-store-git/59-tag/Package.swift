// swift-tools-version:5.9
import PackageDescription
let package=Package(name:"PureApp",dependencies:[.package(url:"file:///nix/store/krp7vr3lh5yv37z0s2lk8nmwjqf3ksis-purifier-alpha-git-fixture/alpha.git",exact:"1.0.0")],targets:[.executableTarget(name:"PureApp",dependencies:[.product(name:"Alpha",package:"alpha")])])
