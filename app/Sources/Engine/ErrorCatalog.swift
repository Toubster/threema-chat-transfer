// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation

/// Where a code sends the wizard (DESIGN §5.5 "Screen" column, from `codes.v1.json`).
public enum CodeDestination: Equatable, Sendable {
    /// A full-screen error (F-*).
    case failure(FailureScreen)
    /// Shown inline on a wizard screen (S03 states, S05 errors, S18 "too early", S21 rollback refusals).
    case inline(Screen)
    /// Navigate to a wizard screen (S10a, S16).
    case screen(Screen)
    /// Nothing to show (R_OK, notes).
    case none
}

/// An error the UI shows: the code (raw, may be unknown), its data, and the screen it came from.
public struct ErrorContext: Equatable, Sendable {
    public let codeRaw: String
    public let data: JSONValue
    public let origin: Screen
    public let deviceModified: String

    public init(codeRaw: String, data: JSONValue = .object([:]), origin: Screen, deviceModified: String = "no") {
        self.codeRaw = codeRaw
        self.data = data
        self.origin = origin
        self.deviceModified = deviceModified
    }

    public var code: EngineCode? { EngineCode(rawValue: codeRaw) }
}

/// Code → screen, texts and actions. Unknown codes map to F-INTERNAL (DESIGN §5.7).
public enum ErrorCatalog {
    public static func destination(for codeRaw: String) -> CodeDestination {
        guard let code = EngineCode(rawValue: codeRaw) else { return .failure(.internalError) }
        let target = code.info.screen
        if target.isEmpty { return .none }
        if let f = FailureScreen(rawValue: target) { return .failure(f) }
        guard let s = Screen(id: target) else { return .failure(.internalError) }
        switch code.info.kind {
        case .warning, .note: return .inline(s)
        case .result: return s == .s16 ? .screen(.s16) : .none
        case .error:
            switch s {
            case .s03, .s05, .s18, .s21, .s00: return .inline(s)
            default: return .screen(s)
            }
        }
    }

    /// Buttons for an error screen. Unknown codes offer the diagnostic report only.
    public static func actions(for codeRaw: String) -> [CodeAction] {
        guard let code = EngineCode(rawValue: codeRaw) else { return [.diagReport] }
        return code.info.actions
    }

    public static func titleKey(_ codeRaw: String) -> String {
        EngineCode(rawValue: codeRaw)?.titleKey ?? EngineCode.E_INTERNAL.titleKey
    }

    public static func bodyKey(_ codeRaw: String) -> String {
        EngineCode(rawValue: codeRaw)?.bodyKey ?? EngineCode.E_INTERNAL.bodyKey
    }

    /// Placeholder values for a code text: every data key as-is, `x_bytes` additionally as `{x_gb}`, and enum
    /// values with localized texts (`code.<CODE>.<key>.<value>`) replaced by that text.
    public static func placeholders(codeRaw: String, data: JSONValue, loc: Localizer,
                                    extra: [String: String] = [:]) -> [String: String] {
        var out: [String: String] = [:]
        for (k, v) in data.object ?? [:] {
            if k.hasSuffix("_bytes"), let b = v.int64 {
                out[String(k.dropLast("_bytes".count)) + "_gb"] = Formatters.gigabytes(b, lang: loc.language)
            }
            if let s = v.placeholderText {
                let enumKey = "code.\(codeRaw).\(k).\(s)"
                out[k] = loc.has(enumKey) ? loc.t(enumKey) : s
                if v.double != nil, v.string == nil, let n = v.int64, !k.hasSuffix("_bytes") {
                    out[k] = Formatters.count(Int(n), lang: loc.language)
                }
            }
        }
        for (k, v) in extra { out[k] = v }
        return out
    }
}
