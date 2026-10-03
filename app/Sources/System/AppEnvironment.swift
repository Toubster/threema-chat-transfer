// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation

/// Runtime configuration from the environment / launch arguments (`-TM_ENGINE mock:happy` works too).
///
/// | Variable | Meaning |
/// |---|---|
/// | `TM_ENGINE` | `live` (default), `fake:<scenario>` (real engine + virtual iPhone, demo), `mock:<scenario>` |
/// | `TM_SCENARIOS_DIR` | folder with `<scenario>.jsonl` for the MockEngine (default: bundled `Scenarios`) |
/// | `TM_MOCK_SPEED` | `instant`, `fast`, `demo` (default) |
/// | `TM_MOCK_AFTER_RELAUNCH` | `1`: continue a scenario after its `relaunch` marker (resume tests) |
/// | `TM_SESSIONS_DIR` | sessions root (tests); default `~/Library/Application Support/Chat Transfer for Threema/sessions` for the
/// |   | live engine and `…/Chat Transfer for Threema/demo-sessions` for mock/fake runs (demo data never mixes with a real session) |
/// | `TM_LANG` | `de` / `en` (default: macOS language) |
/// | `TM_DEMO` | `1`: show the DEMO watermark |
/// | `TM_DEMO_RESOURCES` | staged `Resources/` with the real engine, only honoured for `fake:` (demo screenshots) |
/// | `TM_DEMO_FIXTURES` | `fixtures/` of a checkout for `fake:` runs (the virtual iPhone's backup generator) |
/// | `TM_AUTOPILOT` | `1`: play the scenario's user directives and save screenshots (`DemoAutopilot`, demo only) |
/// | `TM_APPEARANCE` | `light` / `dark`: fixed appearance for demo screenshots (demo only) |
/// | `TM_PYTHON` | Debug builds only: interpreter for `live` from a source checkout (integration) |
public struct AppEnvironment {
    public var engineSpec: String
    public var scenariosDir: URL?
    public var mockSpeed: MockEngine.Speed
    public var mockAfterRelaunch: Bool
    public var sessionsRootOverride: URL?
    public var language: AppLanguage
    public var languageFromEnvironment: Bool
    public var demo: Bool
    public var demoResources: URL?
    public var demoFixtures: URL?
    public var appearance: String?

    public static func current(_ env: [String: String] = ProcessInfo.processInfo.environment,
                               defaults: UserDefaults = .standard) -> AppEnvironment {
        func value(_ k: String) -> String? {
            if let v = env[k], !v.isEmpty { return v }
            if let v = defaults.string(forKey: k), !v.isEmpty { return v }
            return nil
        }
        let spec = value("TM_ENGINE") ?? "live"
        return AppEnvironment(
            engineSpec: spec,
            scenariosDir: value("TM_SCENARIOS_DIR").map { URL(fileURLWithPath: $0) },
            mockSpeed: value("TM_MOCK_SPEED").flatMap(MockEngine.Speed.init(rawValue:)) ?? .demo,
            mockAfterRelaunch: value("TM_MOCK_AFTER_RELAUNCH") == "1",
            sessionsRootOverride: value("TM_SESSIONS_DIR").map { URL(fileURLWithPath: $0) },
            language: value("TM_LANG").flatMap(AppLanguage.init(rawValue:)) ?? AppLanguage.systemDefault(),
            languageFromEnvironment: value("TM_LANG").flatMap(AppLanguage.init(rawValue:)) != nil,
            demo: value("TM_DEMO") == "1",
            demoResources: value("TM_DEMO_RESOURCES").map { URL(fileURLWithPath: $0, isDirectory: true) },
            demoFixtures: value("TM_DEMO_FIXTURES").map { URL(fileURLWithPath: $0, isDirectory: true) },
            appearance: value("TM_APPEARANCE"))
    }

    /// Sessions root for an engine kind. Demo runs (mock/fake) use their own folder.
    public func sessionsRoot(for kind: EngineKind) -> URL {
        if let o = sessionsRootOverride { return o }
        if kind == .live { return SessionStore.standardRoot }
        return SessionStore.standardRoot.deletingLastPathComponent().appendingPathComponent("demo-sessions")
    }

    public enum EngineError: Error { case scenarioNotFound(String) }

    /// Builds the engine client. A mock scenario that cannot be loaded falls back to the live engine refusing
    /// nothing silently: the error is surfaced as F-INTERNAL by the caller.
    public func makeEngine(now: Date = Date()) throws -> EngineClient {
        if engineSpec.hasPrefix("mock:") {
            let name = String(engineSpec.dropFirst(5))
            guard name.range(of: "^[a-z0-9_]{1,60}$", options: .regularExpression) != nil else {
                throw EngineError.scenarioNotFound(name)
            }
            let dir = scenariosDir ?? Bundle.main.resourceURL?.appendingPathComponent("Scenarios")
            guard let url = dir?.appendingPathComponent("\(name).jsonl"),
                  let script = try? ScenarioScript.load(url) else { throw EngineError.scenarioNotFound(name) }
            return MockEngine(script: script, speed: mockSpeed, startAfterRelaunch: mockAfterRelaunch, now: now)
        }
        if engineSpec.hasPrefix("fake:") {
            let name = String(engineSpec.dropFirst(5))
            guard name.range(of: "^[a-z0-9_]{1,60}$", options: .regularExpression) != nil else {
                throw EngineError.scenarioNotFound(name)
            }
            if let r = demoResources { return LiveEngine(resources: r, fakeScenario: name, demoFixtures: demoFixtures) }
            return LiveEngine(fakeScenario: name, demoFixtures: demoFixtures)
        }
        guard engineSpec == "live" else { throw EngineError.scenarioNotFound(engineSpec) }
        #if DEBUG
        // integrator runs from a source checkout: a local interpreter with tmcore installed (never in Release)
        if let p = ProcessInfo.processInfo.environment["TM_PYTHON"], !p.isEmpty {
            return LiveEngine(interpreter: URL(fileURLWithPath: p))
        }
        #endif
        return LiveEngine()
    }

    public var isDemo: Bool { demo || !engineSpec.hasPrefix("live") }
}
