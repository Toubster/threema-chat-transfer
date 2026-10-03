// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S13 Übertragung wird vorbereitet / Preparing the transfer (`prepare`; the iPhone is not changed).
struct S13PrepareTransfer: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    private var act: Activity? { store.activities[.prepare] }

    var body: some View {
        ScreenScaffold(screen: .s13, title: loc.t("screen.S13.name"), symbol: "shippingbox") {
            Paragraph(loc.t("s13.text"))
            CountdownView(state: store.countdown)
            StepList(steps: [EnginePhase.extract, .import, .count, .build, .verify].map { p in
                (loc.t("s13.step.\(p.rawValue)"), act?.status(of: p) ?? (store.prepared != nil ? .pass : .pending))
            })
            if act?.running == true { ProgressPanel(activity: act, label: loc.t("phase.\(act?.phase ?? "extract")")) }
        } footer: {
            EmptyView()
        }
    }
}
