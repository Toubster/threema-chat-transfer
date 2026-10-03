// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation
import os

/// Display state of one engine command (phases, progress, check rows, prompts, retries, notes).
public struct Activity: Equatable, Sendable {
    public let command: EngineCommand
    public var phases: [EnginePhase]
    public var phase: String?
    public var phaseIndex = 0
    public var phaseCount = 0
    public var pct: Double?
    public var done: Double?
    public var total: Double?
    public var unit: String?
    public var checks: [CheckRow] = []
    public var prompts: [String] = []
    public var retry: Retry?
    public var notes: [NoteItem] = []
    public var running = true

    public struct Retry: Equatable, Sendable {
        public let code: String
        public let attempt: Int
        public let max: Int
    }

    public init(command: EngineCommand) {
        self.command = command
        self.phases = phasesByCommand[command] ?? []
    }

    public func check(_ id: CheckID) -> CheckRow? { checks.first { $0.id == id.rawValue } }

    /// Status of a phase for step lists: done / running / pending.
    public func status(of p: EnginePhase) -> CheckRow.Status {
        guard let i = phases.firstIndex(of: p) else { return .pending }
        let current = phase.flatMap { EnginePhase(rawValue: $0) }.flatMap { phases.firstIndex(of: $0) } ?? -1
        if !running && current >= 0 && i <= current { return .pass }
        if i < current { return .pass }
        if i == current { return running ? .running : .pass }
        return .pending
    }
}

/// The wizard's state machine (DESIGN §8.1): screens, back-lock from S11, cancel until S14 (never during
/// `critical`), quit rules, freshness countdown, resume, and every engine call. No safety logic lives here; the
/// app only shows what `tmcore` decided.
@MainActor
public final class WizardStore: ObservableObject {
    public struct Dependencies {
        public var engine: EngineClient
        public var sessions: SessionStore
        public var keychain: KeychainStoring
        public var power: PowerAsserting
        public var now: () -> Date
        public var appVersion: String
        public var openURL: (URL) -> Void
        public var isDemo: Bool
        /// Delay before F-FRESHNESS starts the new backup by itself (DESIGN §8.4 "automatisch S12 → S13").
        public var autoAdvanceDelay: TimeInterval
        /// Run the 1-second countdown timer (off in unit tests, which call `tick()`).
        public var runsTimer: Bool

        public init(engine: EngineClient, sessions: SessionStore, keychain: KeychainStoring, power: PowerAsserting,
                    now: @escaping () -> Date = Date.init, appVersion: String = AppInfo.engineVersion,
                    openURL: @escaping (URL) -> Void = { _ in }, isDemo: Bool = false,
                    autoAdvanceDelay: TimeInterval = 3, runsTimer: Bool = true) {
            self.engine = engine
            self.sessions = sessions
            self.keychain = keychain
            self.power = power
            self.now = now
            self.appVersion = appVersion
            self.openURL = openURL
            self.isDemo = isDemo
            self.autoAdvanceDelay = autoAdvanceDelay
            self.runsTimer = runsTimer
        }
    }

    public enum Sheet: String, Identifiable {
        case whatItDoes, missingIds, diagReport, help, about, passwordPrompt
        public var id: String { rawValue }
    }

    public enum Dialog: String, Identifiable {
        case cancel, quit, quitRefused, discard, resetThreema
        public var id: String { rawValue }
    }

    public enum QuitDecision: Equatable { case allow, confirm, refuse }

    /// S10b: the user knows the stored backup password, or does not (help: where to look, Apple's reset).
    public enum ExistingPasswordChoice: String, Hashable, Sendable { case known, unknown }

    static let log = Logger(subsystem: AppInfo.logSubsystem, category: "wizard")

    public let deps: Dependencies
    public let secrets = SecretsBox()

    // MARK: navigation
    @Published public internal(set) var screen: Screen = .s00
    @Published public internal(set) var history: [Screen] = []
    @Published public var language: AppLanguage { didSet { persistLocale() } }
    public var loc: Localizer { Localizer(language) }

