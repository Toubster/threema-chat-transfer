// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S15 Übertragung läuft / Transfer in progress (`restore` or `rollback-threema`, `critical`): Cancel and Quit
/// are locked, the Mac stays awake.
struct S15Transfer: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    private var act: Activity? { store.activities[store.rollbackMode ? .rollbackThreema : .restore] }

    var body: some View {
        ScreenScaffold(screen: .s15, title: loc.t("s15.title"), symbol: "cable.connector", tone: .warning,
                       showsCancel: false) {
            Paragraph(loc.t("s15.text"))
            ProgressPanel(activity: act, label: loc.t("phase.\(act?.phase ?? "send")"))
            if act?.phase == EnginePhase.reboot.rawValue || act?.running == false {
                InfoBox(kind: .info, loc.t("s15.end")).accessibilityIdentifier("inline.restarting")
            }
        } footer: {
            EmptyView()
        }
    }
}
