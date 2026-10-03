// SPDX-License-Identifier: AGPL-3.0-or-later
import AppKit
import XCTest
@testable import ThreemaChatTransfer

/// Every text the sources use exists in DE ("Sie") and EN with the same placeholders; every SF Symbol exists.
final class StringsAndAssetsTests: XCTestCase {
    private func sourceText() throws -> String {
        let fm = FileManager.default
        var out = ""
        let e = try XCTUnwrap(fm.enumerator(at: Repo.sources, includingPropertiesForKeys: nil))
        for case let url as URL in e where url.pathExtension == "swift" {
            out += try String(contentsOf: url, encoding: .utf8) + "\n"
        }
        return out
    }

    private func matches(_ pattern: String, in text: String) throws -> [String] {
        let re = try NSRegularExpression(pattern: pattern)
        return re.matches(in: text, range: NSRange(text.startIndex..., in: text)).compactMap {
            Range($0.range(at: 1), in: text).map { String(text[$0]) }
        }
    }

    private func catalog() throws -> [String: [String: String]] {
        let url = Repo.appDir.appendingPathComponent("Resources/Localizable.xcstrings")
        let obj = try JSONSerialization.jsonObject(with: Data(contentsOf: url)) as? [String: Any]
        let strings = try XCTUnwrap(obj?["strings"] as? [String: [String: Any]])
        var out: [String: [String: String]] = [:]
        for (k, v) in strings {
            let locs = v["localizations"] as? [String: [String: [String: String]]] ?? [:]
            out[k] = locs.compactMapValues { $0["stringUnit"]?["value"] }
        }
        return out
    }

