// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation
import SwiftUI

/// UI languages (DESIGN §8.2: German "Sie" is the source, English from the same catalog).
public enum AppLanguage: String, CaseIterable, Codable, Sendable {
    case de, en

    /// macOS preference: German if the first preferred language is German, else English.
    public static func systemDefault(preferred: [String] = Locale.preferredLanguages) -> AppLanguage {
        (preferred.first ?? "en").lowercased().hasPrefix("de") ? .de : .en
    }

    /// The user's own locale when it speaks this language (de_CH, de_AT, en_US, …), else a neutral default.
    public var locale: Locale {
        if Locale.current.language.languageCode?.identifier == rawValue { return Locale.current }
        return Locale(identifier: self == .de ? "de_DE" : "en_GB")
    }
}

/// Looks up `Localizable.xcstrings` entries for an explicit language (the app can switch language at runtime on S00)
/// and substitutes `{App}` and `{placeholder}` tokens. Placeholders are literal `{name}` tokens in both languages
/// (ENGINE-PROTOCOL §5); there are no printf formats in the catalog.
public struct Localizer: Equatable, Sendable {
    public let language: AppLanguage

    public init(_ language: AppLanguage) { self.language = language }

    private static let missing = "\u{241F}missing\u{241F}"

    private static func bundle(for lang: AppLanguage) -> Bundle {
        if let path = Bundle.main.path(forResource: lang.rawValue, ofType: "lproj"), let b = Bundle(path: path) {
            return b
        }
        return Bundle.main
    }

    private static let bundles: [AppLanguage: Bundle] = {
        var out: [AppLanguage: Bundle] = [:]
        for l in AppLanguage.allCases { out[l] = bundle(for: l) }
        return out
    }()

    /// Raw catalog value or nil when the key does not exist in this language.
    public func raw(_ key: String) -> String? {
        let b = Localizer.bundles[language] ?? Bundle.main
        let v = b.localizedString(forKey: key, value: Localizer.missing, table: "Localizable")
        return v == Localizer.missing ? nil : v
    }

    public func has(_ key: String) -> Bool { raw(key) != nil }

    /// Localized text with `{App}` = product name and `{key}` = `args[key]`. A missing key returns the key itself
    /// (StringsCoverageTests make sure that never ships).
    public func t(_ key: String, _ args: [String: String] = [:]) -> String {
        let base = raw(key) ?? Localizer(.de).raw(key) ?? key
        return Localizer.substitute(base, args)
    }

    public static func substitute(_ text: String, _ args: [String: String]) -> String {
        var out = text.replacingOccurrences(of: "{App}", with: AppInfo.productName)
        for (k, v) in args { out = out.replacingOccurrences(of: "{\(k)}", with: v) }
        return out
    }

    /// Markdown-capable text (`**bold**` in a few DESIGN texts).
    public func md(_ key: String, _ args: [String: String] = [:]) -> AttributedString {
        let s = t(key, args)
        return (try? AttributedString(markdown: s, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace)))
            ?? AttributedString(s)
    }
}

private struct LocalizerKey: EnvironmentKey {
    static let defaultValue = Localizer(.de)
}

extension EnvironmentValues {
    public var loc: Localizer {
        get { self[LocalizerKey.self] }
        set { self[LocalizerKey.self] = newValue }
    }
}
