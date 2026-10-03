// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S18 Kontroll-Sicherung / Control backup (`backup --role post` + `postcheck`).
struct S18ControlBackup: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    private var backup: Activity? { store.activities[.backup] }
    private var check: Activity? { store.activities[.postcheck] }

    private var steps: [(label: String, status: CheckRow.Status)] {
        let backupStatus: CheckRow.Status = {
            guard let b = backup, b.command == .backup else { return .pending }
            if check != nil { return .pass }
            return b.running ? .running : (store.inlineError == nil ? .pass : .fail)
        }()
        return [(loc.t("s18.step.backup"), backupStatus),
                (loc.t("s18.step.threema"), check?.status(of: .threema) ?? .pending),
                (loc.t("s18.step.compare"), compareStatus)]
    }

    private var compareStatus: CheckRow.Status {
        guard let c = check else { return .pending }
        let a = c.status(of: .compare), b = c.status(of: .verdict)
        if a == .running || b == .running { return .running }
        return b == .pass ? .pass : a
    }

    var body: some View {
        ScreenScaffold(screen: .s18, title: loc.t("screen.S18.name"), symbol: "doc.on.doc", showsCancel: false) {
            Paragraph(loc.t("s18.text", ["min": String(Formatters.backupMinutes(estimatedBytes: store.preBackup?.bytes))]))
            InfoBox(kind: .info, loc.t("backup.passcode_hint"))
            StepList(steps: steps)
            if store.runningCommand == .backup { ProgressPanel(activity: backup, label: loc.t("s18.step.backup")) }
            RetryBanner(retry: backup?.retry)
            if let e = store.inlineError, e.origin == .s18 {
                InfoBox(kind: .warning, title: loc.t(ErrorCatalog.titleKey(e.codeRaw)),
                        loc.t(ErrorCatalog.bodyKey(e.codeRaw)))
                    .accessibilityIdentifier("inline.error")
            }
        } footer: {
            if let e = store.inlineError, e.origin == .s18 {
                ForEach(ErrorCatalog.actions(for: e.codeRaw), id: \.self) { a in
                    PrimaryButton(title: loc.t("action.\(a.rawValue)"), id: "btn.action.\(a.rawValue)") { store.perform(a) }
                }
            }
        }
    }
}
