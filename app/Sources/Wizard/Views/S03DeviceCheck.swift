// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S03 iPhone kurz prüfen / Quick iPhone check (`device-watch` + `device-status`, read-only).
/// Never shows the device name, only model and iOS build (DESIGN §8.2).
struct S03DeviceCheck: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    private var status: Activity? { store.activities[.deviceStatus] }

    var body: some View {
        ScreenScaffold(screen: .s03, title: loc.t("s03.title"), symbol: "iphone.gen3.radiowaves.left.and.right") {
            Paragraph(loc.t("s03.text"))
            if let e = store.inlineError, e.origin == .s03 || store.screen == .s03 {
                InfoBox(kind: .warning, title: loc.t(ErrorCatalog.titleKey(e.codeRaw)),
                        text: AttributedString(loc.t(ErrorCatalog.bodyKey(e.codeRaw),
                                                     ErrorCatalog.placeholders(codeRaw: e.codeRaw, data: e.data, loc: loc))))
                    .accessibilityIdentifier("inline.error")
            } else if store.device == nil {
                stateLine
            }
            if let d = store.device {
                card(d)
                if let code = store.iosUnverifiedCode {
                    InfoBox(kind: .danger, title: loc.t(ErrorCatalog.titleKey(code)),
                            loc.t(ErrorCatalog.bodyKey(code), ["ios_version": d.iosVersion, "ios_build": d.iosBuild]))
                        .accessibilityIdentifier("inline.ios_unknown")
                }
                rows(d)
            }
            if let built = AppInfo.buildDate, store.deps.now().timeIntervalSince(built) > 90 * 86_400 {
                HStack {
                    Text(loc.t("s03.update_hint")).foregroundStyle(.secondary)
                    Button(loc.t("action.see_versions")) { store.perform(.seeVersions) }.buttonStyle(.link)
                }
            }
        } footer: {
            if store.device == nil && !store.isBusy && store.watchHandle == nil {
                SecondaryButton(title: loc.t("action.retry"), id: "btn.retry") { Task { await store.runDeviceCheck() } }
            }
            if store.iosUnverifiedCode != nil {
                // S03 red: "Android-Teil vorbereiten" · "Neue Versionen ansehen", no "Weiter" (DESIGN §8.3 S03)
                SecondaryButton(title: loc.t("action.see_versions"), id: "btn.see_versions") { store.perform(.seeVersions) }
                Button(role: .destructive) { store.prepareAndroidOnly() } label: {
                    Text(loc.t("action.prepare_android")).frame(minWidth: 90)
                }
                .buttonStyle(.borderedProminent)
                .tint(.red)
                .controlSize(.large)
                .fixedSize()
                .disabled(store.isBusy)
                .accessibilityIdentifier("btn.prepare_android")
            } else {
                PrimaryButton(title: loc.t("common.continue"), id: "btn.continue",
                              enabled: store.device?.isVerified == true && !store.isBusy) { store.confirmDevice() }
            }
        }
        .task { if store.device == nil && !store.isBusy && store.watchHandle == nil { await store.runDeviceCheck() } }
    }

    @ViewBuilder private var stateLine: some View {
        let key: String = {
            switch store.deviceState {
            case .locked: return "s03.state.locked"
            case .untrusted: return "s03.state.untrusted"
            case .ready: return "s03.state.ready"
            case .multiple: return "code.E_DEV_MULTIPLE.title"
            case .disconnected: return "code.E_DEV_DISCONNECTED.title"
            default: return "s03.state.none"
            }
        }()
        HStack(spacing: 10) {
            if store.isBusy || store.watchHandle != nil { ProgressView().controlSize(.small) }
            Text(loc.t(key)).font(.headline)
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("device.state")
    }

    private func card(_ d: DeviceStatus) -> some View {
        HStack(spacing: 14) {
            Image(systemName: "iphone").font(.system(size: 34))
                .foregroundStyle(store.iosUnverifiedCode == nil ? Color.accentColor : Color.red).accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 3) {
                Text(loc.t("s03.card.model", ["model": d.productType])).font(.headline)
                Text(loc.t("s03.card.ios", ["version": d.iosVersion, "build": d.iosBuild]))
                if d.isVerified && store.iosUnverifiedCode == nil {
                    Label(loc.t("s03.card.tested"), systemImage: "checkmark.seal.fill")
                        .foregroundStyle(.green).font(.callout)
                }
            }
            Spacer(minLength: 0)
        }
        .padding(14)
        .background(Color.secondary.opacity(0.08), in: RoundedRectangle(cornerRadius: 12))
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("device.card")
    }

    private func rows(_ d: DeviceStatus) -> some View {
        let need = status?.check(.iphoneSpace)?.data?["need_bytes"]?.int64
        let spaceStatus = status?.check(.iphoneSpace)?.status ?? .pass
        return VStack(alignment: .leading, spacing: 8) {
            if d.threema.installed {
                CheckRowView(label: loc.t("s03.row.threema_found", ["version": d.threema.version ?? "–"]),
                             status: d.threema.variant == "regular" ? .pass : .warn, id: "threema")
            } else {
                CheckRowView(label: loc.t("s03.row.threema_missing"), status: .warn, id: "threema")
            }
            CheckRowView(label: loc.t("s03.row.space", [
                "free": Formatters.gigabytes(d.freeBytes, lang: loc.language),
                "need": need.map { Formatters.gigabytes($0, lang: loc.language) } ?? "–"]),
                status: spaceStatus, id: "iphone_space")
            CheckRowView(label: loc.t("s03.row.battery", ["p": d.batteryPct.map(String.init) ?? "–"]),
                         status: status?.check(.battery)?.status ?? .pass, id: "battery")
            CheckRowView(label: loc.t("s03.row.unmanaged"), status: d.managed ? .fail : .pass, id: "managed")
        }
    }
}
