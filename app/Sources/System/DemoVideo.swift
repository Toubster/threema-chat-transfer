// SPDX-License-Identifier: AGPL-3.0-or-later
import AppKit
import Combine
import Foundation
import ImageIO
import SwiftUI
import UniformTypeIdentifiers

/// Pacing of the walkthrough video (pure functions, unit-tested). Video time is a frame counter at a fixed rate; it
/// does not follow the wall clock, so the result does not depend on how fast the Mac renders.
enum DemoVideoTiming {
    static let defaultFPS = 20
    static let minDwell = 2.0, maxDwell = 3.0
    /// Reading time: a fixed part plus the visible text (a layperson gets the gist of a screen, not every word).
    static let dwellBase = 0.8, dwellCharsPerSecond = 450.0
    static let minGlide = 0.25, maxGlide = 0.55
    /// Typing speed of a short entry; a long one (the Android password, shown as dots) takes at most `maxTyping`.
    static let typingCharsPerSecond = 12.0, maxTyping = 1.2
    /// The start screen holds this long before the pointer moves: the welcome and the opening caption (its first frame
    /// is also the still picture GitHub shows instead of the animation). The caption fades out at its end.
    static let startHold = 3.2
    static let captionSeconds = startHold, captionFade = 0.5
    /// The success screen holds this long before the GIF starts over.
    static let endHold = 4.3
    /// Screens the app passes by itself while the chats are prepared, the iPhone is backed up, the transfer is
    /// prepared, sent and checked: in a real run these take many minutes, so the video shows them shortened, with a
    /// "fast-forward" badge.
    static let minWait = 1.5, maxWait = 2.0
    /// A state the app shows on its own (prompt, check row, result box) stays at least this long, at most `maxState`.
    static let minState = 0.6, maxState = 0.8
    /// The final state of a wait step (all rows green) keeps at least this share of the step.
    static let waitFinalShare = 0.4
    /// A state the app shows for less than this (placeholders being filled in while a check runs) is not shown on its
    /// own: the next state replaces it, as on screen (with some room for a Mac that is busy with other work).
    static let passingState = 0.25
    /// A notice the app shows by itself (iPhone prompt, banner, hint box) that is gone again before the next reading
    /// pause stays long enough to be read: `noticeBase` plus its text at `noticeCharsPerSecond`, within limits (a
    /// four-word prompt 1.1 s, the passcode banner 1.7 s, the restart notice 3 s).
    static let minNotice = 1.0, maxNotice = 3.0, noticeBase = 0.5, noticeCharsPerSecond = 40.0
    /// A notice that comes with a wait step (the restart notice while sending) is the key moment of that step: it
    /// stays at least this long whatever its length in the language shown.
    static let minWaitNotice = 3.0
    /// A click: the press (ripple growing, a button darkened) before the action, the release after it. A button
    /// usually changes the screen, so its press shows longer than a checkbox's.
    static let pressSeconds = 0.25, togglePressSeconds = 0.15, releaseSeconds = 0.15
    /// The pointer leaves a footer button right after clicking it (the next screen's button sits in the same place)
    /// and goes back to the footer for a reading pause, at least this fast.
    static let parkGlide = 0.3
    /// Height of the footer bar in points (from the bottom edge of the window).
    static let footerHeight: CGFloat = 58

    /// Whole frames for a duration (at least one).
    static func frames(_ seconds: Double, fps: Int) -> Int { max(1, Int((seconds * Double(fps)).rounded())) }

    /// Reading time of a screen with `chars` visible characters.
    static func dwell(chars: Int) -> Double {
        min(maxDwell, max(minDwell, dwellBase + Double(max(chars, 0)) / dwellCharsPerSecond))
    }

    /// Reading time of a notice with `chars` characters.
    static func noticeTime(chars: Int) -> Double {
        min(maxNotice, max(minNotice, noticeBase + Double(max(chars, 0)) / noticeCharsPerSecond))
    }

    /// Pointer travel time for a distance in points: short hops (next checkbox) quick, across the window slower.
    static func glide(distance: Double) -> Double { min(maxGlide, max(minGlide, 0.25 + max(distance, 0) / 1000)) }

    /// Cubic ease-in-out on 0…1.
    static func ease(_ t: Double) -> Double {
        let x = min(1, max(0, t))
        return x < 0.5 ? 4 * x * x * x : 1 - pow(-2 * x + 2, 3) / 2
    }

    /// Pointer positions of a glide, one per frame: the last one is `to`, the start itself is not repeated.
    static func glidePath(from: CGPoint, to: CGPoint, frames n: Int) -> [CGPoint] {
        guard n > 0 else { return [] }
        return (1...n).map { i in
            let k = ease(Double(i) / Double(n))
            return CGPoint(x: from.x + (to.x - from.x) * k, y: from.y + (to.y - from.y) * k)
        }
    }

    /// Characters visible in each frame while `count` characters are typed (monotonic, ends with all of them).
    static func typing(count: Int, fps: Int, cps: Double = typingCharsPerSecond) -> [Int] {
        guard count > 0 else { return [] }
        let rate = max(cps, Double(count) / maxTyping)
        let n = max(1, Int((Double(count) / rate * Double(fps)).rounded(.up)))
        return (1...n).map { i in min(count, max(1, Int((Double(i) * rate / Double(fps)).rounded(.down)))) }
            .enumerated().map { $0.offset == n - 1 ? count : $0.element }
    }

    /// Opacity of the opening caption at a frame: shown for `captionSeconds`, fading out during the last part.
    static func captionAlpha(frame: Int, fps: Int) -> Double {
        let t = Double(frame) / Double(fps)
        if t >= captionSeconds { return 0 }
        return min(1, (captionSeconds - t) / captionFade)
    }

    /// Click ripple at progress 0…1: radius in points and opacity.
    static func ripple(_ p: Double) -> (radius: Double, alpha: Double) {
        let x = min(1, max(0, p))
        return (6 + 12 * x, 0.9 * (1 - x * x))
    }

    /// Ripple progress per frame of a click: `press` before the action runs (growing from 0), `release` after it
    /// (ending at 1, gone).
    static func click(fps: Int, button: Bool = true) -> (press: [Double], release: [Double]) {
        let p = frames(button ? pressSeconds : togglePressSeconds, fps: fps), r = frames(releaseSeconds, fps: fps)
        let all = (0..<(p + r)).map { Double($0) / Double(p + r - 1) }
        return (Array(all.prefix(p)), Array(all.suffix(r)))
    }

    /// What the pointer aims at, by the kind of control.
    enum Target: Equatable { case button, toggle, field, other }

