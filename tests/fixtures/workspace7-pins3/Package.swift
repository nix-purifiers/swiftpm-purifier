// swift-tools-version:6.0
import PackageDescription
let package = Package(name:"PureApp",dependencies:[.package(url:"http://127.0.0.1:43467/alpha.git",exact:"1.0.0")],targets:[.executableTarget(name:"PureApp",dependencies:[.product(name:"Alpha",package:"alpha")])])
