// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation

/// The phases in the sidebar (DESIGN §8.1). Raw values = `session.v1` `sidebar_phase`.
public enum SidebarPhase: String, CaseIterable, Codable, Sendable {
    case start
    case android
    case prepareIphone = "prepare_iphone"
    case transfer
    case control
    case done

    public var titleKey: String { "sidebar.\(rawValue)" }
}

/// Error screens of DESIGN §8.4. Raw value = screen id used in `codes.v1.json` and `session.json`.
public enum FailureScreen: String, CaseIterable, Sendable {
    case hostUnsupported = "F-HOST-UNSUPPORTED"
    case hostSpace = "F-HOST-SPACE"
    case hostApfs = "F-HOST-APFS"
    case hostPower = "F-HOST-POWER"
    case androidFormat = "F-ANDROID-FORMAT"
    case importDup = "F-IMPORT-DUP"
    case devMulti = "F-DEV-MULTI"
    case devOther = "F-DEV-OTHER"
    case devManaged = "F-DEV-MANAGED"
    case devBattery = "F-DEV-BATTERY"
    case devDisconnected = "F-DEV-DISCONNECTED"
    case iosUnknown = "F-IOS-UNKNOWN"
    case iosChanged = "F-IOS-CHANGED"
    case threemaMissing = "F-THREEMA-MISSING"
    case threemaVariant = "F-THREEMA-VARIANT"
    case threemaSetup = "F-THREEMA-SETUP"
    case threemaRetention = "F-THREEMA-RETENTION"
    case threemaVersion = "F-THREEMA-VERSION"
    case threemaId = "F-THREEMA-ID"
    case backupFailed = "F-BACKUP-FAILED"
    case passwordWrong = "F-PW-WRONG"
    case airplane = "F-AIRPLANE"
    case dcim = "F-DCIM"
    case freshness = "F-FRESHNESS"
    case iphoneSpace = "F-IPHONE-SPACE"
    case photosLimit = "F-PHOTOS-LIMIT"
    case findMy = "F-FINDMY"
    case restoreBefore = "F-RESTORE-BEFORE"
    case restoreMid = "F-RESTORE-MID"
    case internalError = "F-INTERNAL"
}

/// Every wizard screen S00–S23 (S10a/S10b) and the error screens.
public enum Screen: Hashable, Sendable {
    case s00, s01, s02, s03, s04, s05, s06, s07, s08, s09, s10a, s10b, s11, s12, s13, s14, s15, s16, s17, s18
    case s19, s20, s21, s22, s23
    case failure(FailureScreen)

    public static let wizardScreens: [Screen] = [.s00, .s01, .s02, .s03, .s04, .s05, .s06, .s07, .s08, .s09, .s10a,
                                                 .s10b, .s11, .s12, .s13, .s14, .s15, .s16, .s17, .s18, .s19, .s20,
                                                 .s21, .s22, .s23]

    public var id: String {
        switch self {
        case .s00: return "S00"
        case .s01: return "S01"
        case .s02: return "S02"
        case .s03: return "S03"
        case .s04: return "S04"
        case .s05: return "S05"
        case .s06: return "S06"
        case .s07: return "S07"
        case .s08: return "S08"
        case .s09: return "S09"
        case .s10a: return "S10a"
        case .s10b: return "S10b"
        case .s11: return "S11"
        case .s12: return "S12"
        case .s13: return "S13"
        case .s14: return "S14"
        case .s15: return "S15"
        case .s16: return "S16"
        case .s17: return "S17"
        case .s18: return "S18"
        case .s19: return "S19"
        case .s20: return "S20"
        case .s21: return "S21"
        case .s22: return "S22"
        case .s23: return "S23"
        case .failure(let f): return f.rawValue
        }
    }

    public init?(id: String) {
        if let f = FailureScreen(rawValue: id) { self = .failure(f); return }
        guard let s = Screen.wizardScreens.first(where: { $0.id == id }) else { return nil }
        self = s
    }

    /// Position in the flow (nil for error screens, which take the order of the screen they came from).
    public var order: Int? { Screen.wizardScreens.firstIndex(of: self) }

    public var isFailure: Bool { if case .failure = self { return true }; return false }

    public var sidebarPhase: SidebarPhase? {
        switch self {
        case .s00, .s01, .s02, .s03, .s23: return .start
        case .s04, .s05, .s06: return .android
        case .s07, .s08, .s09, .s10a, .s10b: return .prepareIphone
        case .s11, .s12, .s13, .s14, .s15: return .transfer
        case .s16, .s17, .s18: return .control
        case .s21, .s22: return .control          // stopped after the check: the move is not "done"
        case .s19, .s20: return .done
        case .failure: return nil
        }
    }

    /// Localized name of the screen (DESIGN §8.3 headings), used for "Letzter Schritt: {step}".
    public var nameKey: String {
        if case .failure(let f) = self {
            return ErrorCatalog.titleKey(Screen.representativeCode(for: f))
        }
        return "screen.\(id).name"
    }

    /// One code per error screen (for the title of an F-screen without context).
    static func representativeCode(for f: FailureScreen) -> String {
        if let preferred = preferredCode[f] { return preferred.rawValue }
        return EngineCode.allCases.first { $0.info.screen == f.rawValue }?.rawValue ?? EngineCode.E_INTERNAL.rawValue
    }

    /// Screens shared by several codes: the code whose text names the screen (DESIGN §8.4).
    static let preferredCode: [FailureScreen: EngineCode] = [
        .iosUnknown: .E_IOS_UNKNOWN, .internalError: .E_INTERNAL, .androidFormat: .E_ANDROID_FORMAT_NEW,
        .hostUnsupported: .E_HOST_ARCH,
    ]

    public static func < (lhs: Screen, rhs: Screen) -> Bool { (lhs.order ?? -1) < (rhs.order ?? -1) }
}
