// SPDX-License-Identifier: AGPL-3.0-or-later
import XCTest
@testable import ThreemaChatTransfer

/// One version number for app, engine and importer (DESIGN §5.7, §14.2).
final class AppVersionTests: XCTestCase {
    func testDefaultEngineVersionMatchesTmcore() throws {
        let initPy = try String(contentsOf: Repo.root.appendingPathComponent("core/tmcore/__init__.py"), encoding: .utf8)
        let project = try String(contentsOf: Repo.appDir.appendingPathComponent("project.yml"), encoding: .utf8)
        let core = try XCTUnwrap(initPy.range(of: #"__version__ = "([^"]+)""#, options: .regularExpression)
            .map { String(initPy[$0]).components(separatedBy: "\"")[1] })
        XCTAssertTrue(project.contains("TM_ENGINE_VERSION: \"\(core)\""), "project.yml TM_ENGINE_VERSION != tmcore \(core)")
        XCTAssertTrue(project.contains("MARKETING_VERSION: \"\(core.components(separatedBy: "-")[0])\""))
    }

    func testTheRunningAppReportsTheFullEngineVersion() {
        // the test host is the app; its Info.plist carries TMEngineVersion
        XCTAssertFalse(AppInfo.engineVersion.contains("$("))
        XCTAssertTrue(AppInfo.engineVersion.hasPrefix(AppInfo.version), "\(AppInfo.engineVersion) vs \(AppInfo.version)")
    }
}

/// `{x} GB` never reads "0 GB" for data that exists (small synthetic or text-only backups).
final class FormattersTests: XCTestCase {
    func testGigabytes() {
        XCTAssertEqual(Formatters.gigabytes(6_400_000_000, lang: .de), "6,4")
        XCTAssertEqual(Formatters.gigabytes(6_400_000_000, lang: .en), "6.4")
        XCTAssertEqual(Formatters.gigabytes(220_000_000_000, lang: .en), "220")
        XCTAssertEqual(Formatters.gigabytes(70_000_000, lang: .de), "0,1")
        XCTAssertEqual(Formatters.gigabytes(3_000_000, lang: .de), "< 0,1")
        XCTAssertEqual(Formatters.gigabytes(3_000_000, lang: .en), "< 0.1")
        XCTAssertEqual(Formatters.gigabytes(0, lang: .en), "0")
    }
}

/// The demo autopilot never runs with the live engine (it would act on a real iPhone).
@MainActor
final class DemoAutopilotTests: XCTestCase {
    func testOnlyForDemoEngines() {
        let env = ["TM_AUTOPILOT": "1", "TM_SCREENSHOT_IDS": "S00, S21-data"]
        XCTAssertNil(DemoAutopilot.Config.from(env, engineSpec: "live"))
        XCTAssertNil(DemoAutopilot.Config.from([:], engineSpec: "fake:happy"))
        XCTAssertEqual(DemoAutopilot.Config.from(env, engineSpec: "fake:happy")?.scenario, "happy")
        XCTAssertEqual(DemoAutopilot.Config.from(env, engineSpec: "mock:data_fail")?.wanted, ["S00", "S21-data"])
    }

    func testLiveEngineNeverGetsDemoFixtures() {
        let fixtures = URL(fileURLWithPath: "/tmp/fixtures")
        let live = LiveEngine(resources: URL(fileURLWithPath: "/tmp"), demoFixtures: fixtures)
        let fake = LiveEngine(resources: URL(fileURLWithPath: "/tmp"), fakeScenario: "happy", demoFixtures: fixtures)
        let inv = EngineInvocation(command: .version, args: [], session: nil)
        XCTAssertNil(live.environment(for: inv)["TMCORE_FIXTURES"])
        XCTAssertEqual(fake.environment(for: inv)["TMCORE_FIXTURES"], "/tmp/fixtures")
        XCTAssertEqual(Set(live.environment(for: inv).keys), ["PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE", "LANG",
                                                              "TMPDIR", "TMCORE_PROTOCOL", "TMCORE_RESOURCES"])
    }
}
