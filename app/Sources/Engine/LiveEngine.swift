// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation
import os

/// Starts the bundled `tmcore` (one process per command, DESIGN §3.3/§5.1):
///
///     <App>/Contents/Resources/core/python/bin/python3 -I -B -m tmcore <command> [args] [--fake-device <scenario>]
///
/// The environment is set completely (nothing inherited), secrets go to stdin as one JSON line followed by EOF,
/// stdout is parsed line by line (and copied to `<session>/logs/events.jsonl`), stderr goes to
/// `<session>/logs/debug.log` and is never shown. When macOS provides `sandbox-exec`, the engine runs under
/// `engine-sandbox.sb` (no network except the usbmuxd socket, DESIGN §10.1).
///
/// Bundle layout (packaging/): `Contents/Resources/core/python/bin/python3` with `tmcore` in its site-packages,
/// `Contents/Resources/{bin,models,compat,legal}` and `engine-sandbox.sb`; `TMCORE_RESOURCES` = `Contents/Resources`.
/// Demo runs (`fake:<scenario>`) may get the synthetic fixtures of a checkout (`demoFixtures` → `TMCORE_FIXTURES`):
/// the virtual iPhone builds its backups from `fixtures/gen_ios_backup.py`, which the shipped bundle does not carry.
public final class LiveEngine: EngineClient {
    public let kind: EngineKind
    public var requiresMatchingEngineVersion: Bool { true }
    public let writesEventLog = true
    private let resources: URL
    private let fakeScenario: String?
    private let demoFixtures: URL?
    private let interpreter: URL?
    /// Run under `sandbox-exec` when available (off only for the stub-process unit test).
    private let useSandbox: Bool
    static let log = Logger(subsystem: AppInfo.logSubsystem, category: "engine")

    /// `interpreter` replaces the bundled python (unit tests use a stub script); the integrator never sets it.
    public init(resources: URL = Bundle.main.resourceURL ?? URL(fileURLWithPath: "/"), fakeScenario: String? = nil,
                demoFixtures: URL? = nil, interpreter: URL? = nil, useSandbox: Bool = true) {
        self.resources = resources
        self.fakeScenario = fakeScenario
        self.demoFixtures = fakeScenario == nil ? nil : demoFixtures     // never for the live engine
        self.interpreter = interpreter
        self.useSandbox = useSandbox
        self.kind = fakeScenario.map { .fake(scenario: $0) } ?? .live
    }

    var pythonURL: URL {
        if let i = interpreter { return i }
        return resources.appendingPathComponent("core/python/bin/python3")
    }

    var sandboxProfile: URL? {
        guard useSandbox else { return nil }
        let profile = resources.appendingPathComponent("engine-sandbox.sb")
        let tool = URL(fileURLWithPath: "/usr/bin/sandbox-exec")
        let fm = FileManager.default
        guard fm.fileExists(atPath: profile.path), fm.isExecutableFile(atPath: tool.path) else { return nil }
        return profile
    }

    /// argv after the interpreter.
    func arguments(for inv: EngineInvocation) -> [String] {
        var a = ["-I", "-B", "-m", "tmcore", inv.command.rawValue] + inv.args
        if let s = fakeScenario { a += ["--fake-device", s] }
        return a
    }

    /// The complete environment of the engine process (ENGINE-PROTOCOL §1).
    func environment(for inv: EngineInvocation) -> [String: String] {
        let tmp = inv.session?.appendingPathComponent("work/tmp").path ?? NSTemporaryDirectory()
        var env = ["PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1", "LANG": "C.UTF-8", "TMPDIR": tmp,
                   "TMCORE_PROTOCOL": String(engineProtocolVersion), "TMCORE_RESOURCES": resources.path]
        if fakeScenario != nil, let f = demoFixtures { env["TMCORE_FIXTURES"] = f.path }
        return env
    }

    public func start(_ inv: EngineInvocation) -> EngineHandle {
        let handle = LiveHandle()
        let process = Process()
        if let profile = sandboxProfile {
            process.executableURL = URL(fileURLWithPath: "/usr/bin/sandbox-exec")
            process.arguments = ["-f", profile.path, pythonURL.path] + arguments(for: inv)
        } else {
            process.executableURL = pythonURL
            process.arguments = arguments(for: inv)
        }
        process.environment = environment(for: inv)
        if let s = inv.session { process.currentDirectoryURL = s }
        if let s = inv.session {
            try? FileManager.default.createDirectory(at: s.appendingPathComponent("work/tmp"),
                                                     withIntermediateDirectories: true,
                                                     attributes: [.posixPermissions: 0o700])
        }
        let logs = inv.session?.appendingPathComponent("logs")
        let eventsLog = logs.flatMap { LiveHandle.appendHandle($0.appendingPathComponent("events.jsonl")) }
        let debugLog = logs.flatMap { LiveHandle.appendHandle($0.appendingPathComponent("debug.log")) }

        let stdout = Pipe()
        process.standardOutput = stdout
        process.standardError = debugLog ?? FileHandle.nullDevice
        let stdin: Pipe? = inv.secrets == nil ? nil : Pipe()
        process.standardInput = stdin ?? FileHandle.nullDevice

        handle.attach(process: process, stdout: stdout, eventsLog: eventsLog, debugLog: debugLog)
        do {
            try process.run()
        } catch {
            LiveEngine.log.error("engine start failed: \(String(describing: type(of: error)), privacy: .public)")
            handle.failToStart()
            return handle
        }
        if let stdin, let secrets = inv.secrets {
            // exactly one JSON line, then EOF; the Data is dropped right after writing
            stdin.fileHandleForWriting.write(secrets.stdinLine())
            try? stdin.fileHandleForWriting.close()
        }
        return handle
    }
}

