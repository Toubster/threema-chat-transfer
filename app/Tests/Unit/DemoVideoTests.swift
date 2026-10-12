// SPDX-License-Identifier: AGPL-3.0-or-later
import XCTest
@testable import ThreemaChatTransfer

/// Pacing of the README walkthrough video (`DemoVideoTiming`) and when the recording mode exists at all.
final class DemoVideoTests: XCTestCase {
    typealias T = DemoVideoTiming

    func testRecordingOnlyInDemoAutopilotRuns() {
        let env = ["TM_AUTOPILOT": "1", "TM_VIDEO_DIR": "/tmp/v", "TM_VIDEO_FPS": "12", "TM_VIDEO_END": "S19"]
        XCTAssertNil(DemoAutopilot.Config.from(env, engineSpec: "live"), "never with the live engine")
        XCTAssertNil(DemoAutopilot.Config.from(["TM_VIDEO_DIR": "/tmp/v"], engineSpec: "fake:happy"),
                     "needs the autopilot")
        let v = DemoAutopilot.Config.from(env, engineSpec: "fake:happy")?.video
        XCTAssertEqual(v?.dir.path, "/tmp/v")
        XCTAssertEqual(v?.fps, 12)
        XCTAssertEqual(v?.end, "S19")
        XCTAssertNil(DemoAutopilot.Config.from(["TM_AUTOPILOT": "1"], engineSpec: "mock:happy")?.video,
                     "screenshot runs: no recording")
        XCTAssertEqual(DemoVideo.Config.from(["TM_VIDEO_DIR": "/tmp/v", "TM_VIDEO_FPS": "500"])?.fps, 30)
        XCTAssertEqual(DemoVideo.Config.from(["TM_VIDEO_DIR": "/tmp/v", "TM_VIDEO_FPS": "x"])?.fps, T.defaultFPS)
        XCTAssertNil(DemoVideo.Config.from(["TM_VIDEO_DIR": "/tmp/v", "TM_VIDEO_END": ""])?.end)
    }

    func testFramesAreWholeAndNeverZero() {
        XCTAssertEqual(T.frames(3, fps: 10), 30)
        XCTAssertEqual(T.frames(0.26, fps: 10), 3)
        XCTAssertEqual(T.frames(0, fps: 10), 1)
        XCTAssertEqual(T.frames(-1, fps: 10), 1)
    }

    func testReadingTimeFollowsTheTextWithinLimits() {
        XCTAssertEqual(T.dwell(chars: 0), T.minDwell)
        XCTAssertEqual(T.dwell(chars: 100_000), T.maxDwell)
        let short = T.dwell(chars: 600), long = T.dwell(chars: 900)
        XCTAssertGreaterThan(long, short)
        XCTAssertEqual(long - short, 300 / T.dwellCharsPerSecond, accuracy: 1e-9)
        XCTAssertGreaterThanOrEqual(T.minDwell, 2)
        XCTAssertLessThanOrEqual(T.maxDwell, 5)
    }

    func testGlideTimeGrowsWithDistanceWithinLimits() {
        XCTAssertEqual(T.glide(distance: 0), T.minGlide)
        XCTAssertEqual(T.glide(distance: 5000), T.maxGlide)
        XCTAssertLessThan(T.glide(distance: 100), T.glide(distance: 300))
        XCTAssertGreaterThanOrEqual(T.glide(distance: 300), 0.5, "across the window: about half a second")
    }

    func testEaseInOutIsMonotonicAndSymmetric() {
        XCTAssertEqual(T.ease(0), 0)
        XCTAssertEqual(T.ease(1), 1)
        XCTAssertEqual(T.ease(0.5), 0.5, accuracy: 1e-12)
        XCTAssertEqual(T.ease(-1), 0)
        XCTAssertEqual(T.ease(2), 1)
        var last = -1.0
        for i in 0...100 {
            let t = Double(i) / 100
            XCTAssertGreaterThanOrEqual(T.ease(t), last)
            XCTAssertEqual(T.ease(t) + T.ease(1 - t), 1, accuracy: 1e-12)
            last = T.ease(t)
        }
        XCTAssertLessThan(T.ease(0.1), 0.1, "slow start")
        XCTAssertGreaterThan(T.ease(0.9), 0.9, "slow landing")
    }

