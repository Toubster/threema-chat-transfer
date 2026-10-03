// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S20 Wieder einschalten und aufräumen / Turn things back on and clean up (`cleanup`).
struct S20CleanUp: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    var body: some View {
        ScreenScaffold(screen: .s20, title: loc.t("screen.S20.name"), symbol: "sparkles", tone: .success,
                       showsCancel: false) {
            VStack(alignment: .leading, spacing: 6) {
                ForEach(1...7, id: \.self) { i in Bullet(text: loc.t("s20.item.\(i)"), symbol: "checkmark") }
            }
            InfoBox(kind: .info, text: AttributedString(loc.t("s20.cleanup", [
                "x": Formatters.gigabytes(store.chatCopiesBytes, lang: loc.language),
                "y": Formatters.gigabytes(store.safetyCopyBytes, lang: loc.language)]))) {
                VStack(alignment: .leading, spacing: 10) {
                    if store.copiesDeleted {
                        Label(loc.t("s20.copies_deleted"), systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    } else {
                        Button(loc.t("s20.btn.delete_copies")) { Task { await store.cleanup("work") } }
                            .buttonStyle(.borderedProminent)
                            .disabled(store.isBusy)
                            .fixedSize()
                            .accessibilityIdentifier("btn.delete_copies")
                    }
                    if store.safetyCopyDeleted {
                        Label(loc.t("s20.safety.deleted"), systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                    } else if !store.safetyCopyKeepExpired,
                              let until = Formatters.parseTimestamp(store.answers.cleanup?.safetyCopyKeepUntil) {
                        Text(loc.t("s20.safety.kept", ["date": Formatters.date(until, lang: loc.language)]))
                    } else {
                        HStack(spacing: 8) {
                            Text(loc.t("s20.safety.label"))
                            Button(loc.t("s20.safety.keep7")) { store.keepSafetyCopy() }
                                .fixedSize()
                                .accessibilityIdentifier("btn.keep_safety")
                            Button(loc.t("s20.safety.delete")) { Task { await store.deleteSafetyCopy() } }
                                .fixedSize()
                                .disabled(store.isBusy)
                                .accessibilityIdentifier("btn.delete_safety")
                        }
                    }
                }
            }
            Paragraph(loc.t("s20.footer"), secondary: true)
        } footer: {
            PrimaryButton(title: loc.t("common.quit"), id: "btn.quit", enabled: !store.isBusy) { store.perform(.quit) }
        }
    }
}
