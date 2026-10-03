// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S10b Ihr iPhone hat bereits ein Sicherungs-Passwort / Your iPhone already has a backup password (encryption is
/// already on). A decision screen: the iPhone encrypts every computer backup with its STORED password and only exactly
/// that one opens it -- a new or made-up password does not work. "Ich kenne das Passwort" → field (memory only,
/// checked after S12). "Ich weiß es nicht" → where to look and Apple's "Alle Einstellungen zurücksetzen", then
/// "Erneut prüfen" (`device-status`): encryption off → S10a. Nothing is preselected.
struct S10bEnterPassword: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    var body: some View {
        ScreenScaffold(screen: .s10b, title: loc.t("screen.S10b.name"), symbol: "key") {
            Paragraph(md: loc.md("s10b.text"))
            if store.encryptionTurnedOnElsewhere {
                InfoBox(kind: .warning, loc.t("s10b.finder_note")).accessibilityIdentifier("inline.finder_password")
            }
            BackupPasswordWhat()
            VStack(alignment: .leading, spacing: 8) {
                Text(loc.t("s10b.choice")).font(.headline)
                Picker(loc.t("s10b.choice"), selection: $store.existingPasswordChoice) {
                    Text(loc.t("s10b.choice.known")).tag(Optional(WizardStore.ExistingPasswordChoice.known))
                    Text(loc.t("s10b.choice.unknown")).tag(Optional(WizardStore.ExistingPasswordChoice.unknown))
                }
                .pickerStyle(.radioGroup)
                .labelsHidden()
                .disabled(store.isBusy)
                .accessibilityLabel(loc.t("s10b.choice"))
                .accessibilityIdentifier("radio.existing_password")
            }
            switch store.existingPasswordChoice {
            case .known?:
                VStack(alignment: .leading, spacing: 8) {
                    SecureField(loc.t("s10b.field"), text: $store.backupPasswordInput)
                        .textFieldStyle(.roundedBorder)
                        .frame(maxWidth: 400)
                        .accessibilityLabel(loc.t("s10b.field"))
                        .accessibilityIdentifier("field.backup_password")
                        .onSubmit { store.confirmExistingPassword() }
                    Paragraph(loc.t("s10b.known.check"), secondary: true)
                }
            case .unknown?:
                BackupPasswordUnknownHelp()
            case nil:
                EmptyView()
            }
        } footer: {
            PrimaryButton(title: loc.t("common.continue"), id: "btn.continue",
                          enabled: store.canConfirmExistingPassword) { store.confirmExistingPassword() }
        }
    }
}

/// The one plain-language explanation of the "password for iPhone backups on the Mac" (S10a, S10b, F-PW-WRONG):
/// a password only for computer backups; not the passcode, not the Apple Account, nothing to do with iCloud.
/// `compact` (S10a): the same text as a secondary paragraph, so the confirmation field stays visible at the smallest
/// window size.
struct BackupPasswordWhat: View {
    @Environment(\.loc) var loc
    var compact = false

    var body: some View {
        if compact {
            // no accessibility modifiers on selectable text (macOS 27 recursion, see S10a)
            Paragraph(md: loc.md("backup_pw.what"), secondary: true)
        } else {
            InfoBox(kind: .info, title: loc.t("backup_pw.what.title"), text: loc.md("backup_pw.what"))
                .accessibilityIdentifier("inline.backup_password_what")
        }
    }
}

/// "Ich weiß es nicht / habe nie eins festgelegt" (S10b) and "Passwort unbekannt?" (F-PW-WRONG): why it can be on,
/// 1. earlier passwords, 2. the keychain of this Mac, 3. Apple's "Alle Einstellungen zurücksetzen" and "Erneut prüfen"
/// (`device-status`; encryption off → S10a). `allowsReset` is false after the transfer (F-PW-WRONG of the control
/// backup): resetting settings then would change the iPhone the control backup is meant to check.
struct BackupPasswordUnknownHelp: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc
    var allowsReset = true

    private var checking: Bool { store.runningCommand == .deviceStatus }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Paragraph(md: loc.md("pw_help.why"), secondary: true)
            NumberedStep(number: 1, text: loc.md("pw_help.step1"))
            NumberedStep(number: 2, text: loc.md("pw_help.step2"))
            if allowsReset {
                NumberedStep(number: 3, text: loc.md("pw_help.step3"))
                if store.answers.safetyNet == "finder" {
                    InfoBox(kind: .warning, loc.t("pw_help.finder_net")).accessibilityIdentifier("inline.finder_safety_net")
                }
                Paragraph(md: loc.md("pw_help.after"), secondary: true)
                HStack(spacing: 10) {
                    Button(loc.t("action.recheck")) { Task { await store.recheckEncryption() } }
                        .controlSize(.large)
                        .disabled(store.isBusy)
                        .fixedSize()
                        .accessibilityIdentifier("btn.recheck_encryption")
                    if checking {
                        ProgressView().controlSize(.small)
                        Text(loc.t("pw_help.checking")).foregroundStyle(.secondary)
                    }
                }
                if store.encryptionStillOn && !checking {
                    InfoBox(kind: .warning, loc.t("pw_help.still_on"))
                        .accessibilityIdentifier("inline.encryption_still_on")
                }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("inline.password_unknown_help")
    }
}
