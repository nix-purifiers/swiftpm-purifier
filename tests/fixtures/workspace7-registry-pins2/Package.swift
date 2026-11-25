// swift-tools-version:5.9
import PackageDescription
let package = Package(name:"consumer", products:[.executable(name:"consumer",targets:["consumer"])], dependencies:[.package(id:"nix.greeter",from:"1.0.0")],targets:[.executableTarget(name:"consumer",dependencies:[.product(name:"Greeter",package:"nix.greeter")])])
