// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// A check row with status icon (✓ / ! / ✗ / spinner / pending). VoiceOver reads "label, status".
struct CheckRowView: View {
    @Environment(\.loc) var loc
    let label: String
    let status: CheckRow.Status
    var id: String? = nil

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            icon.frame(width: 18)
            Text(label)
                .foregroundStyle(status == .pending ? .secondary : .primary)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(label)
        .accessibilityValue(loc.t("common.status.\(status.rawValue)"))
        .accessibilityIdentifier(id.map { "check.\($0)" } ?? "check.row")
    }

    @ViewBuilder private var icon: some View {
        switch status {
        case .pass: Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case .warn: Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
        case .fail: Image(systemName: "xmark.octagon.fill").foregroundStyle(.red)
        case .skip: Image(systemName: "minus.circle").foregroundStyle(.secondary)
        case .running: ProgressView().controlSize(.small)
        case .pending: Image(systemName: "circle").foregroundStyle(.tertiary)
        }
    }
}

/// The phases of a command as a step list (S06, S13, S18).
struct StepList: View {
    let steps: [(label: String, status: CheckRow.Status)]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ForEach(Array(steps.enumerated()), id: \.offset) { i, s in
                CheckRowView(label: s.label, status: s.status, id: "step.\(i)")
            }
        }
    }
}

/// Progress of the running phase (bytes/files/items) with an accessible value.
struct ProgressPanel: View {
    @Environment(\.loc) var loc
    let activity: Activity?
    var label: String

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            if let pct = activity?.pct {
                ProgressView(value: min(max(pct, 0), 100), total: 100) { Text(label).font(.callout) }
                    .accessibilityLabel(label)
                    .accessibilityValue(loc.t("common.pct", ["pct": String(Int(pct))]))
            } else if activity?.running == true {
                ProgressView { Text(label).font(.callout) }
                    .progressViewStyle(.linear)
                    .accessibilityLabel(label)
            }
        }
        .accessibilityIdentifier("progress")
    }
}

/// Checkbox with a wrapping label (checklists of S01, S07, S08, S09, S11, S14).
struct ChecklistToggle: View {
    let text: String
    let id: String
    @Binding var isOn: Bool

    var body: some View {
        Toggle(isOn: $isOn) {
            Text(text).fixedSize(horizontal: false, vertical: true)
        }
        .toggleStyle(.checkbox)
        .accessibilityIdentifier("check.\(id)")
        .accessibilityLabel(text)
    }
}

/// "Das ist normal, neuer Versuch …" (retry events, W_BACKUP_RETRY / W_DEVICE_RETRY).
struct RetryBanner: View {
    @Environment(\.loc) var loc
    let retry: Activity.Retry?

    var body: some View {
        if let r = retry {
            InfoBox(kind: .info, title: loc.t(ErrorCatalog.titleKey(r.code)),
                    text: AttributedString(loc.t(ErrorCatalog.bodyKey(r.code)) + " "
                                           + loc.t("common.attempt", ["attempt": String(r.attempt), "max": String(r.max)])))
                .accessibilityIdentifier("banner.retry")
        }
    }
}

/// Hint overlay for `prompt` events (unlock, trust, passcode, keep cable).
struct PromptBanner: View {
    @Environment(\.loc) var loc
    let prompts: [String]

    var body: some View {
        if let p = prompts.last {
            HStack(spacing: 12) {
                Image(systemName: p == "keep_cable" ? "cable.connector" : "iphone.gen3")
                    .font(.title2)
                    .accessibilityHidden(true)
                Text(loc.t("prompt.\(p)")).font(.headline).fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            .padding(14)
            .background(Color.accentColor.opacity(0.15), in: RoundedRectangle(cornerRadius: 10))
            .accessibilityElement(children: .combine)
            .accessibilityAddTraits(.updatesFrequently)
            .accessibilityIdentifier("banner.prompt")
        }
    }
}

/// W_/N_ notes as hint lines (S02, S06, S19).
struct NoteList: View {
    @Environment(\.loc) var loc
    let notes: [NoteItem]
    var kind: BoxKind = .warning

    var body: some View {
        ForEach(notes) { n in
            InfoBox(kind: kind, title: loc.t(ErrorCatalog.titleKey(n.code)),
                    text: AttributedString(loc.t(ErrorCatalog.bodyKey(n.code),
                                                 ErrorCatalog.placeholders(codeRaw: n.code, data: n.data ?? .object([:]),
                                                                           loc: loc))))
                .accessibilityIdentifier("note.\(n.code)")
        }
    }
}

/// The freshness countdown (S13/S14).
struct CountdownView: View {
    @Environment(\.loc) var loc
    let state: CountdownState?

    var body: some View {
        if let s = state, !s.expired {
            Label { Text(Countdown.text(s, loc: loc)).fixedSize(horizontal: false, vertical: true) } icon: {
                Image(systemName: "timer").accessibilityHidden(true)
            }
            .font(.headline)
            .foregroundStyle(s.isUrgent ? Color.orange : Color.primary)
            .padding(12)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background((s.isUrgent ? Color.orange : Color.accentColor).opacity(0.10), in: RoundedRectangle(cornerRadius: 10))
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("countdown")
        }
    }
}
