// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S16 Am iPhone nach dem Neustart / On the iPhone after the restart. The answer goes to `postcheck`.
struct S16AfterRestart: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    static let answers = ["account_only", "full_setup", "none"]

    var body: some View {
        ScreenScaffold(screen: .s16, title: loc.t("screen.S16.name"), symbol: "iphone.and.arrow.forward",
                       showsCancel: false) {
            if store.restoreLinkLost {
                InfoBox(kind: .info, loc.t("s16.lost_link")).accessibilityIdentifier("inline.link_lost")
            }
            Text(loc.t("s16.intro")).font(.headline)
            VStack(alignment: .leading, spacing: 10) {
                ForEach(1...5, id: \.self) { i in NumberedStep(number: i, text: AttributedString(loc.t("s16.step.\(i)"))) }
            }
            InfoBox(kind: .danger, text: loc.md("s16.warning")).accessibilityIdentifier("box.never")
            VStack(alignment: .leading, spacing: 8) {
                Text(loc.t("s16.question")).font(.headline).fixedSize(horizontal: false, vertical: true)
                Picker(loc.t("s16.question"), selection: Binding(get: { store.answers.buddyAnswer },
                                                                  set: { if let a = $0 { store.answerBuddy(a) } })) {
                    ForEach(Self.answers, id: \.self) { a in Text(loc.t("s16.answer.\(a)")).tag(Optional(a)) }
                }
                .pickerStyle(.radioGroup)
                .labelsHidden()
                .accessibilityLabel(loc.t("s16.question"))
                .accessibilityIdentifier("radio.buddy")
            }
        } footer: {
            PrimaryButton(title: loc.t("common.continue"), id: "btn.continue",
                          enabled: store.answers.buddyAnswer != nil) { store.confirmAfterRestart() }
        }
    }
}
