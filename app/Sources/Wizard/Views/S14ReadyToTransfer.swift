// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S14 Bereit zur Übertragung / Ready to transfer (confirmation; then "Letzte Prüfungen …" and `restore`).
struct S14ReadyToTransfer: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    private var restoring: Activity? { store.activities[.restore] }
    private var checking: Bool { store.runningCommand == .restore }

    var body: some View {
        ScreenScaffold(screen: .s14, title: loc.t("screen.S14.name"), symbol: "arrow.down.to.line.circle",
                       showsCancel: false) {
            Paragraph(nothingNew ? loc.t("s14.text.nothing_new") : loc.t("s14.text", textArgs))
            InfoBox(kind: .info, title: loc.t("s14.restore.title"), text: loc.md("s14.restore", textArgs)) {
                Text(loc.md("s14.redo")).fixedSize(horizontal: false, vertical: true)
            }
            .accessibilityIdentifier("s14.restore")
            InfoBox(kind: .danger, text: loc.md("s14.after"))
            CountdownView(state: store.countdown)
            VStack(alignment: .leading, spacing: 10) {
                ChecklistToggle(text: loc.t("s14.check.backup"), id: "apple_backup", isOn: $store.transferChecks[0])
                ChecklistToggle(text: loc.t("s14.check.password"), id: "apple_password", isOn: $store.transferChecks[1])
            }
            .disabled(checking)
            if checking {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 8) { ProgressView().controlSize(.small); Text(loc.t("s14.final_checks")).font(.headline) }
                    ForEach(restoring?.checks ?? []) { c in
                        CheckRowView(label: loc.t("check.\(c.id)"), status: c.status, id: c.id)
                    }
                }
                .accessibilityIdentifier("final.checks")
            }
        } footer: {
            if store.canCancel {
                SecondaryButton(title: loc.t("s14.btn.cancel"), id: "btn.cancel", enabled: !checking) {
                    store.requestCancel()
                }
            }
            PrimaryButton(title: loc.t("s14.btn.transfer"), id: "btn.transfer_now", enabled: store.canTransfer) {
                Task { await store.transferNow() }
            }
        }
    }

    /// The prepared set adds nothing (the Android history is already in the iPhone's Threema data): a friendly line
    /// instead of "0 messages and 0 media will be transferred".
    private var nothingNew: Bool {
        guard let p = store.prepared else { return false }
        return p.messages == 0 && p.media == 0
    }

    private var textArgs: [String: String] {
        let p = store.prepared
        let time = Formatters.parseTimestamp(store.preBackup?.finishedAt).map { Formatters.time($0, lang: loc.language) }
        return ["messages": p.map { Formatters.count($0.messages, lang: loc.language) } ?? "–",
                "media": p.map { Formatters.count($0.media, lang: loc.language) } ?? "–",
                "size": p.map { Formatters.gigabytes($0.payloadBytes, lang: loc.language) } ?? "–",
                "time": time ?? "–"]
    }
}
