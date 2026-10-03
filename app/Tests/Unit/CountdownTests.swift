// SPDX-License-Identifier: AGPL-3.0-or-later
import XCTest
@testable import ThreemaChatTransfer

/// The freshness countdown of S13/S14 (DESIGN §8.1, §8.3 S13).
final class CountdownTests: XCTestCase {
    let deadline = Formatters.parseTimestamp("2026-01-01T10:07:00.000Z")!
    let utc = TimeZone(identifier: "UTC")!

    func testMinutesRoundUpAndNeverShowZero() {
        XCTAssertEqual(Countdown.state(deadline: deadline, now: deadline.addingTimeInterval(-3600)).minutesLeft, 60)
        XCTAssertEqual(Countdown.state(deadline: deadline, now: deadline.addingTimeInterval(-61)).minutesLeft, 2)
        XCTAssertEqual(Countdown.state(deadline: deadline, now: deadline.addingTimeInterval(-59)).minutesLeft, 1)
        XCTAssertEqual(Countdown.state(deadline: deadline, now: deadline.addingTimeInterval(-1)).minutesLeft, 1)
        XCTAssertFalse(Countdown.state(deadline: deadline, now: deadline.addingTimeInterval(-1)).expired)
    }

    func testExpiredAtTheDeadline() {
        let s = Countdown.state(deadline: deadline, now: deadline)
        XCTAssertTrue(s.expired)
        XCTAssertEqual(s.minutesLeft, 0)
        XCTAssertFalse(s.isUrgent)
        XCTAssertTrue(Countdown.state(deadline: deadline, now: deadline.addingTimeInterval(5)).expired)
    }

    func testUrgentUnderTenMinutes() {
        XCTAssertFalse(Countdown.state(deadline: deadline, now: deadline.addingTimeInterval(-600)).isUrgent)
        XCTAssertTrue(Countdown.state(deadline: deadline, now: deadline.addingTimeInterval(-590)).isUrgent)
    }

    func testTextInBothLanguages() {
        let s = Countdown.state(deadline: deadline, now: deadline.addingTimeInterval(-25 * 60))
        XCTAssertEqual(Countdown.text(s, loc: Localizer(.de), timeZone: utc),
                       "Die Übertragung muss bis 10:07 starten (noch 25 Minuten).")
        XCTAssertEqual(Countdown.text(s, loc: Localizer(.en), timeZone: utc),
                       "The transfer must start by 10:07 (25 minutes left).")
        let one = Countdown.state(deadline: deadline, now: deadline.addingTimeInterval(-30))
        XCTAssertFalse(Countdown.text(one, loc: Localizer(.de), timeZone: utc).contains("Minuten"))
        XCTAssertFalse(Countdown.text(one, loc: Localizer(.en), timeZone: utc).contains("minutes"))
    }

    @MainActor
    func testStoreTickOnlyActsOnS13AndS14() throws {
        let clock = TestClock(deadline.addingTimeInterval(60))
        let h = try StoreHarness(scenario: "happy", clock: clock)
        h.store.go(.s12)
        h.store.tick()
        XCTAssertEqual(h.store.screen, .s12, "no prepared/backup deadline yet: nothing happens")
    }
}
