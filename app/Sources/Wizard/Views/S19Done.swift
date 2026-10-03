// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S19 Fertig / Done (green). Notes are the harmless N_ classes of the verdict plus the always-true hints.
struct S19Done: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    static let alwaysNotes = ["N_APPLE_PAY_READD", "N_MAIL_LOCAL_MAY_BE_GONE"]

    private var notes: [NoteItem] {
        var codes = store.postcheck?.notes ?? []
        for c in Self.alwaysNotes where !codes.contains(c) { codes.append(c) }
        return codes.map { NoteItem(code: $0, data: nil) }
    }

    var body: some View {
        ScreenScaffold(screen: .s19, title: loc.t("s19.title"), symbol: "checkmark.seal.fill", tone: .success,
                       showsCancel: false) {
            Paragraph(loc.t(store.rollbackMode ? "s19.rollback.text" : "s19.text"))
            Text(loc.t("s19.notes")).font(.headline)
            NoteBullets(notes: notes)
        } footer: {
            PrimaryButton(title: loc.t("common.continue"), id: "btn.continue") { store.finishSuccess() }
        }
    }
}

/// S19 hints as one compact box of bullet lines (DESIGN §8.3 S19 "Hinweise"): all of them fit the smallest window,
/// also with every harmless iOS 27 class plus the two hints that are always shown.
struct NoteBullets: View {
    @Environment(\.loc) var loc
    let notes: [NoteItem]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(notes) { n in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Image(systemName: "info.circle").foregroundStyle(BoxKind.info.color).accessibilityHidden(true)
                    Text(loc.t(ErrorCatalog.bodyKey(n.code),
                               ErrorCatalog.placeholders(codeRaw: n.code, data: n.data ?? .object([:]), loc: loc)))
                        .fixedSize(horizontal: false, vertical: true)
                }
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("note.\(n.code)")
            }
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(BoxKind.info.color.opacity(0.10), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(BoxKind.info.color.opacity(0.35)))
    }
}
