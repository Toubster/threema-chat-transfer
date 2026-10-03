// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S01 Bitte lesen / Please read (mandatory tick).
struct S01Disclaimer: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    var body: some View {
        ScreenScaffold(screen: .s01, title: loc.t("screen.S01.name"), symbol: "hand.raised") {
            ForEach(1...4, id: \.self) { i in Paragraph(loc.t("s01.p\(i)")) }
            ChecklistToggle(text: loc.t("s01.check"), id: "disclaimer", isOn: $store.disclaimerAccepted)
            Paragraph(loc.t("s01.footer"), secondary: true)
        } footer: {
            PrimaryButton(title: loc.t("common.continue"), id: "btn.continue", enabled: store.disclaimerAccepted) {
                store.acceptDisclaimer()
            }
        }
    }
}
