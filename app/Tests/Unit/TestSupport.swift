// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation
import XCTest
@testable import ThreemaChatTransfer

/// Paths of the repository checkout (tests read the scenario files and the sources from there).
enum Repo {
    static let appDir = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent()
    static let scenarios = appDir.appendingPathComponent("Tests/Scenarios")
    static let sources = appDir.appendingPathComponent("Sources")
    static let root = appDir.deletingLastPathComponent()

    static func scenario(_ name: String) throws -> ScenarioScript {
        try ScenarioScript.load(scenarios.appendingPathComponent("\(name).jsonl"))
    }

    static var scenarioNames: [String] {
        ((try? FileManager.default.contentsOfDirectory(atPath: scenarios.path)) ?? [])
            .filter { $0.hasSuffix(".jsonl") }.map { String($0.dropLast(6)) }.sorted()
    }
}

/// Test passwords. They must never show up in argv, session.json or the event log.
enum TestSecrets {
    static let android = "ZZ-android-pw-canary"
    static let backup = "ZZ-backup-pw-canary"
}

/// A controllable clock for countdown/freshness tests.
final class TestClock: @unchecked Sendable {
    var now: Date
    init(_ now: Date = Date()) { self.now = now }
}

/// One store over a MockEngine, a temporary sessions folder and in-memory keychain/power assertion.
@MainActor
final class StoreHarness {
    let root: URL
    let store: WizardStore
    let engine: MockEngine
    let power: FakePowerAssertion
    let keychain: InMemoryKeychain
    let clock: TestClock

    convenience init(scenario: String, language: AppLanguage = .de, afterRelaunch: Bool = false, root: URL? = nil,
                     clock: TestClock = TestClock(), keychain: InMemoryKeychain = InMemoryKeychain(),
                     autoAdvanceDelay: TimeInterval = 3600) throws {
        try self.init(script: Repo.scenario(scenario), language: language, afterRelaunch: afterRelaunch, root: root,
                      clock: clock, keychain: keychain, autoAdvanceDelay: autoAdvanceDelay)
    }

    init(script: ScenarioScript, language: AppLanguage = .de, afterRelaunch: Bool = false, root: URL? = nil,
         clock: TestClock = TestClock(), keychain: InMemoryKeychain = InMemoryKeychain(),
         autoAdvanceDelay: TimeInterval = 3600) throws {
        self.root = root ?? FileManager.default.temporaryDirectory
            .appendingPathComponent("tct-tests-\(UUID().uuidString)", isDirectory: true)
        self.clock = clock
        engine = MockEngine(script: script, speed: .instant, startAfterRelaunch: afterRelaunch, now: clock.now)
        power = FakePowerAssertion()
        self.keychain = keychain
        let sessions = SessionStore(root: self.root, excludeFromBackups: false)
        let deps = WizardStore.Dependencies(engine: engine, sessions: sessions, keychain: keychain, power: power,
                                            now: { clock.now }, appVersion: "0.4.0-test", isDemo: true,
                                            autoAdvanceDelay: autoAdvanceDelay, runsTimer: false)
        store = WizardStore(deps: deps, language: language)
    }

    /// Waits until no command runs any more and spawned tasks had a chance to start.
    func settle(timeout: TimeInterval = 10) async {
        let end = Date().addingTimeInterval(timeout)
        var quiet = 0
        while Date() < end {
            if store.runningCommand == nil && store.watchHandle == nil {
                quiet += 1
                if quiet >= 5 { return }
            } else {
                quiet = 0
            }
            try? await Task.sleep(nanoseconds: 3_000_000)
        }
    }

    var sessionDir: URL? { store.deps.sessions.current }

    /// Every file the app wrote into the session (session.json and logs) as one string.
    func appWrittenText() -> String {
        guard let dir = store.deps.sessions.current ?? lastSession else { return "" }
        var out = ""
        for rel in ["session.json", "logs/events.jsonl"] {
            if let s = try? String(contentsOf: dir.appendingPathComponent(rel), encoding: .utf8) { out += s }
        }
        return out
    }

    var lastSession: URL? {
        (try? FileManager.default.contentsOfDirectory(at: root, includingPropertiesForKeys: nil))?
            .first { $0.hasDirectoryPath }
    }
}

/// Plays the `{"mock":"user"}` directives of a scenario against the store (the unit-test twin of the XCUITest
/// driver). Screens without a directive (S02, S06, S12/S13, S15, S18, S23) are passed automatically.
@MainActor
struct StoreDriver {
    let h: StoreHarness
    var store: WizardStore { h.store }

    enum DriverError: Error, CustomStringConvertible {
        case wrongScreen(expected: String, actual: String)
        case unknownAnswer(String, String)
        var description: String {
            switch self {
            case .wrongScreen(let e, let a): return "expected screen \(e), app is on \(a)"
            case .unknownAnswer(let s, let a): return "no driver action for \(s)/\(a)"
            }
        }
    }

