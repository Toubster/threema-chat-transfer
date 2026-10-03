// SPDX-License-Identifier: AGPL-3.0-or-later
import AppKit
import SwiftUI

/// Entry point. One window (the wizard); the engine, sessions, keychain and power assertion are chosen from the
/// environment (`AppEnvironment`): `live` uses the bundled `tmcore`, `fake:`/`mock:` are demo runs with a DEMO
/// watermark, an in-memory keychain and their own sessions folder.
@main
struct ThreemaChatTransferApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var store: WizardStore

    init() {
        let store = AppBootstrap.makeStore()
        _store = StateObject(wrappedValue: store)
        AppDelegate.store = store
    }

    var body: some Scene {
        Window(AppInfo.productName, id: "main") {
            RootView()
                .environmentObject(store)
                .environment(\.loc, store.loc)
                .environment(\.locale, store.language.locale)
        }
        .defaultSize(width: WindowMetrics.minWidth + 120, height: WindowMetrics.minHeight + 120)
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .appInfo) {
                Button(store.loc.t("menu.about")) { store.sheet = .about }
            }
            CommandGroup(replacing: .newItem) {}
            CommandGroup(replacing: .saveItem) {
                // ⌘W follows the same rules as ⌘Q (DESIGN §8.1): confirm from S11, refused during `critical`.
                Button(store.loc.t("menu.close_window")) { AppDelegate.closeRequested() }
                    .keyboardShortcut("w")
            }
            CommandGroup(replacing: .help) {
                Button(store.loc.t("help.title")) { store.sheet = .help }
            }
        }
    }
}

/// Builds the store from the environment (kept out of the App struct so unit tests do not need it).
@MainActor
enum AppBootstrap {
    /// The app is only the host of the unit-test bundle: no engine, no real sessions folder, no start.
    static var isUnitTestHost: Bool { ProcessInfo.processInfo.environment["XCTestConfigurationFilePath"] != nil }

    static func makeStore(env: AppEnvironment = .current()) -> WizardStore {
        if isUnitTestHost {
            let root = FileManager.default.temporaryDirectory.appendingPathComponent("tct-testhost-\(UUID().uuidString)")
            let deps = WizardStore.Dependencies(engine: RefusingEngine(), sessions: SessionStore(root: root, excludeFromBackups: false),
                                                keychain: InMemoryKeychain(), power: FakePowerAssertion(), isDemo: true,
                                                runsTimer: false)
            return WizardStore(deps: deps, language: env.language)
        }
        var engine: EngineClient
        var startupProblem: String?
        do {
            engine = try env.makeEngine()
        } catch {
            // A mock scenario that cannot be loaded never falls back to the real engine: the app stops (F-INTERNAL).
            engine = RefusingEngine()
            startupProblem = "engine_config"
        }
        let live = engine.kind == .live
        let sessions = SessionStore(root: env.sessionsRoot(for: engine.kind), excludeFromBackups: live)
        let keychain: KeychainStoring = live ? KeychainStore() : InMemoryKeychain()
        let power: PowerAsserting = engine.kind.isMock ? FakePowerAssertion() : PowerAssertion()
        let deps = WizardStore.Dependencies(
            engine: engine, sessions: sessions, keychain: keychain, power: power,
            openURL: { NSWorkspace.shared.open($0) }, isDemo: env.isDemo || !live,
            autoAdvanceDelay: engine.kind.isMock && env.mockSpeed != .demo ? 0.3 : 3)
        let store = WizardStore(deps: deps, language: env.language)
        store.languageFixed = env.languageFromEnvironment
        store.startupProblem = startupProblem
        return store
    }
}

/// Used when the configured engine cannot be built: every command fails with E_INTERNAL, nothing is started.
final class RefusingEngine: EngineClient {
    let kind: EngineKind = .mock(scenario: "invalid")
    let requiresMatchingEngineVersion = false
    let writesEventLog = true

    func start(_ invocation: EngineInvocation) -> EngineHandle {
        var block = MockEngine.fallbackBlock(for: invocation)
        block.lines = [block.lines[0], "{\"v\":1,\"seq\":2,\"ts\":\"2026-01-01T09:00:00.000Z\",\"cmd\":\"\(invocation.command.rawValue)\",\"type\":\"result\",\"ok\":false,\"code\":\"E_INTERNAL\",\"retryable\":false,\"device_modified\":\"no\",\"data\":{\"sub\":\"engine_config\"}}"]
        block.exitCode = 2
        return MockHandle(block: block, speed: .instant, offset: 0)
    }
}
