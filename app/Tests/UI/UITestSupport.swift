// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation
import XCTest

/// Repository paths for the UI tests (scenario files and the string catalog are read from the checkout).
enum UIRepo {
    static let appDir = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent()
        .deletingLastPathComponent()
    static let scenarios = appDir.appendingPathComponent("Tests/Scenarios")
    static let catalog = appDir.appendingPathComponent("Resources/Localizable.xcstrings")
    static let productName = "Chat Transfer for Threema"

    static var scenarioNames: [String] {
        ((try? FileManager.default.contentsOfDirectory(atPath: scenarios.path)) ?? [])
            .filter { $0.hasSuffix(".jsonl") }.map { String($0.dropLast(6)) }.sorted()
    }

    /// Catalog text with `{App}` substituted (other placeholders stay).
    static func text(_ key: String, _ lang: String) -> String? {
        guard let data = try? Data(contentsOf: catalog),
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let strings = obj["strings"] as? [String: Any],
              let entry = strings[key] as? [String: Any],
              let locs = entry["localizations"] as? [String: Any],
              let l = locs[lang] as? [String: Any], let unit = l["stringUnit"] as? [String: Any],
              let value = unit["value"] as? String else { return nil }
        return value.replacingOccurrences(of: "{App}", with: productName)
    }
}

/// The user directives of a scenario file (ENGINE-PROTOCOL §9), plus what the UI test needs to check.
struct UIScenario {
    enum Step: Equatable { case user(screen: String, answer: String), relaunch }
    let name: String
    let expectScreen: String
    let steps: [Step]
    /// The last `ok:false` result code (the title of the final error screen).
    let lastErrorCode: String?
    let hasCleanup: Bool

    init(_ name: String) throws {
        let text = try String(contentsOf: UIRepo.scenarios.appendingPathComponent("\(name).jsonl"), encoding: .utf8)
        var expect = ""
        var steps: [Step] = []
        var lastError: String?
        var cleanup = false
        for line in text.split(separator: "\n") {
            guard let obj = try? JSONSerialization.jsonObject(with: Data(line.utf8)) as? [String: Any] else { continue }
            switch obj["mock"] as? String {
            case "scenario": expect = obj["expect_screen"] as? String ?? ""
            case "user": steps.append(.user(screen: obj["screen"] as? String ?? "", answer: obj["answer"] as? String ?? ""))
            case "relaunch": steps.append(.relaunch)
            case "invoke": if obj["cmd"] as? String == "cleanup" { cleanup = true }
            case nil:
                if obj["type"] as? String == "result", obj["ok"] as? Bool == false, let c = obj["code"] as? String {
                    lastError = c
                }
            default: break
            }
        }
        self.name = name
        self.expectScreen = expect
        self.steps = steps
        self.lastErrorCode = lastError
        self.hasCleanup = cleanup
    }
}

/// Drives the app like a user: one directive per screen, automatic screens are passed by their own buttons.
final class UIDriver {
    let app: XCUIApplication
    let lang: String
    let timeout: TimeInterval = 15
    var visited: [String] = []
    /// Called whenever a new screen is shown (VoiceOver checks, screenshots).
    var onScreen: ((String) -> Void)?

    init(app: XCUIApplication, lang: String) {
        self.app = app
        self.lang = lang
    }

    static func launch(_ app: XCUIApplication, engine: String, lang: String, sessions: URL, afterRelaunch: Bool = false,
                       extra: [String: String] = [:]) {
        var env = ["TM_ENGINE": engine, "TM_SCENARIOS_DIR": UIRepo.scenarios.path, "TM_SESSIONS_DIR": sessions.path,
                   "TM_LANG": lang, "TM_MOCK_SPEED": "fast"]
        if afterRelaunch { env["TM_MOCK_AFTER_RELAUNCH"] = "1" }
        for (k, v) in extra { env[k] = v }
        app.launchEnvironment = env
        app.launchArguments = ["-ApplePersistenceIgnoreState", "YES"]
        app.launch()
    }

    func element(_ id: String) -> XCUIElement { app.descendants(matching: .any)[id].firstMatch }
    func button(_ id: String) -> XCUIElement { app.buttons[id].firstMatch }

    /// The id of the screen that is shown now (from `screen.<id>`), or nil while switching.
    var currentScreen: String? {
        let q = app.descendants(matching: .any).matching(NSPredicate(format: "identifier BEGINSWITH 'screen.' AND NOT (identifier == 'screen.title')"))
        guard let id = q.allElementsBoundByIndex.first?.identifier else { return nil }
        return String(id.dropFirst("screen.".count))
    }

    func waitEnabled(_ e: XCUIElement, file: StaticString = #filePath, line: UInt = #line) {
        let p = NSPredicate(format: "exists == true AND enabled == true")
        let exp = XCTNSPredicateExpectation(predicate: p, object: e)
        let r = XCTWaiter().wait(for: [exp], timeout: timeout)
        XCTAssertEqual(r, .completed, "\(e.identifier) not enabled on \(currentScreen ?? "?")", file: file, line: line)
    }

    func click(_ id: String, file: StaticString = #filePath, line: UInt = #line) {
        let b = button(id)
        waitEnabled(b, file: file, line: line)
        b.click()
    }

    func tick(_ id: String) {
        let c = app.checkBoxes[id].firstMatch
        XCTAssertTrue(c.waitForExistence(timeout: timeout), "checkbox \(id)")
        if (c.value as? Int) != 1 && (c.value as? NSNumber)?.intValue != 1 { c.click() }
    }

    func tickAll(in screen: String) {
        let boxes = element("screen.\(screen)").checkBoxes
        for i in 0..<boxes.count {
            let c = boxes.element(boundBy: i)
            if (c.value as? NSNumber)?.intValue != 1 { c.click() }
        }
    }

