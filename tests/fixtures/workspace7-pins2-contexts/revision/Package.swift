// swift-tools-version:5.9
import PackageDescription
let package=Package(name:"PureApp",dependencies:[.package(url:"https://compat.invalid/alpha.git",revision:"f667da868c01b0fb60d88ae8858b2d2c0f5747fc")],targets:[.executableTarget(name:"PureApp",dependencies:[.product(name:"Alpha",package:"alpha")])])
