// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S10a Passwort für iPhone-Sicherungen festlegen (encryption is off): generated password (6×4, keychain by
/// default) or an own one (≥ 10 characters); `encryption-enable` asks for the passcode on the iPhone.
struct S10aSetPassword: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    private var enabling: Bool { store.runningCommand == .encryptionEnable }

    var body: some View {
        ScreenScaffold(screen: .s10a, title: loc.t("screen.S10a.name"), symbol: "key.fill") {
            Paragraph(md: loc.md("s10a.text"))
            BackupPasswordWhat(compact: true)
            if !store.ownPasswordMode {
                VStack(alignment: .leading, spacing: 8) {
                    Text(loc.t("s10a.generated")).font(.headline)
                    Text(store.generatedPassword)
                        .font(.system(.title2, design: .monospaced).weight(.semibold))
                        .textSelection(.enabled)
                        .padding(12)
                        .background(Color.secondary.opacity(0.1), in: RoundedRectangle(cornerRadius: 8))
                        // one element of its own: a label override directly on selectable text sends SwiftUI's
                        // accessibility label resolution into endless recursion on macOS 27 (the app crashed on S10a
                        // as soon as VoiceOver or a UI test looked at it); VoiceOver spells the password
                        .accessibilityElement(children: .ignore)
                        .accessibilityAddTraits(.isStaticText)
                        .accessibilityLabel(store.generatedPassword.map { String($0) }.joined(separator: " "))
                        .accessibilityIdentifier("label.generated_password")
                    Button(loc.t("s10a.btn.regenerate")) { store.prepareGeneratedPassword(force: true) }
                        .buttonStyle(.link)
                        .disabled(enabling)
                        .accessibilityIdentifier("btn.regenerate")
                }
                InfoBox(kind: .warning, loc.t("s10a.hint"))
            }
            ChecklistToggle(text: loc.t("s10a.option.keychain"), id: "keychain", isOn: $store.saveInKeychain)
            ChecklistToggle(text: loc.t("s10a.option.own"), id: "own_password", isOn: $store.ownPasswordMode)
            if store.ownPasswordMode {
                VStack(alignment: .leading, spacing: 8) {
                    SecureField(loc.t("s10a.own.field"), text: $store.ownPasswordInput)
                        .accessibilityLabel(loc.t("s10a.own.field"))
                        .accessibilityIdentifier("field.own_password")
                    SecureField(loc.t("s10a.own.repeat"), text: $store.ownPasswordRepeat)
                        .accessibilityLabel(loc.t("s10a.own.repeat"))
                        .accessibilityIdentifier("field.own_password_repeat")
                    if let p = store.ownPasswordProblem, !store.ownPasswordInput.isEmpty {
                        Text(loc.t(p)).font(.callout).foregroundStyle(.red)
                    }
                    InfoBox(kind: .warning, loc.t("s10a.hint"))
                }
                .textFieldStyle(.roundedBorder)
                .frame(maxWidth: 400)
            } else {
                VStack(alignment: .leading, spacing: 6) {
                    Text(loc.t("s10a.confirm")).fixedSize(horizontal: false, vertical: true)
                    TextField(loc.t("s10a.confirm.field"), text: $store.lastFourInput)
                        .textFieldStyle(.roundedBorder)
                        .frame(width: 160)
                        .accessibilityLabel(loc.t("s10a.confirm.field"))
                        .accessibilityIdentifier("field.last_four")
                    if store.lastFourInput.count >= 4,
                       !PasswordGenerator.matchesLastFour(store.lastFourInput, of: store.generatedPassword) {
                        Text(loc.t("s10a.confirm.wrong")).font(.callout).foregroundStyle(.red)
                    }
                }
            }
            // what the button does next: passcode on the iPhone, nothing deleted, the setting stays on
            Paragraph(md: loc.md("s10a.next"), secondary: true)
            if enabling {
                InfoBox(kind: .info, title: nil, text: AttributedString(loc.t("s10a.overlay")))
                    .accessibilityIdentifier("banner.passcode")
            }
        } footer: {
            PrimaryButton(title: loc.t("s10a.btn"), id: "btn.enable_encryption", enabled: store.canEnableEncryption) {
                Task { await store.enableEncryption() }
            }
        }
        .onAppear { store.prepareGeneratedPassword() }
    }
}