    func testGlidePathEndsOnTheTargetAndNeverJumps() {
        let from = CGPoint(x: 100, y: 500), to = CGPoint(x: 800, y: 40)
        let path = T.glidePath(from: from, to: to, frames: 7)
        XCTAssertEqual(path.count, 7)
        XCTAssertEqual(path.last, to)
        XCTAssertNotEqual(path.first, from, "the start is the previous frame")
        for p in path {
            XCTAssertTrue((min(from.x, to.x)...max(from.x, to.x)).contains(p.x))
            XCTAssertTrue((min(from.y, to.y)...max(from.y, to.y)).contains(p.y))
        }
        XCTAssertTrue(T.glidePath(from: from, to: to, frames: 0).isEmpty)
    }

    func testTypingAppearsProgressivelyAtTwelveCharactersPerSecond() {
        XCTAssertEqual(T.typing(count: 12, fps: 10).count, 10, "12 characters at 12/s = 1 s")
        XCTAssertEqual(T.typing(count: 4, fps: 10), [1, 2, 3, 4])
        // a long password (dots) is typed faster: at most `maxTyping`
        let steps = T.typing(count: 24, fps: 10)
        XCTAssertEqual(steps.count, T.frames(T.maxTyping, fps: 10))
        XCTAssertEqual(steps.last, 24)
        XCTAssertGreaterThanOrEqual(steps.first ?? 0, 1)
        XCTAssertEqual(steps, steps.sorted(), "never shrinks")
        XCTAssertLessThan(steps[5], 24, "not all at once")
        XCTAssertTrue(T.typing(count: 0, fps: 10).isEmpty)
    }

    func testCaptionShowsDuringTheStartHoldThenFades() {
        // the start screen with its caption is the first picture (also GitHub's still picture): about 4 s with the
        // first click
        XCTAssertGreaterThanOrEqual(T.startHold, 3)
        XCTAssertLessThanOrEqual(T.startHold, 4.5)
        let end = T.frames(T.startHold, fps: 10), fade = T.frames(T.captionFade, fps: 10)
        XCTAssertEqual(T.captionAlpha(frame: 0, fps: 10), 1)
        XCTAssertEqual(T.captionAlpha(frame: end - fade, fps: 10), 1)
        let fading = T.captionAlpha(frame: end - fade / 2, fps: 10)
        XCTAssertGreaterThan(fading, 0)
        XCTAssertLessThan(fading, 1)
        XCTAssertEqual(T.captionAlpha(frame: end, fps: 10), 0, "gone when the pointer starts to move")
        XCTAssertEqual(T.captionAlpha(frame: 500, fps: 10), 0)
    }

    func testRippleGrowsAndFades() {
        let a = T.ripple(0), b = T.ripple(0.5), c = T.ripple(1)
        XCTAssertLessThan(a.radius, b.radius)
        XCTAssertLessThan(b.radius, c.radius)
        XCTAssertGreaterThan(a.alpha, b.alpha)
        XCTAssertEqual(c.alpha, 0)
    }

    private func sample(_ screen: String, _ t: Double, wait: Bool = false, chars: Int = 400) -> T.LiveSample {
        T.LiveSample(screen: screen, time: t, wait: wait, chars: chars)
    }