    // MARK: engine
    @Published public internal(set) var activities: [EngineCommand: Activity] = [:]
    @Published public private(set) var critical = false
    @Published public private(set) var runningCommand: EngineCommand?
    var currentHandle: EngineHandle?
    var watchHandle: EngineHandle?
    /// Bumped when a flow is superseded (freshness expired, cancel); late outcomes of the old flow are ignored.
    var flowGeneration = 0

    // MARK: errors
    @Published public internal(set) var error: ErrorContext?
    @Published public internal(set) var inlineError: ErrorContext?
    @Published public internal(set) var internalReason: String?

    // MARK: results
    @Published public internal(set) var host: HostCheckResult?
    @Published public internal(set) var deviceState: DeviceState?
    @Published public internal(set) var device: DeviceStatus?
    @Published public internal(set) var inspect: AndroidInspectResult?
    @Published public internal(set) var normalized: AndroidNormalizeResult?
    @Published public internal(set) var preBackup: BackupResult?
    @Published public internal(set) var prepared: PrepareResult?
    @Published public internal(set) var postcheck: PostcheckResult?
    @Published public internal(set) var restoreLinkLost = false
    @Published public internal(set) var rollbackMode = false
    /// Backup encryption was off at S08 and is on now although {App} did not turn it on -- typically the Finder backup
    /// of the S09 safety net ("Lokales Backup verschlüsseln" asks for the user's own password). S10b then asks for that
    /// password; the generated one is never kept (REVIEW B1).
    @Published public internal(set) var encryptionTurnedOnElsewhere = false
    /// The engine rejected the password {App} had (memory or keychain): F-PW-WRONG always asks the user (REVIEW B1).
    @Published public internal(set) var storedPasswordRejected = false
    /// "Erneut prüfen" (S10b help, F-PW-WRONG) read the iPhone again and backup encryption is still on.
    @Published public internal(set) var encryptionStillOn = false
    /// The engine refused "Threema zurücksetzen" (more than 6 h, used already, not threema_only).
    @Published public internal(set) var rollbackRefused = false
    /// The user chose "Android-Teil vorbereiten" for an iOS build that is not verified (DESIGN §6.2).
    @Published public internal(set) var iosBlocked = false
    /// S03 red: `E_IOS_UNKNOWN` / `E_IOS_BLOCKED` from `device-status` (no transfer with this iOS build).
    @Published public internal(set) var iosUnverifiedCode: String?
    @Published public internal(set) var resume: ResumePolicy.Decision?
    @Published public internal(set) var resumeLastStep: Screen?
    @Published public internal(set) var welcomeBack = false
    @Published public internal(set) var copiesDeleted = false
    @Published public internal(set) var safetyCopyDeleted = false
    @Published public internal(set) var freedBytes: Int64 = 0
    @Published public internal(set) var diagFile: URL?
    @Published public internal(set) var diagCreating = false
    @Published public internal(set) var missingIds: [String] = []
    @Published public internal(set) var workdirRoot: URL?
    /// The language came from `TM_LANG` (tests): a resumed session does not switch it.
    public var languageFixed = false
    /// Set by the bootstrap when the configured engine could not be built; `start()` then stops at F-INTERNAL.
    public var startupProblem: String?

    // MARK: user input (memory only; passwords never reach session.json)
    @Published public var answers = SessionAnswers()
    @Published public var disclaimerAccepted = false
    @Published public var androidFiles: [URL] = []
    @Published public var androidPasswordInput: [Int: String] = [:]
    @Published public var textOnlyConfirmed = false
    @Published public var backupPasswordInput = ""
    /// S10b: "Ich kenne das Passwort" or "Ich weiß es nicht / habe nie eins festgelegt"; nothing preselected.
    @Published public var existingPasswordChoice: ExistingPasswordChoice?
    @Published public var generatedPassword = ""
    @Published public var ownPasswordMode = false
    @Published public var ownPasswordInput = ""
    @Published public var ownPasswordRepeat = ""
    @Published public var lastFourInput = ""
    @Published public var saveInKeychain = true
    @Published public var transferChecks = [false, false]
    @Published public var safetyNetChoice: String?
    @Published public var safetyNetConfirmed = false

