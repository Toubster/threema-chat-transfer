// SPDX-License-Identifier: AGPL-3.0-or-later
import AppKit
import Combine
import Foundation

/// Demo autopilot (DESIGN §13.1 level 7): drives the REAL app through a scenario without UI automation and saves the
/// window of every wanted screen as PNG. It plays the `{"mock":"user"}` directives of the scenario recording
/// (`Scenarios/<name>.jsonl`) against the store, exactly like the unit-test driver and the XCUITest driver do.
///
/// Only for demo runs: it refuses to start with the live engine (`TM_ENGINE=live`), so it can never act on a real
/// iPhone. With `fake:<scenario>` the real `tmcore` runs on the virtual iPhone; with `mock:<scenario>` the MockEngine.
///
/// | Variable | Meaning |
/// |---|---|
/// | `TM_AUTOPILOT=1` | switch it on (needs `TM_ENGINE=fake:<s>` or `mock:<s>`) |
/// | `TM_AUTOPILOT_ANDROID_FILES` | JSON array of synthetic Android backup files (fake runs; mock: dummy names) |
/// | `TM_AUTOPILOT_AFTER_RELAUNCH=1` | second app start of a scenario with a `relaunch` marker |
/// | `TM_AUTOPILOT_REPORT` | JSON report: end screen, captured / missing screenshots, error |
/// | `TM_SCREENSHOT_DIR`, `TM_SCREENSHOT_IDS` | output folder and the wanted ids (`S00`, `S21-data`, `F-DCIM`, …) |
/// | `TM_DEMO_FIXTURES` | `fixtures/` of a checkout: synthetic passwords (canaries.json); passed to the engine |
///
/// Exit codes: 0 done, 75 = `relaunch` marker reached (the caller starts the app again with
/// `TM_AUTOPILOT_AFTER_RELAUNCH=1`), 3 = the scenario could not be played (see the report).
@MainActor
final class DemoAutopilot {
    struct Config {
        var scenario: String
        var androidFiles: [URL]?
        var afterRelaunch: Bool
        var report: URL?
        var shotsDir: URL?
        var wanted: Set<String>
        var fixtures: URL?

        static func from(_ env: [String: String], engineSpec: String) -> Config? {
            guard env["TM_AUTOPILOT"] == "1" else { return nil }
            let name: String
            if engineSpec.hasPrefix("fake:") { name = String(engineSpec.dropFirst(5)) }
            else if engineSpec.hasPrefix("mock:") { name = String(engineSpec.dropFirst(5)) }
            else { return nil }                          // never with the live engine
            var files: [URL]?
            if let j = env["TM_AUTOPILOT_ANDROID_FILES"], let d = j.data(using: .utf8),
               let arr = try? JSONSerialization.jsonObject(with: d) as? [String] {
                files = arr.map { URL(fileURLWithPath: $0) }
            }
            let ids = (env["TM_SCREENSHOT_IDS"] ?? "").split(separator: ",").map {
                $0.trimmingCharacters(in: .whitespaces)
            }.filter { !$0.isEmpty }
            return Config(scenario: name, androidFiles: files, afterRelaunch: env["TM_AUTOPILOT_AFTER_RELAUNCH"] == "1",
                          report: env["TM_AUTOPILOT_REPORT"].map { URL(fileURLWithPath: $0) },
                          shotsDir: env["TM_SCREENSHOT_DIR"].map { URL(fileURLWithPath: $0, isDirectory: true) },
                          wanted: Set(ids), fixtures: env["TM_DEMO_FIXTURES"].map { URL(fileURLWithPath: $0) })
        }
    }

    enum Failure: Error, CustomStringConvertible {
        case wrongScreen(expected: String, actual: String)
        case unknownAnswer(String, String)
        case noScript
        case timeout(String)
        var description: String {
            switch self {
            case .wrongScreen(let e, let a): return "expected \(e), app is on \(a)"
            case .unknownAnswer(let s, let a): return "no autopilot action for \(s)/\(a)"
            case .noScript: return "scenario recording not found"
            case .timeout(let s): return "timeout on \(s)"
            }
        }
    }

    static let wrongEntry = "ZZ-falsches-Passwort-0"   // synthetic, the e2e flow's wrong entry

    let store: WizardStore
    let cfg: Config
    private var pending: Set<String>
    private var captured: [String] = []
    private var sink: AnyCancellable?
    private var screensSeen: [String] = []

    init(store: WizardStore, cfg: Config) {
        self.store = store
        self.cfg = cfg
        self.pending = cfg.wanted
    }

    /// The synthetic password of the fixtures (Android backups, virtual iPhone with encryption on).
    var rightPassword: String {
        if let f = cfg.fixtures,
           let d = try? Data(contentsOf: f.appendingPathComponent("canaries.json")),
           let o = try? JSONSerialization.jsonObject(with: d) as? [String: Any], let p = o["password"] as? String {
            return p
        }
        return "ZZ-Demo-Sicherung-01"
    }

