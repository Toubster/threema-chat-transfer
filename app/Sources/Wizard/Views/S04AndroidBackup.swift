// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S04 Android-Sicherung erstellen / Create the Android backup.
struct S04AndroidBackup: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    var body: some View {
        ScreenScaffold(screen: .s04, title: loc.t("s04.title"), symbol: "externaldrive.badge.plus") {
            InfoBox(kind: .info, loc.t("s04.hint"))
            VStack(alignment: .leading, spacing: 12) {
                ForEach(1...4, id: \.self) { i in NumberedStep(number: i, text: AttributedString(loc.t("s04.step.\(i)"))) }
            }
            InfoBox(kind: .info, text: AttributedString(loc.t("s04.box")))
        } footer: {
            PrimaryButton(title: loc.t("common.done"), id: "btn.done") { store.go(.s05) }
        }
    }
}