    // MARK: sheets, dialogs, clock
    /// S02/F-HOST-APFS "Anderen Ort wählen …": the view opens the folder panel when this flips.
    @Published public var chooseLocationRequested = false
    @Published public var sheet: Sheet?
    @Published public var dialog: Dialog?
    @Published public private(set) var now: Date
    var passwordPromptContinuation: CheckedContinuation<String?, Never>?
    var autoNewBackupTask: Task<Void, Never>?
    private var timerTask: Task<Void, Never>?

    public init(deps: Dependencies, language: AppLanguage) {
        self.deps = deps
        self.language = language
        self.now = deps.now()
        if deps.runsTimer { startTimer() }
    }

    deinit { timerTask?.cancel() }

    // MARK: - Navigation

    /// The sidebar phase: an error screen shows the phase of the screen it came from.
    public var sidebarPhase: SidebarPhase {
        // S23 (resume) shows where the session stands, not "Start"
        if screen == .s23, let p = (resume?.screen ?? resumeLastStep)?.sidebarPhase { return p }
        return screen.sidebarPhase ?? error?.origin.sidebarPhase ?? history.last?.sidebarPhase ?? .start
    }

    /// The flow position used for the lock rules (error screens count as the screen they came from).
    var policyScreen: Screen {
        if screen.isFailure { return error?.origin ?? history.last ?? .s00 }
        return screen
    }

    public var isBusy: Bool { runningCommand != nil }

    /// "Zurück" until S10 (S10a/S10b); locked from S11 on, while a command runs and during `critical`.
    public var canGoBack: Bool {
        guard !critical, !isBusy, let prev = history.last, screen != .s23 else { return false }
        guard let o = policyScreen.order, o <= (Screen.s10b.order ?? 0) else { return false }
        return prev != .s23
    }

    /// "Abbrechen" until S14 with "nothing on the iPhone was changed"; never during `critical`.
    public var canCancel: Bool {
        guard !critical, deps.sessions.current != nil else { return false }
        guard let o = policyScreen.order else { return false }
        return o >= (Screen.s01.order ?? 0) && o <= (Screen.s14.order ?? 0) && screen != .s23
    }

    /// Window close / ⌘Q: refused during `critical`, confirmed from S11 on (DESIGN §8.1).
    public var quitDecision: QuitDecision {
        if critical { return .refuse }
        guard let o = policyScreen.order else { return .allow }
        if (o >= (Screen.s11.order ?? 0) && o <= (Screen.s18.order ?? 0)) || isBusy { return .confirm }
        return .allow
    }

    /// Moves forward. `replace` = do not keep the current screen in the back history.
    public func go(_ target: Screen, replace: Bool = false) {
        guard target != screen else { return }
        if !replace && !screen.isFailure { history.append(screen) }
        if !target.isFailure { error = nil }
        inlineError = nil
        screen = target
        persistScreen()
    }

    public func goBack() {
        guard canGoBack, let prev = history.popLast() else { return }
        error = nil
        inlineError = nil
        screen = prev
        persistScreen()
    }

    func showFailure(_ ctx: ErrorContext) {
        let dest = ErrorCatalog.destination(for: ctx.codeRaw)
        deps.sessions.update({ $0.lastErrorCode = ctx.codeRaw }, now: deps.now())
        switch dest {
        case .failure(let f):
            error = ctx
            if !screen.isFailure { history.append(screen) }
            inlineError = nil
            screen = .failure(f)
            persistScreen()
            if f == .freshness { scheduleAutoNewBackup() }
        case .inline(let s):
            if screen != s { go(s, replace: screen.isFailure) }
            inlineError = ctx
        case .screen(let s):
            go(s)
            if s == .s16 { restoreLinkLost = true }
        case .none:
            break
        }
    }

    func showInternal(reason: String, code: String?, origin: Screen) {
        internalReason = reason
        Self.log.error("internal: \(reason, privacy: .public) code=\(code ?? "-", privacy: .public)")
        let raw = code.flatMap { EngineCode(rawValue: $0) == nil ? $0 : nil } ?? EngineCode.E_INTERNAL.rawValue
        error = ErrorContext(codeRaw: raw, origin: origin)
        if !screen.isFailure { history.append(screen) }
        inlineError = nil
        screen = .failure(.internalError)
        persistScreen()
    }