    func testWaitStepsGetAReadingTimeWhateverTheVirtualIPhoneTook() {
        // backup (3 states in 0.14 s) and preparation (1 state) of the virtual iPhone, then the next user screen
        let s = [sample("S12", 0, wait: true), sample("S12", 0.05, wait: true), sample("S12", 0.14, wait: true),
                 sample("S13", 0.15, wait: true), sample("S14", 0.6)]
        let f = T.allot(s, end: 0.7, fps: 10)
        let backup = f[0...2].reduce(0, +), prepare = f[3]
        XCTAssertGreaterThanOrEqual(backup, T.frames(T.minWait, fps: 10))
        XCTAssertLessThanOrEqual(backup, T.frames(T.maxWait, fps: 10))
        XCTAssertGreaterThanOrEqual(prepare, T.frames(T.minWait, fps: 10))
        XCTAssertGreaterThanOrEqual(f[2], Int((Double(backup) * T.waitFinalShare).rounded()), "final state stays")
        XCTAssertTrue(f.allSatisfy { $0 >= 1 })
        XCTAssertEqual(f[4], 1, "the next screen gets its reading pause from the next step")
    }

    func testWaitStepsSplitTheirTimeByTheRealGaps() {
        let s = [sample("S15", 0, wait: true), sample("S15", 0.9, wait: true), sample("S15", 1.0, wait: true),
                 sample("S16", 1.1)]
        let f = T.allot(s, end: 1.2, fps: 10)
        XCTAssertGreaterThan(f[0], f[1], "the longer state shows longer")
        let step = T.frames(min(T.maxWait, max(T.minWait, T.dwell(chars: 400) * 0.8)), fps: 10)
        XCTAssertEqual(f[0...2].reduce(0, +), step)
    }

    func testManyStatesInOneWaitStepEachStayVisible() {
        let s = (0..<40).map { sample("S12", Double($0) * 0.01, wait: true) }
        let f = T.allot(s, end: 0.5, fps: 10)
        XCTAssertTrue(f.allSatisfy { $0 >= 1 })
        XCTAssertEqual(f.count, 40)
    }

    func testOtherStatesKeepTheirRealDurationWithinLimits() {
        // S03: iPhone found (a fraction of a second), waiting for the iPhone, a check, then the result
        let s = [sample("S03", 0), sample("S03", 0.3), sample("S03", 3.0), sample("S03", 3.7)]
        let f = T.allot(s, end: 3.8, fps: 10)
        XCTAssertEqual(f[0], T.frames(T.minState, fps: 10), "a short state is still visible")
        XCTAssertEqual(f[1], T.frames(T.maxState, fps: 10), "a long one is capped")
        XCTAssertEqual(f[2], T.frames(0.7, fps: 10))
        XCTAssertEqual(f[3], 1)
        XCTAssertTrue(T.allot([], end: 1, fps: 10).isEmpty)
    }

    func testStatesThatPassAtOnceAreReplacedByTheNextOne() {
        // S02: the Mac check fills in its rows within a split second; only the finished list is shown
        let s = [sample("S02", 0), sample("S02", 0.04), sample("S02", 0.08), sample("S02", 0.12), sample("S02", 0.2)]
        XCTAssertEqual(T.allot(s, end: 0.3, fps: 10), [0, 0, 0, 0, 1])
        // in a wait step every state stays visible
        let w = [sample("S12", 0, wait: true), sample("S12", 0.01, wait: true), sample("S12", 0.02, wait: true)]
        XCTAssertTrue(T.allot(w, end: 0.03, fps: 10).allSatisfy { $0 >= 1 })
    }

