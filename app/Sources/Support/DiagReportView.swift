// SPDX-License-Identifier: AGPL-3.0-or-later
import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// "Diagnosebericht speichern" (DESIGN §10.1): created by `diag-report` (codes and counts only, fresh salt), shown
/// as a preview ("Das steht drin") and saved only where the user chooses. Never uploaded.
struct DiagReportView: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc
    @State private var saved = false

    private var preview: String? {
        guard let url = store.diagFile, let data = try? Data(contentsOf: url), data.count <= 512 * 1024 else { return nil }
        if let obj = try? JSONSerialization.jsonObject(with: data),
           let pretty = try? JSONSerialization.data(withJSONObject: obj, options: [.prettyPrinted, .sortedKeys]) {
            return String(data: pretty, encoding: .utf8)
        }
        return String(data: data, encoding: .utf8)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(loc.t("diag.title")).font(.title2.weight(.semibold)).accessibilityAddTraits(.isHeader)
            Text(loc.t("diag.preview")).font(.headline)
            VStack(alignment: .leading, spacing: 4) {
                ForEach(1...4, id: \.self) { i in Bullet(text: loc.t("diag.item.\(i)")) }
            }
            Text(loc.t("diag.not_included")).fixedSize(horizontal: false, vertical: true)
            Text(loc.t("diag.never_uploaded")).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            if store.diagCreating {
                HStack(spacing: 8) { ProgressView().controlSize(.small); Text(loc.t("diag.creating")) }
            } else if let text = preview {
                ScrollView {
                    Text(text)
                        .font(.caption.monospaced())
                        .textSelection(.enabled)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }
                .frame(height: 200)
                .padding(8)
                .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
                .accessibilityLabel(loc.t("diag.preview"))
                .accessibilityIdentifier("diag.preview")
            } else {
                Text(loc.t("diag.unavailable")).foregroundStyle(.secondary).accessibilityIdentifier("diag.unavailable")
            }
            if saved { Label(loc.t("diag.saved"), systemImage: "checkmark.circle.fill").foregroundStyle(.green) }
            HStack {
                Spacer()
                SecondaryButton(title: loc.t("common.close"), id: "btn.close") { store.sheet = nil }
                PrimaryButton(title: loc.t("diag.save"), id: "btn.diag_save",
                              enabled: store.diagFile != nil && !store.diagCreating) { save() }
            }
        }
        .padding(24)
        .frame(width: 560)
        .accessibilityElement(children: .contain)   // the sheet is a container; its controls keep their ids
        .accessibilityIdentifier("sheet.diag")
    }

    private func save() {
        guard let src = store.diagFile else { return }
        if store.deps.engine.kind.isMock { saved = true; return }   // mock runs never write outside the session
        let panel = NSSavePanel()
        panel.nameFieldStringValue = "\(AppInfo.slug)-diagnose.json"
        panel.allowedContentTypes = [.json]
        guard panel.runModal() == .OK, let dest = panel.url else { return }
        try? FileManager.default.removeItem(at: dest)
        if (try? FileManager.default.copyItem(at: src, to: dest)) != nil { saved = true }
    }
}

/// Über {App}: version, disclaimer (DESIGN §11.3), licence and NOTICE, link to the source (opens the browser).
struct AboutView: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    private var notice: String? {
        let r = Bundle.main.resourceURL
        for rel in ["legal/NOTICE", "NOTICE", "NOTICE.txt"] {
            if let url = r?.appendingPathComponent(rel), let s = try? String(contentsOf: url, encoding: .utf8) { return s }
        }
        return nil
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(loc.t("about.title")).font(.title2.weight(.semibold)).accessibilityAddTraits(.isHeader)
            Text(loc.t("about.version", ["version": "\(AppInfo.version) (\(AppInfo.build))"])).foregroundStyle(.secondary)
            Text(loc.t("about.disclaimer")).fixedSize(horizontal: false, vertical: true)
            Text(loc.t("about.license")).fixedSize(horizontal: false, vertical: true)
            Text(loc.t("about.notice")).font(.headline)
            ScrollView {
                Text(notice ?? loc.t("about.notice_missing"))
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(height: 160)
            .padding(8)
            .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
            HStack {
                if let u = AppInfo.sourceURL {
                    Button(loc.t("about.source")) { store.deps.openURL(u) }
                        .buttonStyle(.link)
                        .accessibilityIdentifier("btn.source")
                }
                Spacer()
                PrimaryButton(title: loc.t("common.close"), id: "btn.close") { store.sheet = nil }
            }
        }
        .padding(24)
        .frame(width: 560)
        .accessibilityElement(children: .contain)   // the sheet is a container; its controls keep their ids
        .accessibilityIdentifier("sheet.about")
    }
}

/// Help for F-IPHONE-SPACE / F-PHOTOS-LIMIT and the Help menu.
struct HelpSheet: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(loc.t("help.title")).font(.title2.weight(.semibold)).accessibilityAddTraits(.isHeader)
            Text(loc.t("help.storage")).fixedSize(horizontal: false, vertical: true)
            HStack {
                if let u = AppInfo.guideURL {
                    Button(loc.t("help.guide")) { store.deps.openURL(u) }
                        .buttonStyle(.link)
                        .accessibilityIdentifier("btn.guide")
                }
                Spacer()
                PrimaryButton(title: loc.t("common.close"), id: "btn.close") { store.sheet = nil }
            }
        }
        .padding(24)
        .frame(width: 480)
        .accessibilityElement(children: .contain)   // the sheet is a container; its controls keep their ids
        .accessibilityIdentifier("sheet.help")
    }
}

/// Asks for the iPhone backup password again (after a restart, or after F-PW-WRONG). Memory only.
struct PasswordPromptSheet: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc
    @State private var value = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(loc.t("pwprompt.title")).font(.title2.weight(.semibold)).accessibilityAddTraits(.isHeader)
            Text(loc.t("pwprompt.text")).fixedSize(horizontal: false, vertical: true)
            Text(loc.md("backup_pw.what")).font(.callout).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            if store.storedPasswordRejected {
                Text(loc.t("pwprompt.rejected")).fixedSize(horizontal: false, vertical: true)
                    .accessibilityIdentifier("label.password_rejected")
            }
            SecureField(loc.t("common.password"), text: $value)
                .textFieldStyle(.roundedBorder)
                .accessibilityLabel(loc.t("pwprompt.title"))
                .accessibilityIdentifier("field.prompt_password")
                .onSubmit { submit() }
            HStack {
                Spacer()
                SecondaryButton(title: loc.t("common.cancel"), id: "btn.prompt_cancel") {
                    value = ""
                    store.submitPasswordPrompt(nil)
                }
                PrimaryButton(title: loc.t("common.continue"), id: "btn.prompt_ok", enabled: !value.isEmpty) { submit() }
            }
        }
        .padding(24)
        .frame(width: 440)
        .accessibilityElement(children: .contain)   // the sheet is a container; its controls keep their ids
        .accessibilityIdentifier("sheet.password_prompt")
        .interactiveDismissDisabled()
    }

    private func submit() {
        guard !value.isEmpty else { return }
        let v = value
        value = ""
        store.submitPasswordPrompt(v)
    }
}
