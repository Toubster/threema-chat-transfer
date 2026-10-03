// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S00 Willkommen / Welcome.
struct S00Welcome: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    var body: some View {
        ScreenScaffold(screen: .s00, title: loc.t("s00.title"), symbol: "arrow.left.arrow.right.circle",
                       showsCancel: false) {
            Paragraph(loc.t("s00.text"))
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Image(systemName: "info.circle").foregroundStyle(.secondary).accessibilityHidden(true)
                Text(loc.t("s00.unofficial")).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                Button(loc.t("s00.unofficial.more")) { store.sheet = .about }
                    .buttonStyle(.link)
                    .fixedSize()
                    .accessibilityIdentifier("btn.about")
            }
            .font(.callout)
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("s00.unofficial")
            InfoBox(kind: .info, loc.t("s00.duration"))
            VStack(alignment: .leading, spacing: 6) {
                Text(loc.t("s00.needs.title")).font(.headline)
                ForEach(1...6, id: \.self) { i in Bullet(text: loc.t("s00.needs.\(i)")) }
            }
            .accessibilityElement(children: .combine)
            HStack(spacing: 10) {
                Text(loc.t("s00.lang.label")).foregroundStyle(.secondary)
                Picker(loc.t("s00.lang.label"), selection: $store.language) {
                    Text(loc.t("s00.lang.de")).tag(AppLanguage.de)
                    Text(loc.t("s00.lang.en")).tag(AppLanguage.en)
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
                .accessibilityLabel(loc.t("s00.lang.label"))
                .accessibilityIdentifier("picker.language")
            }
        } footer: {
            SecondaryButton(title: loc.t("s00.btn.what"), id: "btn.what") { store.sheet = .whatItDoes }
            PrimaryButton(title: loc.t("s00.btn.start"), id: "btn.get_started") { store.getStarted() }
        }
    }
}

/// "Was macht {App} genau?"
struct WhatItDoesSheet: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(loc.t("s00.what.title")).font(.title2.weight(.semibold)).accessibilityAddTraits(.isHeader)
            ForEach(1...5, id: \.self) { i in NumberedStep(number: i, text: AttributedString(loc.t("s00.what.\(i)"))) }
            HStack {
                Spacer()
                PrimaryButton(title: loc.t("common.close"), id: "btn.close") { store.sheet = nil }
            }
        }
        .padding(28)
        .frame(width: 560)
        .accessibilityElement(children: .contain)   // the sheet is a container; its controls keep their ids
        .accessibilityIdentifier("sheet.what")
    }
}