final class LiveHandle: EngineHandle, @unchecked Sendable {
    let output: AsyncStream<EngineOutput>
    private let continuation: AsyncStream<EngineOutput>.Continuation
    private let queue = DispatchQueue(label: "threema-chat-transfer.engine.stdout")
    private var buffer = Data()
    /// Strong until the process ended: the stream must finish even if nobody holds the handle any more.
    private var process: Process?
    private var stdoutPipe: Pipe?
    private var eventsLog: FileHandle?
    private var debugLog: FileHandle?
    static let maxLine = 64 * 1024

    init() {
        var c: AsyncStream<EngineOutput>.Continuation!
        output = AsyncStream(bufferingPolicy: .unbounded) { c = $0 }
        continuation = c
    }

    static func appendHandle(_ url: URL) -> FileHandle? {
        let fm = FileManager.default
        try? fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
                                attributes: [.posixPermissions: 0o700])
        if !fm.fileExists(atPath: url.path) {
            fm.createFile(atPath: url.path, contents: nil, attributes: [.posixPermissions: 0o600])
        }
        guard let h = try? FileHandle(forWritingTo: url) else { return nil }
        _ = try? h.seekToEnd()
        return h
    }

    func attach(process: Process, stdout: Pipe, eventsLog: FileHandle?, debugLog: FileHandle?) {
        self.process = process
        self.stdoutPipe = stdout
        self.eventsLog = eventsLog
        self.debugLog = debugLog
        // The handlers hold the handle strongly (a deliberate cycle with the process) until the process ended.
        stdout.fileHandleForReading.readabilityHandler = { fh in
            let chunk = fh.availableData
            self.queue.async { self.ingest(chunk) }
        }
        process.terminationHandler = { p in
            stdout.fileHandleForReading.readabilityHandler = nil
            let rest = (try? stdout.fileHandleForReading.readToEnd()) ?? Data()
            self.queue.async {
                self.ingest(rest)
                if !self.buffer.isEmpty { self.emitLine(self.buffer); self.buffer.removeAll() }
                let crashed = p.terminationReason == .uncaughtSignal
                self.continuation.yield(.exit(code: p.terminationStatus, crashed: crashed))
                self.continuation.finish()
                try? self.eventsLog?.close()
                try? self.debugLog?.close()
                p.terminationHandler = nil
                self.process = nil
                self.stdoutPipe = nil
            }
        }
    }

    func failToStart() {
        process?.terminationHandler = nil
        process = nil
        stdoutPipe?.fileHandleForReading.readabilityHandler = nil
        stdoutPipe = nil
        try? eventsLog?.close()
        try? debugLog?.close()
        continuation.yield(.exit(code: 127, crashed: false))
        continuation.finish()
    }

    private func ingest(_ chunk: Data) {
        guard !chunk.isEmpty else { return }
        buffer.append(chunk)
        while let nl = buffer.firstIndex(of: 0x0A) {
            let line = buffer.subdata(in: buffer.startIndex..<nl)
            buffer.removeSubrange(buffer.startIndex...nl)
            emitLine(line)
        }
        if buffer.count > LiveHandle.maxLine {
            buffer.removeAll()
            continuation.yield(.invalidLine(reason: "line_too_long"))
        }
    }

    private func emitLine(_ line: Data) {
        guard !line.isEmpty else { return }
        if line.count > LiveHandle.maxLine { continuation.yield(.invalidLine(reason: "line_too_long")); return }
        eventsLog?.write(line + Data([0x0A]))
        guard let s = String(data: line, encoding: .utf8), let e = EngineEvent.parse(s) else {
            continuation.yield(.invalidLine(reason: "not_json"))
            return
        }
        continuation.yield(.event(e))
    }

    func terminate() {
        queue.async {
            guard let p = self.process, p.isRunning else { return }
            p.terminate()   // SIGTERM; the engine defers it while critical is on
        }
    }
}
