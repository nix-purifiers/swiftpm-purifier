// swift-tools-version:4.2
import PackageDescription
let package=Package(name:"NativeApp",products:[.library(name:"AppCore",targets:["AppCore"]),.executable(name:"NativeApp",targets:["NativeApp"])],dependencies:[.package(path:"Local"),.package(url:"http://127.0.0.1:43063/Leaf.git",from:"1.0.0")],targets:[.target(name:"AppCore",dependencies:[.product(name:"LegacyLeaf",package:"Leaf"),.product(name:"LocalLeaf",package:"Local")]),.target(name:"NativeApp",dependencies:["AppCore"]),.testTarget(name:"AppCoreTests",dependencies:["AppCore"])])
