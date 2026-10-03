// SPDX-License-Identifier: AGPL-3.0-or-later
import XCTest

/// Mock-mode screenshots through UI automation (review aid; runs only when `TM_SCREENSHOT_LIST`
/// (docs/images/screenshots.json) and `TM_SCREENSHOT_DIR` are set). The screenshots of the user guide come from the
/// real engine on the virtual iPhone instead: `devtools/demo_screenshots.py` drives the signed app with its demo
/// autopilot (`DemoAutopilot`, no UI automation permission needed; `make demo-screenshots`, demo-screenshots.yml).
/// This test always uses the MockEngine: its UI driver types the test passwords, which the virtual iPhone would
/// refuse. Every image carries the DEMO watermark. One PNG per list item: `<TM_SCREENSHOT_DIR>/<id>.png`.
final class DemoScreenshotTests: XCTestCase {
    struct Item { let id: String; let scenario: String }

    private var env: [String: String] { ProcessInfo.processInfo.environment }

    private func items() throws -> [Item] {
        guard let list = env["TM_SCREENSHOT_LIST"], !list.isEmpty else { return [] }
        let obj = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: list))) as? [String: Any]
        let app = (obj?["groups"] as? [String: Any])?["app"] as? [String: Any]
        return (app?["items"] as? [[String: Any]] ?? []).compactMap {
            guard let id = $0["id"] as? String, let s = $0["scenario"] as? String else { return nil }
            return Item(id: id, scenario: s)
        }
    }

    private var language: String {
        if let l = env["TM_LANG"], ["de", "en"].contains(l) { return l }
        if let d = env["TM_SCREENSHOT_DIR"], let last = d.split(separator: "/").last, ["de", "en"].contains(String(last)) {
            return String(last)
        }
        return (Locale.preferredLanguages.first ?? "en").hasPrefix("de") ? "de" : "en"
    }

    func testDemoScreenshots() throws {
        let all = try items()
        guard !all.isEmpty, let outPath = env["TM_SCREENSHOT_DIR"], !outPath.isEmpty else {
            throw XCTSkip("TM_SCREENSHOT_LIST / TM_SCREENSHOT_DIR not set")
        }
        let out = URL(fileURLWithPath: outPath, isDirectory: true)
        try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
        let lang = language
        var skipped: [String] = []
        let byScenario = Dictionary(grouping: all, by: \.scenario)
        for (scenario, wanted) in byScenario.sorted(by: { $0.key < $1.key }) {
            guard FileManager.default.fileExists(atPath: UIRepo.scenarios.appendingPathComponent("\(scenario).jsonl").path),
                  let script = try? UIScenario(scenario) else {
                skipped += wanted.map { "\($0.id) (no scenario \(scenario))" }
                continue
            }
            var pending = Dictionary(uniqueKeysWithValues: wanted.map { ($0.id, $0) })
            let sessions = FileManager.default.temporaryDirectory.appendingPathComponent("tct-demo-\(UUID().uuidString)")
            let engine = "mock:\(scenario)"
            let extra = ["TM_DEMO": "1", "TM_MOCK_SPEED": "demo"]
            let app = XCUIApplication()
            UIDriver.launch(app, engine: engine, lang: lang, sessions: sessions, extra: extra)
            var driver = UIDriver(app: app, lang: lang)
            let shoot: (String) -> Void = { screen in
                let id = screen == "S21" ? pending.keys.first { $0.hasPrefix("S21-") } ?? screen : screen
                guard pending[id] != nil else { return }
                usleep(400_000)   // let progress settle
                let png = app.windows.firstMatch.screenshot().pngRepresentation
                try? png.write(to: out.appendingPathComponent("\(id).png"))
                pending[id] = nil
            }
            driver.onScreen = shoot
            _ = driver.element("screen.S00").waitForExistence(timeout: 30)
            for step in script.steps {
                if pending.isEmpty { break }
                switch step {
                case .user(let s, let a): driver.perform(screen: s, answer: a)
                case .relaunch:
                    app.terminate()
                    UIDriver.launch(app, engine: engine, lang: lang, sessions: sessions, afterRelaunch: true, extra: extra)
                    driver = UIDriver(app: app, lang: lang)
                    driver.onScreen = shoot
                }
            }
            if !pending.isEmpty, script.hasCleanup { driver.reach("S20") }
            if !pending.isEmpty { driver.reach(script.expectScreen) }
            if pending["S22"] != nil, driver.currentScreen == "S21" { driver.reach("S22") }
            skipped += pending.keys.sorted().map { "\($0) (not reached in \(scenario))" }
            app.terminate()
            try? FileManager.default.removeItem(at: sessions)
        }
        if !skipped.isEmpty {
            let note = XCTAttachment(string: skipped.joined(separator: "\n"))
            note.name = "screenshots not produced"
            note.lifetime = .keepAlways
            add(note)
        }
    }
}
