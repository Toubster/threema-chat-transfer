// SPDX-License-Identifier: AGPL-3.0-or-later
import XCTest

/// XCUITest per mock scenario, German and English (DESIGN §13.1 level 5, P4 acceptance). The app runs with
/// `TM_ENGINE=mock:<scenario>` — never the real engine, never a device. On every screen the test also checks that
/// every control has a VoiceOver label.
final class ScenarioUITests: XCTestCase {
    /// Scenarios with a test method below; `testEveryScenarioHasAUITest` keeps the list complete.
    static let covered = [
        "airplane_off",
        "android_format_new",
        "android_incomplete",
        "android_two_backups",
        "app_crash_after_send",
        "data_fail",
        "dcim_changed",
        "duplicate_chat",
        "find_my_on",
        "first_backup_dropped",
        "freshness_expired",
        "happy",
        "happy_with_notes",
        "id_mismatch",
        "ios_unknown",
        "iphone_space_low",
        "keychain_fail",
        "link_lost_after_send",
        "photos_limit",
        "restore_state",
        "rollback_threema_ok",
        "setup_full",
        "threema_missing",
        "threema_model_unknown",
        "threema_only_fail",
        "wrong_android_password",
        "wrong_backup_password",
    ]

    override func setUp() {
        continueAfterFailure = false
    }

    func testEveryScenarioHasAUITest() {
        XCTAssertEqual(Set(UIRepo.scenarioNames), Set(Self.covered),
                       "add a test method for every app/Tests/Scenarios/*.jsonl")
    }

    func testAirplaneOffDE() throws { try run("airplane_off", "de") }
    func testAirplaneOffEN() throws { try run("airplane_off", "en") }
    func testAndroidFormatNewDE() throws { try run("android_format_new", "de") }
    func testAndroidFormatNewEN() throws { try run("android_format_new", "en") }
    func testAndroidIncompleteDE() throws { try run("android_incomplete", "de") }
    func testAndroidIncompleteEN() throws { try run("android_incomplete", "en") }
    func testAndroidTwoBackupsDE() throws { try run("android_two_backups", "de") }
    func testAndroidTwoBackupsEN() throws { try run("android_two_backups", "en") }
    func testAppCrashAfterSendDE() throws { try run("app_crash_after_send", "de") }
    func testAppCrashAfterSendEN() throws { try run("app_crash_after_send", "en") }
    func testDataFailDE() throws { try run("data_fail", "de") }
    func testDataFailEN() throws { try run("data_fail", "en") }
    func testDcimChangedDE() throws { try run("dcim_changed", "de") }
    func testDcimChangedEN() throws { try run("dcim_changed", "en") }
    func testDuplicateChatDE() throws { try run("duplicate_chat", "de") }
    func testDuplicateChatEN() throws { try run("duplicate_chat", "en") }
    func testFindMyOnDE() throws { try run("find_my_on", "de") }
    func testFindMyOnEN() throws { try run("find_my_on", "en") }
    func testFirstBackupDroppedDE() throws { try run("first_backup_dropped", "de") }
    func testFirstBackupDroppedEN() throws { try run("first_backup_dropped", "en") }
    func testFreshnessExpiredDE() throws { try run("freshness_expired", "de") }
    func testFreshnessExpiredEN() throws { try run("freshness_expired", "en") }
    func testHappyDE() throws { try run("happy", "de") }
    func testHappyEN() throws { try run("happy", "en") }
    func testHappyWithNotesDE() throws { try run("happy_with_notes", "de") }
    func testHappyWithNotesEN() throws { try run("happy_with_notes", "en") }
    func testIdMismatchDE() throws { try run("id_mismatch", "de") }
    func testIdMismatchEN() throws { try run("id_mismatch", "en") }
    func testIosUnknownDE() throws { try run("ios_unknown", "de") }
    func testIosUnknownEN() throws { try run("ios_unknown", "en") }
    func testIphoneSpaceLowDE() throws { try run("iphone_space_low", "de") }
    func testIphoneSpaceLowEN() throws { try run("iphone_space_low", "en") }
    func testKeychainFailDE() throws { try run("keychain_fail", "de") }
    func testKeychainFailEN() throws { try run("keychain_fail", "en") }
    func testLinkLostAfterSendDE() throws { try run("link_lost_after_send", "de") }
    func testLinkLostAfterSendEN() throws { try run("link_lost_after_send", "en") }
    func testPhotosLimitDE() throws { try run("photos_limit", "de") }
    func testPhotosLimitEN() throws { try run("photos_limit", "en") }
    func testRestoreStateDE() throws { try run("restore_state", "de") }
    func testRestoreStateEN() throws { try run("restore_state", "en") }
    func testRollbackThreemaOkDE() throws { try run("rollback_threema_ok", "de") }
    func testRollbackThreemaOkEN() throws { try run("rollback_threema_ok", "en") }
    func testSetupFullDE() throws { try run("setup_full", "de") }
    func testSetupFullEN() throws { try run("setup_full", "en") }
    func testThreemaMissingDE() throws { try run("threema_missing", "de") }
    func testThreemaMissingEN() throws { try run("threema_missing", "en") }
    func testThreemaModelUnknownDE() throws { try run("threema_model_unknown", "de") }
    func testThreemaModelUnknownEN() throws { try run("threema_model_unknown", "en") }
    func testThreemaOnlyFailDE() throws { try run("threema_only_fail", "de") }
    func testThreemaOnlyFailEN() throws { try run("threema_only_fail", "en") }
    func testWrongAndroidPasswordDE() throws { try run("wrong_android_password", "de") }
    func testWrongAndroidPasswordEN() throws { try run("wrong_android_password", "en") }
    func testWrongBackupPasswordDE() throws { try run("wrong_backup_password", "de") }
    func testWrongBackupPasswordEN() throws { try run("wrong_backup_password", "en") }

