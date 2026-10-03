// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S17 Threema prüfen / Check Threema.
struct S17CheckThreema: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    var body: some View {
        ScreenScaffold(screen: .s17, title: loc.t("screen.S17.name"), symbol: "checkmark.message", showsCancel: false) {
            // after "Threema zurücksetzen" Threema is back to its state before the transfer: the Android history is
            // NOT there, so the question is whether Threema works as before
            Paragraph(loc.t(store.rollbackMode ? "s17.rollback.text" : "s17.text"))
                .accessibilityIdentifier(store.rollbackMode ? "s17.variant.rollback" : "s17.variant.final")
            InfoBox(kind: .warning, loc.t("s17.warning"))
        } footer: {
            SecondaryButton(title: loc.t("s17.btn.problem"), id: "btn.problem") { store.threemaChecked(ok: false) }
            PrimaryButton(title: loc.t(store.rollbackMode ? "s17.rollback.btn.ok" : "s17.btn.ok"), id: "btn.chats_ok") {
                store.threemaChecked(ok: true)
            }
        }
    }
}