    func testNoticesThatGoAwayStayLongEnoughToBeRead() {
        XCTAssertEqual(T.noticeTime(chars: 0), T.minNotice)
        XCTAssertEqual(T.noticeTime(chars: 10_000), T.maxNotice)
        XCTAssertLessThan(T.noticeTime(chars: 50), T.noticeTime(chars: 70))
        // S10a: the passcode banner shows while the iPhone asks (0.4 s on the virtual iPhone), then S11 comes
        var banner = sample("S10a", 0)
        banner.notice = 45
        let f = T.allot([banner, sample("S11", 0.4)], end: 0.5, fps: 10)
        XCTAssertEqual(f[0], T.frames(T.noticeTime(chars: 45), fps: 10))
        XCTAssertGreaterThanOrEqual(f[0], T.frames(T.minNotice, fps: 10))
        // S15: "the iPhone restarts by itself" closes the transfer: the step lasts longer than a plain wait step
        var restart = sample("S15", 0.3, wait: true)
        restart.notice = 150
        let t = T.allot([sample("S15", 0, wait: true), restart, sample("S16", 0.5)], end: 0.6, fps: 10)
        XCTAssertEqual(t[1], T.frames(T.maxNotice, fps: 10), "the notice can be read")
        XCTAssertGreaterThanOrEqual(t[0] + t[1], T.frames(3, fps: 10), "the transfer shows at least 3 s")
        // a state that passes at once still shows when it brings a notice (an iPhone prompt)
        var prompt = sample("S03", 0.05)
        prompt.notice = 40
        XCTAssertEqual(T.allot([sample("S03", 0), prompt, sample("S03", 0.1)], end: 0.2, fps: 10)[1],
                       T.frames(T.noticeTime(chars: 40), fps: 10))
    }

    func testClickPressesBeforeTheActionAndReleasesAfter() {
        let c = T.click(fps: 20)
        XCTAssertEqual(c.press.count, T.frames(T.pressSeconds, fps: 20))
        XCTAssertEqual(c.release.count, T.frames(T.releaseSeconds, fps: 20))
        XCTAssertEqual(c.press.first, 0)
        XCTAssertEqual(c.release.last, 1)
        let all = c.press + c.release
        XCTAssertEqual(all, all.sorted())
        XCTAssertEqual(Set(all).count, all.count, "every frame moves on")
        XCTAssertGreaterThan(T.ripple(c.press.last ?? 0).alpha, 0.5, "clearly visible when the action runs")
    }

    func testClickPointsKeepLabelsAndTypedTextFree() {
        let button = CGRect(x: 760, y: 14, width: 140, height: 30)
        let b = T.clickPoint(in: button, kind: .button)
        XCTAssertTrue(button.contains(b), "still on the button")
        XCTAssertLessThan(b.y, button.midY - 6, "below the label")
        XCTAssertGreaterThan(b.x, button.midX, "right of the middle")
        // the ripple leaves the label line free; the click point lies outside it
        let label = T.labelRect(in: button, kind: .button, labelWidth: 70) ?? .null
        XCTAssertTrue(button.contains(label))
        XCTAssertEqual(label.midX, button.midX, accuracy: 0.01, "a button's label is centred")
        XCTAssertGreaterThanOrEqual(label.width, 70)
        XCTAssertFalse(label.contains(b))
        XCTAssertEqual(T.labelRect(in: button, kind: .button, labelWidth: 500)?.width, button.width)
        let small = CGRect(x: 300, y: 200, width: 90, height: 22)
        XCTAssertTrue(small.contains(T.clickPoint(in: small, kind: .button)), "a regular-size button too")
        let field = CGRect(x: 230, y: 120, width: 160, height: 22)
        let f = T.clickPoint(in: field, kind: .field)
        XCTAssertTrue(field.contains(f))
        XCTAssertGreaterThan(f.x, field.midX, "clear of the text typed from the left")
        XCTAssertNil(T.labelRect(in: field, kind: .field, labelWidth: 50))
        let box = CGRect(x: 230, y: 300, width: 500, height: 36)
        let t = T.clickPoint(in: box, kind: .toggle)
        XCTAssertTrue(box.contains(t))
        XCTAssertLessThan(t.x, box.minX + 14, "on the checkbox")
        let text = T.labelRect(in: box, kind: .toggle, labelWidth: 0) ?? .null
        XCTAssertFalse(text.contains(t), "the label beside the box stays free")
        XCTAssertEqual(text.maxX, box.maxX)
        XCTAssertEqual(T.clickPoint(in: box, kind: .other), CGPoint(x: box.midX, y: box.midY))
    }

