// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "CPDFGear",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "CPDFGear",
            path: "Sources/CPDFGear",
            resources: [.copy("Resources/AppIcon.png")]
        ),
        .executableTarget(
            name: "ocr_cli",
            path: "Sources/ocr_cli"
        )
    ]
)
