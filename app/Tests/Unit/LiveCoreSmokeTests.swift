// SPDX-License-Identifier: AGPL-3.0-or-later
import XCTest
@testable import ThreemaChatTransfer

/// LiveEngine against the real `core/tmcore` of this checkout (read-only commands only: `version`, `host-check`,
/// `session-status`; never a device command). Opt-in: runs when the repository's `.venv/bin/python3` exists and is
/// skipped otherwise (CI runs the engine contract in its own jobs). A wrapper replaces the bundled interpreter until
/// packaging installs `tmcore` into the runtime (P5).
@MainActor
final class LiveCoreSmokeTests: XCTestCase {
    private func engine() throws -> LiveEngine {
        let py = Repo.root.appendingPathComponent(".venv/bin/python3")
        guard FileManager.default.isExecutableFile(atPath: py.path),
              FileManager.default.fileExists(atPath: Repo.root.appendingPathComponent("core/tmcore/__main__.py").path)
        else { throw XCTSkip("no local core checkout with .venv") }
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("tct-core-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let wrapper = dir.appendingPathComponent("python3")
        // argv from LiveEngine: -I -B -m tmcore <command> …; run the checkout's package instead of site-packages
        try """
        #!/bin/sh
        shift 4
        PYTHONPATH='\(Repo.root.appendingPathComponent("core").path)' exec '\(py.path)' -B -m tmcore "$@"
        """.write(to: wrapper, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: wrapper.path)
        return LiveEngine(resources: dir, interpreter: wrapper, useSandbox: false)
    }

    private func run(_ e: LiveEngine, _ inv: EngineInvocation) async -> (CommandOutcome, [EngineEvent]) {
        var t = CommandTracker(command: inv.command, expectedEngineVersion: nil)
        var events: [EngineEvent] = []
        var exit: (Int32, Bool) = (0, false)
        for await o in e.start(inv).output {
            switch o {
            case .event(let ev): events.append(ev); t.consume(ev)
            case .invalidLine(let r): t.invalidLine(r)
            case .exit(let c, let crashed): exit = (c, crashed)
            }
        }
        return (t.outcome(exitCode: exit.0, crashed: exit.1), events)
    }

    func testVersionAndHostCheckDecode() async throws {
        let e = try engine()
        let (v, events) = await run(e, EngineInvocation(command: .version, args: [], session: nil))
        guard case .success = v else { return XCTFail("version: \(v)") }
        XCTAssertEqual(events.first?.protocol, engineProtocolVersion)
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("tct-hc-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let (h, hEvents) = await run(e, EngineInvocation(command: .hostCheck, args: ["--workdir", root.path], session: nil))
        switch h {
        case .success(let r):
            XCTAssertNoThrow(try r.data.decode(HostCheckResult.self), "app's HostCheckResult matches the engine")
            XCTAssertTrue(hEvents.contains { $0.type == "check" })
        case .failure(let r):
            XCTAssertTrue(r.codeRaw.hasPrefix("E_HOST_") || r.codeRaw == "E_INTERNAL", "host-check: \(r.codeRaw)")
        default:
            XCTFail("host-check: \(h)")
        }
    }

    func testEngineAcceptsTheSessionFolderTheAppCreates() async throws {
        let e = try engine()
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("tct-ss-\(UUID().uuidString)")
        let store = SessionStore(root: root, excludeFromBackups: false)
        let dir = try store.create { id in
            var s = SessionState(sessionId: id, now: Date(), appVersion: "0.4.0-dev", locale: .de)
            s.answers.disclaimerAcceptedAt = Formatters.timestamp(Date())
            return s
        }
        let (o, _) = await run(e, EngineInvocation(command: .sessionStatus, args: ["--session", dir.path], session: dir))
        switch o {
        case .success(let r):
            let st = try r.data.decode(SessionStatusResult.self)
            XCTAssertNotNil(Screen(id: st.resumeAt), "resume_at is a screen id")
            XCTAssertFalse(ResumePolicy.decide(status: st, local: store.state, now: Date()).afterSend)
        case .failure(let r) where r.codeRaw == "E_INTERNAL" && r.data["sub"]?.string == "not_implemented":
            throw XCTSkip("session-status not implemented yet")
        default:
            XCTFail("session.json written by the app was not accepted: \(o)")
        }
    }
}