    func testPointerParksOnAFreeSpotOfTheFooter() {
        let size = CGSize(width: 940, height: 680)
        let p = T.parkingPoint(canvas: size, sidebar: 200)
        XCTAssertEqual(p, CGPoint(x: 562, y: T.footerHeight / 2), "the middle of the free footer")
        XCTAssertTrue(T.inFooter(p ?? .zero))
        // S00: a wide button over the middle ("What exactly does … do?") beside the main button
        let help = CGRect(x: 450, y: 14, width: 330, height: 30), go = CGRect(x: 790, y: 14, width: 120, height: 30)
        let q = T.parkingPoint(canvas: size, sidebar: 200, occupied: [help, go]) ?? .zero
        XCTAssertLessThan(q.x + 20, help.minX, "the arrow stays clear of the button")
        XCTAssertGreaterThan(help.minX - q.x, 80, "in the middle of the free stretch, not beside the button")
        XCTAssertGreaterThan(q.x, 200, "in the footer of the screen, not under the sidebar")
        // Back, Cancel and the "cancel is locked" text on the left, the next screen's main button on the right: the
        // pointer rests between them, on neither
        let back = CGRect(x: 232, y: 14, width: 60, height: 30), cancel = CGRect(x: 302, y: 14, width: 78, height: 30)
        let locked = CGRect(x: 390, y: 20, width: 200, height: 16), next = CGRect(x: 800, y: 14, width: 108, height: 30)
        let r = T.parkingPoint(canvas: size, sidebar: 200, occupied: [back, cancel, locked, next]) ?? .zero
        XCTAssertGreaterThan(r.x, locked.maxX + 8)
        XCTAssertLessThan(r.x + 20, next.minX)
        XCTAssertEqual(r.x, ((locked.maxX + 8) + (next.minX - 24)) / 2, accuracy: 1)
        let full = CGRect(x: 210, y: 10, width: 720, height: 36), content = CGRect(x: 400, y: 300, width: 400, height: 30)
        XCTAssertNil(T.parkingPoint(canvas: size, sidebar: 200, occupied: [full]), "no room: the pointer stays put")
        XCTAssertEqual(T.parkingPoint(canvas: size, sidebar: 200, occupied: [content]), p,
                       "controls of the content do not count")
        XCTAssertTrue(T.inFooter(T.clickPoint(in: go, kind: .button)))
        XCTAssertFalse(T.inFooter(CGPoint(x: 400, y: 300)), "a checkbox in the content")
        XCTAssertGreaterThanOrEqual(T.endHold, 4, "the success screen reads as the end")
        XCTAssertLessThanOrEqual(T.endHold, 5)
    }

