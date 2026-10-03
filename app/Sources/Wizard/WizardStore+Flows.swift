// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation

/// The engine steps of the wizard, one function per command (DESIGN §5.4 / §8.3). Every function maps the outcome
/// to a screen; error codes go through `ErrorCatalog` so the screen always comes from `codes.v1.json`.
extension WizardStore {
    // MARK: - Start and resume

    /// App start: an open session → `session-status` → S23; otherwise S00 (DESIGN §8.1).
    public func start(languageFromEnvironment: Bool = false) async {
        if let problem = startupProblem {
            showInternal(reason: problem, code: nil, origin: .s00)
            return
        }
        guard let (dir, state) = deps.sessions.findLatest() else { screen = .s00; return }
        deps.sessions.open(dir, state: state)
        if !languageFromEnvironment { language = state.locale }
        answers = state.answers
        resumeLastStep = Screen(id: state.screen)
        workdirRoot = dir.deletingLastPathComponent()
        screen = .s23
        var status: SessionStatusResult?
        let outcome = await run(.sessionStatus, args: sessionArgs())
        if case .success(let r) = outcome { status = try? r.data.decode(SessionStatusResult.self) }
        let decision = ResumePolicy.decide(status: status, local: state, now: deps.now())
        if decision.closed {
            deps.sessions.close()
            answers = SessionAnswers()
            resume = nil
            screen = .s00
            return
        }
        restoreCachedResults(from: dir)
        if status?.phase == "rollback_sent" { rollbackMode = true }   // crash during the rollback: no result yet
        resume = decision
    }

    /// S23 "Weitermachen".
    public func continueResume() {
        guard let d = resume else { return }
        history = []
        welcomeBack = d.welcomeBack
        if d.afterSend && d.screen == .s16 { restoreLinkLost = false }
        go(d.screen, replace: true)
        history = []
        switch d.screen {
        case .s13: Task { await runPrepare() }
        case .s11: preBackup = nil; prepared = nil
        default: break
        }
    }

    /// S23 "Sitzung verwerfen" (only before anything was sent, or after a postcheck).
    public func discardSession() {
        guard resume?.canDiscard ?? true, !critical else { return }
        try? deps.sessions.discard()
        resetForNewSession()
        go(.s00, replace: true)
        history = []
    }

    func resetForNewSession() {
        secrets.wipe()
        answers = SessionAnswers()
        host = nil; device = nil; deviceState = nil; inspect = nil; normalized = nil
        preBackup = nil; prepared = nil; postcheck = nil; resume = nil; resumeLastStep = nil
        activities = [:]; error = nil; inlineError = nil; iosBlocked = false; iosUnverifiedCode = nil; welcomeBack = false
        androidFiles = []; androidPasswordInput = [:]; backupPasswordInput = ""; generatedPassword = ""
        disclaimerAccepted = false; transferChecks = [false, false]; safetyNetChoice = nil; safetyNetConfirmed = false
        copiesDeleted = false; safetyCopyDeleted = false; restoreLinkLost = false; rollbackMode = false
        encryptionTurnedOnElsewhere = false; storedPasswordRejected = false; rollbackRefused = false
        lastFourInput = ""; ownPasswordInput = ""; ownPasswordRepeat = ""; ownPasswordMode = false
        existingPasswordChoice = nil; encryptionStillOn = false
    }

    /// Counts of earlier steps after a restart, from the app's own copy of the engine results.
    func restoreCachedResults(from dir: URL) {
        let r = EventLog.lastResults(session: dir)
        normalized = try? r["android-normalize"]?.data.decode(AndroidNormalizeResult.self)
        device = try? r["device-status"]?.data.decode(DeviceStatus.self)
        preBackup = try? r["backup:pre"]?.data.decode(BackupResult.self)
        prepared = try? r["prepare"]?.data.decode(PrepareResult.self)
        postcheck = try? r["postcheck"]?.data.decode(PostcheckResult.self)
        // after "Threema zurücksetzen" S17/S19 speak about Threema as it was before the transfer
        rollbackMode = r["rollback-threema"] != nil
        if let files = answers.androidFiles { androidFiles = files.sorted { $0.ref < $1.ref }.map { URL(fileURLWithPath: $0.path) } }
        safetyNetChoice = answers.safetyNet
    }

    // MARK: - S00–S02

    public func getStarted() { go(.s01) }

    public func acceptDisclaimer() {
        guard disclaimerAccepted else { return }
        answers.disclaimerAcceptedAt = Formatters.timestamp(deps.now())
        go(.s02)
    }