    func type(_ fieldId: String, _ text: String, secure: Bool = true) {
        let f = secure ? app.secureTextFields[fieldId].firstMatch : app.textFields[fieldId].firstMatch
        XCTAssertTrue(f.waitForExistence(timeout: timeout), "field \(fieldId)")
        f.click()
        f.typeText(text)
    }

    func radio(_ groupId: String, index: Int) {
        let g = app.radioGroups[groupId].firstMatch
        XCTAssertTrue(g.waitForExistence(timeout: timeout), "radio group \(groupId)")
        g.radioButtons.element(boundBy: index).click()
    }

    /// Waits for `target`, passing screens without a directive (S02, S06, S12/S13, S15, S18, S23).
    func reach(_ target: String, file: StaticString = #filePath, line: UInt = #line) {
        let end = Date().addingTimeInterval(timeout * 3)
        while Date() < end {
            if app.secureTextFields["field.prompt_password"].exists {
                // after a restart the existing backup password is asked again (memory only)
                type("field.prompt_password", "zz-test")
                click("btn.prompt_ok")
                continue
            }
            guard let s = currentScreen else { usleep(50_000); continue }
            if visited.last != s { visited.append(s); onScreen?(s) }
            if s == target { return }
            // F-FRESHNESS starts the new backup by itself; the screen may already be gone
            if target == "F-FRESHNESS" && visited.contains(target) { return }
            switch s {
            case "S02" where button("btn.continue").isEnabled: button("btn.continue").click()
            case "S06" where button("btn.continue").isEnabled: button("btn.continue").click()
            case "S23" where button("btn.resume_continue").isEnabled: button("btn.resume_continue").click()
            case "S21" where target == "S22": click("btn.guide_apple")
            default: usleep(50_000)
            }
        }
        XCTFail("screen \(target) not reached; on \(currentScreen ?? "?"), visited \(visited)", file: file, line: line)
    }

    func perform(screen: String, answer: String, file: StaticString = #filePath, line: UInt = #line) {
        reach(screen, file: file, line: line)
        switch (screen, answer) {
        case ("S00", "get_started"): click("btn.get_started")
        case ("S01", "accepted"):
            tick("check.disclaimer")
            click("btn.continue")
        case ("S03", "continue"): click("btn.continue")
        case ("S03", "prepare_android"): click("btn.prepare_android")
        case ("S10a", _):
            let pw = app.descendants(matching: .any)["label.generated_password"].firstMatch
            XCTAssertTrue(pw.waitForExistence(timeout: timeout))
            let shown = pw.value as? String ?? pw.label
            type("field.last_four", String(shown.replacingOccurrences(of: " ", with: "").suffix(4)), secure: false)
            click("btn.enable_encryption")
        case ("F-DCIM", "new_backup"), ("F-AIRPLANE", "new_backup"): click("btn.action.new_backup")
        case ("F-FRESHNESS", "new_backup"):
            if currentScreen == "F-FRESHNESS", button("btn.action.auto_new_backup").isEnabled {
                button("btn.action.auto_new_backup").click()
            }
        case ("F-PW-WRONG", "password_entered"):
            click("btn.action.reenter_password")
            type("field.prompt_password", "zz-test")
            click("btn.prompt_ok")
        case ("F-RESTORE-MID", "iphone_restarted"): click("btn.action.continue_s16")
        case ("S21", "reset_threema"):
            click("btn.action.reset_threema")
            let confirm = app.sheets.buttons[UIRepo.text("action.reset_threema", lang) ?? "-"].firstMatch
            XCTAssertTrue(confirm.waitForExistence(timeout: timeout), "R1 confirmation")
            confirm.click()
        case ("S04", "done"):
            click("btn.done")
            // the file choice starts S05 (mock runs: no file panel, the scenario's files are taken)
            click("btn.choose")
        case ("S05", "password_entered"):
            if !app.secureTextFields["field.android_password.0"].exists { click("btn.choose") }
            // one password field per chosen backup (android_two_backups: text + media backup)
            for ref in 0..<8 where app.secureTextFields["field.android_password.\(ref)"].exists {
                type("field.android_password.\(ref)", "zz-test")
            }
            click("btn.check")
            // the next directive decides where we are (S06 or S05 again)
            usleep(300_000)
        case ("S07", "all_checked"), ("S08", "all_checked"), ("S11", "all_checked"):
            tickAll(in: screen)
            click(screen == "S11" ? "btn.start_backup" : "btn.continue")
        case ("S09", "icloud"), ("S09", "finder"):
            tick("check.safety_net")
            radio("radio.safety_net", index: answer == "icloud" ? 0 : 1)
            click("btn.continue")
        case ("S10b", "password_entered"):
            radio("radio.existing_password", index: 0)          // "Ich kenne das Passwort" (nothing preselected)
            type("field.backup_password", "zz-test")
            click("btn.continue")
        case ("S14", "transfer_now"):
            tick("check.apple_backup")
            tick("check.apple_password")
            click("btn.transfer_now")
        case ("S16", _):
            radio("radio.buddy", index: ["account_only", "full_setup", "none"].firstIndex(of: answer) ?? 0)
            click("btn.continue")
        case ("S17", "old_chats_present"): click("btn.chats_ok")
        case ("S17", "problem"): click("btn.problem")
        case ("S19", "continue"): click("btn.continue")
        case ("S23", "continue"): click("btn.resume_continue")
        default: XCTFail("no UI action for \(screen)/\(answer)", file: file, line: line)
        }
    }
}

extension XCUIElement {
    /// What VoiceOver reads for a control.
    var spokenText: String {
        [label, title, (value as? String) ?? "", placeholderValue ?? ""].first { !$0.isEmpty } ?? ""
    }
}