    /// Click point in a control's frame (window points, bottom-left origin). A button is clicked right of its middle,
    /// just below its label line (the arrow hangs below the tip, so it hides no letters); a checkbox or radio button on
    /// its box (beside the first label line); a text field in its right part, clear of the typed text.
    static func clickPoint(in r: CGRect, kind: Target) -> CGPoint {
        switch kind {
        case .button: return CGPoint(x: r.midX + r.width * 0.18, y: max(r.minY + 3, r.midY - labelHalfHeight - 1))
        case .toggle: return CGPoint(x: r.minX + 8, y: r.maxY - 9)
        case .field: return CGPoint(x: r.maxX - min(28, r.width / 4), y: r.midY)
        case .other: return CGPoint(x: r.midX, y: r.midY)
        }
    }

    /// Half the height of a control's label line (13 pt system font) and the width of a checkbox with its gap.
    static let labelHalfHeight: CGFloat = 8, toggleBox: CGFloat = 18

    /// The part of a control the click ripple leaves free so no letter is hidden: a button's centred label line
    /// (`labelWidth` wide), the label beside a checkbox or radio button; nil for a text field (its click point is clear
    /// of the text) and anything else.
    static func labelRect(in r: CGRect, kind: Target, labelWidth: CGFloat) -> CGRect? {
        switch kind {
        case .button:
            let w = min(r.width, labelWidth + 6)
            return CGRect(x: r.midX - w / 2, y: r.midY - labelHalfHeight, width: w, height: 2 * labelHalfHeight)
        case .toggle:
            return CGRect(x: r.minX + toggleBox, y: r.minY, width: max(0, r.width - toggleBox), height: r.height)
        case .field, .other: return nil
        }
    }

    /// Where the pointer rests while a screen is read and after it clicked a footer button: in the middle of the
    /// widest free stretch of the footer bar, clear of everything shown there (`occupied`, window points; the arrow
    /// covers a little left and about 20 pt right of its tip), so it hides no text and points at no button; nil when
    /// the footer has no room.
    static func parkingPoint(canvas: CGSize, sidebar: CGFloat, occupied: [CGRect] = []) -> CGPoint? {
        let lo = sidebar + 24, hi = canvas.width - 40
        let blocked = covered(by: occupied).sorted { $0.0 < $1.0 }
        var free: [(CGFloat, CGFloat)] = []
        var start = lo
        for (a, b) in blocked {
            if a > start { free.append((start, min(a, hi))) }
            start = max(start, b)
        }
        if start < hi { free.append((start, hi)) }
        return free.filter { $0.1 - $0.0 >= minParkingRoom }.max { $0.1 - $0.0 < $1.1 - $1.0 }
            .map { CGPoint(x: (($0.0 + $0.1) / 2).rounded(), y: footerHeight / 2) }
    }

    /// The narrowest free stretch of the footer the pointer rests in.
    static let minParkingRoom: CGFloat = 16

    /// The pointer at `p` hides nothing the footer shows (`occupied`, as for `parkingPoint`); above the footer it is
    /// not checked.
    static func isClear(_ p: CGPoint, occupied: [CGRect]) -> Bool {
        !inFooter(p) || !covered(by: occupied).contains { p.x > $0.0 && p.x < $0.1 }
    }

    /// The pointer positions (x) at which the arrow would cover one of the footer's elements.
    private static func covered(by occupied: [CGRect]) -> [(CGFloat, CGFloat)] {
        occupied.filter { $0.minY < footerHeight }.map { ($0.minX - 24, $0.maxX + 8) }
    }

    /// The pointer is on the footer bar (it just clicked one of its buttons).
    static func inFooter(_ p: CGPoint) -> Bool { p.y < footerHeight }

    /// One picture the app showed on its own between two user actions (sampled when its state changed).
    struct LiveSample: Equatable {
        var screen: String
        /// Wall-clock seconds of the sample (only the gaps count).
        var time: Double
        /// A long wait in real life (backup, preparation, sending, restart, control backup).
        var wait: Bool
        /// Visible characters (reading time of a wait screen).
        var chars: Int
        /// Characters of the notices that appear with this state and are gone before the next reading pause.
        var notice: Int = 0
        /// The notice is still shown in this many of the following samples (their frames count for its reading time).
        var noticeStays: Int = 0
        /// One step of the pointer moving aside on a state the app opened by itself: one frame, not timed.
        var glide = false
    }

    /// Frames per sample. Consecutive samples of one screen form a segment; in a wait step a state that brings a notice
    /// starts a new one (the transfer and the restart notice after it are read one after the other). A wait segment
    /// gets a reading time between `minWait` and `maxWait`: its final state `waitFinalShare` of it, the earlier states
    /// the rest by their real gaps (at least one frame each). Any other state keeps its real duration within
    /// `minState`…`maxState`; one that passed in less than `passingState` gets no frame. A notice that is gone before
    /// the next reading pause stays at least `noticeTime` (the state that brings it gets what the following states
    /// showing it lack). The last sample is followed by the next reading pause and gets one frame; a step of the
    /// pointer moving aside gets one frame and belongs to no segment.
    static func allot(_ samples: [LiveSample], end: Double, fps: Int) -> [Int] {
        guard !samples.isEmpty else { return [] }
        let gaps = samples.indices.map { i in
            max(0, (i + 1 < samples.count ? samples[i + 1].time : end) - samples[i].time)
        }
        var out = [Int](repeating: 1, count: samples.count)
        var i = 0
        while i < samples.count {
            if samples[i].glide {
                i += 1
                continue
            }
            var j = i
            while j + 1 < samples.count, !samples[j + 1].glide, samples[j + 1].screen == samples[i].screen,
                  samples[j + 1].wait == samples[i].wait, !(samples[i].wait && samples[j + 1].notice > 0) {
                j += 1
            }
            if samples[i].wait {
                let total = frames(min(maxWait, max(minWait, dwell(chars: samples[j].chars) * 0.8)), fps: fps)
                let earlier = j - i
                let final = earlier == 0 ? total
                    : max(1, min(total - earlier, Int((Double(total) * waitFinalShare).rounded())))
                out[j] = final
                if earlier > 0 {
                    let rest = total - final
                    let span = gaps[i..<j].reduce(0, +)
                    var given = 0
                    for k in i..<j {
                        let share = span > 0 ? gaps[k] / span : 1 / Double(earlier)
                        out[k] = max(1, Int((share * Double(rest)).rounded(.down)))
                        given += out[k]
                    }
                    out[j] += max(0, rest - given)           // rounding remainder on the final state of the step
                }
            } else {
                for k in i...j {
                    out[k] = gaps[k] < passingState && k + 1 < samples.count ? 0
                        : min(frames(maxState, fps: fps), max(frames(minState, fps: fps), frames(gaps[k], fps: fps)))
                }
            }
            i = j + 1
        }
        if !samples[samples.count - 1].wait { out[samples.count - 1] = 1 }
        for k in samples.indices where samples[k].notice > 0 {
            let shown = out[k...min(samples.count - 1, k + max(0, samples[k].noticeStays))].reduce(0, +)
            let need = samples[k].wait ? max(minWaitNotice, noticeTime(chars: samples[k].notice))
                : noticeTime(chars: samples[k].notice)
            out[k] += max(0, frames(need, fps: fps) - shown)
        }
        return out
    }
}