    // MARK: - Persistence

    func persistScreen() {
        guard deps.sessions.current != nil else { return }
        let s = screen.isFailure ? (error?.origin ?? history.last ?? .s02) : screen
        let phase = s.sidebarPhase ?? .start
        let a = answers
        deps.sessions.update({ st in
            st.screen = s.id
            st.sidebarPhase = phase
            st.answers = a
        }, now: deps.now())
    }

    func persistAnswers() {
        let a = answers
        deps.sessions.update({ $0.answers = a }, now: deps.now())
    }

    private func persistLocale() {
        let l = language
        deps.sessions.update({ $0.locale = l }, now: deps.now())
    }

    // MARK: - Clock and freshness countdown

    private func startTimer() {
        timerTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 1_000_000_000)
                await MainActor.run { self?.tick() }
            }
        }
    }

    /// The deadline for starting the transfer (PRE backup + 60 min, from the engine).
    public var freshUntil: Date? {
        Formatters.parseTimestamp(prepared?.freshUntil ?? preBackup?.freshUntil)
    }

    public var countdown: CountdownState? {
        guard let d = freshUntil else { return nil }
        return Countdown.state(deadline: d, now: now)
    }

    /// Called every second (and by tests): updates the clock and handles an expired deadline on S13/S14.
    public func tick() {
        now = deps.now()
        guard screen == .s13 || screen == .s14, !critical, let c = countdown, c.expired else { return }
        freshnessExpired()
    }

    // MARK: - Running engine commands

    public var sessionURL: URL? { deps.sessions.current }

    func sessionArgs() -> [String] { ["--session", deps.sessions.current?.path ?? ""] }

    /// Runs one engine command to completion, updating `activities[command]` from its events.
    @discardableResult
    func run(_ cmd: EngineCommand, args: [String], secrets: EngineSecrets? = nil,
             onEvent: ((EngineEvent) -> Void)? = nil) async -> CommandOutcome {
        var tracker = CommandTracker(command: cmd,
                                     expectedEngineVersion: deps.engine.requiresMatchingEngineVersion
                                         ? deps.appVersion : nil)
        let inv = EngineInvocation(command: cmd, args: args, secrets: secrets, session: deps.sessions.current)
        activities[cmd] = Activity(command: cmd)
        let isWatch = cmd == .deviceWatch
        if !isWatch { runningCommand = cmd }
        let keepAwake = (policyScreen.order ?? 0) >= (Screen.s12.order ?? 0)
        if keepAwake { deps.power.acquire() }
        let handle = deps.engine.start(inv)
        if isWatch { watchHandle = handle } else { currentHandle = handle }
        let eventLog = deps.engine.writesEventLog ? nil : EventLog(session: deps.sessions.current)
        var exit: (Int32, Bool) = (0, false)
        for await out in handle.output {
            switch out {
            case .event(let e):
                tracker.consume(e)
                eventLog?.append(e)
                apply(e, to: cmd)
                onEvent?(e)
            case .invalidLine(let reason):
                tracker.invalidLine(reason)
            case .exit(let code, let crashed):
                exit = (code, crashed)
            }
        }
        eventLog?.close()
        if critical { setCritical(false) }   // the process is gone; nothing can be sent any more
        if keepAwake { deps.power.release() }
        activities[cmd]?.running = false
        activities[cmd]?.prompts = []
        if isWatch { watchHandle = nil } else { currentHandle = nil; runningCommand = nil }
        return tracker.outcome(exitCode: exit.0, crashed: exit.1)
    }

    private func apply(_ e: EngineEvent, to cmd: EngineCommand) {
        guard var a = activities[cmd] else { return }
        switch e.eventType {
        case .phase:
            a.phase = e.phase
            a.phaseIndex = e.index ?? a.phaseIndex
            a.phaseCount = e.count ?? a.phaseCount
            a.pct = nil
            a.done = nil
            a.total = nil
            a.retry = nil
        case .progress:
            if let p = e.phase { a.phase = p }
            a.pct = e.pct
            a.done = e.done
            a.total = e.total
            a.unit = e.unit
        case .check:
            guard let id = e.id else { break }
            let row = CheckRow(id: id, status: CheckRow.Status(rawValue: e.status ?? "") ?? .pending, code: e.code,
                               data: e.data)
            if let i = a.checks.firstIndex(where: { $0.id == id }) { a.checks[i] = row } else { a.checks.append(row) }
        case .prompt:
            guard let k = e.kind else { break }
            a.prompts.removeAll { $0 == k }
            if e.active == true { a.prompts.append(k) }
        case .retry:
            a.retry = Activity.Retry(code: e.reason ?? "", attempt: e.attempt ?? 1, max: e.max ?? 1)
        case .note:
            if let c = e.code, !a.notes.contains(where: { $0.code == c }) { a.notes.append(NoteItem(code: c, data: e.data)) }
        case .device:
            deviceState = e.state.flatMap(DeviceState.init(rawValue:))
        case .critical:
            setCritical(e.on ?? false)
            if e.on == true, cmd == .restore || cmd == .rollbackThreema {
                // From here on the restore may have been sent: S15, saved, never "start over" (DESIGN §8.1).
                go(.s15, replace: true)
            }
        default:
            break
        }
        activities[cmd] = a
    }

    func setCritical(_ on: Bool) {
        guard on != critical else { return }
        critical = on
        if on { deps.power.acquire() } else { deps.power.release() }
    }

    /// SIGTERM to the running command (never while `critical`).
    func terminateRunning() {
        guard !critical else { return }
        currentHandle?.terminate()
    }

    /// Waits until no command is running (tests, cancel).
    public func waitUntilIdle() async {
        while runningCommand != nil || watchHandle != nil {
            try? await Task.sleep(nanoseconds: 5_000_000)
        }
    }
}

