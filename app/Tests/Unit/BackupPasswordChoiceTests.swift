// SPDX-License-Identifier: AGPL-3.0-or-later
import XCTest
@testable import ThreemaChatTransfer

/// S10b as a decision screen (owner feedback: "which password, or any?"): the stored password or the help with Apple's
/// "Alle Einstellungen zurücksetzen"; "Erneut prüfen" reads the iPhone again and leads to S10a once encryption is off.
/// The recordings are real engine output; only the encryption facts of `device-status` are changed (MockEngine
/// replays the next block of a command, so the S09 re-read of "happy" answers "Erneut prüfen").
@MainActor
final class BackupPasswordChoiceTests: XCTestCase {
    private func lines(_ name: String) throws -> [String] {
        try String(contentsOf: Repo.scenarios.appendingPathComponent("\(name).jsonl"), encoding: .utf8)
            .split(separator: "\n", omittingEmptySubsequences: true).map(String.init)
    }

    private func isUser(_ line: String, _ screen: String) -> Bool {
        line.contains("\"mock\":\"user\"") && line.contains("\"screen\":\"\(screen)\"")
    }

    /// "happy" with encryption on until the `{"mock":"user"}` directive of `screen` (S03 and S08 say "on"; the S09
    /// re-read keeps "off" unless `stillOn`): at S09 the app goes straight to S10b, and that later block answers.
    private func encryptionOnScript(stillOn: Bool = false) throws -> ScenarioScript {
        var on = true
        let out = try lines("happy").map { line -> String in
            if isUser(line, stillOn ? "S10a" : "S09") { on = false }
            return on ? line.replacingOccurrences(of: "\"encryption\":\"off\"", with: "\"encryption\":\"on\"") : line
        }
        return try ScenarioScript.parse(out.joined(separator: "\n"))
    }

    /// The block (invoke … exit) of the `n`-th invocation of `cmd` in a recording.
    private func block(_ name: String, _ cmd: String, _ n: Int = 0) throws -> [String] {
        let all = try lines(name)
        var seen = -1
        var out: [String] = []
        var inside = false
        for line in all {
            if line.contains("\"mock\":\"invoke\"") && line.contains("\"cmd\":\"\(cmd)\"") {
                seen += 1
                inside = seen == n
            }
            if inside { out.append(line) }
            if inside && line.contains("\"mock\":\"exit\"") { break }
        }
        return out
    }

    private func toS10b(_ h: StoreHarness, _ script: ScenarioScript) async throws {
        try await play(h, script, through: "S09")
        await h.settle()
        XCTAssertEqual(h.store.screen, .s10b)
    }

    func testNothingIsPreselectedAndOnlyAKnownPasswordContinues() async throws {
        let script = try encryptionOnScript()
        let h = try StoreHarness(script: script)
        try await toS10b(h, script)
        let s = h.store
        XCTAssertNil(s.existingPasswordChoice, "nothing preselected")
        s.backupPasswordInput = "ZZ-typed"
        XCTAssertFalse(s.canConfirmExistingPassword, "no choice yet")
        s.existingPasswordChoice = .unknown
        XCTAssertFalse(s.canConfirmExistingPassword, "the help path never continues with a typed password")
        s.existingPasswordChoice = .known
        XCTAssertTrue(s.canConfirmExistingPassword)
        s.backupPasswordInput = ""
        XCTAssertFalse(s.canConfirmExistingPassword)
    }

    func testResetThenRecheckLeadsToS10aWhereTheAppSetsTheNewPassword() async throws {
        let script = try encryptionOnScript()
        let h = try StoreHarness(script: script)
        try await toS10b(h, script)
        let s = h.store
        s.existingPasswordChoice = .unknown
        // the user did "Alle Einstellungen zurücksetzen" on the iPhone; "Erneut prüfen"
        await s.recheckEncryption()
        await h.settle()
        XCTAssertEqual(s.screen, .s10a, "encryption off now: the app sets a new password")
        XCTAssertFalse(s.encryptionStillOn)
        XCTAssertFalse(s.encryptionTurnedOnElsewhere)
        XCTAssertNil(s.existingPasswordChoice)
        XCTAssertNil(s.secrets.backupPassword)
        XCTAssertEqual(s.history.last, .s09, "Zurück leads to S09, not to S10b")
        let cmds = h.engine.log.map(\.command)
        XCTAssertEqual(cmds.filter { $0 == .deviceStatus }.count, 3, "S03, S08 and Erneut prüfen")
        XCTAssertFalse(cmds.contains(.encryptionEnable), "reading only; nothing changed on the iPhone")
        s.prepareGeneratedPassword()
        let pw = s.generatedPassword
        s.lastFourInput = String(pw.suffix(4))
        await s.enableEncryption()
        await h.settle()
        XCTAssertEqual(s.screen, .s11)
        XCTAssertEqual(s.secrets.backupPassword, pw, "this password protects the backups from now on")
        XCTAssertEqual(s.answers.encryptionWas, "off")
    }

    func testRecheckWhileEncryptionIsStillOnStaysAndSaysSo() async throws {
        let script = try encryptionOnScript(stillOn: true)
        let h = try StoreHarness(script: script)
        try await toS10b(h, script)
        let s = h.store
        s.existingPasswordChoice = .unknown
        await s.recheckEncryption()
        await h.settle()
        XCTAssertEqual(s.screen, .s10b)
        XCTAssertTrue(s.encryptionStillOn)
        XCTAssertEqual(s.existingPasswordChoice, .unknown, "the help stays open")
        XCTAssertFalse(h.engine.log.contains { $0.command == .encryptionEnable })
    }

    func testWrongPasswordHelpResetsAndMakesANewBackupWithTheNewPassword() async throws {
        // wrong_backup_password until F-PW-WRONG, then (after the reset) the S09 re-read, encryption-enable, PRE backup
        // and prepare of "happy"
        var text = try lines("wrong_backup_password")
        if let i = text.firstIndex(where: { isUser($0, "F-PW-WRONG") }) { text = Array(text[..<i]) }
        text += try block("happy", "device-status", 2) + block("happy", "encryption-enable")
            + block("happy", "backup") + block("happy", "prepare")
        let script = try ScenarioScript.parse(text.joined(separator: "\n"))
        let h = try StoreHarness(script: script)
        try await play(h, script, through: "S11")
        await h.settle()
        let s = h.store
        XCTAssertEqual(s.screen, .failure(.passwordWrong))
        await s.recheckEncryption()
        await h.settle()
        XCTAssertEqual(s.screen, .s10a)
        XCTAssertNil(s.error)
        XCTAssertEqual(s.history.last, .s09, "the S10b/S11/S12 of the old password are gone")
        XCTAssertNil(s.answers.offlineChecklist, "the reset may have switched airplane mode off: S11 again")
        s.prepareGeneratedPassword()
        let pw = s.generatedPassword
        s.lastFourInput = String(pw.suffix(4))
        await s.enableEncryption()
        await h.settle()
        XCTAssertEqual(s.screen, .s11)
        for i in WizardStore.offlineItems { s.toggle(\.offlineChecklist, i, true) }
        s.startBackup()
        await h.settle()
        XCTAssertEqual(s.screen, .s14)
        let backup = try XCTUnwrap(h.engine.log.last { $0.command == .backup })
        XCTAssertEqual(backup.secrets?.backupPassword, pw, "the new backup uses the new password")
        XCTAssertNil(s.sheet, "no password prompt: the new password is known")
    }
}
