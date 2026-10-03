// SPDX-License-Identifier: AGPL-3.0-or-later
import XCTest
@testable import ThreemaChatTransfer

/// The resume table of DESIGN §8.1. Invariant: once the restore may have been sent, never "start over".
final class ResumePolicyTests: XCTestCase {
    let now = Formatters.parseTimestamp("2026-01-01T10:00:00.000Z")!

    func local(_ screen: String) -> SessionState {
        var s = SessionState(sessionId: "t", now: now, appVersion: "t", locale: .de)
        s.screen = screen
        return s
    }

    func status(_ phase: String, resumeAt: String = "S00", sent: String? = nil, fresh: String? = nil,
                verdict: String? = nil) -> SessionStatusResult {
        SessionStatusResult(phase: phase, resumeAt: resumeAt, restoreSentAt: sent, freshUntil: fresh, verdict: verdict)
    }

    func testNoSessionStartsAtS00() {
        let d = ResumePolicy.decide(status: nil, local: nil, now: now)
        XCTAssertEqual(d.screen, .s00)
        XCTAssertTrue(d.closed)
    }

    func testAndroidDoneWithoutPreBackupGoesToS07WithWelcomeBack() {
        let d = ResumePolicy.decide(status: status("android_done"), local: local("S08"), now: now)
        XCTAssertEqual(d.screen, .s07)
        XCTAssertTrue(d.welcomeBack)
        XCTAssertTrue(d.canDiscard)
    }

    func testPreBackupWithinDeadline() {
        let fresh = "2026-01-01T10:30:00.000Z"
        XCTAssertEqual(ResumePolicy.decide(status: status("pre_backup_done", fresh: fresh), local: local("S13"), now: now).screen, .s13)
        XCTAssertEqual(ResumePolicy.decide(status: status("prepared", fresh: fresh), local: local("S14"), now: now).screen, .s14)
    }

    func testPreBackupDeadlinePassedNeedsANewBackup() {
        let fresh = "2026-01-01T09:59:00.000Z"
        let d = ResumePolicy.decide(status: status("prepared", fresh: fresh), local: local("S14"), now: now)
        XCTAssertEqual(d.screen, .s11)
        XCTAssertFalse(d.afterSend)
    }

    func testRestoreSentAlwaysGoesToS16() {
        for phase in ["restore_sent", "restore_finished", "post_backup_done", "rollback_sent", "prepared"] {
            let d = ResumePolicy.decide(status: status(phase, sent: "2026-01-01T09:50:00.000Z"), local: local("S14"),
                                        now: now)
            XCTAssertEqual(d.screen, .s16, phase)
            XCTAssertTrue(d.afterSend, phase)
            XCTAssertFalse(d.canDiscard, phase)
        }
    }

    func testAppSawCriticalButEngineHasNoRecordStillS16() {
        // session.json says S15 (critical was seen) — even an engine that reports an earlier phase cannot pull it back
        let d = ResumePolicy.decide(status: status("prepared", fresh: "2026-01-01T10:30:00.000Z"), local: local("S15"),
                                    now: now)
        XCTAssertEqual(d.screen, .s16)
        XCTAssertEqual(ResumePolicy.decide(status: nil, local: local("S15"), now: now).screen, .s16)
    }

    func testPostcheckVerdicts() {
        XCTAssertEqual(ResumePolicy.decide(status: status("postcheck_done", verdict: "ok"), local: local("S19"), now: now).screen, .s20)
        XCTAssertEqual(ResumePolicy.decide(status: status("postcheck_done", verdict: "ok_with_notes"), local: local("S19"), now: now).screen, .s20)
        for v in ["data", "data_keychain", "threema_only", "setup_full", "restore_state", "something_new"] {
            let d = ResumePolicy.decide(status: status("postcheck_done", verdict: v), local: local("S21"), now: now)
            XCTAssertEqual(d.screen, .s21, v)
            XCTAssertTrue(d.canDiscard, "discarding is allowed after a postcheck")
        }
        XCTAssertEqual(ResumePolicy.decide(status: status("postcheck_done", verdict: "needs_answer"), local: local("S18"), now: now).screen, .s16)
    }

    func testClosedSessionStartsNew() {
        XCTAssertTrue(ResumePolicy.decide(status: status("closed"), local: local("S20"), now: now).closed)
    }

    func testUnknownEnginePhaseNeverSkipsAhead() {
        let d = ResumePolicy.decide(status: status("phase_from_the_future", resumeAt: "S14"), local: local("S14"),
                                    now: now)
        XCTAssertEqual(d.screen, .s02, "an unknown phase inside the window restarts from the host check")
        let e = ResumePolicy.decide(status: status("phase_from_the_future", resumeAt: "S05"), local: local("S05"), now: now)
        XCTAssertEqual(e.screen, .s05)
    }

    func testWithoutEngineAnswerTheSafeSideWins() {
        XCTAssertEqual(ResumePolicy.decide(status: nil, local: local("S13"), now: now).screen, .s11)
        XCTAssertEqual(ResumePolicy.decide(status: nil, local: local("S08"), now: now).screen, .s07)
        XCTAssertEqual(ResumePolicy.decide(status: nil, local: local("S04"), now: now).screen, .s02)
        XCTAssertEqual(ResumePolicy.decide(status: nil, local: local("S17"), now: now).screen, .s16)
        XCTAssertEqual(ResumePolicy.decide(status: nil, local: local("S21"), now: now).screen, .s21)
    }
}
