// SPDX-License-Identifier: AGPL-3.0-or-later
import AppKit
import SwiftUI
import XCTest
@testable import ThreemaChatTransfer

/// Snapshot and size checks (DESIGN §13.1, P4 acceptance): every screen S00–S23 and every F-screen, in German
/// (the long texts) and English, light and dark, at the smallest window size. A screen fails when any content or
/// footer row wants to be wider than the window allows (`OverflowProbe`). The rendered PNGs go to
/// `$TM_SNAPSHOT_DIR/<lang>/<appearance>/<screen>.png` for review; they are never committed (no `tm-demo` marker).
@MainActor
final class LayoutSnapshotTests: XCTestCase {
    static let size = CGSize(width: WindowMetrics.minWidth, height: WindowMetrics.minHeight)

    private var outDir: URL? {
        let env = ProcessInfo.processInfo.environment
        guard let d = env["TM_SNAPSHOT_DIR"] ?? env["TEST_RUNNER_TM_SNAPSHOT_DIR"], !d.isEmpty else { return nil }
        return URL(fileURLWithPath: d, isDirectory: true)
    }

    /// A store in the state of a screen: results from replaying a scenario up to the end.
    private func preparedStore(_ scenario: String, lang: AppLanguage) async throws -> StoreHarness {
        let (h, _) = try await playScenario(scenario, language: lang)
        return h
    }

    private func render(_ store: WizardStore, name: String, lang: AppLanguage, dark: Bool) throws -> [OverflowRegistry.Entry] {
        OverflowRegistry.start()
        let view = WizardWindowContent()
            .environmentObject(store)
            .environment(\.loc, store.loc)
            .environment(\.locale, lang.locale)
            .frame(width: Self.size.width, height: Self.size.height)
        let host = NSHostingView(rootView: view)
        host.frame = CGRect(origin: .zero, size: Self.size)
        host.appearance = NSAppearance(named: dark ? .darkAqua : .aqua)
        let window = NSWindow(contentRect: host.frame, styleMask: [.titled], backing: .buffered, defer: false)
        window.appearance = host.appearance
        window.contentView = host
        host.layoutSubtreeIfNeeded()
        RunLoop.main.run(until: Date().addingTimeInterval(0.05))
        host.layoutSubtreeIfNeeded()
        if let dir = outDir {
            let rep = try XCTUnwrap(host.bitmapImageRepForCachingDisplay(in: host.bounds))
            host.cacheDisplay(in: host.bounds, to: rep)
            let file = dir.appendingPathComponent("\(lang.rawValue)/\(dark ? "dark" : "light")/\(name).png")
            try FileManager.default.createDirectory(at: file.deletingLastPathComponent(), withIntermediateDirectories: true)
            try rep.representation(using: .png, properties: [:])?.write(to: file)
        }
        window.contentView = nil
        return OverflowRegistry.stop()
    }

    /// Sample data for each error screen (canonical example values only, DESIGN §10.4).
    static func sample(for f: FailureScreen) -> (String, JSONValue) {
        let code = Screen.representativeCode(for: f)
        let data: [String: JSONValue]
        switch code {
        case "E_HOST_SPACE", "E_GUARD_IPHONE_SPACE": data = ["need_bytes": .number(9_600_000_000), "free_bytes": .number(6_400_000_000)]
        case "E_GUARD_PHOTOS_LIMIT": data = ["limit_bytes": .number(20_000_000_000), "photos_bytes": .number(26_400_000_000)]
        case "E_IOS_UNKNOWN", "E_IOS_BLOCKED": data = ["ios_version": .string("27.0"), "ios_build": .string("24A437")]
        case "E_ANDROID_FORMAT_NEW", "E_ANDROID_FORMAT_UNVERIFIED": data = ["ref": .number(0), "format_version": .number(28)]
        case "E_THREEMA_MODEL_UNKNOWN": data = ["app_version": .string("7.4")]
        case "E_RESTORE_NOT_STARTED": data = ["reason": .string("other")]
        case "E_GUARD_FRESHNESS": data = ["age_min": .number(61), "limit_min": .number(60)]
        default: data = [:]
        }
        return (code, .object(data))
    }

