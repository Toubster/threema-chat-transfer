// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S21 Angehalten / Stopped (red). Variant by verdict (DESIGN §7, §8.5): R1 `threema_only` (or S17 "Problem" with
/// green system checks), R2 `data`/`restore_state`, R2k `data_keychain`, R4 `setup_full`. Always: the "do this now"
/// box and the diagnostic report.
struct S21Stopped: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    var body: some View {
        ScreenScaffold(screen: .s21, title: loc.t("s21.title"), symbol: "hand.raised.fill", tone: .danger,
                       showsCancel: false) {
            InfoBox(kind: .danger, text: loc.md("s21.now")).accessibilityIdentifier("box.now")
            Paragraph(variantText)
                .accessibilityIdentifier("s21.variant.\(store.stopVariant.rawValue)")
            if let e = store.inlineError, e.origin == .s21 {
                InfoBox(kind: .warning, title: loc.t(ErrorCatalog.titleKey(e.codeRaw)),
                        loc.t(ErrorCatalog.bodyKey(e.codeRaw),
                              ErrorCatalog.placeholders(codeRaw: e.codeRaw, data: e.data, loc: loc)))
                    .accessibilityIdentifier("inline.error")
            }
            Paragraph(loc.t("s21.always"), secondary: true)
        } footer: {
            SecondaryButton(title: loc.t("action.diag_report"), id: "btn.action.diag_report", enabled: !store.isBusy) {
                store.perform(.diagReport)
            }
            switch store.stopVariant {
            case .r1:
                if store.inlineError == nil && !store.rollbackRefused {
                    PrimaryButton(title: loc.t("action.reset_threema"), id: "btn.action.reset_threema",
                                  enabled: !store.isBusy) { store.perform(.resetThreema) }
                } else {
                    // the engine refused the reset (more than 6 h, or already used): the way on is R2/R3
                    SecondaryButton(title: loc.t("s21.r2.leave"), id: "btn.leave_as_is") { store.leaveAsIs() }
                    PrimaryButton(title: loc.t("s21.r2.guide"), id: "btn.guide_apple") { store.go(.s22) }
                }
            case .r2, .r2k:
                SecondaryButton(title: loc.t("s21.r2.leave"), id: "btn.leave_as_is") { store.leaveAsIs() }
                PrimaryButton(title: loc.t("s21.r2.guide"), id: "btn.guide_apple") { store.go(.s22) }
            case .r4:
                PrimaryButton(title: loc.t("s21.r2.guide"), id: "btn.guide_apple") { store.go(.s22) }
            }
        }
    }

    private var variantText: String {
        switch store.stopVariant {
        case .r1: return loc.t("s21.r1")
        case .r2: return loc.t("s21.r2", ["areas": store.affectedAreasText(loc)])
        case .r2k: return loc.t("s21.r2k")
        case .r4: return loc.t("s21.r4")
        }
    }
}

/// S22 Apple-Sicherung zurückspielen / Restore your Apple backup (guide according to the S09 choice).
struct S22RestoreApple: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    private var args: [String: String] {
        var out = ["date": "–", "time": "–", "transfer_time": "–"]
        if let d = Formatters.parseTimestamp(store.answers.safetyNetAt) {
            out["date"] = Formatters.date(d, lang: loc.language)
            out["time"] = Formatters.time(d, lang: loc.language)
        }
        if let t = Formatters.parseTimestamp(store.answers.transferConfirmedAt) {
            out["transfer_time"] = Formatters.time(t, lang: loc.language)
        }
        return out
    }

    /// R4 (`setup_full`): the iPhone sits in Setup Assistant -- nothing to erase, the restore starts right there
    /// (REVIEW M2). Otherwise the iCloud way starts with checking that today's backup exists (REVIEW M3).
    private var inSetupAssistant: Bool { store.stopVariant == .r4 }

    var body: some View {
        ScreenScaffold(screen: .s22, title: loc.t("screen.S22.name"), symbol: "arrow.counterclockwise.icloud",
                       tone: .danger, showsCancel: false) {
            let choice = store.answers.safetyNet
            if choice != "finder" {
                InfoBox(kind: .info, title: loc.t("s22.label.icloud"),
                        text: loc.md(inSetupAssistant ? "s22.setup.icloud" : "s22.icloud", args))
                    .accessibilityIdentifier("guide.icloud")
            }
            if choice != "icloud" {
                InfoBox(kind: .info, title: loc.t("s22.label.finder"),
                        text: loc.md(inSetupAssistant ? "s22.setup.finder" : "s22.finder", args))
                    .accessibilityIdentifier("guide.finder")
            }
            Paragraph(loc.t("s22.after"))
            TurnBackOnBox(titleKey: "turn_on.title_after", keys: Self.allTurnBackOn)
        } footer: {
            SecondaryButton(title: loc.t("action.diag_report"), id: "btn.action.diag_report", enabled: !store.isBusy) {
                store.perform(.diagReport)
            }
            PrimaryButton(title: loc.t("common.back"), id: "btn.back_s21") { store.go(.s21, replace: true) }
        }
    }
}

extension S22RestoreApple {
    /// After the Apple restore the iPhone has the settings of the S09 backup (made after S08): everything is listed.
    static let allTurnBackOn = ["turn_on.airplane", "turn_on.bluetooth", "turn_on.find_my", "turn_on.updates_after"]
}

/// S23 Weitermachen? / Continue? (resume after a restart or "Später weitermachen").
struct S23Resume: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    var body: some View {
        ScreenScaffold(screen: .s23, title: loc.t("screen.S23.name"), symbol: "arrow.uturn.forward.circle",
                       showsCancel: false) {
            if let d = store.resume {
                Paragraph(loc.t("s23.text", ["step": stepName]))
                if d.afterSend {
                    InfoBox(kind: .warning, loc.t("s23.after_send")).accessibilityIdentifier("inline.after_send")
                } else if store.showsStopReminder {
                    TurnBackOnBox(titleKey: "turn_on.title", keys: store.turnBackOnKeys)
                }
            } else {
                HStack(spacing: 8) { ProgressView().controlSize(.small); Text(loc.t("s23.loading")) }
                    .accessibilityElement(children: .combine)
            }
        } footer: {
            if store.resume?.canDiscard == true {
                SecondaryButton(title: loc.t("s23.btn.discard"), id: "btn.discard", enabled: !store.isBusy) {
                    store.dialog = .discard
                }
            }
            PrimaryButton(title: loc.t("s23.btn.continue"), id: "btn.resume_continue",
                          enabled: store.resume != nil && !store.isBusy) { store.continueResume() }
        }
    }

    private var stepName: String {
        guard let s = store.resumeLastStep else { return "–" }
        return loc.t(s.nameKey)
    }
}

/// "Wieder einschalten" list for every way out before the end (REVIEW M4): Find My, Stolen Device Protection,
/// updates, airplane mode, Bluetooth -- what S08/S11 asked the user to switch off.
struct TurnBackOnBox: View {
    @Environment(\.loc) var loc
    let titleKey: String
    let keys: [String]

    var body: some View {
        InfoBox(kind: .warning, title: loc.t(titleKey),
                keys.map { "• " + loc.t($0) }.joined(separator: "\n"))
            .accessibilityIdentifier("box.turn_back_on")
    }
}
