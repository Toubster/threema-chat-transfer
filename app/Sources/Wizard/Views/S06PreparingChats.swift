// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// S06 Chats werden vorbereitet / Preparing your chats (`android-normalize`).
struct S06PreparingChats: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc

    private var act: Activity? { store.activities[.androidNormalize] }

    private var mediaLabel: String {
        if act?.phase == EnginePhase.media.rawValue, let d = act?.done, let t = act?.total, t > 0 {
            return loc.t("s06.step.media", ["n": Formatters.count(Int(d), lang: loc.language),
                                            "m": Formatters.count(Int(t), lang: loc.language)])
        }
        if let n = store.normalized {
            return loc.t("s06.step.media", ["n": Formatters.count(n.mediaPresent, lang: loc.language),
                                            "m": Formatters.count(n.mediaTotal, lang: loc.language)])
        }
        return loc.t("s06.step.media_plain")
    }

    /// Notes from the engine; if the engine only reports counts, the matching W_ hint is derived from them.
    private var notes: [NoteItem] {
        var n = act?.notes ?? []
        if let r = store.normalized {
            func add(_ code: EngineCode, _ data: [String: JSONValue]) {
                if !n.contains(where: { $0.code == code.rawValue }) { n.append(NoteItem(code: code.rawValue, data: .object(data))) }
            }
            if r.mediaPresent < r.mediaTotal {
                add(.W_ANDROID_MEDIA_PARTIAL, ["media_present": .number(Double(r.mediaPresent)),
                                               "media_total": .number(Double(r.mediaTotal))])
            }
            if r.ownUnsentAsSent > 0 {
                add(.W_OWN_UNSENT_AS_SENT, ["own_unsent_as_sent": .number(Double(r.ownUnsentAsSent))])
            }
            if r.missingKeySenders > 0 {
                add(.W_MISSING_KEY_SENDERS, ["missing_key_senders": .number(Double(r.missingKeySenders)),
                                             "missing_key_messages": .number(Double(r.missingKeyMessages)),
                                             "missing_key_groups": .number(Double(r.missingKeyGroups ?? 0))])
            }
        }
        return n
    }

    var body: some View {
        ScreenScaffold(screen: .s06, title: loc.t("screen.S06.name"), symbol: "bubble.left.and.text.bubble.right") {
            StepList(steps: [
                (loc.t("s06.step.read"), act?.status(of: .read) ?? .pending),
                (mediaLabel, act?.status(of: .media) ?? .pending),
                (loc.t("s06.step.verify"), act?.status(of: .verify) ?? .pending)])
            if act?.running == true { ProgressPanel(activity: act, label: loc.t("phase.\(act?.phase ?? "read")")) }
            if let r = store.normalized {
                InfoBox(kind: .success, text: AttributedString(loc.t("s06.result", [
                    "chats": Formatters.count(r.chats, lang: loc.language),
                    "groups": Formatters.count(r.groups, lang: loc.language),
                    "messages": Formatters.count(r.messages, lang: loc.language),
                    "media": Formatters.count(r.mediaTotal, lang: loc.language),
                    "pct": String(r.mediaPercent),
                    "polls": Formatters.count(r.polls, lang: loc.language)])))
                    .accessibilityIdentifier("android.result")
                if store.inspect?.plan == "text_plus_media" {
                    InfoBox(kind: .info, loc.t("s06.combination", [
                        "n": Formatters.count(r.mediaPresent, lang: loc.language),
                        "m": Formatters.count(r.mediaTotal, lang: loc.language)]))
                        .accessibilityIdentifier("android.combination")
                }
                NoteList(notes: notes)
                if store.iosBlocked {
                    InfoBox(kind: .warning, loc.t("s06.waiting_ios")).accessibilityIdentifier("inline.waiting_ios")
                }
            }
        } footer: {
            PrimaryButton(title: loc.t("common.continue"), id: "btn.continue",
                          enabled: store.normalized != nil && !store.isBusy) { store.confirmAndroid() }
        }
    }
}
