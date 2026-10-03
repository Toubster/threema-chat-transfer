// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S11 iPhone offline schalten / Take your iPhone offline. From here on "Zurück" is locked (DESIGN §8.1).
struct S11Offline: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    private func binding(_ item: String) -> Binding<Bool> {
        Binding(get: { store.isChecked(\.offlineChecklist, item) },
                set: { store.toggle(\.offlineChecklist, item, $0) })
    }

    var body: some View {
        ScreenScaffold(screen: .s11, title: loc.t("s11.title"), symbol: "airplane") {
            InfoBox(kind: .warning, loc.t("s11.text"))
            VStack(alignment: .leading, spacing: 12) {
                ForEach(Array(WizardStore.offlineItems.enumerated()), id: \.offset) { i, item in
                    ChecklistToggle(text: loc.t("s11.item.\(i + 1)"), id: item, isOn: binding(item))
                }
            }
            InfoBox(kind: .info, loc.t("backup.passcode_hint"))
        } footer: {
            PrimaryButton(title: loc.t("s11.btn"), id: "btn.start_backup",
                          enabled: store.allChecked(\.offlineChecklist, WizardStore.offlineItems) && !store.isBusy) {
                store.startBackup()
            }
        }
    }
}