    /// Passes screens that need no scenario directive until `target` (or an error screen) is shown.
    func reach(_ target: String) async throws {
        for _ in 0..<12 {
            await h.settle()
            if store.sheet == .passwordPrompt {
                // after a restart the existing backup password is asked again (memory only)
                store.submitPasswordPrompt(TestSecrets.backup)
                continue
            }
            let id = store.screen.id
            if id == target { return }
            switch store.screen {
            case .s02 where store.host != nil: store.confirmHost()
            case .s02: await store.runHostCheck()
            case .s03 where store.device == nil: await store.runDeviceCheck()
            case .s06 where store.normalized != nil: store.confirmAndroid()
            case .s23 where store.resume != nil: store.continueResume()
            default:
                throw DriverError.wrongScreen(expected: target, actual: id)
            }
        }
        throw DriverError.wrongScreen(expected: target, actual: store.screen.id)
    }

    func perform(screen: String, answer: String) async throws {
        try await reach(screen)
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
            // the file choice is part of S05's start (recordings: android-inspect follows "S04 done")
            await store.setAndroidFiles(store.demoAndroidFiles)
        case ("S05", "password_entered"):
            if store.inspect == nil { await store.setAndroidFiles(store.demoAndroidFiles) }
            for r in store.inspect?.refsNeedingPassword ?? [] { store.androidPasswordInput[r] = TestSecrets.android }
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
            store.existingPasswordChoice = .known
            store.backupPasswordInput = TestSecrets.backup
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
            XCTAssertEqual(store.dialog, .resetThreema, "R1 asks before resetting")
            store.dialog = nil
            await store.resetThreema()
        case ("F-DCIM", "new_backup"), ("F-AIRPLANE", "new_backup"): store.perform(.newBackup)
        case ("F-FRESHNESS", "new_backup"): store.perform(.autoNewBackup)
        case ("F-PW-WRONG", "password_entered"):
            store.perform(.reenterPassword)
            for _ in 0..<200 where store.sheet != .passwordPrompt { try? await Task.sleep(nanoseconds: 2_000_000) }
            store.submitPasswordPrompt(TestSecrets.backup)
        case ("F-RESTORE-MID", "iphone_restarted"): store.perform(.continueS16)
        case ("S20", "delete_copies"): await store.cleanup("work")
        case ("S23", "continue"): store.continueResume()
        default: throw DriverError.unknownAnswer(screen, answer)
        }
        await h.settle()
    }
}

/// Replays the directives of a scenario until (and including) `stopAfter` screen.
@MainActor
func play(_ h: StoreHarness, _ script: ScenarioScript, through stopAfter: String) async throws {
    await h.store.start(languageFromEnvironment: true)
    for item in script.items {
        guard case .user(let screen, let answer) = item else { continue }
        try await StoreDriver(h: h).perform(screen: screen, answer: answer)
        if screen == stopAfter { return }
    }
}

/// Replays a whole scenario, including `relaunch` (a new store on the same sessions folder).
@MainActor
func playScenario(_ name: String, language: AppLanguage = .de,
                  onRelaunch: ((StoreHarness) -> Void)? = nil) async throws -> (StoreHarness, ScenarioScript) {
    let script = try Repo.scenario(name)
    var h = try StoreHarness(scenario: name, language: language)
    await h.store.start(languageFromEnvironment: true)
    for item in script.items {
        switch item {
        case .user(let screen, let answer):
            do {
                try await StoreDriver(h: h).perform(screen: screen, answer: answer)
            } catch {
                throw StoreDriver.DriverError.wrongScreen(expected: "\(screen) [\(name)]", actual: h.store.screen.id)
            }
        case .relaunch:
            onRelaunch?(h)
            let next = try StoreHarness(scenario: name, language: language, afterRelaunch: true, root: h.root,
                                        keychain: h.keychain)
            h = next
            await h.store.start(languageFromEnvironment: true)
            await h.settle()
        case .invoke:
            continue
        }
    }
    await h.settle()
    // the happy path ends with "Chat-Kopien jetzt löschen" on S20
    if h.store.screen == .s20, script.blocks.contains(where: { $0.cmd == EngineCommand.cleanup.rawValue }),
       !h.store.copiesDeleted {
        await h.store.cleanup("work")
    }
    if let expect = script.header.expectScreen, expect != h.store.screen.id {
        try await StoreDriver(h: h).reach(expect)
    }
    return (h, script)
}

/// Hand-written protocol lines for tracker tests.
enum Events {
    static func line(_ cmd: String, seq: Int, _ body: String) -> String {
        "{\"v\":1,\"seq\":\(seq),\"ts\":\"2026-01-01T09:00:00.000Z\",\"cmd\":\"\(cmd)\",\(body)}"
    }

    static func hello(_ cmd: String, version: String = "0.4.0-test", protocolVersion: Int = 1) -> String {
        line(cmd, seq: 1, "\"type\":\"hello\",\"engine_version\":\"\(version)\",\"protocol\":\(protocolVersion),\"pymobiledevice3\":\"11.19.4\",\"importer_version\":\"\(version)\",\"models\":[\"V56\"],\"compat_digest\":\"h:00000000\"")
    }

    static func critical(_ cmd: String, seq: Int, on: Bool) -> String {
        line(cmd, seq: seq, "\"type\":\"critical\",\"on\":\(on)")
    }

    static func result(_ cmd: String, seq: Int, ok: Bool, code: String, deviceModified: String = "no",
                       data: String = "{}") -> String {
        line(cmd, seq: seq, "\"type\":\"result\",\"ok\":\(ok),\"code\":\"\(code)\",\"retryable\":false,\"device_modified\":\"\(deviceModified)\",\"data\":\(data)")
    }
}
