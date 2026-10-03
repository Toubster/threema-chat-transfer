// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// The error screens of DESIGN §8.4. Title, text and buttons come from `codes.v1.json` (via `ErrorCatalog`), so
/// the screen always matches what the engine decided. Unknown codes show F-INTERNAL with the code.
struct ErrorScreen: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc
    let failure: FailureScreen
    /// F-PW-WRONG "Passwort unbekannt? So kommen Sie weiter" (the help of S10b "Ich weiß es nicht").
    @State private var showUnknownHelp = false

    private var ctx: ErrorContext {
        store.error ?? ErrorContext(codeRaw: Screen.representativeCode(for: failure), origin: store.policyScreen)
    }

    private var knownCode: Bool { ctx.code != nil }

    /// F-FRESHNESS starts the new backup by itself (DESIGN §8.4 "automatisch S12 → S13").
    private var automatic: Bool { failure == .freshness }

    var body: some View {
        ScreenScaffold(screen: .failure(failure), title: title, symbol: symbol, tone: .danger,
                       showsCancel: failure != .restoreMid) {
            Paragraph(body(for: ctx))
            if !knownCode {
                Paragraph(loc.t("error.unknown_code"), secondary: true)
            }
            if failure == .passwordWrong {
                // the same explanation and help as S10b: which password is meant; unknown → where to look, Apple's
                // reset and "Erneut prüfen" (not after the transfer: the control backup checks the iPhone as it is)
                BackupPasswordWhat()
                DisclosureGroup(loc.t("pw_help.link"), isExpanded: $showUnknownHelp) {
                    BackupPasswordUnknownHelp(allowsReset: ctx.origin != .s18).padding(.top, 6)
                }
                .accessibilityIdentifier("disclosure.password_unknown")
            }
            if ctx.deviceModified == "unknown" {
                InfoBox(kind: .warning, loc.t("error.device_unknown")).accessibilityIdentifier("inline.device_unknown")
            }
            if stopsHere && store.showsStopReminder {
                TurnBackOnBox(titleKey: "turn_on.title", keys: store.turnBackOnKeys)
            }
            if automatic {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text(loc.t("error.auto_new_backup"))
                }
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("inline.auto_new_backup")
            }
            Text(loc.t("common.code", ["code": ctx.codeRaw]))
                .font(.caption.monospaced())
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
                .accessibilityIdentifier("label.code")
        } footer: {
            ForEach(Array(actions.enumerated()), id: \.offset) { i, a in
                if i == actions.count - 1 {
                    PrimaryButton(title: loc.t("action.\(a.rawValue)"), id: "btn.action.\(a.rawValue)",
                                  enabled: !store.isBusy) { store.perform(a) }
                } else {
                    SecondaryButton(title: loc.t("action.\(a.rawValue)"), id: "btn.action.\(a.rawValue)",
                                    enabled: !store.isBusy) { store.perform(a) }
                }
            }
        }
    }

    /// No button continues the move here (internal error, wait for an update, unsupported, make room on the
    /// iPhone): the user may stop, so the screen says what to turn back on (REVIEW M4).
    private var stopsHere: Bool {
        let continuing: Set<CodeAction> = [.retry, .recheck, .chooseLocation, .backS03, .backS05, .backS07, .newBackup,
                                           .autoNewBackup, .reenterPassword, .continueS16, .continue, .back,
                                           .setEncryption, .resetThreema]
        return !actions.contains { continuing.contains($0) }
    }

    private var actions: [CodeAction] {
        let list = ErrorCatalog.actions(for: ctx.codeRaw).filter {
            $0 != .none && $0 != .continue && !($0 == .prepareAndroid && store.normalized != nil)
        }
        return list.isEmpty ? [.diagReport] : list
    }

    private var title: String {
        loc.t(ErrorCatalog.titleKey(ctx.codeRaw), placeholders)
    }

    private func body(for c: ErrorContext) -> String {
        loc.t(ErrorCatalog.bodyKey(c.codeRaw), placeholders)
    }

    private var placeholders: [String: String] {
        var extra: [String: String] = [:]
        if ctx.data["ios_version"] == nil, let d = store.device {
            extra["ios_version"] = d.iosVersion
            extra["ios_build"] = d.iosBuild
        }
        if ctx.data["limit_bytes"] == nil { extra["limit_gb"] = "20" }
        if ctx.data["app_version"] == nil, let v = store.preBackup?.threema.appVersion ?? store.device?.threema.version {
            extra["app_version"] = v
        }
        var out = ErrorCatalog.placeholders(codeRaw: ctx.codeRaw, data: ctx.data, loc: loc, extra: [:])
        for (k, v) in extra where out[k] == nil { out[k] = v }
        // never leave a raw {token} visible
        for k in ErrorScreen.knownTokens where out[k] == nil { out[k] = "–" }
        return out
    }

    static let knownTokens = ["ios_version", "ios_build", "need_gb", "free_gb", "limit_gb", "photos_gb", "format_version",
                              "app_version", "reason", "attempt", "max", "files", "age_min", "limit_min", "variant",
                              "chats", "fs", "macos", "arch", "power", "battery_pct", "count", "last_progress"]

    private var symbol: String {
        switch failure {
        case .hostUnsupported, .hostSpace, .hostApfs, .hostPower: return "laptopcomputer.trianglebadge.exclamationmark"
        case .androidFormat, .importDup: return "doc.badge.ellipsis"
        case .devMulti, .devOther, .devManaged, .devBattery, .devDisconnected: return "iphone.slash"
        case .iosUnknown, .iosChanged: return "exclamationmark.shield"
        case .threemaMissing, .threemaVariant, .threemaSetup, .threemaRetention, .threemaVersion, .threemaId:
            return "exclamationmark.bubble"
        case .backupFailed, .passwordWrong: return "externaldrive.badge.xmark"
        case .airplane: return "airplane"
        case .dcim: return "photo.badge.exclamationmark"
        case .freshness: return "timer"
        case .iphoneSpace, .photosLimit: return "internaldrive"
        case .findMy: return "location.slash"
        case .restoreBefore, .restoreMid: return "cable.connector.slash"
        case .internalError: return "exclamationmark.octagon"
        }
    }
}
