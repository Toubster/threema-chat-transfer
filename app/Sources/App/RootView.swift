// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// Window content: sidebar with the six phases, the current screen, sheets, dialogs and the DEMO watermark.
struct RootView: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc
    @State private var started = false

    var body: some View {
        WizardWindowContent()
        .sheet(item: $store.sheet) { sheet in
            SheetView(sheet: sheet)
                .environmentObject(store)
                .environment(\.loc, store.loc)
        }
        .modifier(DialogsModifier())
        .fileImporter(isPresented: $store.chooseLocationRequested, allowedContentTypes: [.folder]) { result in
            if case .success(let url) = result { Task { await store.chooseWorkdir(url) } }
        }
        .task {
            guard !started, !AppBootstrap.isUnitTestHost else { return }
            started = true
            DemoAutopilot.applyAppearance(store: store)
            await store.start(languageFromEnvironment: store.languageFixed)
            _ = DemoAutopilot.startIfRequested(store: store)
        }
    }
}

/// Sidebar + current screen + DEMO watermark (also rendered by the snapshot tests).
struct WizardWindowContent: View {
    @EnvironmentObject var store: WizardStore

    var body: some View {
        HStack(spacing: 0) {
            SidebarView(current: store.sidebarPhase)
            Divider()
            ScreenView(screen: store.screen)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Color(nsColor: .windowBackgroundColor))
                .id(store.screen)
        }
        .frame(minWidth: WindowMetrics.minWidth, minHeight: WindowMetrics.minHeight)
        .overlay { if store.deps.isDemo { DemoWatermark() } }
    }
}

/// The screen switch (S00–S23 and the F-screens).
struct ScreenView: View {
    let screen: Screen

    var body: some View {
        switch screen {
        case .s00: S00Welcome()
        case .s01: S01Disclaimer()
        case .s02: S02HostCheck()
        case .s03: S03DeviceCheck()
        case .s04: S04AndroidBackup()
        case .s05: S05ChooseBackup()
        case .s06: S06PreparingChats()
        case .s07: S07ThreemaIPhone()
        case .s08: S08IPhoneSettings()
        case .s09: S09SafetyNet()
        case .s10a: S10aSetPassword()
        case .s10b: S10bEnterPassword()
        case .s11: S11Offline()
        case .s12: S12Backup()
        case .s13: S13PrepareTransfer()
        case .s14: S14ReadyToTransfer()
        case .s15: S15Transfer()
        case .s16: S16AfterRestart()
        case .s17: S17CheckThreema()
        case .s18: S18ControlBackup()
        case .s19: S19Done()
        case .s20: S20CleanUp()
        case .s21: S21Stopped()
        case .s22: S22RestoreApple()
        case .s23: S23Resume()
        case .failure(let f): ErrorScreen(failure: f)
        }
    }
}

struct SheetView: View {
    let sheet: WizardStore.Sheet

    var body: some View {
        switch sheet {
        case .whatItDoes: WhatItDoesSheet()
        case .missingIds: MissingIdsSheet()
        case .diagReport: DiagReportView()
        case .help: HelpSheet()
        case .about: AboutView()
        case .passwordPrompt: PasswordPromptSheet()
        }
    }
}

/// Cancel, quit, discard and "reset Threema" confirmations (texts: cancel.*, quit.*, s23.discard.*, s21.r1.confirm.*).
struct DialogsModifier: ViewModifier {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    private func binding(_ d: WizardStore.Dialog) -> Binding<Bool> {
        Binding(get: { store.dialog == d }, set: { if !$0, store.dialog == d { store.dialog = nil } })
    }

    func body(content: Content) -> some View {
        content
            .alert(loc.t("cancel.title"), isPresented: binding(.cancel)) {
                Button(loc.t("cancel.later")) { Task { await store.confirmCancel(discard: false) } }
                    .accessibilityIdentifier("dialog.cancel.later")
                Button(loc.t("cancel.discard"), role: .destructive) { Task { await store.confirmCancel(discard: true) } }
                    .accessibilityIdentifier("dialog.cancel.discard")
                Button(loc.t("cancel.keep"), role: .cancel) { store.dialog = nil }
                    .accessibilityIdentifier("dialog.cancel.keep")
            } message: {
                Text(store.withStopReminder(loc.t("cancel.text"), loc))
            }
            .alert(loc.t("quit.title"), isPresented: binding(.quit)) {
                Button(loc.t("quit.confirm"), role: .destructive) { store.confirmQuit() }
                    .accessibilityIdentifier("dialog.quit.confirm")
                Button(loc.t("quit.stay"), role: .cancel) { store.dialog = nil }
                    .accessibilityIdentifier("dialog.quit.stay")
            } message: {
                Text(store.withStopReminder(loc.t("quit.text"), loc))
            }
            .alert(loc.t("quit.refused.title"), isPresented: binding(.quitRefused)) {
                Button(loc.t("common.ok"), role: .cancel) { store.dialog = nil }
                    .accessibilityIdentifier("dialog.quit_refused.ok")
            } message: {
                Text(loc.t("quit.refused.text"))
            }
            .alert(loc.t("s23.discard.title"), isPresented: binding(.discard)) {
                Button(loc.t("s23.discard.confirm"), role: .destructive) {
                    store.dialog = nil
                    store.discardSession()
                }
                .accessibilityIdentifier("dialog.discard.confirm")
                Button(loc.t("common.cancel"), role: .cancel) { store.dialog = nil }
                    .accessibilityIdentifier("dialog.discard.cancel")
            } message: {
                Text(store.withStopReminder(loc.t("s23.discard.text"), loc))
            }
            .alert(loc.t("s21.r1.confirm.title"), isPresented: binding(.resetThreema)) {
                Button(loc.t("action.reset_threema"), role: .destructive) {
                    store.dialog = nil
                    Task { await store.resetThreema() }
                }
                .accessibilityIdentifier("dialog.reset_threema.confirm")
                Button(loc.t("common.cancel"), role: .cancel) { store.dialog = nil }
                    .accessibilityIdentifier("dialog.reset_threema.cancel")
            } message: {
                Text(loc.t("s21.r1.confirm.text"))
            }
    }
}
