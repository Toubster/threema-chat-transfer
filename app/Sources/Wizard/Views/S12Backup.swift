// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S12 Sicherung läuft / Backing up your iPhone (`backup --role pre` and its checks).
struct S12Backup: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    static let rows: [CheckID] = [.password, .airplane, .threemaSetup, .threemaRetention, .threemaModel, .identity,
                                  .photosLimit]

    private var act: Activity? { store.activities[.backup] }

    var body: some View {
        ScreenScaffold(screen: .s12, title: loc.t("screen.S12.name"), symbol: "externaldrive.badge.icloud") {
            Paragraph(loc.t("s12.text", ["min": String(Formatters.backupMinutes(
                estimatedBytes: store.device?.photosBytesEstimate))]))
            InfoBox(kind: .info, loc.t("backup.passcode_hint"))
            ProgressPanel(activity: act, label: loc.t("phase.\(act?.phase ?? "backup")"))
            RetryBanner(retry: act?.retry)
            NoteList(notes: act?.notes ?? [], kind: .info)
            VStack(alignment: .leading, spacing: 8) {
                ForEach(Self.rows, id: \.self) { id in
                    CheckRowView(label: loc.t("check.\(id.rawValue)"), status: status(id), id: id.rawValue)
                }
            }
        } footer: {
            EmptyView()
        }
    }

    private func status(_ id: CheckID) -> CheckRow.Status {
        if let c = act?.check(id) { return c.status }
        if act?.phase == EnginePhase.checks.rawValue && act?.running == true { return .running }
        return store.preBackup != nil ? .pass : .pending
    }
}
