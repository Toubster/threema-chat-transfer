// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation

/// The freshness countdown of S13/S14 (DESIGN §8.1): the transfer must start before `fresh_until`
/// (PRE backup + 60 minutes, decided by the engine). Pure, so it is unit-tested with a fixed clock.
public struct CountdownState: Equatable, Sendable {
    public let deadline: Date
    public let secondsLeft: Int
    public var expired: Bool { secondsLeft <= 0 }
    /// Whole minutes shown to the user, rounded up ("noch 1 Minute" until the very end, never "noch 0 Minuten").
    public var minutesLeft: Int { expired ? 0 : Int((Double(secondsLeft) / 60).rounded(.up)) }
    /// Under 10 minutes the countdown is shown as a warning.
    public var isUrgent: Bool { !expired && secondsLeft < 600 }
}

public enum Countdown {
    public static func state(deadline: Date, now: Date) -> CountdownState {
        CountdownState(deadline: deadline, secondsLeft: Int(deadline.timeIntervalSince(now).rounded(.down)))
    }

    /// "Die Übertragung muss bis {time} starten (noch {n} Minuten)."
    public static func text(_ s: CountdownState, loc: Localizer, timeZone: TimeZone = .current) -> String {
        let time = Formatters.time(s.deadline, lang: loc.language, timeZone: timeZone)
        if s.minutesLeft == 1 { return loc.t("countdown.text_one", ["time": time]) }
        return loc.t("countdown.text", ["time": time, "n": String(s.minutesLeft)])
    }
}
