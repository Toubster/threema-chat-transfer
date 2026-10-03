// SPDX-License-Identifier: AGPL-3.0-or-later
import Combine
import XCTest
@testable import ThreemaChatTransfer

/// The wizard state machine against every mock scenario (DESIGN §8.1, §13.1 level 4).
@MainActor
final class WizardStoreTests: XCTestCase {
    // MARK: every scenario ends on its expected screen and calls the engine exactly as recorded

    func testEveryScenarioReachesItsExpectedScreen() async throws {
        XCTAssertFalse(Repo.scenarioNames.isEmpty)
        for name in Repo.scenarioNames {
            for lang in AppLanguage.allCases {
                let (h, script) = try await playScenario(name, language: lang)
                XCTAssertEqual(h.store.screen.id, script.header.expectScreen, "\(name) [\(lang)]")
                XCTAssertFalse(h.store.critical, "\(name): critical must be off at the end")
                XCTAssertEqual(h.power.count, 0, "\(name): power assertion released")
            }
        }
    }

    func testInvocationsMatchTheRecordedScenario() async throws {
        for name in Repo.scenarioNames {
            var logs: [[EngineInvocation]] = []
            let (h, script) = try await playScenario(name) { old in logs.append(old.engine.log) }
            logs.append(h.engine.log)
            let calls = logs.flatMap { $0 }
            let recorded = script.blocks
            XCTAssertEqual(calls.map(\.command.rawValue), recorded.map(\.cmd), "\(name): command sequence")
            for (call, block) in zip(calls, recorded) {
                XCTAssertEqual(call.secrets?.fieldNames.sorted() ?? [], block.secrets.sorted(),
                               "\(name) \(block.cmd): stdin secret fields")
                XCTAssertEqual(call.args.count, block.args.count, "\(name) \(block.cmd): argv \(call.args)")
                for (a, b) in zip(call.args, block.args) {
                    if b.hasPrefix("<") && b.hasSuffix(">") { continue }   // <session>, <workdir>, <android-backup-N>
                    XCTAssertEqual(a, b, "\(name) \(block.cmd): argv")
                }
                XCTAssertFalse(call.args.contains { $0.contains(TestSecrets.android) || $0.contains(TestSecrets.backup) },
                               "\(name): a password in argv")
            }
        }
    }

    func testPasswordsNeverReachTheSessionFolder() async throws {
        for name in Repo.scenarioNames {
            let (h, _) = try await playScenario(name)
            let text = h.appWrittenText()
            XCTAssertFalse(text.contains(TestSecrets.android), "\(name): Android password written")
            XCTAssertFalse(text.contains(TestSecrets.backup), "\(name): backup password written")
            XCTAssertNil(h.store.secrets.androidPasswords.first, "\(name): Android passwords kept after normalize")
        }
    }

    func testHappyFlowScreensAndResults() async throws {
        let (h, _) = try await playScenario("happy")
        XCTAssertEqual(h.store.screen, .s20)
        XCTAssertGreaterThan(h.store.normalized?.messages ?? 0, 0)
        XCTAssertEqual(h.store.postcheck?.verdictValue, .ok)
        XCTAssertTrue(h.store.copiesDeleted)
        XCTAssertGreaterThan(h.power.maxCount, 0, "power assertion during the transfer window")
        // session.json follows session.v1: no password fields, answers recorded
        let s = try XCTUnwrap(h.store.deps.sessions.state)
        XCTAssertEqual(s.answers.passwordSource, "generated", "happy: encryption switched on by the tool")
        XCTAssertEqual(s.answers.passwordInKeychain, true)
        XCTAssertNotNil(try h.keychain.load(), "the generated password is in the (test) keychain")
        XCTAssertEqual(s.answers.buddyAnswer, "account_only")
        XCTAssertNotNil(s.answers.transferConfirmedAt)
    }

    func testWrongAndroidPasswordStaysInlineOnS05() async throws {
        let h = try StoreHarness(scenario: "wrong_android_password")
        let d = StoreDriver(h: h)
        try await play(h, try Repo.scenario("wrong_android_password"), through: "S05")
        XCTAssertEqual(h.store.screen, .s05)
        XCTAssertEqual(h.store.inlineError?.code, .E_ANDROID_PASSWORD)
        XCTAssertTrue(h.store.androidPasswordInput.isEmpty, "the wrong password is cleared")
        try await d.perform(screen: "S05", answer: "password_entered")
        try await d.reach("S07")
    }

    // MARK: back-lock, cancel, quit (DESIGN §8.1)

