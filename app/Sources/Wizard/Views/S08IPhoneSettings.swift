// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S08 iPhone-Einstellungen / iPhone settings (ticks; "Weiter" runs `device-status` for Find My).
struct S08IPhoneSettings: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    private func binding(_ item: String) -> Binding<Bool> {
        Binding(get: { store.isChecked(\.settingsChecklist, item) },
                set: { store.toggle(\.settingsChecklist, item, $0) })
    }

    var body: some View {
        ScreenScaffold(screen: .s08, title: loc.t("screen.S08.name"), symbol: "gearshape.2") {
            VStack(alignment: .leading, spacing: 12) {
                ForEach(Array(WizardStore.settingsItems.enumerated()), id: \.offset) { i, item in
                    ChecklistToggle(text: loc.t("s08.item.\(i + 1)"), id: item, isOn: binding(item))
                }
            }
            Paragraph(loc.t("s08.footer"), secondary: true)
            if store.runningCommand == .deviceStatus {
                HStack(spacing: 8) { ProgressView().controlSize(.small); Text(loc.t("s08.checking")) }
                    .accessibilityElement(children: .combine)
            }
        } footer: {
            PrimaryButton(title: loc.t("common.continue"), id: "btn.continue",
                          enabled: store.allChecked(\.settingsChecklist, WizardStore.settingsItems) && !store.isBusy) {
                Task { await store.confirmSettings() }
            }
        }
    }
}
