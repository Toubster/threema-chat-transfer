// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S09 Sicherheitsnetz bei Apple / Apple safety net (mandatory).
struct S09SafetyNet: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    var body: some View {
        ScreenScaffold(screen: .s09, title: loc.t("s09.title"), symbol: "lifepreserver") {
            Paragraph(loc.t("s09.text"))
            InfoBox(kind: .info, title: loc.t("s09.option.icloud"), loc.t("s09.icloud"))
            InfoBox(kind: .info, title: loc.t("s09.option.finder"), loc.t("s09.finder"))
            VStack(alignment: .leading, spacing: 8) {
                ChecklistToggle(text: loc.t("s09.check"), id: "safety_net", isOn: $store.safetyNetConfirmed)
                Picker(loc.t("s09.check"), selection: $store.safetyNetChoice) {
                    Text(loc.t("s09.option.icloud")).tag(Optional("icloud"))
                    Text(loc.t("s09.option.finder")).tag(Optional("finder"))
                }
                .pickerStyle(.radioGroup)
                .labelsHidden()
                .padding(.leading, 22)
                .accessibilityLabel(loc.t("s09.check"))
                .accessibilityIdentifier("radio.safety_net")
            }
        } footer: {
            PrimaryButton(title: loc.t("common.continue"), id: "btn.continue",
                          enabled: store.safetyNetConfirmed && store.safetyNetChoice != nil && !store.isBusy) {
                Task { await store.confirmSafetyNet() }
            }
        }
    }
}
