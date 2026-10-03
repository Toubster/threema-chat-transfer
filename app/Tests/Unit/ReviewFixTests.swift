// SPDX-License-Identifier: AGPL-3.0-or-later
import XCTest
@testable import ThreemaChatTransfer

/// The wizard paths found by the adversarial review after integration (docs/REVIEW.md B1, M1, M2, M4, M5).
@MainActor
final class ReviewFixTests: XCTestCase {
    /// A recorded scenario with one change applied to every line after the first `{"mock":"user"}` directive of
    /// `screen` (the recordings are real engine output; only the fact under test is changed).
    private func patched(_ name: String, after screen: String, _ change: (String) -> String) throws -> ScenarioScript {
        let text = try String(contentsOf: Repo.scenarios.appendingPathComponent("\(name).jsonl"), encoding: .utf8)
        var seen = false
        let lines = text.split(separator: "\n", omittingEmptySubsequences: true).map { raw -> String in
            let line = String(raw)
            if line.contains("\"mock\":\"user\"") && line.contains("\"screen\":\"\(screen)\"") { seen = true }
            return seen ? change(line) : line
        }
        return try ScenarioScript.parse(lines.joined(separator: "\n"))
    }

    private func drive(_ h: StoreHarness, _ script: ScenarioScript, through screen: String) async throws {
        try await play(h, script, through: screen)
        await h.settle()
    }

    // MARK: B1 -- the Finder safety net turns encryption on with the user's own password

    func testFinderSafetyNetReadsEncryptionAgainAndAsksForTheFinderPassword() async throws {
        // happy: encryption off at S08; the Finder backup of S09 turned it on (device-status after S09 says "on")
        let script = try patched("happy", after: "S09") {
            $0.replacingOccurrences(of: "\"encryption\":\"off\"", with: "\"encryption\":\"on\"")
        }
        let h = try StoreHarness(script: script)
        try await drive(h, script, through: "S08")
        XCTAssertEqual(h.store.device?.encryption, "off")
        h.store.safetyNetChoice = "finder"
        h.store.safetyNetConfirmed = true
        await h.store.confirmSafetyNet()
        await h.settle()
        XCTAssertEqual(h.store.screen, .s10b, "encryption is on now: S10b, never S10a with a generated password")
        XCTAssertTrue(h.store.encryptionTurnedOnElsewhere)
        let cmds = h.engine.log.map(\.command)
        XCTAssertEqual(cmds.filter { $0 == .deviceStatus }.count, 3, "S03, S08 and the re-read at S09")
        XCTAssertFalse(cmds.contains(.encryptionEnable))
        XCTAssertNil(try h.keychain.load())
    }

    func testEncryptionAlreadyOnNeverKeepsTheOfferedPassword() async throws {
        // the device reports "already on" to encryption-enable (changed:false)
        let script = try patched("happy", after: "S10a") {
            $0.replacingOccurrences(of: "\"changed\":true", with: "\"changed\":false")
        }
        let h = try StoreHarness(script: script)
        try await drive(h, script, through: "S09")
        XCTAssertEqual(h.store.screen, .s10a)
        h.store.prepareGeneratedPassword()
        h.store.lastFourInput = String(h.store.generatedPassword.suffix(4))
        await h.store.enableEncryption()
        await h.settle()
        XCTAssertEqual(h.store.screen, .s10b)
        XCTAssertTrue(h.store.encryptionTurnedOnElsewhere)
        XCTAssertNil(h.store.secrets.backupPassword, "the offered password does not protect the backups")
        XCTAssertNil(try h.keychain.load(), "nothing in the keychain")
        XCTAssertNotEqual(h.store.answers.passwordInKeychain, true)
        XCTAssertTrue(h.store.generatedPassword.isEmpty)
    }

    func testRejectedKeychainPasswordIsNeverLoadedAgain() async throws {
        // wrong_backup_password: the first PRE backup fails with E_BACKUP_PASSWORD
        let script = try Repo.scenario("wrong_backup_password")
        let h = try StoreHarness(script: script)
        try await drive(h, script, through: "S10b")
        // as if S10a had stored a password in the keychain that the device does not accept
        h.store.secrets.setBackupPassword(nil)
        h.store.answers.passwordInKeychain = true
        try h.keychain.save("ZZ-keychain-but-wrong")
        for i in WizardStore.offlineItems { h.store.toggle(\.offlineChecklist, i, true) }
        h.store.startBackup()
        await h.settle()
        XCTAssertEqual(h.store.screen, .failure(.passwordWrong))
        XCTAssertTrue(h.store.storedPasswordRejected)
        XCTAssertEqual(h.store.answers.passwordInKeychain, false)
        h.store.perform(.reenterPassword)
        for _ in 0..<500 where h.store.sheet != .passwordPrompt { try? await Task.sleep(nanoseconds: 2_000_000) }
        XCTAssertEqual(h.store.sheet, .passwordPrompt, "F-PW-WRONG always asks; no loop over the keychain")
        h.store.submitPasswordPrompt(TestSecrets.backup)
        await h.settle()
        XCTAssertNotEqual(h.store.screen, .failure(.passwordWrong))
    }