/// Recording mode of the demo autopilot (`TM_VIDEO_DIR`): renders the window (title bar included) in-process into a
/// PNG frame sequence at a fixed rate, with a synthetic mouse pointer that glides to each control before the autopilot
/// acts, a click ripple, visible typing, a reading pause per screen (the pointer rests on a free spot of the
/// footer meanwhile, and leaves a footer button right after clicking it), notices the app shows by itself kept in view
/// long enough to be read, and a shortened ("fast-forward") view of the long waits. Nothing is scrolled: the window is
/// tall enough for every screen of the run (`demo_video.py --size`), and a screen whose content does not fit fails the
/// run (`overflow`). `devtools/demo_video.py` turns the frames into the README video. Demo runs only: it exists only
/// inside a `DemoAutopilot`, which refuses the live engine.
///
/// Output: `<dir>/frames/f000000.png …` (one file per distinct picture, hard links for repeated frames) and
/// `<dir>/video.json` (rate, size, key frames per screen, controls that could not be located, inactive-window frames,
/// content overflow per screen, and per sample of the app's own states: screen, real duration, notice characters,
/// frames).
@MainActor
final class DemoVideo {
    struct Config: Equatable {
        var dir: URL
        var fps: Int
        /// The recording ends with the closing hold on this screen (the run itself goes on to the scenario's end).
        var end: String?

        static func from(_ env: [String: String]) -> Config? {
            guard let d = env["TM_VIDEO_DIR"], !d.isEmpty else { return nil }
            let fps = env["TM_VIDEO_FPS"].flatMap(Int.init) ?? DemoVideoTiming.defaultFPS
            return Config(dir: URL(fileURLWithPath: d, isDirectory: true), fps: min(30, max(5, fps)),
                          end: env["TM_VIDEO_END"].flatMap { $0.isEmpty ? nil : $0 })
        }
    }

    /// Frames are rendered at twice the window size, also on a 1x display, so text stays sharp after downscaling.
    static let scale: CGFloat = 2
    static let waitScreens: Set<String> = ["S12", "S13", "S15", "S18"]
    /// Long commands on screens the user also reads: preparing the chats (S06), the final checks and sending (S14).
    static let waitCommands: Set<EngineCommand> = [.androidNormalize, .restore]
    /// Ids of what the app shows by itself to tell the user something (iPhone prompts, banners, hint boxes): such a
    /// notice gets a reading time of its own when it is gone before the next reading pause (`DemoVideoTiming.allot`).
    static let noticePrefixes = ["banner.", "inline.", "box.", "note.", "device.state"]

    let cfg: Config
    let store: WizardStore
    private let writer: FrameWriter
    private var frame = 0
    private var canvas: CGSize?
    /// Height of the title bar in points (the fast-forward badge sits in it, clear of the content).
    private var titleBar: CGFloat = 28
    private var pointer: CGPoint?
    private var started = false
    private var stopped = false
    private var scripted = 0
    private var sink: AnyCancellable?
    private var inputGuard: Any?
    private var sampleScheduled = false
    private var live: [(sample: DemoVideoTiming.LiveSample, file: URL, inactive: Bool, notices: [String: Int])] = []
    /// Notices of the last picture (screen id, "id + text" → characters): what is new in the next one.
    private var shownNotices: (screen: String, keys: [String: Int]) = ("", [:])
    /// Notices of the last scripted picture, compared with the first sample of the next flush.
    private var noticesBeforeLive: [String: Int] = [:]
    private var liveCount = 0
    private var lastBase: CGImage?
    private var keyframes: [[String: Any]] = []
    private var screens: [[String: Any]] = []
    private var missing: [String] = []
    private var inactiveFrames = 0
    private var fastForwardFrames = 0
    private var samplesLog: [[String: Any]] = []
    /// Per screen, how far its content reaches beyond the visible part of its scroll view (points, the most of any
    /// picture; negative: room to spare). A scrolled screen cannot be recorded as it looks: the content would be drawn
    /// over the title bar (`renderWindow`), so demo_video.py fails the run when a value is positive.
    private var overflow: [String: CGFloat] = [:]

    init(cfg: Config, store: WizardStore) {
        self.cfg = cfg
        self.store = store
        self.writer = FrameWriter()
    }

    private var window: NSWindow? { NSApp.windows.first(where: { $0.isVisible && $0.canBecomeMain }) }
    private var framesDir: URL { cfg.dir.appendingPathComponent("frames", isDirectory: true) }
    private var liveDir: URL { cfg.dir.appendingPathComponent("live", isDirectory: true) }
    private func frameURL(_ i: Int) -> URL { framesDir.appendingPathComponent(String(format: "f%06d.png", i)) }

    // MARK: - public steps (called by the autopilot)