    // MARK: - screenshots

    /// The id of the current screen in the screenshot list (S21 per variant, DESIGN §15).
    func shotID(_ screen: Screen) -> String {
        guard screen == .s21 else { return screen.id }
        switch store.stopVariant {
        case .r1: return "S21-threema_only"
        case .r2: return "S21-data"
        case .r2k: return "S21-data_keychain"
        case .r4: return "S21-setup_full"
        }
    }

    /// Saves the window (title bar included) when `screen` is still wanted. The view hierarchy is laid out and drawn
    /// right before the copy, so the picture shows the current state even when the window is in the background.
    func capture(_ screen: Screen? = nil, id explicit: String? = nil) {
        let id = explicit ?? shotID(screen ?? store.screen)
        guard pending.contains(id), let dir = cfg.shotsDir else { return }
        guard let window = NSApp.windows.first(where: { $0.isVisible && $0.canBecomeMain }),
              let content = window.contentView else { return }
        let view = content.superview ?? content          // the frame view draws the title bar too
        content.layoutSubtreeIfNeeded()
        view.layoutSubtreeIfNeeded()
        view.display()
        guard let rep = view.bitmapImageRepForCachingDisplay(in: view.bounds) else { return }
        view.cacheDisplay(in: view.bounds, to: rep)
        guard let png = rep.representation(using: .png, properties: [:]) else { return }
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        if (try? png.write(to: dir.appendingPathComponent("\(id).png"))) != nil {
            pending.remove(id)
            captured.append(id)
        }
    }

    /// Lets SwiftUI apply and draw the latest state, then captures (async call sites only).
    func captureSettled(_ screen: Screen? = nil, id explicit: String? = nil) async {
        guard pending.contains(explicit ?? shotID(screen ?? store.screen)) else { return }
        try? await Task.sleep(nanoseconds: 250_000_000)
        capture(screen, id: explicit)
    }

    /// Makes the window as tall as the screen's content (nothing to scroll) and returns the previous content size: the
    /// shot of help that unfolds below the fold (S10b-unknown) shows the whole screen. The content scroll view is the
    /// one with the tallest document.
    func growWindowToContent() -> NSSize? {
        guard let window = NSApp.windows.first(where: { $0.isVisible && $0.canBecomeMain }),
              let root = window.contentView else { return nil }
        func scrollViews(_ v: NSView) -> [NSScrollView] {
            ((v as? NSScrollView).map { [$0] } ?? []) + v.subviews.flatMap(scrollViews)
        }
        root.layoutSubtreeIfNeeded()
        guard let sv = scrollViews(root).max(by: {
            ($0.documentView?.frame.height ?? 0) < ($1.documentView?.frame.height ?? 0)
        }), let doc = sv.documentView else { return nil }
        let room = max(0, doc.frame.height - sv.contentView.bounds.height)
        let old = root.frame.size
        guard room > 0 else { return nil }
        window.setContentSize(NSSize(width: old.width, height: min(old.height + room + 40, 1600)))
        root.layoutSubtreeIfNeeded()
        return old
    }

    // MARK: - run

    func run() async {
        let script: ScenarioScript
        do {
            script = try loadScript()
        } catch {
            finish(error: Failure.noScript, exit: 3)
            return
        }
        // "capture on leave" for the screens the app passes by itself (S12 → S13 → S14 happen in one flow): the
        // publisher fires before the change, so the window still shows the outgoing screen with its final state.
        sink = store.$screen.sink { [weak self] next in
            guard let self, next != self.store.screen else { return }
            self.screensSeen.append(next.id)
            self.capture(self.store.screen)
        }
        var items = script.items
        if cfg.afterRelaunch, let i = items.firstIndex(where: { if case .relaunch = $0 { return true }; return false }) {
            items = Array(items[(i + 1)...])
        }
        do {
            for (n, item) in items.enumerated() {
                switch item {
                case .user(let screen, let answer):
                    let later = items[(n + 1)...]
                    try await perform(screen: screen, answer: answer, later: later)
                case .relaunch:
                    await settle()
                    await captureSettled()
                    finish(error: nil, exit: 75)
                    return
                case .invoke:
                    continue
                }
            }
            await settle()
            if store.screen == .s20, script.blocks.contains(where: { $0.cmd == EngineCommand.cleanup.rawValue }),
               !store.copiesDeleted {
                await captureSettled()
                await store.cleanup("work")
                await settle()
            }
            if let expect = script.header.expectScreen, expect != store.screen.id { try await reach(expect) }
            await captureSettled()
            // S22 is the guide behind S21 R2 "Anleitung: Apple-Sicherung zurückspielen"
            if pending.contains("S22"), store.screen == .s21 {
                store.go(.s22)
                await settle()
                await captureSettled()
            }
            finish(error: nil, exit: 0)
        } catch {
            await captureSettled()
            finish(error: error, exit: 3)
        }
    }

