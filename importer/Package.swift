// swift-tools-version:5.9
// SPDX-License-Identifier: AGPL-3.0-or-later
// threema-import: Android normalized.sqlite -> Threema iOS Core Data store (model V56). arm64, macOS 14+.
//
//   swift build -c release --arch arm64 --product threema-import     (packaging/build-importer.sh)
//   python3 importer/Tests/run_checks.py                               (the importer check suite, CI job "importer")
//
// The test tools (make-empty-store, seed-store, safe-seed) build synthetic input stores for the checks; they are
// never part of the app bundle.
import PackageDescription

let frameworks: [LinkerSetting] = [
    .linkedFramework("CoreData"), .linkedFramework("AVFoundation"), .linkedFramework("ImageIO"),
    .linkedFramework("CoreGraphics"), .linkedLibrary("sqlite3"),
]

let package = Package(
    name: "ThreemaImport",
    platforms: [.macOS(.v14)],
    products: [
        .executable(name: "threema-import", targets: ["ThreemaImport"]),
        .executable(name: "make-empty-store", targets: ["MakeEmptyStore"]),
        .executable(name: "seed-store", targets: ["SeedStore"]),
        .executable(name: "safe-seed", targets: ["SafeSeed"]),
    ],
    targets: [
        .executableTarget(name: "ThreemaImport", path: "Sources/ThreemaImport", linkerSettings: frameworks),
        .executableTarget(name: "MakeEmptyStore", path: "Tests/Tools/MakeEmptyStore",
                          linkerSettings: [.linkedFramework("CoreData")]),
        .executableTarget(name: "SeedStore", path: "Tests/Tools/SeedStore",
                          linkerSettings: [.linkedFramework("CoreData")]),
        .executableTarget(name: "SafeSeed", path: "Tests/Tools/SafeSeed",
                          linkerSettings: [.linkedFramework("CoreData")]),
    ],
    swiftLanguageVersions: [.v5]
)
