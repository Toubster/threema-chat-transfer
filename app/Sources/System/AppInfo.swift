// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation

/// Product name and versions. The name lives here only; all texts use `{App}` (DESIGN §8.2).
public enum AppInfo {
    /// Display name (unofficial; "for Threema" only describes compatibility, see TRADEMARKS.md).
    public static let productName = "Chat Transfer for Threema"
    /// File-name form of the product name (diagnostic report, DMG, repository).
    public static let slug = "threema-chat-transfer"
    /// Unified-logging subsystem: the bundle identifier of the running app.
    public static var logSubsystem: String { Bundle.main.bundleIdentifier ?? slug }

    /// `engine_version` = app version = importer version (DESIGN §5.7).
    public static var version: String {
        (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "0.0.0"
    }

    /// The version the engine must report in `hello.engine_version` (DESIGN §5.3/§5.7). `CFBundleShortVersionString`
    /// carries only the numeric part, so packaging writes the full version (incl. `-dev`) as `TMEngineVersion`.
    public static var engineVersion: String {
        if let v = Bundle.main.object(forInfoDictionaryKey: "TMEngineVersion") as? String, !v.isEmpty, !v.contains("$(") {
            return v
        }
        return version
    }

    public static var build: String {
        (Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String) ?? "0"
    }

    private static func url(_ key: String) -> URL? {
        (Bundle.main.object(forInfoDictionaryKey: key) as? String).flatMap(URL.init(string:))
    }

    /// "Neue Versionen ansehen" only opens the release page in the browser; the app itself never goes online.
    public static var releasesURL: URL? { url("TMReleasesURL") }
    public static var sourceURL: URL? { url("TMSourceURL") }
    public static var guideURL: URL? { url("TMGuideURL") }

    /// Build date of the running binary, for the "possibly a newer version" hint after 90 days (DESIGN §6.2).
    public static var buildDate: Date? {
        guard let exe = Bundle.main.executableURL,
              let attrs = try? FileManager.default.attributesOfItem(atPath: exe.path) else { return nil }
        return attrs[.modificationDate] as? Date
    }
}

/// Window sizes. The smallest size is what the snapshot/size checks use for long German texts.
public enum WindowMetrics {
    public static let minWidth: CGFloat = 880
    public static let minHeight: CGFloat = 620
    public static let sidebarWidth: CGFloat = 200
    public static let contentPadding: CGFloat = 32
    /// Width available to screen content at the smallest window size.
    public static var minContentWidth: CGFloat { minWidth - sidebarWidth - 1 - 2 * contentPadding }
}
