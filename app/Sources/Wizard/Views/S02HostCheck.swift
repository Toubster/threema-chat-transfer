// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S02 Mac prüfen / Checking your Mac (`host-check`).
struct S02HostCheck: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    private var act: Activity? { store.activities[.hostCheck] }

    private func status(_ id: CheckID) -> CheckRow.Status {
        if let c = act?.check(id) { return c.status }
        if store.host != nil { return .pass }
        return act?.running == true ? .running : .pending
    }

    var body: some View {
        ScreenScaffold(screen: .s02, title: loc.t("s02.title"), symbol: "laptopcomputer") {
            VStack(alignment: .leading, spacing: 10) {
                let h = store.host
                CheckRowView(label: loc.t("s02.row.macos", ["v": h?.macos ?? "…"]), status: status(.macos), id: "macos")
                CheckRowView(label: loc.t("s02.row.chip"), status: status(.arch), id: "arch")
                CheckRowView(label: loc.t("s02.row.space", [
                    "free": h.map { Formatters.gigabytes($0.freeBytes, lang: loc.language) } ?? "…",
                    "need": h?.needBytes.map { Formatters.gigabytes($0, lang: loc.language) } ?? "…"]),
                    status: status(.freeSpace), id: "free_space")
                CheckRowView(label: loc.t("s02.row.apfs"), status: status(.fsApfs), id: "fs_apfs")
                CheckRowView(label: loc.t("s02.row.power"), status: status(.power), id: "power")
                CheckRowView(label: loc.t("s02.row.filevault"),
                             status: h.map { $0.filevault ? .pass : .warn } ?? status(.filevault), id: "filevault")
            }
            if store.host?.filevault == false || act?.notes.contains(where: { $0.code == "W_FILEVAULT_OFF" }) == true {
                InfoBox(kind: .warning, title: loc.t("code.W_FILEVAULT_OFF.title"), loc.t("code.W_FILEVAULT_OFF.body"))
                    .accessibilityIdentifier("note.W_FILEVAULT_OFF")
            }
            NoteList(notes: (act?.notes ?? []).filter { $0.code != "W_FILEVAULT_OFF" })
            Button(loc.t("s02.link.location")) {
                if let url = LocationPicker.chooseFolder() { Task { await store.chooseWorkdir(url) } }
            }
            .buttonStyle(.link)
            .disabled(store.isBusy)
            .accessibilityIdentifier("btn.choose_location")
            if let root = store.workdirRoot {
                Text(loc.t("s02.location.custom", ["path": root.deletingLastPathComponent().deletingLastPathComponent().path]))
                    .font(.callout).foregroundStyle(.secondary)
            }
        } footer: {
            PrimaryButton(title: loc.t("common.continue"), id: "btn.continue",
                          enabled: store.host != nil && !store.isBusy) { store.confirmHost() }
        }
        .task { if store.host == nil && !store.isBusy { await store.runHostCheck() } }
    }
}

/// Folder/file panels (AppKit).
enum LocationPicker {
    @MainActor
    static func chooseFolder() -> URL? {
        let p = NSOpenPanel()
        p.canChooseDirectories = true
        p.canChooseFiles = false
        p.canCreateDirectories = true
        p.allowsMultipleSelection = false
        return p.runModal() == .OK ? p.url : nil
    }

    @MainActor
    static func chooseFiles() -> [URL] {
        let p = NSOpenPanel()
        p.canChooseDirectories = false
        p.canChooseFiles = true
        p.allowsMultipleSelection = true
        return p.runModal() == .OK ? p.urls : []
    }
}
