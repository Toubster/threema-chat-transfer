// SPDX-License-Identifier: AGPL-3.0-or-later
import XCTest
@testable import ThreemaChatTransfer

/// CommandTracker (DESIGN §5.6), stdin secrets (§5.1), MockEngine replay and the LiveEngine process plumbing.
final class EngineClientTests: XCTestCase {
    // MARK: CommandTracker

    private func track(_ lines: [String], exit: Int32 = 0, crashed: Bool = false,
                       expectVersion: String? = nil) -> CommandOutcome {
        var t = CommandTracker(command: .restore, expectedEngineVersion: expectVersion)
        for l in lines { if let e = EngineEvent.parse(l) { t.consume(e) } else { t.invalidLine("not_json") } }
        return t.outcome(exitCode: exit, crashed: crashed)
    }

    func testResultDecides() {
        let ok = track([Events.hello("restore"), Events.result("restore", seq: 2, ok: true, code: "R_OK", deviceModified: "yes")])
        guard case .success(let r) = ok else { return XCTFail("\(ok)") }
        XCTAssertEqual(r.deviceModified, "yes")
        let fail = track([Events.hello("restore"), Events.result("restore", seq: 2, ok: false, code: "E_GUARD_FINDMY")], exit: 1)
        guard case .failure(let f) = fail else { return XCTFail("\(fail)") }
        XCTAssertEqual(f.code, .E_GUARD_FINDMY)
    }

    func testMissingResultAfterCriticalMeansPossiblySent() {
        let o = track([Events.hello("restore"), Events.critical("restore", seq: 2, on: true)], exit: -9, crashed: true)
        XCTAssertEqual(o, .lostDuringCritical(exitCode: -9))
        // even if critical was switched off again before the crash
        let o2 = track([Events.hello("restore"), Events.critical("restore", seq: 2, on: true),
                        Events.critical("restore", seq: 3, on: false)], exit: 1)
        XCTAssertEqual(o2, .lostDuringCritical(exitCode: 1))
    }

    func testMissingResultWithoutCriticalIsInternal() {
        XCTAssertEqual(track([Events.hello("restore")], exit: 1, crashed: true),
                       .internalError(reason: "crashed", codeRaw: nil))
        XCTAssertEqual(track([], exit: 127), .internalError(reason: "no_result", codeRaw: nil))
    }

    func testProtocolViolations() {
        XCTAssertEqual(track([Events.hello("restore", protocolVersion: 2),
                              Events.result("restore", seq: 2, ok: true, code: "R_OK")]),
                       .internalError(reason: "protocol_mismatch", codeRaw: "R_OK"))
        XCTAssertEqual(track([Events.hello("restore", version: "9.9.9"), Events.result("restore", seq: 2, ok: true, code: "R_OK")],
                             expectVersion: "0.4.0"),
                       .internalError(reason: "engine_version_mismatch", codeRaw: "R_OK"))
        XCTAssertEqual(track([Events.hello("restore"), Events.result("restore", seq: 2, ok: true, code: "E_INTERNAL")]),
                       .internalError(reason: "ok_code_mismatch", codeRaw: "E_INTERNAL"))
        XCTAssertEqual(track([Events.result("restore", seq: 1, ok: true, code: "R_OK")]),
                       .internalError(reason: "hello_missing", codeRaw: nil))
    }

    // MARK: secrets on stdin

    func testStdinIsExactlyOneJSONLineWithOnlyTheSetFields() throws {
        let secret = "pw-1"
        let s = EngineSecrets(backupPassword: secret, androidPasswords: [0: "a0", 2: "a2"])
        let line = s.stdinLine()
        XCTAssertEqual(line.last, 0x0A)
        XCTAssertEqual(line.filter { $0 == 0x0A }.count, 1)
        let obj = try XCTUnwrap(try JSONSerialization.jsonObject(with: line) as? [String: Any])
        XCTAssertEqual(Set(obj.keys), ["backup_password", "android_passwords"])
        XCTAssertEqual(obj["android_passwords"] as? [String: String], ["0": "a0", "2": "a2"])
        XCTAssertEqual(s.fieldNames.sorted(), ["android_passwords", "backup_password"])
    }

    // MARK: MockEngine

    func testEveryScenarioFileParses() throws {
        for name in Repo.scenarioNames {
            let s = try Repo.scenario(name)
            XCTAssertEqual(s.header.name, name)
            XCTAssertNotNil(s.header.expectScreen.flatMap(Screen.init(id:)), "\(name): expect_screen is a screen")
            for b in s.blocks {
                XCTAssertNotNil(EngineCommand(rawValue: b.cmd), "\(name): \(b.cmd)")
                XCTAssertFalse(b.lines.isEmpty, "\(name): \(b.cmd) has no events")
                for l in b.lines { XCTAssertNotNil(EngineEvent.parse(l), "\(name): unparseable \(l.prefix(80))") }
            }
        }
    }

    func testMockRebasesTimestampsAndCancels() async throws {
        let script = try Repo.scenario("happy")
        let now = Date()
        let engine = MockEngine(script: script, speed: .instant, now: now)
        let h = engine.start(EngineInvocation(command: .hostCheck, args: [], session: nil))
        var events: [EngineEvent] = []
        for await o in h.output { if case .event(let e) = o { events.append(e) } }
        let first = try XCTUnwrap(Formatters.parseTimestamp(events.first?.ts))
        XCTAssertLessThan(abs(first.timeIntervalSince(now)), 60, "recorded start is rebased onto now")
        XCTAssertEqual(events.last?.type, "result")
    }

    // MARK: LiveEngine plumbing with a stub interpreter (no real engine, no device)

