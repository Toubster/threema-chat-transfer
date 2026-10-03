// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation

/// Where to continue after a restart (DESIGN §8.1, resume table). Inputs: the engine's `session-status` (may be
/// missing when the engine cannot answer) and the app's own `session.json`.
///
/// Invariant: once the restore may have been sent (`restore_sent_at`, or the app saw `critical` and saved S15),
/// the answer is S16 or later, never "start over".
public enum ResumePolicy {
    public struct Decision: Equatable, Sendable {
        public let screen: Screen
        /// The restore was (possibly) sent: S23 shows the "already sent" text and no "discard".
        public let afterSend: Bool
        /// Discarding is allowed: nothing sent yet, or the postcheck is done.
        public let canDiscard: Bool
        /// The session is finished; the app starts a new one at S00.
        public let closed: Bool
        /// Show "Willkommen zurück" on S07.
        public let welcomeBack: Bool
    }

    static let afterSendPhases: Set<String> = ["restore_sent", "restore_finished", "post_backup_done", "rollback_sent"]
    static let localAfterSend: Set<String> = ["S15", "S16", "S17", "S18"]

    public static func decide(status: SessionStatusResult?, local: SessionState?, now: Date) -> Decision {
        guard let local else {
            return Decision(screen: .s00, afterSend: false, canDiscard: true, closed: true, welcomeBack: false)
        }
        if let s = status {
            if s.phase == "closed" {
                return Decision(screen: .s00, afterSend: false, canDiscard: true, closed: true, welcomeBack: false)
            }
            if s.phase == "postcheck_done" {
                return Decision(screen: screen(forVerdict: s.verdict), afterSend: true, canDiscard: true, closed: false,
                                welcomeBack: false)
            }
            if s.restoreSentAt != nil || afterSendPhases.contains(s.phase) || localAfterSend.contains(local.screen) {
                return Decision(screen: .s16, afterSend: true, canDiscard: false, closed: false, welcomeBack: false)
            }
            switch s.phase {
            case "pre_backup_done", "prepared":
                if let fresh = Formatters.parseTimestamp(s.freshUntil), fresh > now {
                    return Decision(screen: s.phase == "prepared" ? .s14 : .s13, afterSend: false, canDiscard: true,
                                    closed: false, welcomeBack: false)
                }
                return Decision(screen: .s11, afterSend: false, canDiscard: true, closed: false, welcomeBack: false)
            case "android_done", "iphone_prepared":
                return Decision(screen: .s07, afterSend: false, canDiscard: true, closed: false, welcomeBack: true)
            case "host_checked":
                return Decision(screen: .s03, afterSend: false, canDiscard: true, closed: false, welcomeBack: false)
            default:
                // "new" or a phase this app version does not know: the engine's own screen if it is before the
                // time window, otherwise the start of the window (fail-safe: never past a check).
                let engineScreen = Screen(id: s.resumeAt)
                let target = (engineScreen?.order ?? 99) <= (Screen.s10b.order ?? 0) ? engineScreen! : .s02
                return Decision(screen: target, afterSend: false, canDiscard: true, closed: false, welcomeBack: false)
            }
        }
        // No answer from the engine: decide from session.json alone, always on the safe side.
        if localAfterSend.contains(local.screen) {
            return Decision(screen: .s16, afterSend: true, canDiscard: false, closed: false, welcomeBack: false)
        }
        switch local.screen {
        case "S19", "S20":
            return Decision(screen: .s20, afterSend: true, canDiscard: true, closed: false, welcomeBack: false)
        case "S21", "S22":
            return Decision(screen: .s21, afterSend: true, canDiscard: true, closed: false, welcomeBack: false)
        default:
            let saved = Screen(id: local.screen)
            if let o = saved?.order, o >= (Screen.s11.order ?? 0) {
                // inside the time window but no confirmation from the engine: new backup
                return Decision(screen: .s11, afterSend: false, canDiscard: true, closed: false, welcomeBack: false)
            }
            if let o = saved?.order, o >= (Screen.s07.order ?? 0) {
                return Decision(screen: .s07, afterSend: false, canDiscard: true, closed: false, welcomeBack: true)
            }
            return Decision(screen: .s02, afterSend: false, canDiscard: true, closed: false, welcomeBack: false)
        }
    }

    public static func screen(forVerdict v: String?) -> Screen {
        guard let v, let verdict = Verdict(rawValue: v) else { return .s21 }
        if verdict.isGreen { return .s20 }
        if verdict == .needsAnswer { return .s16 }
        return .s21
    }
}
