// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation

/// What the app hands to the engine for one command (DESIGN §5.1). `args` never contain secrets; `secrets` go to
/// stdin as exactly one JSON line and are never written to disk, logs or the session.
public struct EngineInvocation: Sendable {
    public let command: EngineCommand
    public let args: [String]
    public let secrets: EngineSecrets?
    /// Session folder (nil only for `version`, `selftest`, `host-check`).
    public let session: URL?

    public init(command: EngineCommand, args: [String], secrets: EngineSecrets? = nil, session: URL?) {
        self.command = command
        self.args = args
        self.secrets = secrets
        self.session = session
    }
}

/// Secrets for stdin. Unused fields stay nil and are omitted from the JSON line (ENGINE-PROTOCOL §1).
public struct EngineSecrets: Sendable, Equatable {
    public var backupPassword: String?
    public var newBackupPassword: String?
    /// Keyed by the argv index of the Android file (0-based among the files).
    public var androidPasswords: [Int: String]?

    public init(backupPassword: String? = nil, newBackupPassword: String? = nil, androidPasswords: [Int: String]? = nil) {
        self.backupPassword = backupPassword
        self.newBackupPassword = newBackupPassword
        self.androidPasswords = androidPasswords
    }

    /// Field names that are set (for logs and mock comparisons; never the values).
    public var fieldNames: [String] {
        var out: [String] = []
        if backupPassword != nil { out.append("backup_password") }
        if newBackupPassword != nil { out.append("new_backup_password") }
        if androidPasswords != nil { out.append("android_passwords") }
        return out
    }

    /// The single stdin line (UTF-8 JSON + "\n").
    public func stdinLine() -> Data {
        var obj: [String: Any] = [:]
        if let p = backupPassword { obj["backup_password"] = p }
        if let p = newBackupPassword { obj["new_backup_password"] = p }
        if let m = androidPasswords {
            obj["android_passwords"] = Dictionary(uniqueKeysWithValues: m.map { (String($0.key), $0.value) })
        }
        var data = (try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys])) ?? Data("{}".utf8)
        data.append(0x0A)
        return data
    }
}

/// Output of an engine process: parsed events, unparseable lines, and finally the exit.
public enum EngineOutput: Sendable {
    case event(EngineEvent)
    case invalidLine(reason: String)
    /// The process ended. `crashed` = killed by a signal without an orderly exit.
    case exit(code: Int32, crashed: Bool)
}

/// A running engine process.
public protocol EngineHandle: AnyObject, Sendable {
    /// Finishes after `.exit`.
    var output: AsyncStream<EngineOutput> { get }
    /// SIGTERM. The engine stops at the next safe point (E_CANCELLED) and defers it while `critical` is on.
    func terminate()
}

public enum EngineKind: Equatable, Sendable {
    case live
    case fake(scenario: String)   // real engine with --fake-device (demo mode, CI)
    case mock(scenario: String)   // MockEngine replaying app/Tests/Scenarios/<name>.jsonl

    public var isDemo: Bool { if case .live = self { return false }; return true }
    public var isMock: Bool { if case .mock = self { return true }; return false }
}

/// The app's only way to talk to `tmcore` (LiveEngine and MockEngine behind one protocol, DESIGN §3.2).
public protocol EngineClient: AnyObject {
    var kind: EngineKind { get }
    /// LiveEngine requires `hello.engine_version == app version`; recorded mock scenarios carry their own version.
    var requiresMatchingEngineVersion: Bool { get }
    /// The engine client itself copies stdout to `<session>/logs/events.jsonl` (LiveEngine). Otherwise the
    /// WizardStore keeps the result events there (MockEngine), so a resumed session can show earlier counts.
    var writesEventLog: Bool { get }
    func start(_ invocation: EngineInvocation) -> EngineHandle
}

// MARK: - Tracking one command

/// How a command ended, from the app's point of view (DESIGN §5.6: the result event decides, not the exit code).
public enum CommandOutcome: Equatable, Sendable {
    /// `result.ok == true` (an R_ code).
    case success(ResultEvent)
    /// `result.ok == false` with a known or unknown E_ code.
    case failure(ResultEvent)
    /// No result, but `critical` was on: the restore was possibly sent → S16.
    case lostDuringCritical(exitCode: Int32)
    /// No result without `critical`, protocol violation, version mismatch → F-INTERNAL.
    case internalError(reason: String, codeRaw: String?)
}

/// Pure state machine over the event stream of one process. Unit-tested; used by the WizardStore for every command.
public struct CommandTracker: Sendable {
    public let command: EngineCommand
    public let expectedEngineVersion: String?
    public private(set) var sawHello = false
    public private(set) var criticalOn = false
    public private(set) var criticalSeen = false
    public private(set) var result: ResultEvent?
    public private(set) var violations: [String] = []
    public private(set) var lastSeq = 0
    public private(set) var engineVersion: String?

    public init(command: EngineCommand, expectedEngineVersion: String?) {
        self.command = command
        self.expectedEngineVersion = expectedEngineVersion
    }

    public mutating func consume(_ e: EngineEvent) {
        if result != nil { violations.append("event_after_result"); return }
        if e.v != engineProtocolVersion { violations.append("envelope_version") }
        if lastSeq > 0 && e.seq != lastSeq + 1 { violations.append("seq_gap") }
        lastSeq = e.seq
        if !sawHello {
            guard e.type == EventType.hello.rawValue else { violations.append("hello_missing"); return }
            sawHello = true
            engineVersion = e.engineVersion
            if e.protocol != engineProtocolVersion { violations.append("protocol_mismatch") }
            if let want = expectedEngineVersion, e.engineVersion != want { violations.append("engine_version_mismatch") }
            return
        }
        switch e.eventType {
        case .critical:
            criticalOn = e.on ?? false
            if criticalOn { criticalSeen = true }
        case .result:
            if let r = ResultEvent(e) {
                result = r
                criticalOn = false
            } else {
                violations.append("result_malformed")
            }
        default:
            break
        }
    }

    public mutating func invalidLine(_ reason: String) {
        violations.append("invalid_line:\(reason)")
    }

    /// Fatal violations make the whole command an internal error even if a result arrived.
    private var fatalViolation: String? {
        violations.first { ["hello_missing", "protocol_mismatch", "engine_version_mismatch", "envelope_version",
                            "result_malformed"].contains($0) }
    }

    public func outcome(exitCode: Int32, crashed: Bool) -> CommandOutcome {
        if let r = result {
            if let fatal = fatalViolation { return .internalError(reason: fatal, codeRaw: r.codeRaw) }
            let isR = r.codeRaw.hasPrefix("R_")
            if r.ok != isR { return .internalError(reason: "ok_code_mismatch", codeRaw: r.codeRaw) }
            if r.code == nil {
                // Unknown code from a newer engine: F-INTERNAL with the code in the diagnostic report (§5.7).
                return .internalError(reason: "unknown_code", codeRaw: r.codeRaw)
            }
            return r.ok ? .success(r) : .failure(r)
        }
        if criticalSeen { return .lostDuringCritical(exitCode: exitCode) }
        return .internalError(reason: fatalViolation ?? (crashed ? "crashed" : "no_result"), codeRaw: nil)
    }
}