    private func stub(_ body: String) throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("tct-stub-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        let url = dir.appendingPathComponent("python3")
        try ("#!/bin/sh\n" + body).write(to: url, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: url.path)
        return url
    }

    private func session() throws -> URL {
        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("tct-live-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        return dir
    }

    private func collect(_ h: EngineHandle) async -> ([EngineEvent], Int32, Bool) {
        var events: [EngineEvent] = []
        var exit: (Int32, Bool) = (0, false)
        for await o in h.output {
            switch o {
            case .event(let e): events.append(e)
            case .exit(let c, let crashed): exit = (c, crashed)
            case .invalidLine: break
            }
        }
        return (events, exit.0, exit.1)
    }

    func testLiveEngineArgvEnvironmentStdinAndLogs() async throws {
        let stubSecret = "stub-secret"
        let s = try session()
        let py = try stub("""
        cat > "$TMPDIR/stdin.txt"
        env > "$TMPDIR/env.txt"
        echo "$@" > "$TMPDIR/argv.txt"
        echo "debug line" >&2
        echo '\(Events.hello("backup"))'
        echo '\(Events.result("backup", seq: 2, ok: false, code: "E_BACKUP_FAILED", data: "{\"attempts\":3}"))'
        exit 3
        """)
        let engine = LiveEngine(resources: s, interpreter: py, useSandbox: false)
        let inv = EngineInvocation(command: .backup, args: ["--session", s.path, "--role", "pre", "--secrets-stdin"],
                                   secrets: EngineSecrets(backupPassword: stubSecret), session: s)
        let (events, code, crashed) = await collect(engine.start(inv))
        XCTAssertEqual(code, 3)
        XCTAssertFalse(crashed)
        XCTAssertEqual(events.map(\.type), ["hello", "result"])
        let tmp = s.appendingPathComponent("work/tmp")
        let stdin = try String(contentsOf: tmp.appendingPathComponent("stdin.txt"), encoding: .utf8)
        XCTAssertEqual(stdin, "{\"backup_password\":\"stub-secret\"}\n")
        let argv = try String(contentsOf: tmp.appendingPathComponent("argv.txt"), encoding: .utf8)
        XCTAssertTrue(argv.hasPrefix("-I -B -m tmcore backup --session"), argv)
        XCTAssertFalse(argv.contains("stub-secret"))
        let env = try String(contentsOf: tmp.appendingPathComponent("env.txt"), encoding: .utf8)
        let keys = Set(env.split(separator: "\n").compactMap { $0.split(separator: "=").first.map(String.init) })
            .subtracting(["PWD", "SHLVL", "_", "OLDPWD"])   // set by /bin/sh itself
        XCTAssertEqual(keys, ["PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE", "LANG", "TMPDIR", "TMCORE_PROTOCOL",
                              "TMCORE_RESOURCES"], "nothing inherited")
        XCTAssertFalse(env.contains("stub-secret"))
        let debug = try String(contentsOf: s.appendingPathComponent("logs/debug.log"), encoding: .utf8)
        XCTAssertEqual(debug, "debug line\n")
        let log = try String(contentsOf: s.appendingPathComponent("logs/events.jsonl"), encoding: .utf8)
        XCTAssertEqual(log.split(separator: "\n").count, 2)
        let mode = try FileManager.default.attributesOfItem(atPath: s.appendingPathComponent("logs/debug.log").path)[.posixPermissions] as? Int
        XCTAssertEqual(mode, 0o600)
    }

    func testLiveEngineWithoutSecretsGetsDevNull() async throws {
        let s = try session()
        let py = try stub("""
        if [ -t 0 ]; then t=tty; else t=$(head -c 1 | wc -c | tr -d ' '); fi
        echo "$t" > "$TMPDIR/stdin_bytes.txt"
        echo '\(Events.hello("session-status"))'
        echo '\(Events.result("session-status", seq: 2, ok: true, code: "R_OK", data: "{\"phase\":\"new\",\"resume_at\":\"S02\"}"))'
        """)
        let engine = LiveEngine(resources: s, interpreter: py, useSandbox: false)
        let (events, code, _) = await collect(engine.start(EngineInvocation(command: .sessionStatus,
                                                                            args: ["--session", s.path], session: s)))
        XCTAssertEqual(code, 0)
        XCTAssertEqual(events.last?.code, "R_OK")
        let n = try String(contentsOf: s.appendingPathComponent("work/tmp/stdin_bytes.txt"), encoding: .utf8)
        XCTAssertEqual(n.trimmingCharacters(in: .whitespacesAndNewlines), "0")
    }

    func testLiveEngineCrashAfterCritical() async throws {
        let s = try session()
        let py = try stub("""
        echo '\(Events.hello("restore"))'
        echo '\(Events.critical("restore", seq: 2, on: true))'
        kill -9 $$
        """)
        let engine = LiveEngine(resources: s, interpreter: py, useSandbox: false)
        let (events, _, crashed) = await collect(engine.start(EngineInvocation(command: .restore, args: [], session: s)))
        XCTAssertTrue(crashed)
        var t = CommandTracker(command: .restore, expectedEngineVersion: nil)
        events.forEach { t.consume($0) }
        if case .lostDuringCritical = t.outcome(exitCode: 9, crashed: true) {} else { XCTFail("must be lostDuringCritical") }
    }

    func testLiveEngineMissingInterpreterFailsClosed() async throws {
        let s = try session()
        let engine = LiveEngine(resources: s, interpreter: s.appendingPathComponent("missing"), useSandbox: false)
        let (events, code, _) = await collect(engine.start(EngineInvocation(command: .version, args: [], session: nil)))
        XCTAssertTrue(events.isEmpty)
        XCTAssertEqual(code, 127)
    }
}