    func testBackIsFreeUntilS10AndLockedFromS11() async throws {
        let h = try StoreHarness(scenario: "happy")
        let s = h.store
        s.go(.s04); s.go(.s05)
        XCTAssertTrue(s.canGoBack)
        s.go(.s10b)
        XCTAssertTrue(s.canGoBack)
        s.go(.s11)
        XCTAssertFalse(s.canGoBack, "S11 starts the time window")
        s.go(.s14)
        XCTAssertFalse(s.canGoBack)
    }

    func testCancelUntilS14AndQuitRules() async throws {
        let h = try StoreHarness(scenario: "happy")
        let s = h.store
        try h.store.deps.sessions.create { SessionState(sessionId: $0, now: Date(), appVersion: "t", locale: .de) }
        s.go(.s05)
        XCTAssertTrue(s.canCancel)
        XCTAssertEqual(s.quitDecision, .allow)
        s.go(.s11)
        XCTAssertTrue(s.canCancel)
        XCTAssertEqual(s.quitDecision, .confirm)
        s.go(.s14)
        XCTAssertTrue(s.canCancel)
        s.go(.s16)
        XCTAssertFalse(s.canCancel, "nothing to cancel after sending")
        XCTAssertEqual(s.quitDecision, .confirm)
        s.go(.s20)
        XCTAssertEqual(s.quitDecision, .allow)
    }

    func testCriticalLocksCancelAndQuit() async throws {
        let h = try StoreHarness(scenario: "happy")
        let s = h.store
        try s.deps.sessions.create { SessionState(sessionId: $0, now: Date(), appVersion: "t", locale: .de) }
        s.go(.s14)
        s.setCritical(true)
        XCTAssertFalse(s.canCancel)
        XCTAssertFalse(s.canGoBack)
        XCTAssertEqual(s.quitDecision, .refuse)
        XCTAssertFalse(s.requestQuit())
        XCTAssertEqual(s.dialog, .quitRefused)
        XCTAssertTrue(h.power.isHeld, "the Mac stays awake while critical")
        s.confirmQuit()
        XCTAssertEqual(s.dialog, .quitRefused, "confirming quit during critical is refused again")
        s.setCritical(false)
        XCTAssertFalse(h.power.isHeld)
    }

    func testRestoreShowsS15WhileCriticalAndEndsOnS16() async throws {
        var sawS15 = false
        let h = try StoreHarness(scenario: "happy")
        let cancellable = h.store.$screen.sink { if $0 == .s15 { sawS15 = true } }
        defer { cancellable.cancel() }
        try await play(h, try Repo.scenario("happy"), through: "S14")
        XCTAssertTrue(sawS15)
        XCTAssertEqual(h.store.screen, .s16)
        XCTAssertFalse(h.store.critical)
    }

    func testCrashDuringCriticalResumesAtS16NeverStartOver() async throws {
        var screenBeforeRelaunch: Screen?
        var resumeAfterRelaunch: ResumePolicy.Decision?
        let script = try Repo.scenario("app_crash_after_send")
        let first = try StoreHarness(scenario: "app_crash_after_send")
        try await play(first, script, through: "S14")
        await first.settle()
        screenBeforeRelaunch = first.store.screen
        XCTAssertEqual(screenBeforeRelaunch, .s16, "missing result after critical → S16 (DESIGN §5.6)")
        let second = try StoreHarness(scenario: "app_crash_after_send", afterRelaunch: true, root: first.root,
                                      keychain: first.keychain)
        await second.store.start(languageFromEnvironment: true)
        await second.settle()
        XCTAssertEqual(second.store.screen, .s23)
        resumeAfterRelaunch = second.store.resume
        XCTAssertEqual(resumeAfterRelaunch?.screen, .s16)
        XCTAssertEqual(resumeAfterRelaunch?.afterSend, true)
        XCTAssertEqual(resumeAfterRelaunch?.canDiscard, false, "no discard after sending")
        let (h, _) = try await playScenario("app_crash_after_send")
        XCTAssertEqual(h.store.screen.id, script.header.expectScreen)
    }

    // MARK: freshness (DESIGN §8.1 Frist)

    func testExpiredDeadlineOnS14StartsANewBackupAutomatically() async throws {
        let clock = TestClock()
        let h = try StoreHarness(scenario: "happy", clock: clock, autoAdvanceDelay: 0)
        try await play(h, try Repo.scenario("happy"), through: "S11")
        await h.settle()
        XCTAssertEqual(h.store.screen, .s14)
        let c = try XCTUnwrap(h.store.countdown)
        XCTAssertFalse(c.expired)
        let before = h.engine.log.count
        clock.now = c.deadline.addingTimeInterval(1)
        h.store.tick()
        XCTAssertEqual(h.store.error?.code, .E_GUARD_FRESHNESS)
        XCTAssertEqual(h.store.screen, .failure(.freshness))
        XCTAssertFalse(h.store.canTransfer)
        XCTAssertNil(h.store.prepared, "the old transfer package is dropped")
        await h.settle()
        // a new PRE backup is started by itself (the mock then replays whatever block comes next)
        let after = h.engine.log.dropFirst(before)
        XCTAssertEqual(after.first?.command, .backup)
        XCTAssertEqual(after.first?.args.contains("pre"), true)
        XCTAssertFalse(after.contains { $0.command == .restore }, "never a restore after the deadline")
    }