/// Copy of the engine's stdout in `<session>/logs/events.jsonl` (DESIGN §5.8), written by the app for both
/// engines. On resume the app reads its own copy back to show counts of earlier steps.
final class EventLog {
    private let handle: FileHandle

    init?(session: URL?) {
        guard let s = session else { return nil }
        let url = s.appendingPathComponent("logs/events.jsonl")
        let fm = FileManager.default
        try? fm.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true,
                                attributes: [.posixPermissions: 0o700])
        if !fm.fileExists(atPath: url.path) {
            fm.createFile(atPath: url.path, contents: nil, attributes: [.posixPermissions: 0o600])
        }
        guard let h = try? FileHandle(forWritingTo: url) else { return nil }
        _ = try? h.seekToEnd()
        handle = h
    }

    func append(_ e: EngineEvent) {
        guard e.type == EventType.result.rawValue, let line = EventLog.encode(e) else { return }
        handle.write(line)
    }

    func close() { try? handle.close() }

    /// Only `result` events are kept (counts and codes; the schema already forbids personal data in events).
    static func encode(_ e: EngineEvent) -> Data? {
        var obj: [String: JSONValue] = ["v": .number(Double(e.v)), "seq": .number(Double(e.seq)), "ts": .string(e.ts),
                                        "cmd": .string(e.cmd), "type": .string(e.type)]
        if let ok = e.ok { obj["ok"] = .bool(ok) }
        if let c = e.code { obj["code"] = .string(c) }
        if let r = e.retryable { obj["retryable"] = .bool(r) }
        if let d = e.deviceModified { obj["device_modified"] = .string(d) }
        if let d = e.data { obj["data"] = d }
        guard var data = try? JSONEncoder().encode(JSONValue.object(obj)) else { return nil }
        data.append(0x0A)
        return data
    }

    /// The last successful result per command (and per backup role) from `logs/events.jsonl`.
    static func lastResults(session: URL) -> [String: ResultEvent] {
        guard let text = try? String(contentsOf: session.appendingPathComponent("logs/events.jsonl"), encoding: .utf8)
        else { return [:] }
        var out: [String: ResultEvent] = [:]
        for line in text.split(separator: "\n") {
            guard let e = EngineEvent.parse(String(line)), let r = ResultEvent(e), r.ok else { continue }
            var key = r.command
            if r.command == EngineCommand.backup.rawValue, let role = r.data["role"]?.string { key += ":" + role }
            out[key] = r
        }
        return out
    }
}