    func testThePointerMovesAsideWhenTheAppCoversItsSpot() {
        // S13 → S14 by itself: S14's wider cancel button lies where the pointer rests; S15: "cancel is locked"
        let cancel = CGRect(x: 232, y: 14, width: 80, height: 30), wide = CGRect(x: 526, y: 14, width: 264, height: 30)
        let locked = CGRect(x: 232, y: 20, width: 190, height: 16)
        XCTAssertTrue(T.isClear(CGPoint(x: 450, y: 29), occupied: [cancel, wide]))
        XCTAssertFalse(T.isClear(CGPoint(x: 600, y: 29), occupied: [cancel, wide]), "on the wider button")
        XCTAssertFalse(T.isClear(CGPoint(x: 515, y: 29), occupied: [wide]), "the arrow reaches about 20 pt right")
        XCTAssertFalse(T.isClear(CGPoint(x: 360, y: 29), occupied: [locked]), "on the text")
        XCTAssertTrue(T.isClear(CGPoint(x: 600, y: 300), occupied: [wide]), "above the footer: not checked")
        let size = CGSize(width: 1000, height: 950)
        let to = T.parkingPoint(canvas: size, sidebar: 200, occupied: [cancel, wide]) ?? .zero
        XCTAssertTrue(T.isClear(to, occupied: [cancel, wide]), "the spot it moves to is clear")
        // its steps take one frame each and leave the timing of the states around them alone
        var g = sample("S14", 1.0)
        g.glide = true
        let s = [sample("S13", 0, wait: true), sample("S13", 0.5, wait: true), g, g, g, sample("S14", 1.0),
                 sample("S14", 2.0)]
        let f = T.allot(s, end: 2.1, fps: 10)
        XCTAssertEqual(Array(f[2...4]), [1, 1, 1])
        let step = T.frames(min(T.maxWait, max(T.minWait, T.dwell(chars: 400) * 0.8)), fps: 10)
        XCTAssertEqual(f[0] + f[1], step, "the wait step keeps its time")
        XCTAssertEqual(f[5], T.frames(T.maxState, fps: 10), "S14's first state keeps its real duration within limits")
        XCTAssertEqual(f[6], 1)
    }

    func testANoticeInAWaitStepIsReadAfterTheStatesBeforeIt() {
        // S15: the transfer runs (two states), then "the iPhone restarts by itself" appears and stays one more state
        var restart = sample("S15", 0.3, wait: true)
        restart.notice = 105
        restart.noticeStays = 1
        let s = [sample("S15", 0, wait: true), sample("S15", 0.25, wait: true), restart,
                 sample("S15", 0.35, wait: true), sample("S16", 0.5)]
        let f = T.allot(s, end: 0.6, fps: 10)
        let step = T.frames(min(T.maxWait, max(T.minWait, T.dwell(chars: 400) * 0.8)), fps: 10)
        XCTAssertEqual(f[0] + f[1], step, "the transfer keeps a whole wait step")
        XCTAssertEqual(f[2] + f[3], T.frames(T.noticeTime(chars: 105), fps: 10), "then the notice is read")
        XCTAssertGreaterThanOrEqual(T.noticeTime(chars: 105), 3)
        XCTAssertGreaterThanOrEqual(f[0...3].reduce(0, +), T.frames(4.5, fps: 10))
    }

    func testAShorterWaitNoticeIsReadAsLongAsALongOne() {
        // the English restart notice (92 characters) would get 2.8 s by its length; in a wait step it keeps 3 s
        var restart = sample("S15", 0.3, wait: true)
        restart.notice = 92
        restart.noticeStays = 1
        let s = [sample("S15", 0, wait: true), restart, sample("S15", 0.35, wait: true), sample("S16", 0.5)]
        let f = T.allot(s, end: 0.6, fps: 20)
        XCTAssertLessThan(T.noticeTime(chars: 92), T.minWaitNotice)
        XCTAssertGreaterThanOrEqual(f[1] + f[2], T.frames(T.minWaitNotice, fps: 20))
    }

    func testANoticeCountsTheStatesThatStillShowIt() {
        // S10a: the banner comes with the passcode prompt and stays through two more states (cancel locked, done)
        var banner = sample("S10a", 0)
        banner.notice = 45
        banner.noticeStays = 2
        let g = T.allot([banner, sample("S10a", 1.6), sample("S10a", 1.9), sample("S11", 3.5)], end: 3.6, fps: 10)
        let cap = T.frames(T.maxState, fps: 10)
        XCTAssertEqual(g, [cap, 6, cap, 1], "on screen long enough with the states after it: nothing added")
        banner.noticeStays = 1
        let h = T.allot([banner, sample("S10a", 0.2), sample("S11", 0.4)], end: 0.5, fps: 10)
        XCTAssertEqual(h[0] + h[1], T.frames(T.noticeTime(chars: 45), fps: 10), "the first state gets what is missing")
    }
}