    func testEveryLiteralKeyExistsInBothLanguages() throws {
        let src = try sourceText()
        let keys = Set(try matches(#"loc\.(?:t|md|has)\("([a-z0-9_.]+)""#, in: src)
                       + matches(#"store\.loc\.t\("([a-z0-9_.]+)""#, in: src))
        XCTAssertGreaterThan(keys.count, 150)
        for lang in AppLanguage.allCases {
            let loc = Localizer(lang)
            for k in keys where !k.hasSuffix(".") {
                XCTAssertTrue(loc.has(k), "\(lang): missing \(k)")
            }
        }
    }

    func testInterpolatedKeyFamiliesAreComplete() {
        let families: [(String, [String])] = [
            ("s00.needs.", (1...6).map(String.init)), ("s00.what.", (1...5).map(String.init)),
            ("s01.p", (1...4).map(String.init)), ("s04.step.", (1...4).map(String.init)),
            ("s07.item.", (1...5).map(String.init)), ("s08.item.", (1...4).map(String.init)),
            ("s11.item.", (1...4).map(String.init)), ("s16.step.", (1...5).map(String.init)),
            ("s16.answer.", S16AfterRestart.answers), ("s20.item.", (1...7).map(String.init)),
            ("diag.item.", (1...4).map(String.init)), ("sidebar.", SidebarPhase.allCases.map(\.rawValue)),
            ("sidebar.state.", ["completed", "current", "upcoming"]),
            ("screen.", Screen.wizardScreens.map { "\($0.id).name" }),
            ("common.status.", ["pass", "warn", "fail", "skip", "running", "pending"]),
            ("prompt.", ["unlock_device", "trust_device", "passcode_on_device", "keep_cable"]),
            ("s13.step.", ["extract", "import", "count", "build", "verify"]),
            ("check.", S12Backup.rows.map(\.rawValue) + ["macos", "arch", "free_space", "fs_apfs", "power", "filevault",
                                                         "freshness", "dcim_unchanged", "set_integrity", "device",
                                                         "iphone_space", "battery", "findmy", "compat_ios"]),
        ]
        for lang in AppLanguage.allCases {
            let loc = Localizer(lang)
            for (prefix, items) in families {
                for i in items { XCTAssertTrue(loc.has(prefix + i), "\(lang): missing \(prefix + i)") }
            }
            for p in phasesByCommand.values.flatMap({ $0 }) {
                XCTAssertTrue(loc.has("phase.\(p.rawValue)"), "\(lang): missing phase.\(p.rawValue)")
            }
        }
    }

    func testAppTextsAreCompleteFormalAndHaveTheSamePlaceholders() throws {
        let cat = try catalog()
        let token = try NSRegularExpression(pattern: #"\{([A-Za-z_][A-Za-z0-9_]*)\}"#)
        func tokens(_ s: String) -> Set<String> {
            Set(token.matches(in: s, range: NSRange(s.startIndex..., in: s)).compactMap {
                Range($0.range(at: 1), in: s).map { String(s[$0]) }
            })
        }
        let du = try NSRegularExpression(pattern: #"(?i)\b(du|dich|dir|dein|deine|deinen|deinem|deiner|deines)\b"#)
        for (k, v) in cat where !k.hasPrefix("code.") && !k.hasPrefix("action.") {
            let de = try XCTUnwrap(v["de"], k), en = try XCTUnwrap(v["en"], k)
            XCTAssertFalse(de.trimmingCharacters(in: .whitespaces).isEmpty, "empty de \(k)")
            XCTAssertFalse(en.trimmingCharacters(in: .whitespaces).isEmpty, "empty en \(k)")
            XCTAssertEqual(tokens(de), tokens(en), "placeholders differ: \(k)")
            XCTAssertNil(du.firstMatch(in: de, range: NSRange(de.startIndex..., in: de)), "\"du\" in German: \(k)")
            XCTAssertFalse(de.contains("Threema GmbH") || en.contains("Threema GmbH"), k)
        }
    }

    func testNoDeviceNameOrUDIDIsEverShown() throws {
        let src = try sourceText()
        XCTAssertFalse(src.contains("DeviceName"), "the device name is never read or shown (DESIGN §8.2)")
        XCTAssertFalse(src.contains("UniqueDeviceID"))
    }

    func testEverySFSymbolExists() throws {
        let src = try sourceText()
        let names = Set(try matches(#"(?:systemName|symbol|systemImage): \"([a-z0-9.]+)\""#, in: src)
                        + matches(#"return \"([a-z][a-z0-9]*(?:\.[a-z0-9]+)+)\""#, in: src)
                            .filter { !$0.contains("_") && !$0.hasPrefix("screen.") && !$0.hasPrefix("s0") })
        XCTAssertGreaterThan(names.count, 30)
        for n in names where !n.hasPrefix("code.") && !n.hasPrefix("action.") && !n.hasPrefix("common.")
            && !n.hasPrefix("s1") && !n.hasPrefix("s2") {
            XCTAssertNotNil(NSImage(systemSymbolName: n, accessibilityDescription: nil), "SF Symbol \(n)")
        }
    }

    func testSubstitutionReplacesAppAndKeepsUnknownTokensVisibleForReview() {
        XCTAssertEqual(Localizer.substitute("{App} {n}", ["n": "3"]), "\(AppInfo.productName) 3")
        XCTAssertEqual(Localizer(.de).t("no.such.key"), "no.such.key")
    }
}

/// Secrets: generated password format, keychain only on request, memory-only box (DESIGN §9).
final class SecretsTests: XCTestCase {
    func testGeneratedPasswordFormat() {
        for _ in 0..<50 {
            let p = PasswordGenerator.generate()
            let groups = p.split(separator: "-")
            XCTAssertEqual(groups.count, 6)
            XCTAssertTrue(groups.allSatisfy { $0.count == 4 })
            XCTAssertTrue(p.replacingOccurrences(of: "-", with: "").allSatisfy { PasswordGenerator.alphabet.contains($0) })
            XCTAssertFalse(p.contains { "0O1IL".contains($0) }, "no easily confused characters")
        }
        XCTAssertNotEqual(PasswordGenerator.generate(), PasswordGenerator.generate())
    }

    func testLastFourCheckAndOwnPasswordRule() {
        XCTAssertTrue(PasswordGenerator.matchesLastFour("wxyz", of: "ABCD-EFGH-JKMN-PQRS-TUVW-WXYZ"))
        XCTAssertTrue(PasswordGenerator.matchesLastFour(" W X Y Z", of: "ABCD-EFGH-JKMN-PQRS-TUVW-WXYZ"))
        XCTAssertFalse(PasswordGenerator.matchesLastFour("VWXY", of: "ABCD-EFGH-JKMN-PQRS-TUVW-WXYZ"))
        XCTAssertFalse(PasswordGenerator.isAcceptableOwn("short-pw1"))
        XCTAssertTrue(PasswordGenerator.isAcceptableOwn("long-enough-1"))
    }

    func testSecretsBoxIsMemoryOnly() {
        let b = SecretsBox()
        b.setBackupPassword("x-1")
        b.setAndroidPassword("a", ref: 0)
        b.dropAndroidPasswords()
        XCTAssertTrue(b.androidPasswords.isEmpty)
        b.setBackupPassword("")
        XCTAssertNil(b.backupPassword, "an empty password is no password")
        b.setBackupPassword("x-2")
        b.wipe()
        XCTAssertNil(b.backupPassword)
    }

    @MainActor
    func testGeneratedPasswordGoesToTheKeychainOnlyWhenChosen() async throws {
        for keep in [true, false] {
            let h = try StoreHarness(scenario: "happy")
            h.store.go(.s10a)
            h.store.saveInKeychain = keep
            h.store.prepareGeneratedPassword()
            let pw = h.store.generatedPassword
            h.store.lastFourInput = String(pw.suffix(4))
            XCTAssertTrue(h.store.canEnableEncryption)
            await h.store.enableEncryption()
            XCTAssertEqual(h.store.screen, .s11)
            XCTAssertEqual(try h.keychain.load(), keep ? pw : nil)
            XCTAssertEqual(h.store.secrets.backupPassword, pw, "memory for this run in any case")
            XCTAssertTrue(h.store.generatedPassword.isEmpty, "the shown password is cleared after use")
            XCTAssertFalse(h.appWrittenText().contains(pw))
        }
    }
}

/// The session folder (DESIGN §5.8).
@MainActor
final class SessionStoreTests: XCTestCase {
    func testCreateLayoutModesAndNoSecrets() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("tct-ss-\(UUID().uuidString)")
        let store = SessionStore(root: root, excludeFromBackups: false)
        let dir = try store.create { SessionState(sessionId: $0, now: Date(), appVersion: "t", locale: .en) }
        let fm = FileManager.default
        XCTAssertEqual(try fm.attributesOfItem(atPath: dir.path)[.posixPermissions] as? Int, 0o700)
        XCTAssertEqual(try fm.attributesOfItem(atPath: dir.appendingPathComponent("session.json").path)[.posixPermissions] as? Int, 0o600)
        XCTAssertTrue(fm.fileExists(atPath: dir.appendingPathComponent(".metadata_never_index").path))
        let json = try JSONSerialization.jsonObject(with: Data(contentsOf: dir.appendingPathComponent("session.json"))) as? [String: Any]
        XCTAssertEqual(json?["schema"] as? String, "session.v1")
        XCTAssertEqual(Set(json?.keys.map { $0 } ?? []).subtracting(["schema", "session_id", "created_at", "updated_at",
                                                                     "app_version", "locale", "sidebar_phase", "screen",
                                                                     "answers", "workdir_custom", "last_error_code"]), [])
        XCTAssertEqual(store.findLatest()?.0.standardizedFileURL, dir.standardizedFileURL)
    }

    func testFileKeysCannotLeaveTheSession() throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("tct-ss-\(UUID().uuidString)")
        let store = SessionStore(root: root, excludeFromBackups: false)
        let dir = try store.create { SessionState(sessionId: $0, now: Date(), appVersion: "t", locale: .de) }
        XCTAssertEqual(store.file(forKey: "android/missing-senders.json"), dir.appendingPathComponent("android/missing-senders.json"))
        XCTAssertNil(store.file(forKey: "../x"))
        XCTAssertNil(store.file(forKey: "android/../../x"))
        XCTAssertNil(store.file(forKey: "/etc/passwd"))
        XCTAssertNil(store.file(forKey: "secrets/x"))
    }

    func testDemoRunsUseTheirOwnSessionsFolder() {
        let env = AppEnvironment.current(["TM_ENGINE": "mock:happy"], defaults: UserDefaults(suiteName: "tct-test-\(UUID().uuidString)")!)
        XCTAssertNotEqual(env.sessionsRoot(for: .mock(scenario: "happy")), SessionStore.standardRoot)
        XCTAssertEqual(env.sessionsRoot(for: .live), SessionStore.standardRoot)
        XCTAssertTrue(env.isDemo)
        XCTAssertThrowsError(try AppEnvironment.current(["TM_ENGINE": "mock:../x"], defaults: .standard).makeEngine())
        XCTAssertThrowsError(try AppEnvironment.current(["TM_ENGINE": "livex"], defaults: .standard).makeEngine())
    }
}