    /// S02: `host-check --workdir <sessions root>`.
    public func runHostCheck() async {
        guard !isBusy else { return }
        host = nil
        let root = workdirRoot ?? deps.sessions.defaultRoot
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true,
                                                 attributes: [.posixPermissions: 0o700])
        let gen = flowGeneration
        let outcome = await run(.hostCheck, args: ["--workdir", root.path])
        guard gen == flowGeneration else { return }
        switch outcome {
        case .success(let r):
            host = try? r.data.decode(HostCheckResult.self)
            if host == nil { showInternal(reason: "host_check_data", code: nil, origin: .s02) }
        default:
            handle(outcome, origin: .s02)
        }
    }

    /// S02 "Anderen Speicherort wählen …": the engine checks APFS and space again.
    public func chooseWorkdir(_ url: URL) async {
        workdirRoot = url.appendingPathComponent(AppInfo.productName).appendingPathComponent("sessions")
        if screen != .s02 { go(.s02, replace: screen.isFailure) }
        await runHostCheck()
    }

    /// S02 "Weiter": the session folder is created now (the first command that needs `--session` follows).
    public func confirmHost() {
        guard host != nil else { return }
        if deps.sessions.current == nil {
            let a = answers
            let lang = language
            let version = deps.appVersion
            let now = deps.now()
            do {
                try deps.sessions.create(root: workdirRoot) { id in
                    var s = SessionState(sessionId: id, now: now, appVersion: version, locale: lang)
                    s.answers = a
                    s.workdirCustom = (self.workdirRoot != nil) ? true : nil
                    return s
                }
            } catch {
                showInternal(reason: "session_create", code: nil, origin: .s02)
                return
            }
        }
        go(.s03)
    }

    // MARK: - S03 device

    /// S03: `device-watch` until the iPhone is ready, then `device-status` (read-only).
    public func runDeviceCheck() async {
        guard !isBusy, watchHandle == nil else { return }
        device = nil
        iosUnverifiedCode = nil
        deviceState = DeviceState.none
        let gen = flowGeneration
        let watch = await run(.deviceWatch, args: sessionArgs()) { [weak self] e in
            if e.eventType == .device, e.state == DeviceState.ready.rawValue {
                self?.watchHandle?.terminate()   // live: the stream ends at SIGTERM
            }
        }
        guard gen == flowGeneration else { return }
        guard deviceState == .ready else {
            if case .failure = watch { handle(watch, origin: .s03) }
            return
        }
        await runDeviceStatus(origin: .s03)
    }

    func runDeviceStatus(origin: Screen) async {
        let gen = flowGeneration
        let outcome = await run(.deviceStatus, args: sessionArgs())
        guard gen == flowGeneration else { return }
        guard case .success(let r) = outcome else { handle(outcome, origin: origin); return }
        guard let st = try? r.data.decode(DeviceStatus.self) else {
            showInternal(reason: "device_status_data", code: nil, origin: origin)
            return
        }
        device = st
        let failing = activities[.deviceStatus]?.checks.first { $0.status == .fail && $0.code != nil }
        let iosCode = failing?.code.flatMap { $0.hasPrefix("E_IOS_") ? $0 : nil }
            ?? (st.isVerified ? nil : (st.compat == "blocked" ? EngineCode.E_IOS_BLOCKED : .E_IOS_UNKNOWN).rawValue)
        // A failing check that carries a code decides (managed, battery, …); the iOS allow-list is handled below.
        if let f = failing, let code = f.code, !code.hasPrefix("E_IOS_") {
            showFailure(ErrorContext(codeRaw: code, data: deviceData(st, f.data), origin: origin))
            return
        }
        if let code = iosCode {
            if origin == .s03 && normalized == nil {
                // S03 red: the Android part may run, nothing is transferred (DESIGN §6.2, S03 "Rot: iOS unbekannt")
                iosUnverifiedCode = code
                return
            }
            showFailure(ErrorContext(codeRaw: code, data: deviceData(st, failing?.data), origin: origin))
            return
        }
        iosUnverifiedCode = nil
        if st.managed {
            showFailure(ErrorContext(codeRaw: EngineCode.E_DEV_MANAGED.rawValue, origin: origin))
            return
        }
        if origin == .s07 {
            // the Android part was prepared while this iOS version was not released yet; it is now
            iosBlocked = false
            go(.s08)
        }
        if origin == .s08 || origin == .s09 || origin == .s10b {
            if st.findMy == "on" {
                showFailure(ErrorContext(codeRaw: EngineCode.E_GUARD_FINDMY.rawValue,
                                         data: .object(["source": .string("live")]), origin: .s08))
                return
            }
        }
        if origin == .s08 { go(.s09) }
        if origin == .s09 {
            // S09 "Weiter" after encryption was off at S08: a Finder backup for the safety net turns encryption on
            // with the user's own password, so the state is read again before S10a/S10b (REVIEW B1)
            encryptionTurnedOnElsewhere = st.encryption != "off"
            go(st.encryption == "off" ? .s10a : .s10b)
        }
        if origin == .s10b { encryptionRechecked(st) }
    }

    /// "Erneut prüfen" (S10b "Ich weiß es nicht", F-PW-WRONG help): after Apple's "Alle Einstellungen zurücksetzen"
    /// the iPhone has no backup password any more → S10a, where {App} sets a new one. Still on → stay and say so.
    private func encryptionRechecked(_ st: DeviceStatus) {
        guard st.encryption == "off" else {
            encryptionStillOn = true
            return
        }
        encryptionStillOn = false
        encryptionTurnedOnElsewhere = false
        existingPasswordChoice = nil
        backupPasswordInput = ""
        secrets.setBackupPassword(nil)              // the old password went with the reset
        storedPasswordRejected = false
        // the reset may have switched airplane mode or Bluetooth back on: the S11 list is ticked again
        answers.offlineChecklist = nil
        persistAnswers()
        // "Zurück" on S10a leads to S09, never to S10b/S11/S12 of the old password
        if let i = history.lastIndex(of: .s10b) { history.removeSubrange(i...) }
        go(.s10a, replace: true)
    }

    /// S10b / F-PW-WRONG "Erneut prüfen": reads the iPhone again (`device-status`, read-only).
    public func recheckEncryption() async {
        guard !isBusy else { return }
        encryptionStillOn = false
        await runDeviceStatus(origin: .s10b)
    }

    private func deviceData(_ st: DeviceStatus, _ extra: JSONValue?) -> JSONValue {
        var o: [String: JSONValue] = ["ios_version": .string(st.iosVersion), "ios_build": .string(st.iosBuild)]
        for (k, v) in extra?.object ?? [:] { o[k] = v }
        return .object(o)
    }

    public func confirmDevice() {
        guard let d = device, d.isVerified, iosUnverifiedCode == nil else { return }
        if normalized != nil {
            go(.s07)
            return
        }
        go(.s04)
    }

    /// S03 red / F-IOS-UNKNOWN "Android-Teil vorbereiten": the Android part may run, no restore (DESIGN §6.2).
    public func prepareAndroidOnly() {
        iosBlocked = true
        go(normalized == nil ? .s04 : .s07)
    }

    // MARK: - S05/S06 Android

    /// Mock runs have no file panel: dummy URLs, one per `<android-backup-N>` of the scenario (never opened).
    public var demoAndroidFiles: [URL] {
        let n = (deps.engine as? MockEngine)?.androidFileCount ?? 1
        let dir = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
        return (0..<n).map { dir.appendingPathComponent("threema-backup_demo-\($0).zip") }
    }

    public func setAndroidFiles(_ urls: [URL]) async {
        androidFiles = Array(urls.prefix(8))
        inspect = nil
        inlineError = nil
        androidPasswordInput = [:]
        textOnlyConfirmed = false
        answers.androidFiles = androidFiles.enumerated().map { AndroidFileRef(ref: $0.offset, path: $0.element.path) }
        persistAnswers()
        guard !androidFiles.isEmpty else { return }
        await runAndroidInspect()
    }

    public func removeAndroidFile(at index: Int) async {
        guard androidFiles.indices.contains(index) else { return }
        var f = androidFiles
        f.remove(at: index)
        await setAndroidFiles(f)
    }

    func runAndroidInspect() async {
        let gen = flowGeneration
        let outcome = await run(.androidInspect, args: sessionArgs() + androidFiles.map(\.path))
        guard gen == flowGeneration else { return }
        guard case .success(let r) = outcome else { handle(outcome, origin: .s05); return }
        guard let res = try? r.data.decode(AndroidInspectResult.self) else {
            showInternal(reason: "android_inspect_data", code: nil, origin: .s05)
            return
        }
        inspect = res
        // inline S05 errors from the classification (DESIGN §8.3 S05)
        if let bad = res.files.first(where: { $0.kind == "incomplete" }) {
            inlineError = ErrorContext(codeRaw: EngineCode.E_ANDROID_INCOMPLETE.rawValue,
                                       data: .object(["ref": .number(Double(bad.ref))]), origin: .s05)
        } else if res.textRef == nil || res.plan == "none" {
            let ref = res.files.first?.ref ?? 0
            inlineError = ErrorContext(codeRaw: EngineCode.E_ANDROID_NO_TEXT.rawValue,
                                       data: .object(["ref": .number(Double(ref))]), origin: .s05)
        }
    }

    /// S05: the plan has no media at all → "Nur Texte übertragen" must be confirmed.
    public var inspectWithoutMedia: Bool {
        guard let i = inspect else { return false }
        return i.mediaRefs.isEmpty || !i.files.contains { $0.hasMedia }
    }

    public var canNormalize: Bool {
        guard let i = inspect, inlineError == nil || inlineError?.code == .E_ANDROID_PASSWORD, !isBusy else {
            return false
        }
        guard i.textRef != nil else { return false }
        if inspectWithoutMedia && !textOnlyConfirmed { return false }
        return i.refsNeedingPassword.allSatisfy { !(androidPasswordInput[$0] ?? "").isEmpty }
    }

    /// S05 "Prüfen": `android-normalize --plan … --secrets-stdin <files>` (S06 shows the progress).
    public func runAndroidNormalize() async {
        guard canNormalize, let plan = inspect else { return }
        for (ref, pw) in androidPasswordInput where !pw.isEmpty { secrets.setAndroidPassword(pw, ref: ref) }
        answers.textOnlyConfirmed = inspectWithoutMedia ? textOnlyConfirmed : nil
        normalized = nil
        go(.s06)
        let gen = flowGeneration
        let pws = secrets.androidPasswords
        let outcome = await run(.androidNormalize,
                                args: sessionArgs() + ["--plan", plan.planArgument, "--secrets-stdin"]
                                    + androidFiles.map(\.path),
                                secrets: EngineSecrets(androidPasswords: pws))
        // Android passwords are not needed any more, whatever happened (DESIGN §9).
        secrets.dropAndroidPasswords()
        guard gen == flowGeneration else { return }
        switch outcome {
        case .success(let r):
            androidPasswordInput = [:]
            normalized = try? r.data.decode(AndroidNormalizeResult.self)
            if normalized == nil { showInternal(reason: "normalize_data", code: nil, origin: .s06) }
        case .failure(let r) where r.code == .E_ANDROID_PASSWORD:
            androidPasswordInput = [:]
            go(.s05, replace: true)
            inlineError = ErrorContext(codeRaw: r.codeRaw, data: r.data, origin: .s05)
        default:
            handle(outcome, origin: .s06)
        }
    }

    public var missingKeySenders: Int { normalized?.missingKeySenders ?? 0 }

    public func confirmAndroid() {
        guard normalized != nil else { return }
        go(.s07)
    }

    /// S07 "IDs anzeigen": reads `android/missing-senders.json` (0600, the only place with Threema IDs).
    public func loadMissingIds() {
        missingIds = []
        guard let key = normalized?.missingSendersFile, let url = deps.sessions.file(forKey: key),
              let data = try? Data(contentsOf: url), let json = try? JSONDecoder().decode(JSONValue.self, from: data)
        else { sheet = .missingIds; return }
        var ids: [String] = []
        func walk(_ v: JSONValue) {
            switch v {
            case .string(let s) where s.range(of: "^[0-9A-Z*][0-9A-Z]{7}$", options: .regularExpression) != nil:
                if !ids.contains(s) { ids.append(s) }
            case .array(let a): a.forEach(walk)
            case .object(let o): o.keys.sorted().forEach { walk(o[$0]!) }
            default: break
            }
        }
        walk(json)
        missingIds = ids
        sheet = .missingIds
    }

    // MARK: - S07–S10

    public static let threemaItems = ["install", "safe_restore", "setup_done", "keep_forever", "contacts_added"]
    public static let settingsItems = ["ios_updates_off", "app_updates_off", "find_my_off", "battery"]
    public static let offlineItems = ["threema_closed", "watch_bluetooth_off", "airplane_on", "unlocked_connected"]

    public var threemaItemsNeeded: [String] {
        missingKeySenders > 0 ? Self.threemaItems : Array(Self.threemaItems.prefix(4))
    }

    public func toggle(_ list: WritableKeyPath<SessionAnswers, [String: Bool]?>, _ item: String, _ on: Bool) {
        var m = answers[keyPath: list] ?? [:]
        m[item] = on
        answers[keyPath: list] = m
        persistAnswers()
    }

    public func isChecked(_ list: KeyPath<SessionAnswers, [String: Bool]?>, _ item: String) -> Bool {
        answers[keyPath: list]?[item] ?? false
    }

    public func allChecked(_ list: KeyPath<SessionAnswers, [String: Bool]?>, _ items: [String]) -> Bool {
        items.allSatisfy { isChecked(list, $0) }
    }

    public func confirmThreema() async {
        guard allChecked(\.threemaChecklist, threemaItemsNeeded), !isBusy else { return }
        welcomeBack = false
        if iosBlocked {
            // the iOS version was not released at S03: check again before the iPhone is prepared (`device-status`)
            await runDeviceStatus(origin: .s07)
            return
        }
        go(.s08)
    }

    /// S08 "Weiter": `device-status` for Find My (DESIGN §8.3 S08).
    public func confirmSettings() async {
        guard allChecked(\.settingsChecklist, Self.settingsItems), !isBusy else { return }
        await runDeviceStatus(origin: .s08)
    }

    /// S09 "Weiter". Encryption on at S08 → S10b. Off at S08 → `device-status` again (read-only): the Finder option of
    /// S09 ("Lokales Backup verschlüsseln") may have turned it on with the user's own password (REVIEW B1).
    public func confirmSafetyNet() async {
        guard let choice = safetyNetChoice, safetyNetConfirmed, !isBusy else { return }
        answers.safetyNet = choice
        answers.safetyNetAt = Formatters.timestamp(deps.now())
        persistAnswers()
        guard device?.encryption == "off" else {
            go(.s10b)
            return
        }
        await runDeviceStatus(origin: .s09)
    }

    /// The password S10a will set (generated, or the user's own).
    public var chosenNewPassword: String { ownPasswordMode ? ownPasswordInput : generatedPassword }

    public var ownPasswordProblem: String? {
        guard ownPasswordMode else { return nil }
        if !PasswordGenerator.isAcceptableOwn(ownPasswordInput) { return "s10a.own.too_short" }
        if ownPasswordInput != ownPasswordRepeat { return "s10a.own.mismatch" }
        return nil
    }

    public var canEnableEncryption: Bool {
        guard !isBusy, ownPasswordProblem == nil, !chosenNewPassword.isEmpty else { return false }
        return ownPasswordMode || PasswordGenerator.matchesLastFour(lastFourInput, of: generatedPassword)
    }

    public func prepareGeneratedPassword(force: Bool = false) {
        if generatedPassword.isEmpty || force { generatedPassword = PasswordGenerator.generate(); lastFourInput = "" }
    }

    /// S10a "Verschlüsselung einschalten": `encryption-enable` (the iPhone asks for its passcode).
    public func enableEncryption() async {
        guard canEnableEncryption else { return }
        let pw = chosenNewPassword
        let gen = flowGeneration
        let outcome = await run(.encryptionEnable, args: sessionArgs() + ["--secrets-stdin"],
                                secrets: EngineSecrets(newBackupPassword: pw))
        guard gen == flowGeneration else { return }
        guard case .success(let r) = outcome else { handle(outcome, origin: .s10a); return }
        if r.data["changed"]?.bool == false {
            // encryption was already on (e.g. turned on by the Finder backup of S09): the backups are protected by the
            // password chosen there, not by this one -- never keep it, never store it in the keychain (REVIEW B1)
            encryptionTurnedOnElsewhere = true
            generatedPassword = ""
            ownPasswordInput = ""
            ownPasswordRepeat = ""
            lastFourInput = ""
            go(.s10b, replace: true)
            return
        }
        secrets.setBackupPassword(pw)
        var inKeychain = false
        if saveInKeychain {
            do { try deps.keychain.save(pw); inKeychain = true } catch { inKeychain = false }
        }
        answers.encryptionWas = "off"
        answers.passwordSource = ownPasswordMode ? "own" : "generated"
        answers.passwordInKeychain = inKeychain
        generatedPassword = ""
        ownPasswordInput = ""
        ownPasswordRepeat = ""
        lastFourInput = ""
        persistAnswers()
        go(.s11)
    }

    /// S10b "Weiter" only after "Ich kenne das Passwort" and a typed password (nothing is preselected).
    public var canConfirmExistingPassword: Bool {
        existingPasswordChoice == .known && !backupPasswordInput.isEmpty && !isBusy
    }

    /// S10b "Weiter": the existing password stays in memory; the engine checks it right after the PRE backup.
    public func confirmExistingPassword() {
        guard !backupPasswordInput.isEmpty else { return }
        secrets.setBackupPassword(backupPasswordInput)
        backupPasswordInput = ""
        answers.encryptionWas = "on"
        answers.passwordSource = "existing"
        answers.passwordInKeychain = false
        persistAnswers()
        go(.s11)
    }

    // MARK: - S11–S15 transfer window

    public func startBackup() {
        guard allChecked(\.offlineChecklist, Self.offlineItems) else { return }
        go(.s12)
        Task { await runPreBackup() }
    }

    /// The backup password: memory, else the keychain (generated password), else ask again (after a restart).
    /// After the engine rejected the password (F-PW-WRONG) the keychain is not used any more: the user is asked.
    func backupPassword() async -> String? {
        if let p = secrets.backupPassword { return p }
        if answers.passwordInKeychain == true, !storedPasswordRejected, let p = try? deps.keychain.load(), !p.isEmpty {
            secrets.setBackupPassword(p)
            return p
        }
        let p: String? = await withCheckedContinuation { cont in
            passwordPromptContinuation = cont
            sheet = .passwordPrompt
        }
        if let p, !p.isEmpty { secrets.setBackupPassword(p) }
        return secrets.backupPassword
    }

    /// Password sheet (resume, F-PW-WRONG): hands the typed password to the waiting step.
    public func submitPasswordPrompt(_ pw: String?) {
        sheet = nil
        let c = passwordPromptContinuation
        passwordPromptContinuation = nil
        c?.resume(returning: pw)
    }

    /// S12: `backup --role pre` and its checks (password, airplane, Threema, identity, photos).
    public func runPreBackup() async {
        guard !isBusy else { return }
        autoNewBackupTask?.cancel()
        autoNewBackupTask = nil
        guard let pw = await backupPassword() else { go(.s11, replace: true); return }
        if screen != .s12 { go(.s12, replace: screen.isFailure) }
        preBackup = nil
        prepared = nil
        let gen = flowGeneration
        let outcome = await run(.backup, args: sessionArgs() + ["--role", "pre", "--secrets-stdin"],
                                secrets: EngineSecrets(backupPassword: pw))
        guard gen == flowGeneration else { return }
        switch outcome {
        case .success(let r):
            preBackup = try? r.data.decode(BackupResult.self)
            guard preBackup != nil else { showInternal(reason: "backup_data", code: nil, origin: .s12); return }
            go(.s13)
            await runPrepare()
        default:
            handle(outcome, origin: .s12)
        }
    }

    /// S13: `prepare` (extract, import, verify, restore set, freeze). The iPhone is not touched.
    public func runPrepare() async {
        guard !isBusy else { return }
        guard let pw = await backupPassword() else { return }
        if screen != .s13 { go(.s13, replace: true) }
        prepared = nil
        let gen = flowGeneration
        let outcome = await run(.prepare, args: sessionArgs() + ["--secrets-stdin"],
                                secrets: EngineSecrets(backupPassword: pw))
        guard gen == flowGeneration else { return }
        guard case .success(let r) = outcome else { handle(outcome, origin: .s13); return }
        prepared = try? r.data.decode(PrepareResult.self)
        guard prepared != nil else { showInternal(reason: "prepare_data", code: nil, origin: .s13); return }
        transferChecks = [false, false]
        go(.s14)
    }

    public var canTransfer: Bool {
        transferChecks.allSatisfy { $0 } && !isBusy && !(countdown?.expired ?? true) && prepared != nil
    }

    /// S14 "Jetzt übertragen": `restore` (all guards in the engine, then exactly one restore).
    public func transferNow() async {
        guard canTransfer, let pw = await backupPassword() else { return }
        answers.transferConfirmedAt = Formatters.timestamp(deps.now())
        persistAnswers()
        await runRestoreCommand(.restore, password: pw)
    }

    func runRestoreCommand(_ cmd: EngineCommand, password pw: String) async {
        restoreLinkLost = false
        let origin = screen
        let gen = flowGeneration
        let outcome = await run(cmd, args: sessionArgs() + ["--secrets-stdin"],
                                secrets: EngineSecrets(backupPassword: pw))
        guard gen == flowGeneration else { return }
        if cmd == .rollbackThreema, case .failure(let r) = outcome,
           r.code == .E_GUARD_ROLLBACK_NOT_ALLOWED || r.code == .E_GUARD_ROLLBACK_WINDOW {
            rollbackRefused = true      // S21 R1 then offers R2/R3 instead of a second "reset" (REVIEW M1)
        }
        switch outcome {
        case .success:
            go(.s16, replace: true)
        case .lostDuringCritical:
            // no result although critical was on: "restore possibly sent" (DESIGN §5.6)
            restoreLinkLost = true
            go(.s16, replace: true)
        default:
            handle(outcome, origin: origin == .s15 ? .s14 : origin)
        }
    }

    /// F-FRESHNESS: the PRE backup is older than 60 minutes → new backup + prepare, automatically.
    func freshnessExpired() {
        flowGeneration += 1
        terminateRunning()
        let ctx = ErrorContext(codeRaw: EngineCode.E_GUARD_FRESHNESS.rawValue,
                               data: .object(["limit_min": .number(60)]), origin: screen)
        preBackup = nil
        prepared = nil
        showFailure(ctx)
    }

    /// F-FRESHNESS starts the new backup by itself after a short pause (DESIGN §8.4 "automatisch S12 → S13");
    /// "Neue Sicherung" starts it at once. Either way exactly one backup runs.
    func scheduleAutoNewBackup() {
        autoNewBackupTask?.cancel()
        let delay = deps.autoAdvanceDelay
        autoNewBackupTask = Task { [weak self] in
            if delay > 0 { try? await Task.sleep(nanoseconds: UInt64(delay * 1_000_000_000)) }
            guard !Task.isCancelled else { return }
            await self?.waitUntilIdle()
            guard let self, !Task.isCancelled, self.screen == .failure(.freshness) else { return }
            // runPreBackup() cancels autoNewBackupTask (a click must not start a second backup); this task IS that
            // task, so detach it first -- otherwise the backup runs inside a cancelled task, its event stream ends
            // at once and the automatic new backup always ended on F-INTERNAL ("no_result")
            self.autoNewBackupTask = nil
            await self.runPreBackup()
        }
    }

    // MARK: - S16–S20 control

    public func answerBuddy(_ a: String) {
        answers.buddyAnswer = a
        persistAnswers()
    }

    public func confirmAfterRestart() {
        guard let a = answers.buddyAnswer else { return }
        if a == "full_setup" {
            // the iPhone shows the full Setup Assistant: Threema cannot be opened (S17) and no control backup is made
            // (S18); the engine records setup_full from the answer alone → S21 R4 (REVIEW M2)
            answers.threemaCheck = nil
            persistAnswers()
            Task { await runPostcheck() }
            return
        }
        go(.s17)
    }

    public func threemaChecked(ok: Bool) {
        answers.threemaCheck = ok ? "ok" : "problem"
        persistAnswers()
        go(.s18)
        Task { await runPostcheck() }
    }

    /// S18: `backup --role post`, then `postcheck --buddy-answer … --threema-answer …` → verdict. The S16 answer
    /// "also language, country or Apps & data" skips both the backup and the password: `postcheck --buddy-answer
    /// full_setup` decides setup_full alone (REVIEW M2).
    public func runPostcheck() async {
        guard !isBusy else { return }
        let answer = answers.buddyAnswer ?? "none"
        if answer == "full_setup" {
            inlineError = nil
            let gen = flowGeneration
            let outcome = await run(.postcheck, args: sessionArgs() + ["--buddy-answer", answer])
            guard gen == flowGeneration else { return }
            finishPostcheck(outcome)
            return
        }
        guard let pw = await backupPassword() else { return }
        if screen != .s18 { go(.s18, replace: true) }
        inlineError = nil
        let gen = flowGeneration
        let backup = await run(.backup, args: sessionArgs() + ["--role", "post", "--secrets-stdin"],
                               secrets: EngineSecrets(backupPassword: pw))
        guard gen == flowGeneration else { return }
        guard case .success = backup else { handle(backup, origin: .s18); return }
        // S17: "problem" with every system check green is threema_only in the engine (R1), REVIEW M1
        let threema = answers.threemaCheck == "problem" ? "problem" : "ok"
        let outcome = await run(.postcheck, args: sessionArgs() + ["--buddy-answer", answer, "--threema-answer", threema,
                                                                   "--secrets-stdin"],
                                secrets: EngineSecrets(backupPassword: pw))
        guard gen == flowGeneration else { return }
        finishPostcheck(outcome)
    }

    private func finishPostcheck(_ outcome: CommandOutcome) {
        guard case .success(let r) = outcome else { handle(outcome, origin: .s18); return }
        guard let pc = try? r.data.decode(PostcheckResult.self), let v = pc.verdictValue else {
            showInternal(reason: "postcheck_data", code: nil, origin: .s18)
            return
        }
        postcheck = pc
        if v == .needsAnswer { go(.s16, replace: true); return }
        // the engine decides: S17 "Problem" with green system checks is already threema_only (R1), REVIEW M1
        go(v.isGreen ? .s19 : .s21, replace: true)
    }

    /// The S21 variant: R1 / R2 / R2k / R4 (DESIGN §8.3 S21).
    public enum StopVariant: String { case r1, r2, r2k, r4 }

    /// Only the engine's verdict decides; R1 is offered only for threema_only, the one verdict the rollback guard
    /// accepts (REVIEW M1). Without a verdict (should not happen on S21) the safe text is R2.
    public var stopVariant: StopVariant {
        guard let v = postcheck?.verdictValue else { return .r2 }
        switch v {
        case .setupFull: return .r4
        case .dataKeychain: return .r2k
        case .data, .restoreState: return .r2
        case .threemaOnly: return .r1
        case .needsAnswer, .ok, .okWithNotes: return .r2
        }
    }

    /// S21 R1 "Threema zurücksetzen": `rollback-threema` (engine decides: ≤ 6 h, only after the final, only R1).
    public func resetThreema() async {
        guard stopVariant == .r1, !isBusy, let pw = await backupPassword() else { return }
        rollbackMode = true
        answers.buddyAnswer = nil
        answers.threemaCheck = nil
        persistAnswers()
        await runRestoreCommand(.rollbackThreema, password: pw)
    }

    public func finishSuccess() { go(.s20) }

    /// S21 R2/R2k "So lassen": the user keeps the iPhone as it is and fixes the areas by hand → S20.
    public func leaveAsIs() { go(.s20) }

    /// `{Bereiche}` of S21 R2: the affected areas of the verdict as localized words (tokens without a text are
    /// summarised as "nicht näher bestimmte Bereiche"; no free text from the engine is ever shown).
    public func affectedAreasText(_ loc: Localizer) -> String {
        var words: [String] = []
        var unknown = false
        for a in postcheck?.areas ?? [] {
            let key = "area.\(a.area)"
            guard loc.has(key) else { unknown = true; continue }
            let w = loc.t(key)
            if !words.contains(w) { words.append(w) }
        }
        if unknown || words.isEmpty { words.append(loc.t("s21.areas.unknown")) }
        return words.joined(separator: ", ")
    }

    /// The safety copy (PRE) and the control backup (POST) are both encrypted copies of the iPhone.
    public func deleteSafetyCopy() async {
        await cleanup("pre")
        guard safetyCopyDeleted else { return }
        await cleanup("post")
    }

    /// "Sicherheitskopie behalten: 7 Tage" ran out: S20 proposes the deletion again (DESIGN §10.1).
    public var safetyCopyKeepExpired: Bool {
        guard let until = Formatters.parseTimestamp(answers.cleanup?.safetyCopyKeepUntil) else { return false }
        return until <= now
    }

    /// S20 cleanup: `cleanup --what work` (readable chat copies) or `--what all` (also the safety copy).
    public func cleanup(_ what: String) async {
        guard !isBusy else { return }
        let gen = flowGeneration
        let outcome = await run(.cleanup, args: sessionArgs() + ["--what", what])
        guard gen == flowGeneration else { return }
        guard case .success(let r) = outcome else { handle(outcome, origin: screen); return }
        freedBytes += (try? r.data.decode(CleanupResult.self))?.freedBytes ?? 0
        var c = answers.cleanup ?? CleanupAnswers()
        if what == "work" || what == "all" {
            copiesDeleted = true
            c.chatCopiesDeletedAt = Formatters.timestamp(deps.now())
        }
        if what == "all" || what == "pre" {
            safetyCopyDeleted = true
            c.safetyCopyKeepUntil = nil
        }
        answers.cleanup = c
        persistAnswers()
    }

    public func keepSafetyCopy(days: Int = 7) {
        var c = answers.cleanup ?? CleanupAnswers()
        c.safetyCopyKeepUntil = Formatters.timestamp(deps.now().addingTimeInterval(TimeInterval(days) * 86_400))
        answers.cleanup = c
        persistAnswers()
    }

    /// S20 sizes: readable chat copies ≈ Android data + transfer package; safety copy = PRE backup.
    public var chatCopiesBytes: Int64 {
        let android = inspect?.files.reduce(Int64(0)) { $0 + $1.bytes } ?? 0
        return max(android, 0) + (prepared?.payloadBytes ?? 0)
    }

    public var safetyCopyBytes: Int64 { preBackup?.bytes ?? 0 }

    // MARK: - Diagnostic report

    public func createDiagReport() async {
        guard !isBusy else { return }
        diagFile = nil
        diagCreating = true
        sheet = .diagReport
        let outcome = await run(.diagReport, args: sessionArgs())
        diagCreating = false
        if case .success(let r) = outcome, let d = try? r.data.decode(DiagReportResult.self),
           let url = deps.sessions.file(forKey: d.file), FileManager.default.fileExists(atPath: url.path) {
            diagFile = url
        }
    }

    // MARK: - Leaving early (REVIEW M4)

    /// What the user switched off on the iPhone for the move (S08, S11) and has to turn on again when stopping before
    /// the end: keys `turn_on.*`, from the ticked checklists of this session (also after a restart).
    public var turnBackOnKeys: [String] {
        let off = answers.offlineChecklist ?? [:]
        let set = answers.settingsChecklist ?? [:]
        var out: [String] = []
        if off["airplane_on"] == true { out.append("turn_on.airplane") }
        if off["watch_bluetooth_off"] == true { out.append("turn_on.bluetooth") }
        if set["find_my_off"] == true { out.append("turn_on.find_my") }
        if set["ios_updates_off"] == true || set["app_updates_off"] == true { out.append("turn_on.updates") }
        return out
    }

    /// Before anything was sent: a stop (cancel, quit, discard, a dead-end error, waiting for an update) leaves the
    /// iPhone without Find My, updates, maybe in airplane mode -- say what to turn back on. After the send the user
    /// continues with the check (S16) and S20/S22 carry the list.
    public var showsStopReminder: Bool {
        guard !turnBackOnKeys.isEmpty else { return false }
        if resume?.afterSend == true { return false }
        let o = policyScreen == .s23 ? (resume?.screen ?? resumeLastStep ?? .s00).order : policyScreen.order
        return (o ?? 0) < (Screen.s15.order ?? 0)
    }

    /// Alert text plus the turn-back-on list (cancel, quit, discard).
    public func withStopReminder(_ text: String, _ loc: Localizer) -> String {
        guard showsStopReminder else { return text }
        let lines = turnBackOnKeys.map { "• " + loc.t($0) }
        return ([text, "", loc.t("turn_on.intro")] + lines).joined(separator: "\n")
    }

    // MARK: - Outcome routing

    /// The password {App} had (typed, generated or from the keychain) does not open the backup: forget it; F-PW-WRONG
    /// asks the user, and the keychain copy is not offered again (REVIEW B1).
    func passwordRejected() {
        secrets.setBackupPassword(nil)
        storedPasswordRejected = true
        if answers.passwordInKeychain == true {
            answers.passwordInKeychain = false
            persistAnswers()
        }
    }

    func handle(_ outcome: CommandOutcome, origin: Screen) {
        switch outcome {
        case .success:
            break
        case .failure(let r):
            if r.code == .E_CANCELLED { return }   // the cancel flow decides where to go
            if r.code == .E_BACKUP_PASSWORD { passwordRejected() }
            if r.code?.info.needsNewBackup == true { preBackup = nil; prepared = nil }
            let after = r.deviceModified != "no" || critical
            if after, r.code != .E_RESTORE_INTERRUPTED, origin.order ?? 0 >= (Screen.s14.order ?? 0) {
                restoreLinkLost = true
                go(.s16, replace: true)
                return
            }
            showFailure(ErrorContext(codeRaw: r.codeRaw, data: r.data, origin: origin, deviceModified: r.deviceModified))
        case .lostDuringCritical:
            restoreLinkLost = true
            go(.s16, replace: true)
        case .internalError(let reason, let code):
            showInternal(reason: reason, code: code, origin: origin)
        }
    }
}