    private func loadScript() throws -> ScenarioScript {
        let env = ProcessInfo.processInfo.environment
        let dir = env["TM_SCENARIOS_DIR"].map { URL(fileURLWithPath: $0) }
            ?? Bundle.main.resourceURL?.appendingPathComponent("Scenarios")
        guard let url = dir?.appendingPathComponent("\(cfg.scenario).jsonl") else { throw Failure.noScript }
        return try ScenarioScript.load(url)
    }

    private func finish(error: Error?, exit code: Int32) {
        sink = nil
        if let r = cfg.report {
            let obj: [String: Any] = ["scenario": cfg.scenario, "end_screen": shotID(store.screen),
                                      "captured": captured, "missing": pending.sorted(),
                                      "screens": screensSeen, "exit": Int(code),
                                      "internal_reason": store.internalReason ?? NSNull(),
                                      "last_code": store.error?.codeRaw ?? NSNull(),
                                      "error": error.map { String(describing: $0) } ?? NSNull()]
            if let d = try? JSONSerialization.data(withJSONObject: obj, options: [.prettyPrinted, .sortedKeys]) {
                try? d.write(to: r)
            }
        }
        store.prepareForTermination()
        Darwin.exit(code)
    }

    /// No command and no device watch for 250 ms in a row (the next step of a flow starts without a gap).
    func settle(timeout: TimeInterval = 180) async {
        let end = Date().addingTimeInterval(timeout)
        var quiet = 0
        while Date() < end {
            if store.runningCommand == nil && store.watchHandle == nil {
                quiet += 1
                if quiet >= 25 { return }
            } else {
                quiet = 0
            }
            try? await Task.sleep(nanoseconds: 10_000_000)
        }
    }

    /// Passes the screens that need no directive (S02, S03, S06, S23, password prompt) until `target` is shown.
    func reach(_ target: String) async throws {
        for _ in 0..<16 {
            await settle()
            if store.sheet == .passwordPrompt {
                store.submitPasswordPrompt(rightPassword)     // after a restart the password is asked again
                continue
            }
            if store.screen.id == target { return }
            await captureSettled()
            switch store.screen {
            case .s02 where store.host != nil: store.confirmHost()
            case .s02: await store.runHostCheck()
            case .s03 where store.device == nil: await store.runDeviceCheck()
            case .s06 where store.normalized != nil: store.confirmAndroid()
            case .s23 where store.resume != nil: store.continueResume()
            default: throw Failure.wrongScreen(expected: target, actual: store.screen.id)
            }
        }
        throw Failure.wrongScreen(expected: target, actual: store.screen.id)
    }

    private func androidFiles() -> [URL] { cfg.androidFiles ?? store.demoAndroidFiles }

