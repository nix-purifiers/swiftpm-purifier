// swift-tools-version:5.9
import PackageDescription
let package=Package(name:"PureApp",dependencies:[.package(url:"https://compat.invalid/alpha.git",branch:"master")],targets:[.executableTarget(name:"PureApp",dependencies:[.product(name:"Alpha",package:"alpha")])])