    /// Brings the window to the front as the key window (title bar drawn active) and starts sampling the app's own
    /// state changes between the scripted steps. Keys and clicks of someone working on the Mac are dropped while the
    /// autopilot drives: a Return meant for another app would press the default button and derail the recording.
    func begin() {
        let fm = FileManager.default
        for d in [framesDir, liveDir] {
            try? fm.removeItem(at: d)
            try? fm.createDirectory(at: d, withIntermediateDirectories: true)
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
        inputGuard = NSEvent.addLocalMonitorForEvents(matching: [.keyDown, .keyUp, .leftMouseDown, .leftMouseUp,
                                                                 .rightMouseDown, .otherMouseDown, .scrollWheel]) { _ in
            nil
        }
        sink = store.objectWillChange.sink { [weak self] _ in self?.scheduleSample() }
    }

    /// Reading pause on the current screen, proportional to its visible text (at most `upTo` seconds; the start screen
    /// `startHold`). On the end screen (`TM_VIDEO_END`) it is the closing hold, and the recording stops.
    func dwell(upTo cap: Double = DemoVideoTiming.maxDwell) async {
        if stopped { return }
        if store.screen.id == cfg.end { await endHold(); return }
        await scriptedStep {
            guard let base = await settledBase() else { return }
            let chars = await screenChars()
            let read = keyframes.isEmpty ? DemoVideoTiming.startHold : min(cap, DemoVideoTiming.dwell(chars: chars))
            let total = DemoVideoTiming.frames(read, fps: cfg.fps)
            if pointer == nil { pointer = await parkingPoint() ?? restingPoint() }   // resting from the first frame on
            let parked = await park(base)
            keyframe("dwell", chars: chars)
            emit(base, frames: max(DemoVideoTiming.frames(0.6, fps: cfg.fps), total - parked))
        }
    }

    /// The pointer glides to the control with accessibility identifier `id` (the radio button `option` of a radio
    /// group), the click ripple starts, `apply` runs (the autopilot's action), and the ripple ends on the new state.
    /// After a footer button the pointer moves off it meanwhile, to the free spot of the footer of what the click
    /// opened (without waiting for its elements: the step ends at once, so the app's own states after the click are
    /// sampled; the spot before the click when they are not there yet).
    func tap(_ id: String, option: Int?, apply: () -> Void) async {
        if stopped { apply(); return }
        await scriptedStep {
            guard let base = await settledBase() else { apply(); return }
            if let (rect, kind, label) = await locate(id, option: option) {
                let target = DemoVideoTiming.clickPoint(in: rect, kind: kind)
                let from = pointer ?? restingPoint()
                let d = hypot(Double(target.x - from.x), Double(target.y - from.y))
                let n = DemoVideoTiming.frames(DemoVideoTiming.glide(distance: d), fps: cfg.fps)
                for p in DemoVideoTiming.glidePath(from: from, to: target, frames: n) {
                    pointer = p
                    emit(base, frames: 1)
                }
                // the press shows before the action (ripple, the button darkened as when pressed), the release after;
                // the ripple leaves the label free and is light on a dark (filled) button, dark on a light one
                let click = DemoVideoTiming.click(fps: cfg.fps, button: kind == .button)
                let font: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 13)]
                let width = NSAttributedString(string: label, attributes: font).size().width
                var ripple = Ripple(progress: 0, at: target,
                                    clear: DemoVideoTiming.labelRect(in: rect, kind: kind, labelWidth: width),
                                    onDark: brightness(base, at: target) < 0.55)
                for (i, r) in click.press.enumerated() {
                    if i == click.press.count - 1 { keyframe("click", id) }
                    ripple.progress = r
                    emit(base, frames: 1, ripple: ripple, pressed: kind == .button ? rect : nil)
                }
                let footer = kind == .button && DemoVideoTiming.inFooter(target)
                let before = footer ? await parkingPoint() : nil
                apply()
                let after = await settledBase(wait: 0.12) ?? base
                let rest = footer ? await parkingPoint(attempts: 1) ?? before : nil
                ripple.onDark = brightness(after, at: target) < 0.55
                let flick = DemoVideoTiming.frames(DemoVideoTiming.parkGlide, fps: cfg.fps)
                let away = rest.map { DemoVideoTiming.glidePath(from: target, to: $0, frames: flick) } ?? []
                for i in 0..<max(click.release.count, away.count) {
                    if i < away.count { pointer = away[i] }
                    if i < click.release.count { ripple.progress = click.release[i] }
                    emit(after, frames: 1, ripple: i < click.release.count ? ripple : nil)
                }
            } else {
                missing.append("\(store.screen.id):\(id)" + (option.map { "#\($0)" } ?? ""))
                apply()
                if let after = await settledBase(wait: 0.12) {
                    emit(after, frames: DemoVideoTiming.frames(0.3, fps: cfg.fps))
                }
            }
        }
    }

    /// Typing (`DemoVideoTiming.typing`): `set` receives the growing prefix of `text` frame by frame.
    func type(_ text: String, set: (String) -> Void) async {
        if stopped { set(text); return }
        await scriptedStep {
            for n in DemoVideoTiming.typing(count: text.count, fps: cfg.fps) {
                set(String(text.prefix(n)))
                guard let base = await settledBase(wait: 0.03) else { continue }
                emit(base, frames: 1)
            }
            if let base = await settledBase(wait: 0.05) {
                emit(base, frames: DemoVideoTiming.frames(0.2, fps: cfg.fps))
            }
        }
    }

    /// The end screen holds for `endHold`; nothing is recorded after it.
    func endHold() async {
        if stopped { return }
        await scriptedStep {
            guard let base = await settledBase() else { return }
            let parked = await park(base)
            keyframe("end")
            emit(base, frames: max(1, DemoVideoTiming.frames(DemoVideoTiming.endHold, fps: cfg.fps) - parked))
        }
        stopped = true
        sink = nil
    }

    /// Flushes the last samples, waits for the writer and writes `video.json`; returns the summary for the report.
    func finish() -> [String: Any] {
        sink = nil
        if !live.isEmpty { flushLive() }
        writer.drain()
        try? FileManager.default.removeItem(at: liveDir)
        let px = canvas.map { (Int($0.width * Self.scale), Int($0.height * Self.scale)) } ?? (0, 0)
        let summary: [String: Any] = [
            "fps": cfg.fps, "scale": Double(Self.scale), "width": px.0, "height": px.1, "frames": frame,
            "seconds": Double(frame) / Double(cfg.fps), "keyframes": keyframes, "screens": screens,
            "missing_controls": missing, "inactive_frames": inactiveFrames, "fast_forward_frames": fastForwardFrames,
            "live_samples": liveCount, "write_failures": writer.failures, "lang": store.language.rawValue,
            "end_screen": (screens.last?["id"] as? String) ?? NSNull(), "samples": samplesLog,
            "overflow": overflow.mapValues { Double($0.rounded()) }]
        if let d = try? JSONSerialization.data(withJSONObject: summary, options: [.prettyPrinted, .sortedKeys]) {
            try? d.write(to: cfg.dir.appendingPathComponent("video.json"))
        }
        return summary.filter { !["keyframes", "screens", "samples"].contains($0.key) }
    }

    // MARK: - scripted frames

    /// A scripted step: the samples the app showed since the last step go into the video first; no sampling inside.
    private func scriptedStep(_ body: () async -> Void) async {
        if !started {
            started = true
            live = []                                    // the start screen itself is the first reading pause
        }
        if !live.isEmpty { flushLive() }
        scripted += 1
        await body()
        scripted -= 1
        shownNotices = (store.screen.id, notices())
        scheduleSample()                                 // a change the app made during the step
    }

    /// Lets SwiftUI apply the latest state, then renders the window.
    private func settledBase(wait: Double = 0.2) async -> CGImage? {
        try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
        return renderWindow()
    }

    /// Writes one picture (base + overlays) for `frames` consecutive frames.
    private func emit(_ base: CGImage, frames n: Int, ripple: Ripple? = nil, pressed: CGRect? = nil) {
        noteScreen()
        lastBase = base
        if pointer == nil { pointer = restingPoint() }   // visible from the first frame, no pop-in at the first click
        let wait = waiting
        guard let img = compose(base, at: frame, ripple: ripple, pressed: pressed, wait: wait) else { return }
        let urls = (frame..<(frame + n)).map(frameURL)
        writer.write(img, to: urls[0], links: Array(urls.dropFirst()))
        if inactive { inactiveFrames += n }
        if wait { fastForwardFrames += n }
        frame += n
    }

    /// The app is in one of the long waits (badge "fast-forward"), also in the frames right after the click.
    private var waiting: Bool {
        Self.waitScreens.contains(store.screen.id) || store.runningCommand.map(Self.waitCommands.contains) == true
    }

    /// Before a reading pause the pointer glides to the free spot of the footer (`DemoVideoTiming.parkingPoint`)
    /// unless it rests there already, so it hides no text and does not point at the next click; returns the frames
    /// used.
    private func park(_ base: CGImage) async -> Int {
        guard let p = pointer, let to = await parkingPoint(),
              hypot(Double(p.x - to.x), Double(p.y - to.y)) > 24 else { return 0 }
        let d = hypot(Double(p.x - to.x), Double(p.y - to.y))
        let n = DemoVideoTiming.frames(max(DemoVideoTiming.parkGlide, DemoVideoTiming.glide(distance: d)), fps: cfg.fps)
        let path = DemoVideoTiming.glidePath(from: p, to: to, frames: n)
        for q in path {
            pointer = q
            emit(base, frames: 1)
        }
        return path.count
    }

    /// Another app is in front: the title bar is drawn inactive (the controls are not, `ActiveControls`).
    private var inactive: Bool { !(window?.isKeyWindow ?? false) || !NSApp.isActive }

    private func keyframe(_ kind: String, _ id: String? = nil, chars: Int? = nil) {
        var k: [String: Any] = ["frame": frame, "screen": store.screen.id, "kind": kind]
        if let id { k["control"] = id }
        if let chars { k["chars"] = chars }
        keyframes.append(k)
    }

    private func noteScreen() {
        let id = store.screen.id
        if (screens.last?["id"] as? String) != id { screens.append(["id": id, "frame": frame]) }
    }

    // MARK: - live samples (what the app shows by itself)

    private func scheduleSample() {
        guard started, !stopped, scripted == 0, !sampleScheduled else { return }
        sampleScheduled = true
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.sampleScheduled = false
            self.sample()
        }
    }

    private func sample() {
        guard started, !stopped, scripted == 0, let window else { return }
        window.contentView?.layoutSubtreeIfNeeded()
        let screen = store.screen.id
        let notes = notices()
        let prior = shownNotices
        shownNotices = (screen, notes)
        guard let base = renderWindow() else { return }
        if let last = lastBase, Self.sameBytes(last, base) { return }   // nothing visible changed (clock tick, answers)
        lastBase = base
        let wait = waiting
        let first = live.last.map { $0.sample.screen != screen || $0.sample.wait != wait } ?? true
        let chars = !wait ? 0 : first ? (visibleChars() ?? 300) : (live.last?.sample.chars ?? 0)
        let s = DemoVideoTiming.LiveSample(screen: screen, time: CACurrentMediaTime(), wait: wait, chars: chars)
        if live.isEmpty { noticesBeforeLive = prior.screen == screen ? prior.keys : [:] }
        // the app now shows something where the pointer rests (the next screen's wider button, "cancel is locked"):
        // the pointer moves aside first, as a user would
        if let p = pointer, DemoVideoTiming.inFooter(p), let footer = footerElements(),
           !DemoVideoTiming.isClear(p, occupied: footer), let to = spot(clearOf: footer) {
            var step = s
            step.glide = true
            let n = DemoVideoTiming.frames(DemoVideoTiming.parkGlide, fps: cfg.fps)
            for q in DemoVideoTiming.glidePath(from: p, to: to, frames: n) {
                pointer = q
                addLive(base, step, wait: wait, notices: notes)
            }
        }
        addLive(base, s, wait: wait, notices: notes)
    }

    private func addLive(_ base: CGImage, _ s: DemoVideoTiming.LiveSample, wait: Bool, notices: [String: Int]) {
        guard let img = compose(base, at: frame, ripple: nil, pressed: nil, wait: wait) else { return }
        let url = liveDir.appendingPathComponent(String(format: "s%06d.png", liveCount))
        liveCount += 1
        writer.write(img, to: url, links: [])
        live.append((s, url, inactive, notices))
    }

    private static func sameBytes(_ a: CGImage, _ b: CGImage) -> Bool {
        guard a.width == b.width, a.height == b.height, let da = a.dataProvider?.data, let db = b.dataProvider?.data
        else { return false }
        return (da as Data) == (db as Data)
    }

    /// The samples get their frames (DemoVideoTiming.allot) and are linked into the frame sequence; the last state of
    /// every segment (one screen, waiting or not) is a key frame for the review.
    private func flushLive() {
        // notices that are gone by the last sample get their reading time here; the ones still shown are read in
        // the reading pause that follows
        let kept = live[live.count - 1].notices
        var before = noticesBeforeLive
        var samples = live.map(\.sample)
        for i in live.indices where !live[i].sample.glide {          // the pointer's steps aside bring no notice
            let notes = live[i].notices
            let fresh = notes.filter { before[$0.key] == nil && kept[$0.key] == nil }
            samples[i].notice = fresh.values.reduce(0, +)
            samples[i].noticeStays = live[(i + 1)...].prefix { n in fresh.keys.contains { n.notices[$0] != nil } }.count
            before = notes
        }
        let end = CACurrentMediaTime()
        let counts = DemoVideoTiming.allot(samples, end: end, fps: cfg.fps)
        func segment(_ a: DemoVideoTiming.LiveSample, _ b: DemoVideoTiming.LiveSample) -> Bool {
            a.screen == b.screen && a.wait == b.wait
        }
        for (i, item) in live.enumerated() {
            let n = counts[i]
            let s = samples[i]
            samplesLog.append(["screen": s.screen, "wait": s.wait, "notice": s.notice, "frames": n, "glide": s.glide,
                               "ms": Int(((i + 1 < samples.count ? samples[i + 1].time : end) - s.time) * 1000)])
            if n > 0 {
                if (screens.last?["id"] as? String) != s.screen { screens.append(["id": s.screen, "frame": frame]) }
                // the last state of a segment that is shown is a key frame for the review
                let later = samples[(i + 1)...].prefix { segment($0, s) }.indices
                if !later.contains(where: { counts[$0] > 0 }) {
                    keyframes.append(["frame": frame, "screen": s.screen, "kind": s.wait ? "wait" : "live"])
                }
            }
            writer.link(item.file, to: (frame..<(frame + n)).map(frameURL))
            if s.wait { fastForwardFrames += n }
            if item.inactive { inactiveFrames += n }
            frame += n
        }
        live = []
    }

    // MARK: - rendering

    /// The window (title bar included) at `scale`; laid out and drawn right before the copy, like `capture()`. The
    /// content fills the whole window, also under the transparent title bar: on screen, the window server covers what
    /// is scrolled there with the scroll view's blur (`NSScrollPocket`, backdrop layers), which `cacheDisplay` cannot
    /// draw, so a scrolled screen would show its content over the title bar. Hence the recording never scrolls.
    private func renderWindow() -> CGImage? {
        guard let window, let content = window.contentView else { return nil }
        let view = content.superview ?? content
        content.layoutSubtreeIfNeeded()
        view.layoutSubtreeIfNeeded()
        view.display()
        let size = view.bounds.size
        guard size.width > 0, size.height > 0,
              let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: Int(size.width * Self.scale),
                                         pixelsHigh: Int(size.height * Self.scale), bitsPerSample: 8,
                                         samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                         colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)
        else { return nil }
        rep.size = size
        view.cacheDisplay(in: view.bounds, to: rep)
        if canvas == nil {
            canvas = size
            let bar = window.frame.height - window.contentLayoutRect.maxY
            if bar >= 20, bar <= 60 { titleBar = bar }
        }
        measureOverflow(content)
        return rep.cgImage
    }

    /// Notes for the current screen how far its content reaches beyond the visible part of its scroll view (the
    /// scroll view with the tallest content; its insets, such as the title bar's, do not count as visible).
    private func measureOverflow(_ root: NSView) {
        let values = Self.scrollViews(root).compactMap { sv -> CGFloat? in
            guard let doc = sv.documentView else { return nil }
            let insets = sv.contentInsets
            return doc.frame.height - (sv.contentView.bounds.height - insets.top - insets.bottom)
        }
        guard let v = values.max() else { return }
        let id = store.screen.id
        overflow[id] = max(overflow[id] ?? -.greatestFiniteMagnitude, v)
    }

    private static func scrollViews(_ v: NSView) -> [NSScrollView] {
        ((v as? NSScrollView).map { [$0] } ?? []) + v.subviews.flatMap(scrollViews)
    }

    /// A click ripple: progress 0…1, centre, the label it leaves free, and whether it is drawn on a dark (filled)
    /// control.
    struct Ripple {
        var progress: Double
        var at: CGPoint
        var clear: CGRect?
        var onDark: Bool
    }

    /// Base picture on an opaque white canvas (the window corners are transparent), then the pressed button, caption,
    /// fast-forward badge, click ripple and pointer. Drawing happens in points (bottom-left origin) on a context scaled
    /// by `scale`.
    private func compose(_ base: CGImage, at frame: Int, ripple: Ripple?, pressed: CGRect?, wait: Bool) -> CGImage? {
        guard let size = canvas, let space = CGColorSpace(name: CGColorSpace.sRGB),
              let ctx = CGContext(data: nil, width: Int(size.width * Self.scale), height: Int(size.height * Self.scale),
                                  bitsPerComponent: 8, bytesPerRow: 0, space: space,
                                  bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return nil }
        let full = CGRect(x: 0, y: 0, width: ctx.width, height: ctx.height)
        ctx.setFillColor(CGColor(gray: 1, alpha: 1))
        ctx.fill(full)
        ctx.interpolationQuality = .high
        ctx.draw(base, in: CGRect(x: 0, y: CGFloat(ctx.height - base.height), width: CGFloat(base.width),
                                  height: CGFloat(base.height)))
        ctx.scaleBy(x: Self.scale, y: Self.scale)
        let gc = NSGraphicsContext(cgContext: ctx, flipped: false)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = gc
        defer { NSGraphicsContext.restoreGraphicsState() }
        if let r = pressed {                          // a pressed button is drawn a little darker
            NSColor.black.withAlphaComponent(0.18).setFill()
            NSBezierPath(roundedRect: r, xRadius: r.height / 2, yRadius: r.height / 2).fill()
        }
        let alpha = DemoVideoTiming.captionAlpha(frame: frame, fps: cfg.fps)
        if alpha > 0 { drawCaption(store.loc.t("common.demo.video.caption"), in: size, alpha: alpha) }
        if wait { drawBadge(store.loc.t("common.demo.video.fast_forward"), in: size) }
        if let p = pointer {
            if let ripple { drawRipple(ripple) }
            drawPointer(at: p)
        }
        return ctx.makeImage()
    }

    private func pill(_ rect: CGRect, alpha: Double) {
        NSColor.black.withAlphaComponent(0.72 * alpha).setFill()
        NSBezierPath(roundedRect: rect, xRadius: rect.height / 2, yRadius: rect.height / 2).fill()
    }

    /// Opening caption, centred above the footer bar.
    private func drawCaption(_ text: String, in size: CGSize, alpha: Double) {
        let attrs: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 15, weight: .semibold),
                                                    .foregroundColor: NSColor.white.withAlphaComponent(alpha)]
        let s = NSAttributedString(string: text, attributes: attrs)
        let t = s.size()
        let mid = (WindowMetrics.sidebarWidth + size.width) / 2
        let rect = CGRect(x: mid - t.width / 2 - 18, y: 82, width: t.width + 36, height: t.height + 16)
        pill(rect, alpha: alpha)
        s.draw(at: CGPoint(x: rect.minX + 18, y: rect.minY + 8))
    }

    /// "Fast-forward" badge at the right end of the title bar, where it covers no content.
    private func drawBadge(_ text: String, in size: CGSize) {
        let attrs: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 12, weight: .semibold),
                                                    .foregroundColor: NSColor.white]
        let s = NSAttributedString(string: text, attributes: attrs)
        let t = s.size()
        let icon: CGFloat = 16
        let w = t.width + icon + 6 + 28
        let h = min(t.height + 8, titleBar - 4)
        let rect = CGRect(x: size.width - w - 12, y: size.height - titleBar + (titleBar - h) / 2, width: w, height: h)
        pill(rect, alpha: 1)
        if let sym = NSImage(systemSymbolName: "forward.fill", accessibilityDescription: nil)?
            .withSymbolConfiguration(NSImage.SymbolConfiguration(pointSize: 12, weight: .bold)
                .applying(NSImage.SymbolConfiguration(paletteColors: [.white]))) {
            let sz = sym.size
            sym.draw(in: CGRect(x: rect.minX + 14, y: rect.midY - sz.height / 2, width: sz.width, height: sz.height))
        }
        s.draw(at: CGPoint(x: rect.minX + 14 + icon + 6, y: rect.midY - t.height / 2))
    }

    /// A ring that grows and fades: white with a thin dark edge on a filled (dark) button, dark on light content.
    /// It leaves the control's label free.
    private func drawRipple(_ ripple: Ripple) {
        let r = DemoVideoTiming.ripple(ripple.progress), p = ripple.at
        let rect = CGRect(x: p.x - r.radius, y: p.y - r.radius, width: 2 * r.radius, height: 2 * r.radius)
        NSGraphicsContext.saveGraphicsState()
        defer { NSGraphicsContext.restoreGraphicsState() }
        if let keep = ripple.clear {
            let clip = NSBezierPath(rect: rect.insetBy(dx: -4, dy: -4))
            clip.append(NSBezierPath(rect: keep))
            clip.windingRule = .evenOdd
            clip.addClip()
        }
        let ring = NSBezierPath(ovalIn: rect)
        if ripple.onDark {
            NSColor.white.withAlphaComponent(r.alpha * 0.22).setFill()
            ring.fill()
            ring.lineWidth = 4.5
            NSColor.black.withAlphaComponent(r.alpha * 0.35).setStroke()
            ring.stroke()
            ring.lineWidth = 3
            NSColor.white.withAlphaComponent(r.alpha).setStroke()
        } else {
            NSColor.black.withAlphaComponent(r.alpha * 0.08).setFill()
            ring.fill()
            ring.lineWidth = 2.5
            NSColor.black.withAlphaComponent(r.alpha * 0.6).setStroke()
        }
        ring.stroke()
    }

    /// Mean brightness (0…1) of the picture around `p` (window points, bottom-left origin).
    private func brightness(_ img: CGImage, at p: CGPoint) -> Double {
        let s = Self.scale
        let rect = CGRect(x: (p.x - 3) * s, y: CGFloat(img.height) - (p.y + 3) * s, width: 6 * s, height: 6 * s)
        guard let crop = img.cropping(to: rect.integral),
              let ctx = CGContext(data: nil, width: 1, height: 1, bitsPerComponent: 8, bytesPerRow: 4,
                                  space: CGColorSpaceCreateDeviceRGB(),
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return 1 }
        ctx.setFillColor(CGColor(gray: 1, alpha: 1))
        ctx.fill(CGRect(x: 0, y: 0, width: 1, height: 1))
        ctx.interpolationQuality = .medium
        ctx.draw(crop, in: CGRect(x: 0, y: 0, width: 1, height: 1))
        guard let px = ctx.data?.assumingMemoryBound(to: UInt8.self) else { return 1 }
        return (0.2126 * Double(px[0]) + 0.7152 * Double(px[1]) + 0.0722 * Double(px[2])) / 255
    }

    /// The system arrow with its hot spot on `p`, a little larger than on screen so it stays visible in the GIF.
    private func drawPointer(at p: CGPoint) {
        let img = NSCursor.arrow.image
        let hot = NSCursor.arrow.hotSpot              // from the image's top left
        let k: CGFloat = 1.25
        let rect = CGRect(x: p.x - hot.x * k, y: p.y - (img.size.height - hot.y) * k, width: img.size.width * k,
                          height: img.size.height * k)
        img.draw(in: rect)
    }

    /// Where the pointer is when no step has placed it (no footer elements found): the middle of the footer.
    private func restingPoint() -> CGPoint {
        let size = canvas ?? CGSize(width: 940, height: 680)
        return CGPoint(x: (WindowMetrics.sidebarWidth + size.width) / 2, y: DemoVideoTiming.footerHeight / 2)
    }

    /// The free spot in the footer of the current screen; nil when the screen's footer elements are not built after
    /// `attempts` tries 150 ms apart (SwiftUI builds them on the first request).
    private func parkingPoint(attempts: Int = 4) async -> CGPoint? {
        for attempt in 0..<max(1, attempts) {
            if attempt > 0 { try? await Task.sleep(nanoseconds: 150_000_000) }
            if let footer = footerElements() { return spot(clearOf: footer) }
        }
        return nil
    }

    private func spot(clearOf footer: [CGRect]) -> CGPoint? {
        guard let size = canvas ?? window?.frame.size else { return nil }
        return DemoVideoTiming.parkingPoint(canvas: size, sidebar: WindowMetrics.sidebarWidth, occupied: footer)
    }

    /// Everything the footer of the current screen shows (buttons and texts, from the accessibility tree, window
    /// points); nil while the screen's elements are not built.
    private func footerElements() -> [CGRect]? {
        guard let window, AX.find("screen.\(store.screen.id)", in: window) != nil else { return nil }
        // split into typed steps: one chained expression is too much for the type checker of Xcode 16
        let leaves: [Any] = AX.descendants(window).filter { AX.children($0).isEmpty }
        let frames: [CGRect] = leaves.map { window.convertFromScreen(AX.frame($0)) }
        let top: CGFloat = DemoVideoTiming.footerHeight + 1
        let footer: [CGRect] = frames.filter { (r: CGRect) -> Bool in r.width > 0 && r.height > 0 && r.maxY <= top }
        return footer.isEmpty ? nil : footer
    }

    // MARK: - locating controls (the app's own accessibility tree, ids as in the UI tests)

    /// Frame, kind and label of a control in window coordinates (points, bottom-left origin); nil when the control
    /// cannot be found or lies outside the visible part of the content (the recording does not scroll: a window too
    /// small for the screen also shows in `overflow`).
    private func locate(_ id: String, option: Int?) async -> (CGRect, DemoVideoTiming.Target, String)? {
        guard let window else { return nil }
        func element() -> Any? {
            guard let el = AX.find(id, in: window) else { return nil }
            guard let option else { return el }
            let radios = AX.descendants(el).filter { AX.role($0) == .radioButton }
            return radios.indices.contains(option) ? radios[option] : nil
        }
        var found = element()
        for _ in 0..<3 where found == nil {             // SwiftUI builds its elements on the first request
            try? await Task.sleep(nanoseconds: 150_000_000)
            found = element()
        }
        guard let el = found else { return nil }
        let rect = window.convertFromScreen(AX.frame(el))
        guard rect.width > 0, rect.height > 0 else { return nil }
        let areas = AX.descendants(window).filter { AX.role($0) == .scrollArea }
        if let area = areas.first(where: { AX.find(id, in: $0) != nil }),
           !window.convertFromScreen(AX.frame(area)).insetBy(dx: -1, dy: -1).contains(rect) { return nil }
        let label = AX.text(el)
        switch AX.role(el) {
        case .checkBox?, .radioButton?: return (rect, .toggle, label)
        case .textField?: return (rect, .field, label)
        case .button?: return (rect, .button, label)
        default: return (rect, .other, label)
        }
    }

    /// Notices on the current screen ("id + text" → characters, ids with `noticePrefixes`).
    private func notices() -> [String: Int] {
        guard let window, let screen = AX.find("screen.\(store.screen.id)", in: window) else { return [:] }
        var out: [String: Int] = [:]
        for e in AX.descendants(screen) {
            let id = AX.identifier(e)
            guard Self.noticePrefixes.contains(where: { id.hasPrefix($0) }) else { continue }
            let t = AX.allText(e)
            if !t.isEmpty { out[id + "\u{1}" + t] = t.count }
        }
        return out
    }

    /// `visibleChars()`, waiting a moment when the screen's elements are not built yet (first request).
    private func screenChars() async -> Int {
        for _ in 0..<3 {
            if let n = visibleChars() { return n }
            try? await Task.sleep(nanoseconds: 150_000_000)
        }
        return visibleChars() ?? 300
    }

    /// Visible characters of the current screen (labels and values of its accessibility leaves).
    private func visibleChars() -> Int? {
        guard let window, let screen = AX.find("screen.\(store.screen.id)", in: window) else { return nil }
        let visible = window.convertToScreen(window.contentView?.frame ?? .zero)
        return AX.descendants(screen).filter { AX.children($0).isEmpty && AX.frame($0).intersects(visible) }
            .reduce(0) { $0 + AX.text($1).count }
    }
}