    // MARK: M1 -- S17 "Problem" with green system checks is decided by the engine

    func testS17ProblemGoesToTheEngineAndR1IsAccepted() async throws {
        let script = try Repo.scenario("threema_problem_reported")
        let h = try StoreHarness(script: script)
        try await drive(h, script, through: "S17")
        XCTAssertEqual(h.store.screen, .s21)
        XCTAssertEqual(h.store.postcheck?.verdictValue, .threemaOnly)
        XCTAssertEqual(h.store.stopVariant, .r1)
        let pc = h.engine.log.last { $0.command == .postcheck }
        XCTAssertEqual(pc?.args.suffix(5).map { $0 }, ["--buddy-answer", "account_only", "--threema-answer", "problem",
                                                       "--secrets-stdin"])
        let (end, _) = try await playScenario("threema_problem_reported")
        XCTAssertEqual(end.store.screen, .s20)
        XCTAssertTrue(end.store.rollbackMode, "S17/S19 speak about Threema as before the transfer")
        XCTAssertTrue(end.engine.log.contains { $0.command == .rollbackThreema })
    }

    // MARK: M2 -- "also language, country or Apps & data": no S17, no control backup

    func testSetupFullSkipsThreemaCheckAndControlBackup() async throws {
        let (h, _) = try await playScenario("setup_full")
        XCTAssertEqual(h.store.screen, .s21)
        XCTAssertEqual(h.store.stopVariant, .r4)
        let log = h.engine.log
        XCTAssertFalse(log.contains { $0.command == .backup && $0.args.contains("post") }, "no POST backup")
        let pc = try XCTUnwrap(log.last { $0.command == .postcheck })
        XCTAssertEqual(Array(pc.args.suffix(2)), ["--buddy-answer", "full_setup"])
        XCTAssertNil(pc.secrets, "no password needed for the answer alone")
    }

    // MARK: M4 -- every way out before the send says what to turn back on

    func testStopReminderListsWhatTheUserSwitchedOff() async throws {
        let h = try StoreHarness(scenario: "happy")
        let s = h.store
        XCTAssertFalse(s.showsStopReminder, "nothing switched off yet")
        s.answers.settingsChecklist = ["ios_updates_off": true, "app_updates_off": true, "find_my_off": true,
                                       "battery": true]
        s.go(.s09)
        XCTAssertEqual(s.turnBackOnKeys, ["turn_on.find_my", "turn_on.updates"])
        XCTAssertTrue(s.showsStopReminder)
        let text = s.withStopReminder(Localizer(.de).t("cancel.text"), Localizer(.de))
        XCTAssertTrue(text.contains("„Wo ist?“"), text)
        s.answers.offlineChecklist = ["threema_closed": true, "watch_bluetooth_off": true, "airplane_on": true,
                                      "unlocked_connected": true]
        s.go(.s12)
        s.showInternal(reason: "test", code: nil, origin: .s12)
        XCTAssertEqual(s.turnBackOnKeys, ["turn_on.airplane", "turn_on.bluetooth", "turn_on.find_my",
                                          "turn_on.updates"])
        XCTAssertTrue(s.showsStopReminder, "F-INTERNAL in the offline window")
        s.go(.s16)
        XCTAssertFalse(s.showsStopReminder, "after the send the check continues; S20/S22 carry the list")
    }

    // MARK: M5 -- an unreadable iPhone Threema ID is explained, with a way on

    func testUnreadableThreemaIdHasItsOwnTextAndANewBackup() {
        let c = EngineCode.E_THREEMA_ID_UNREADABLE
        XCTAssertEqual(ErrorCatalog.destination(for: c.rawValue), .failure(.threemaId))
        XCTAssertEqual(ErrorCatalog.actions(for: c.rawValue).last, .newBackup)
        XCTAssertTrue(c.info.needsNewBackup)
        let de = Localizer(.de)
        XCTAssertNotEqual(de.t(c.titleKey), de.t(EngineCode.E_INTERNAL.titleKey))
        XCTAssertTrue(de.t(c.bodyKey).contains("Am iPhone wurde nichts verändert"))
    }
}