    // MARK: the run

    private func run(_ name: String, _ lang: String) throws {
        let scenario = try UIScenario(name)
        let sessions = FileManager.default.temporaryDirectory.appendingPathComponent("tct-ui-\(UUID().uuidString)")
        let app = XCUIApplication()
        UIDriver.launch(app, engine: "mock:\(name)", lang: lang, sessions: sessions)
        var driver = UIDriver(app: app, lang: lang)
        driver.onScreen = { [unowned self] s in self.checkVoiceOverLabels(app, screen: s) }
        XCTAssertTrue(driver.element("screen.S00").waitForExistence(timeout: 20), "app did not start on S00")
        checkLanguage(app, lang)
        for step in scenario.steps {
            switch step {
            case .user(let screen, let answer):
                driver.perform(screen: screen, answer: answer)
            case .relaunch:
                // the app is killed (as by a crash or power loss) and started again on the same session
                app.terminate()
                UIDriver.launch(app, engine: "mock:\(name)", lang: lang, sessions: sessions, afterRelaunch: true)
                let visited = driver.visited
                driver = UIDriver(app: app, lang: lang)
                driver.visited = visited
                driver.onScreen = { [unowned self] s in self.checkVoiceOverLabels(app, screen: s) }
                XCTAssertTrue(driver.element("screen.S23").waitForExistence(timeout: 20), "resume starts on S23")
                XCTAssertTrue(app.buttons["btn.resume_continue"].waitForExistence(timeout: 10))
                XCTAssertFalse(app.buttons["btn.discard"].exists, "no discard after the transfer was sent")
            }
        }
        if scenario.hasCleanup {
            driver.reach("S20")
            driver.click("btn.delete_copies")
            XCTAssertTrue(app.staticTexts[UIRepo.text("s20.copies_deleted", lang) ?? "-"].waitForExistence(timeout: 10))
        }
        driver.reach(scenario.expectScreen)
        if scenario.expectScreen.hasPrefix("F-"), let code = scenario.lastErrorCode,
           let title = UIRepo.text("code.\(code).title", lang) {
            XCTAssertEqual(app.descendants(matching: .any)["screen.title"].firstMatch.label, title, "error title in \(lang)")
        }
        try? FileManager.default.removeItem(at: sessions)
        app.terminate()
    }

    private func checkLanguage(_ app: XCUIApplication, _ lang: String) {
        guard let title = UIRepo.text("s00.title", lang) else { return XCTFail("catalog") }
        XCTAssertEqual(app.descendants(matching: .any)["screen.title"].firstMatch.label, title, "S00 title in \(lang)")
    }

    /// Every interactive control has something VoiceOver can read (DESIGN P4 "VoiceOver-Labels vollständig").
    private func checkVoiceOverLabels(_ app: XCUIApplication, screen: String) {
        let window = app.windows.firstMatch
        let types: [XCUIElement.ElementType] = [.button, .checkBox, .radioButton, .textField, .secureTextField,
                                                .popUpButton, .slider, .link]
        for t in types {
            let all = window.descendants(matching: t).allElementsBoundByIndex
            // the window's own title-bar buttons (_XCUI:CloseWindow, …) belong to AppKit, not to the wizard
            for e in all where e.exists && e.isHittable && !e.identifier.hasPrefix("_XCUI:") {
                XCTAssertFalse(e.spokenText.trimmingCharacters(in: .whitespaces).isEmpty,
                               "\(screen): \(t) \(e.identifier) has no VoiceOver label")
            }
        }
        let images = window.descendants(matching: .image).allElementsBoundByIndex
        for i in images where i.exists && !i.identifier.isEmpty && i.label.isEmpty {
            XCTFail("\(screen): image \(i.identifier) without label")
        }
    }
}