    func testAllScreensFitTheSmallestWindow() async throws {
        var problems: [String] = []
        for lang in AppLanguage.allCases {
            let happy = try await preparedStore("happy", lang: lang)
            let red = try await preparedStore("data_fail", lang: lang)
            let crash = try await preparedStore("app_crash_after_send", lang: lang)
            for screen in Screen.wizardScreens {
                let h: StoreHarness
                switch screen {
                case .s21, .s22: h = red
                case .s23: h = crash
                default: h = happy
                }
                let s = h.store
                s.screen = screen
                s.error = nil
                s.inlineError = nil
                if screen == .s23 {
                    s.resume = ResumePolicy.Decision(screen: .s16, afterSend: true, canDiscard: false, closed: false,
                                                     welcomeBack: false)
                    s.resumeLastStep = .s15
                }
                if screen == .s16 { s.restoreLinkLost = true }
                if screen == .s07 { s.welcomeBack = true }
                if screen == .s10a { s.prepareGeneratedPassword() }
                for dark in [false, true] {
                    let over = try render(s, name: screen.id, lang: lang, dark: dark)
                    problems += over.map { "\(lang) \(dark ? "dark" : "light") \($0)" }
                }
            }
            for f in FailureScreen.allCases {
                let s = happy.store
                let (code, data) = Self.sample(for: f)
                s.screen = .failure(f)
                s.error = ErrorContext(codeRaw: code, data: data, origin: .s12)
                for dark in [false, true] {
                    let over = try render(s, name: f.rawValue, lang: lang, dark: dark)
                    problems += over.map { "\(lang) \(dark ? "dark" : "light") \($0)" }
                }
            }
            // S03 red: iOS build not verified (DESIGN §8.3 S03 "Rot: iOS unbekannt")
            do {
                let s = happy.store
                s.screen = .s03
                s.error = nil
                s.iosUnverifiedCode = EngineCode.E_IOS_UNKNOWN.rawValue
                for dark in [false, true] {
                    problems += try render(s, name: "S03-ios_unknown", lang: lang, dark: dark).map { "\(lang) \($0)" }
                }
                s.iosUnverifiedCode = nil
            }
            // S10b in both choices; "Ich weiß es nicht" with every conditional box (Finder safety net, still on)
            do {
                let s = happy.store
                s.screen = .s10b
                s.error = nil
                s.inlineError = nil
                let net = s.answers.safetyNet
                s.existingPasswordChoice = .known
                for dark in [false, true] {
                    problems += try render(s, name: "S10b-known", lang: lang, dark: dark).map { "\(lang) \($0)" }
                }
                s.existingPasswordChoice = .unknown
                s.answers.safetyNet = "finder"
                s.encryptionStillOn = true
                for dark in [false, true] {
                    problems += try render(s, name: "S10b-unknown", lang: lang, dark: dark).map { "\(lang) \($0)" }
                }
                s.existingPasswordChoice = nil
                s.answers.safetyNet = net
                s.encryptionStillOn = false
            }
            // S21 in all four variants (DESIGN §15 screenshot list)
            for v in ["threema_only", "data", "data_keychain", "setup_full"] {
                let s = red.store
                s.screen = .s21
                s.postcheck = try JSONValue.object(["verdict": .string(v), "notes": .array([]),
                                                    "areas": .array([.object(["area": .string("preferences"),
                                                                              "severity": .string("alert")])]),
                                                    "threema_ok": .bool(v != "threema_only")]).decode(PostcheckResult.self)
                let over = try render(s, name: "S21-\(v)", lang: lang, dark: false)
                problems += over.map { "\(lang) \($0)" }
            }
        }
        XCTAssertTrue(problems.isEmpty, "content wider than the smallest window:\n" + problems.joined(separator: "\n"))
    }

    func testTheProbeDetectsOverflow() throws {
        OverflowRegistry.start()
        let view = OverflowProbe(id: "probe") {
            HStack { Text(String(repeating: "Sicherheitskopie ", count: 20)).fixedSize() }
        }
        .frame(width: 200, height: 50)
        let host = NSHostingView(rootView: view)
        host.frame = CGRect(x: 0, y: 0, width: 200, height: 50)
        host.layoutSubtreeIfNeeded()
        let entries = OverflowRegistry.stop()
        XCTAssertEqual(entries.first?.id, "probe")
    }

    func testDemoWatermarkOnEveryDemoWindow() async throws {
        let h = try StoreHarness(scenario: "happy")
        XCTAssertTrue(h.store.deps.isDemo)
        let view = WizardWindowContent().environmentObject(h.store).environment(\.loc, h.store.loc)
        let host = NSHostingView(rootView: view)
        host.frame = CGRect(origin: .zero, size: Self.size)
        host.layoutSubtreeIfNeeded()
        // the watermark is part of the accessibility tree with its own label
        XCTAssertTrue(Localizer(.de).has("common.demo.a11y"))
    }
}