extension DemoVideo {
    /// Recording mode is configured for this process (autopilot with `TM_VIDEO_DIR`, never with the live engine).
    nonisolated static let isRecording: Bool = {
        let env = ProcessInfo.processInfo.environment
        return DemoAutopilot.Config.from(env, engineSpec: AppEnvironment.current(env).engineSpec)?.video != nil
    }()

    /// Recording mode only: the controls are drawn as in the key window even while another app is in front (someone
    /// working on the Mac during a local run), so the video never flips between active and inactive buttons.
    struct ActiveControls: ViewModifier {
        func body(content: Content) -> some View {
            if DemoVideo.isRecording { content.environment(\.controlActiveState, .key) } else { content }
        }
    }
}

/// In-process accessibility lookup: the same element tree and identifiers (btn.*, check.*, field.*, radio.*) the UI
/// tests and VoiceOver use, without UI automation permission. SwiftUI's elements implement the accessibility getters
/// without declaring `NSAccessibilityProtocol`, so they are read by name (only when the object responds to it).
@MainActor
enum AX {
    private static func get(_ o: Any, _ key: String) -> Any? {
        guard let obj = o as? NSObject, obj.responds(to: NSSelectorFromString(key)) else { return nil }
        return obj.value(forKey: key)
    }

    static func children(_ o: Any) -> [Any] { get(o, "accessibilityChildren") as? [Any] ?? [] }
    static func identifier(_ o: Any) -> String { get(o, "accessibilityIdentifier") as? String ?? "" }
    static func role(_ o: Any) -> NSAccessibility.Role? {
        (get(o, "accessibilityRole") as? String).map { NSAccessibility.Role(rawValue: $0) }
    }
    static func frame(_ o: Any) -> CGRect { (get(o, "accessibilityFrame") as? NSValue)?.rectValue ?? .zero }