    private func perform(screen: String, answer: String, later: ArraySlice<ScenarioScript.Item>) async throws {
        // F-FRESHNESS starts the new backup by itself after a short pause; when the app was faster than the
        // directive, the click is simply not needed any more
        if screen == "F-FRESHNESS" {
            if store.screen == .failure(.freshness) {
                capture()
                store.perform(.autoNewBackup)       // no-op when the automatic start was first
                await settle()
                return
            }
            if screensSeen.contains("F-FRESHNESS") {
                await settle()
                return
            }
        }
        try await reach(screen)
        func comesAgain(_ s: String, _ a: String) -> Bool {
            later.contains { if case .user(let s2, let a2) = $0 { return s2 == s && a2 == a }; return false }
        }
        if screen == "S10b" {
            // the decision screen in both states (DESIGN §15): "Ich weiß es nicht" with its help, in a window tall
            // enough for the whole help; then "Ich kenne das Passwort" (the S10b shot), as the user continues
            if pending.contains("S10b-unknown") {
                store.existingPasswordChoice = .unknown
                try? await Task.sleep(nanoseconds: 250_000_000)
                let size = growWindowToContent()
                await captureSettled(id: "S10b-unknown")
                if let size, let w = NSApp.windows.first(where: { $0.isVisible && $0.canBecomeMain }) {
                    w.setContentSize(size)
                }
            }
            store.existingPasswordChoice = .known
        }
        if screen != "S05" { await captureSettled() }
        switch (screen, answer) {
        case ("S00", "get_started"): store.getStarted()
        case ("S01", "accepted"):
            store.disclaimerAccepted = true
            store.acceptDisclaimer()
        case ("S03", "continue"):
            if store.device == nil { await store.runDeviceCheck() }
            store.confirmDevice()
        case ("S03", "prepare_android"):
            if store.device == nil { await store.runDeviceCheck() }
            store.prepareAndroidOnly()
        case ("S04", "done"):
            store.go(.s05)
            await store.setAndroidFiles(androidFiles())
        case ("S05", "password_entered"):
            if store.inspect == nil { await store.setAndroidFiles(androidFiles()) }
            await settle()
            await captureSettled()
            // a scenario that enters the Android password twice types the wrong one first
            let pw = comesAgain("S05", "password_entered") ? Self.wrongEntry : rightPassword
            for r in store.inspect?.refsNeedingPassword ?? [] { store.androidPasswordInput[r] = pw }
            store.textOnlyConfirmed = true
            await store.runAndroidNormalize()
        case ("S07", "all_checked"):
            for i in store.threemaItemsNeeded { store.toggle(\.threemaChecklist, i, true) }
            await store.confirmThreema()
        case ("S08", "all_checked"):
            for i in WizardStore.settingsItems { store.toggle(\.settingsChecklist, i, true) }
            await store.confirmSettings()
        case ("S09", "icloud"), ("S09", "finder"):
            store.safetyNetChoice = answer
            store.safetyNetConfirmed = true
            await store.confirmSafetyNet()
        case ("S10b", "password_entered"):
            store.backupPasswordInput = comesAgain("F-PW-WRONG", "password_entered") ? Self.wrongEntry : rightPassword
            store.confirmExistingPassword()
        case ("S10a", _):
            store.prepareGeneratedPassword()
            store.lastFourInput = String(store.generatedPassword.suffix(4))
            await store.enableEncryption()
        case ("S11", "all_checked"):
            for i in WizardStore.offlineItems { store.toggle(\.offlineChecklist, i, true) }
            store.startBackup()
        case ("S14", "transfer_now"):
            store.transferChecks = [true, true]
            await store.transferNow()
        case ("S16", _):
            store.answerBuddy(answer)
            store.confirmAfterRestart()
        case ("S17", "old_chats_present"): store.threemaChecked(ok: true)
        case ("S17", "problem"): store.threemaChecked(ok: false)
        case ("S19", "continue"): store.finishSuccess()
        case ("S21", "reset_threema"):
            store.perform(.resetThreema)
            store.dialog = nil
            await store.resetThreema()
        case ("F-DCIM", "new_backup"), ("F-AIRPLANE", "new_backup"): store.perform(.newBackup)
        case ("F-FRESHNESS", "new_backup"): store.perform(.autoNewBackup)
        case ("F-PW-WRONG", "password_entered"):
            store.perform(.reenterPassword)
            for _ in 0..<500 where store.sheet != .passwordPrompt { try? await Task.sleep(nanoseconds: 2_000_000) }
            store.submitPasswordPrompt(rightPassword)
        case ("F-RESTORE-MID", "iphone_restarted"): store.perform(.continueS16)
        case ("S20", "delete_copies"): await store.cleanup("work")
        case ("S23", "continue"): store.continueResume()
        default: throw Failure.unknownAnswer(screen, answer)
        }
        await settle()
    }
}

extension DemoAutopilot {
    /// `TM_APPEARANCE=light|dark` fixes the appearance of demo runs (screenshots); ignored with the live engine.
    static func applyAppearance(store: WizardStore, env: AppEnvironment = .current()) {
        guard store.deps.engine.kind != .live, store.deps.isDemo else { return }
        switch env.appearance {
        case "light": NSApp.appearance = NSAppearance(named: .aqua)
        case "dark": NSApp.appearance = NSAppearance(named: .darkAqua)
        default: break
        }
    }

    /// Starts the autopilot when the environment asks for it (demo runs only); returns false otherwise.
    static func startIfRequested(store: WizardStore, env: AppEnvironment = .current()) -> Bool {
        guard store.deps.engine.kind != .live,
              let cfg = Config.from(ProcessInfo.processInfo.environment, engineSpec: env.engineSpec) else { return false }
        let pilot = DemoAutopilot(store: store, cfg: cfg)
        if let size = ProcessInfo.processInfo.environment["TM_SCREENSHOT_SIZE"],
           let w = Double(size.split(separator: "x").first ?? ""), let h = Double(size.split(separator: "x").last ?? ""),
           let window = NSApp.windows.first(where: { $0.isVisible && $0.canBecomeMain }) {
            window.setContentSize(NSSize(width: max(w, WindowMetrics.minWidth), height: max(h, WindowMetrics.minHeight)))
        }
        NSApp.activate(ignoringOtherApps: true)
        NSApp.windows.first(where: { $0.isVisible && $0.canBecomeMain })?.orderFrontRegardless()
        Task { @MainActor in
            await pilot.run()
        }
        return true
    }
}