    func testAutomaticNewBackupAfterTheDeadlineCompletes() async throws {
        // F-FRESHNESS without any click: the new backup and prepare run by themselves and end on S14 again
        let h = try StoreHarness(scenario: "freshness_expired", autoAdvanceDelay: 0.01)
        try await play(h, try Repo.scenario("freshness_expired"), through: "S14")   // transfer -> E_GUARD_FRESHNESS
        for _ in 0..<400 where h.engine.log.filter({ $0.command == .prepare }).count < 2 {
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
        await h.settle()
        let cmds = h.engine.log.map(\.command)
        let restore = try XCTUnwrap(cmds.firstIndex(of: .restore))
        XCTAssertEqual(Array(cmds[(restore + 1)...]), [.backup, .prepare], "new backup + prepare by themselves")
        XCTAssertEqual(h.store.screen, .s14, "internal: \(h.store.internalReason ?? "-")")
        XCTAssertNotNil(h.store.prepared)
        XCTAssertNil(h.store.internalReason)
    }

    // MARK: S21 variants and areas

    func testDataFailShowsR2WithLocalizedAreas() async throws {
        let (h, _) = try await playScenario("data_fail")
        XCTAssertEqual(h.store.screen, .s21)
        XCTAssertEqual(h.store.stopVariant, .r2)
        let text = h.store.affectedAreasText(Localizer(.de))
        XCTAssertTrue(text.contains("Einstellungen"), text)
        XCTAssertFalse(text.contains("_"), "no raw tokens: \(text)")
    }

    func testStopVariantPerVerdict() async throws {
        let h = try StoreHarness(scenario: "happy")
        let s = h.store
        func pc(_ v: String) -> PostcheckResult {
            try! JSONValue.object(["verdict": .string(v), "notes": .array([]), "areas": .array([]),
                                   "threema_ok": .bool(false)]).decode(PostcheckResult.self)
        }
        let table: [(String, WizardStore.StopVariant)] = [("setup_full", .r4), ("data_keychain", .r2k), ("data", .r2),
                                                          ("restore_state", .r2), ("threema_only", .r1)]
        for (v, expect) in table {
            s.postcheck = pc(v)
            XCTAssertEqual(s.stopVariant, expect, v)
        }
        // only the engine's verdict offers R1: S17 "Problem" arrives as threema_only (REVIEW M1), never as ok + answer
        s.postcheck = pc("ok")
        s.answers.threemaCheck = "problem"
        XCTAssertEqual(s.stopVariant, .r2, "no R1 the rollback guard would refuse")
    }

    // MARK: unknown codes and protocol violations → F-INTERNAL

    func testUnknownCodeGoesToInternalWithTheCode() async throws {
        let h = try StoreHarness(scenario: "happy")
        var t = CommandTracker(command: .prepare, expectedEngineVersion: nil)
        t.consume(try XCTUnwrap(EngineEvent.parse(Events.hello("prepare"))))
        t.consume(try XCTUnwrap(EngineEvent.parse(Events.result("prepare", seq: 2, ok: false, code: "E_FROM_THE_FUTURE"))))
        let outcome = t.outcome(exitCode: 1, crashed: false)
        XCTAssertEqual(outcome, .internalError(reason: "unknown_code", codeRaw: "E_FROM_THE_FUTURE"))
        h.store.go(.s13)
        h.store.handle(outcome, origin: .s13)
        XCTAssertEqual(h.store.screen, .failure(.internalError))
        XCTAssertEqual(h.store.error?.codeRaw, "E_FROM_THE_FUTURE", "the code goes into the diagnostic report")
    }

    func testDeviceModifiedAfterSendGoesToS16() async throws {
        let h = try StoreHarness(scenario: "happy")
        h.store.go(.s14)
        let r = ResultEvent(command: "restore", ok: false, codeRaw: "E_RESTORE_DEVICE_ERROR", retryable: false,
                            deviceModified: "unknown", data: .object(["last_progress": .number(80)]))
        h.store.handle(.failure(r), origin: .s14)
        XCTAssertEqual(h.store.screen, .s16)
        XCTAssertTrue(h.store.restoreLinkLost)
    }
}