    static func text(_ o: Any) -> String {
        guard !identifier(o).hasPrefix("demo.") else { return "" }
        let parts = ["accessibilityLabel", "accessibilityTitle", "accessibilityValue"].map {
            get(o, $0) as? String ?? ""
        }
        return parts.max { $0.count < $1.count } ?? ""
    }

    /// The text of an element, or of its leaves when it is a container.
    static func allText(_ o: Any) -> String {
        let own = text(o)
        if !own.isEmpty || children(o).isEmpty { return own }
        return descendants(o).filter { children($0).isEmpty }.map(text).filter { !$0.isEmpty }.joined(separator: " ")
    }

    static func descendants(_ o: Any, depth: Int = 0) -> [Any] {
        guard depth < 40 else { return [] }
        return children(o).flatMap { [$0] + descendants($0, depth: depth + 1) }
    }

    /// The first element with this identifier (breadth first).
    static func find(_ id: String, in root: Any) -> Any? {
        var queue = children(root)
        var seen = 0
        while !queue.isEmpty, seen < 20_000 {
            let e = queue.removeFirst()
            seen += 1
            if identifier(e) == id { return e }
            queue += children(e)
        }
        return nil
    }
}

/// Writes the frames off the main thread: one PNG per distinct picture, hard links for repeated frames. At most a few
/// pictures wait for the encoder, so memory stays small.
final class FrameWriter: @unchecked Sendable {
    private let queue = DispatchQueue(label: "demo-video.frames", qos: .userInitiated)
    private let slots = DispatchSemaphore(value: 6)
    private var failed = 0

    var failures: Int { queue.sync { failed } }

    func write(_ image: CGImage, to url: URL, links: [URL]) {
        slots.wait()
        queue.async { [self] in
            defer { slots.signal() }
            guard let dest = CGImageDestinationCreateWithURL(url as CFURL, UTType.png.identifier as CFString, 1, nil)
            else { failed += 1; return }
            CGImageDestinationAddImage(dest, image, nil)
            if !CGImageDestinationFinalize(dest) { failed += 1; return }
            for l in links where link(url, l) == false { failed += 1 }
        }
    }

    /// Links `src` to every `dsts` and removes `src` (a live sample that is now part of the sequence).
    func link(_ src: URL, to dsts: [URL]) {
        queue.async { [self] in
            for d in dsts where link(src, d) == false { failed += 1 }
            try? FileManager.default.removeItem(at: src)
        }
    }

    func drain() { queue.sync {} }

    private func link(_ a: URL, _ b: URL) -> Bool {
        (try? FileManager.default.linkItem(at: a, to: b)) != nil
            || (try? FileManager.default.copyItem(at: a, to: b)) != nil
    }
}
