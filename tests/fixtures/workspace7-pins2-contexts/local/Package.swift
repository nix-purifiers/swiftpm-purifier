// swift-tools-version:5.9
import PackageDescription
let package=Package(name:"PureApp",dependencies:[.package(url:"https://compat.invalid/alpha.git",exact:"1.0.0"),.package(path:"Local")],targets:[.executableTarget(name:"PureApp",dependencies:[.product(name:"Alpha",package:"alpha"),.product(name:"Extras",package:"Local")])])
