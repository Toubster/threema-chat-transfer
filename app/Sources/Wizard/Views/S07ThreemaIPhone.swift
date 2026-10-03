// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S07 Threema auf dem iPhone / Threema on the iPhone (one tick per item).
struct S07ThreemaIPhone: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    private func binding(_ item: String) -> Binding<Bool> {
        Binding(get: { store.isChecked(\.threemaChecklist, item) },
                set: { store.toggle(\.threemaChecklist, item, $0) })
    }

    var body: some View {
        ScreenScaffold(screen: .s07, title: loc.t("screen.S07.name"), symbol: "lock.iphone") {
            if store.welcomeBack { InfoBox(kind: .info, loc.t("s07.welcome_back")).accessibilityIdentifier("inline.welcome_back") }
            VStack(alignment: .leading, spacing: 12) {
                ForEach(Array(WizardStore.threemaItems.prefix(4).enumerated()), id: \.offset) { i, item in
                    ChecklistToggle(text: loc.t("s07.item.\(i + 1)"), id: item, isOn: binding(item))
                }
                if store.missingKeySenders > 0, let n = store.normalized {
                    VStack(alignment: .leading, spacing: 6) {
                        ChecklistToggle(text: loc.t("s07.item.5", [
                            "k": Formatters.count(n.missingKeySenders, lang: loc.language),
                            "m": Formatters.count(n.missingKeyMessages, lang: loc.language)]),
                                        id: "contacts_added", isOn: binding("contacts_added"))
                        Button(loc.t("s07.show_ids")) { store.loadMissingIds() }
                            .buttonStyle(.link)
                            .padding(.leading, 22)
                            .accessibilityIdentifier("btn.show_ids")
                    }
                }
            }
            Paragraph(loc.t("s07.footer"), secondary: true)
            if store.runningCommand == .deviceStatus {
                HStack(spacing: 8) { ProgressView().controlSize(.small); Text(loc.t("s08.checking")) }
                    .accessibilityElement(children: .combine)
            }
        } footer: {
            PrimaryButton(title: loc.t("common.continue"), id: "btn.continue",
                          enabled: store.allChecked(\.threemaChecklist, store.threemaItemsNeeded) && !store.isBusy) {
                Task { await store.confirmThreema() }
            }
        }
    }
}

/// S07 "IDs anzeigen": the only place where Threema IDs are shown (DESIGN §10.1).
struct MissingIdsSheet: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(loc.t("s07.ids.title")).font(.title2.weight(.semibold)).accessibilityAddTraits(.isHeader)
            if store.missingIds.isEmpty {
                Text(loc.t("s07.ids.unavailable")).foregroundStyle(.secondary)
            } else {
                Text(loc.t("s07.ids.text"))
                ScrollView {
                    VStack(alignment: .leading, spacing: 4) {
                        ForEach(store.missingIds, id: \.self) { Text($0).font(.body.monospaced()).textSelection(.enabled) }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(maxHeight: 300)
            }
            HStack { Spacer(); PrimaryButton(title: loc.t("common.close"), id: "btn.close") { store.sheet = nil } }
        }
        .padding(24)
        .frame(width: 440)
        .accessibilityElement(children: .contain)   // the sheet is a container; its controls keep their ids
        .accessibilityIdentifier("sheet.missing_ids")
    }
}
