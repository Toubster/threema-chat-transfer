// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation

/// Buttons of error screens (`codes.v1.json` → `actions`), cancel and quit (DESIGN §8.1, §8.4).
extension WizardStore {
    /// What a code action does. Every path either repeats the failed step after the user's fix or goes back to the
    /// screen where the user fixes it; nothing skips a check.
    public func perform(_ action: CodeAction) {
        let origin = error?.origin ?? inlineError?.origin ?? policyScreen
        let code = error?.code ?? inlineError?.code
        switch action {
        case .quit:
            dialog = nil
            terminateApp?()
        case .retry, .recheck:
            retry(code: code, origin: origin)
        case .chooseLocation:
            chooseLocationRequested = true
        case .seeVersions:
            if let u = AppInfo.releasesURL { deps.openURL(u) }
        case .diagReport:
            Task { await createDiagReport() }
        case .prepareAndroid:
            prepareAndroidOnly()
        case .backS03:
            preBackup = nil
            prepared = nil
            go(.s03, replace: true)
        case .backS05:
            inspect = nil
            normalized = nil
            go(.s05, replace: true)
        case .backS07:
            preBackup = nil
            prepared = nil
            go(.s07, replace: true)
        case .newBackup:
            // a stale click (the screen already moved on) never starts a second backup
            guard screen.isFailure || inlineError != nil else { return }
            if origin == .s18 || code == .E_POST_TOO_EARLY {
                Task { await runPostcheck() }
            } else {
                // F-AIRPLANE / F-DCIM: the user fixed the cause; the offline checklist of S11 still holds
                Task { await runPreBackup() }
            }
        case .autoNewBackup:
            // F-FRESHNESS starts the new backup by itself as well (scheduleAutoNewBackup): exactly one backup runs
            guard screen == .failure(.freshness) else { return }
            Task { await runPreBackup() }
        case .help:
            sheet = .help
        case .reenterPassword:
            if code == .E_ANDROID_PASSWORD {
                inlineError = nil
            } else {
                // E_BACKUP_PASSWORD: no new backup needed; the engine re-checks the kept backup. The user is always
                // asked (the rejected password is not loaded from the keychain again, REVIEW B1); a POST backup
                // (S18) is checked again by the control step, never by a new PRE backup.
                secrets.setBackupPassword(nil)
                storedPasswordRejected = true
                if origin == .s18 {
                    Task { await runPostcheck() }
                } else {
                    Task { await runPreBackup() }
                }
            }
        case .continueS16:
            restoreLinkLost = true
            go(.s16, replace: true)
        case .continue:
            error = nil
            inlineError = nil
        case .back:
            if screen.isFailure, let o = error?.origin { error = nil; screen = o; persistScreen() } else { goBack() }
        case .setEncryption:
            go(.s10a, replace: true)
        case .resetThreema:
            dialog = .resetThreema
        case .none:
            error = nil
            inlineError = nil
        }
    }

    /// "Erneut versuchen": repeat the step that failed, from the screen where it belongs.
    func retry(code: EngineCode?, origin: Screen) {
        inlineError = nil
        switch origin {
        case .s02:
            go(.s02, replace: true)
            Task { await runHostCheck() }
        case .s03:
            go(.s03, replace: true)
            Task { await runDeviceCheck() }
        case .s08:
            go(.s08, replace: true)
        case .s12:
            Task { await runPreBackup() }
        case .s13:
            Task { await runPrepare() }
        case .s14, .s15:
            // back to the confirmation; the countdown and the S14 ticks are checked again
            transferChecks = [false, false]
            go(.s14, replace: true)
        case .s18:
            Task { await runPostcheck() }
        default:
            go(origin, replace: true)
        }
    }

    // MARK: cancel (until S14, never during critical)

    public func requestCancel() {
        guard canCancel else { return }
        dialog = .cancel
    }

    /// "Später weitermachen" (keep the session, show S23) or "Sitzung verwerfen".
    public func confirmCancel(discard: Bool) async {
        dialog = nil
        guard canCancel else { return }
        flowGeneration += 1
        terminateRunning()
        watchHandle?.terminate()
        await waitUntilIdle()
        if discard {
            try? deps.sessions.discard()
            resetForNewSession()
            screen = .s00
            history = []
            return
        }
        let last = policyScreen
        resumeLastStep = last
        let decision = ResumePolicy.decide(status: nil, local: deps.sessions.state, now: deps.now())
        resume = decision
        history = []
        error = nil
        inlineError = nil
        screen = .s23
    }

    // MARK: quit

    /// From the app delegate (⌘Q, last window closed).
    public func requestQuit() -> Bool {
        switch quitDecision {
        case .allow: return true
        case .confirm: dialog = .quit; return false
        case .refuse: dialog = .quitRefused; return false
        }
    }

    /// The app is terminating (quit was allowed or confirmed): stop non-critical commands, drop secrets.
    public func prepareForTermination() {
        if !critical {
            terminateRunning()
            watchHandle?.terminate()
        }
        secrets.wipe()
    }

    public func confirmQuit() {
        dialog = nil
        guard !critical else { dialog = .quitRefused; return }
        flowGeneration += 1
        terminateRunning()
        watchHandle?.terminate()
        terminateApp?()
    }
}

/// Hooks the AppKit layer sets (keeps the store free of AppKit for unit tests).
@MainActor
public final class AppHooks {
    public static var terminate: (() -> Void)?
}

extension WizardStore {
    var terminateApp: (() -> Void)? { AppHooks.terminate }
}
