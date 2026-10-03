// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI
import UniformTypeIdentifiers

/// S05 Sicherung wählen / Choose the backup (`android-inspect`). The password stays in memory only.
struct S05ChooseBackup: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc
    @State private var dropTargeted = false

    var body: some View {
        ScreenScaffold(screen: .s05, title: loc.t("s05.title"), symbol: "doc.zipper") {
            dropZone
            if store.activities[.androidInspect]?.running == true {
                HStack(spacing: 8) { ProgressView().controlSize(.small); Text(loc.t("s05.inspecting")) }
                    .accessibilityElement(children: .combine)
            }
            if let i = store.inspect { fileList(i) }
            if let e = store.inlineError, e.origin == .s05 {
                InfoBox(kind: .danger, title: loc.t(ErrorCatalog.titleKey(e.codeRaw)),
                        text: AttributedString(loc.t(ErrorCatalog.bodyKey(e.codeRaw),
                                                     ErrorCatalog.placeholders(codeRaw: e.codeRaw, data: e.data, loc: loc,
                                                                               extra: ["date": dateText(ref: e.data["ref"]?.int)]))))
                    .accessibilityIdentifier("inline.error")
            }
            if let i = store.inspect, store.inlineError == nil || store.inlineError?.code == .E_ANDROID_PASSWORD {
                if store.inspectWithoutMedia { noMediaBox }
                passwordFields(i)
            }
        } footer: {
            PrimaryButton(title: loc.t("s05.btn.check"), id: "btn.check", enabled: store.canNormalize) {
                Task { await store.runAndroidNormalize() }
            }
        }
    }

    private var dropZone: some View {
        VStack(spacing: 10) {
            Image(systemName: "tray.and.arrow.down").font(.system(size: 30)).foregroundStyle(.secondary)
                .accessibilityHidden(true)
            Text(loc.t("s05.drop")).foregroundStyle(.secondary)
            Button(loc.t("s05.btn.choose")) { choose() }
                .controlSize(.large)
                .disabled(store.isBusy)
                .accessibilityIdentifier("btn.choose")
        }
        .frame(maxWidth: .infinity)
        .padding(22)
        .background(RoundedRectangle(cornerRadius: 12).strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6]))
            .foregroundStyle(dropTargeted ? Color.accentColor : Color.secondary.opacity(0.5)))
        .onDrop(of: [.fileURL], isTargeted: $dropTargeted) { providers in
            Task { await handleDrop(providers) }
            return true
        }
    }

    private func choose() {
        if store.deps.engine.kind.isMock {
            Task { await store.setAndroidFiles(store.demoAndroidFiles) }
            return
        }
        let urls = LocationPicker.chooseFiles()
        if !urls.isEmpty { Task { await store.setAndroidFiles(urls) } }
    }

    private func handleDrop(_ providers: [NSItemProvider]) async {
        var urls: [URL] = []
        for p in providers {
            if let item = try? await p.loadItem(forTypeIdentifier: UTType.fileURL.identifier),
               let data = item as? Data, let url = URL(dataRepresentation: data, relativeTo: nil) {
                urls.append(url)
            }
        }
        if !urls.isEmpty { await store.setAndroidFiles(store.androidFiles + urls) }
    }

    func dateText(ref: Int?) -> String {
        guard let ref, let f = store.inspect?.files.first(where: { $0.ref == ref }),
              let d = Formatters.parseTimestamp(f.createdAt) else { return "–" }
        return Formatters.date(d, lang: loc.language) + ", " + Formatters.time(d, lang: loc.language)
    }

    private func title(_ f: AndroidInspectResult.File) -> String {
        guard let d = Formatters.parseTimestamp(f.createdAt) else { return loc.t("s05.file.title_unknown") }
        return loc.t("s05.file.title", ["date": Formatters.date(d, lang: loc.language),
                                        "time": Formatters.time(d, lang: loc.language)])
    }

    private func fileList(_ i: AndroidInspectResult) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(i.files, id: \.ref) { f in
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Image(systemName: f.kind == "incomplete" ? "exclamationmark.triangle.fill" : "doc.fill")
                        .foregroundStyle(f.kind == "incomplete" ? Color.red : Color.accentColor)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 2) {
                        Text(title(f)).font(.headline)
                        Text(details(f, plan: i)).font(.callout).foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                    Button {
                        Task { await store.removeAndroidFile(at: f.ref) }
                    } label: { Image(systemName: "xmark.circle.fill") }
                    .buttonStyle(.borderless)
                    .disabled(store.isBusy)
                    .accessibilityLabel(loc.t("s05.btn.remove"))
                    .accessibilityIdentifier("btn.remove.\(f.ref)")
                }
                .padding(10)
                .background(Color.secondary.opacity(0.07), in: RoundedRectangle(cornerRadius: 10))
                .accessibilityElement(children: .contain)
            }
            if i.plan == "text_plus_media" {
                InfoBox(kind: .info, loc.t("s05.combination"))
            }
        }
        .accessibilityIdentifier("android.files")
    }

    private func details(_ f: AndroidInspectResult.File, plan: AndroidInspectResult) -> String {
        var parts = [loc.t(f.hasMedia ? "s05.file.with_media" : "s05.file.without_media"),
                     loc.t("common.gb", ["size": Formatters.gigabytes(f.bytes, lang: loc.language)])]
        if plan.textRef == f.ref { parts.append(loc.t("s05.file.recommended")) }
        else if plan.mediaRefs.contains(f.ref) { parts.append(loc.t("s05.file.media_source")) }
        return parts.joined(separator: " · ")
    }

    private var noMediaBox: some View {
        InfoBox(kind: .warning, text: AttributedString(loc.t("s05.no_media.text"))) {
            HStack(spacing: 12) {
                ChecklistToggle(text: loc.t("s05.no_media.text_only"), id: "text_only", isOn: $store.textOnlyConfirmed)
                Button(loc.t("s05.no_media.other")) { Task { await store.setAndroidFiles([]) } }
                    .accessibilityIdentifier("btn.other_backup")
            }
        }
    }

    private func passwordFields(_ i: AndroidInspectResult) -> some View {
        let refs = i.refsNeedingPassword
        return VStack(alignment: .leading, spacing: 8) {
            ForEach(refs, id: \.self) { ref in
                Text(refs.count > 1 ? loc.t("s05.password.for", ["date": dateText(ref: ref)]) : loc.t("s05.password.label"))
                    .font(.callout)
                    .fixedSize(horizontal: false, vertical: true)
                SecureField(loc.t("common.password"), text: Binding(
                    get: { store.androidPasswordInput[ref] ?? "" },
                    set: { store.androidPasswordInput[ref] = $0; if store.inlineError?.code == .E_ANDROID_PASSWORD { store.inlineError = nil } }))
                    .textFieldStyle(.roundedBorder)
                    .frame(maxWidth: 360)
                    .accessibilityLabel(loc.t("s05.password.label"))
                    .accessibilityIdentifier("field.android_password.\(ref)")
                    .onSubmit { Task { await store.runAndroidNormalize() } }
            }
        }
    }
}
