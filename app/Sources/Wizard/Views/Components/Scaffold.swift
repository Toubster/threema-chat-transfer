// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// Colour tone of a screen header (S19 green, S21/F-screens red). No "yellow" verdict exists (DESIGN §7).
enum ScreenTone {
    case normal, success, danger, warning

    var color: Color {
        switch self {
        case .normal: return .accentColor
        case .success: return .green
        case .danger: return .red
        case .warning: return .orange
        }
    }
}

/// Common frame of every screen: header with illustration, scrollable content, footer bar.
/// Back/Cancel sit on the left (only when the WizardStore allows them), the screen's own buttons on the right.
struct ScreenScaffold<Content: View, Footer: View>: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc
    let screen: Screen
    let title: String
    var symbol: String? = nil
    var tone: ScreenTone = .normal
    var showsCancel = true
    @ViewBuilder var content: () -> Content
    @ViewBuilder var footer: () -> Footer

    var body: some View {
        VStack(spacing: 0) {
            ScrollView { main }
            Divider()
            FooterBar(showsCancel: showsCancel) { footer() }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("screen.\(screen.id)")
    }

    private var main: some View {
        OverflowProbe(id: "\(screen.id).content") {
            VStack(alignment: .leading, spacing: 18) {
                header
                content()
            }
        }
        .padding(WindowMetrics.contentPadding)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var header: some View {
        HStack(alignment: .top, spacing: 16) {
            if let symbol {
                Illustration(symbol: symbol, tone: tone)
            }
            VStack(alignment: .leading, spacing: 4) {
                if !screen.isFailure, loc.has(screen.nameKey), loc.t(screen.nameKey) != title {
                    Text(loc.t(screen.nameKey))
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(.secondary)
                        .textCase(.uppercase)
                        .accessibilityHidden(true)
                }
                Text(title)
                    .font(.title.weight(.semibold))
                    .foregroundStyle(tone == .danger ? Color.red : Color.primary)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityAddTraits(.isHeader)
                    .accessibilityIdentifier("screen.title")
            }
            Spacer(minLength: 0)
        }
    }
}

/// Bottom bar: [Zurück] [Abbrechen] … [screen buttons].
struct FooterBar<Trailing: View>: View {
    @EnvironmentObject var store: WizardStore
    @Environment(\.loc) var loc
    var showsCancel = true
    @ViewBuilder var trailing: () -> Trailing

    var body: some View {
        HStack(spacing: 10) {
            if store.canGoBack {
                Button(loc.t("common.back")) { store.goBack() }
                    .controlSize(.large)
                    .fixedSize()
                    .keyboardShortcut(.cancelAction)
                    .accessibilityIdentifier("btn.back")
            }
            if showsCancel && store.canCancel {
                Button(loc.t("common.cancel")) { store.requestCancel() }
                    .controlSize(.large)
                    .fixedSize()
                    .accessibilityIdentifier("btn.cancel")
            }
            if store.critical {
                Label(loc.t("common.cancel_locked"), systemImage: "lock.fill")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .fixedSize()
                    .accessibilityIdentifier("label.cancel_locked")
            }
            Spacer(minLength: 12)
            trailing()
        }
        .modifier(FooterProbe(id: "\(store.screen.id).footer"))
        .padding(.horizontal, WindowMetrics.contentPadding)
        .padding(.vertical, 14)
        .background(.bar)
    }
}

private struct FooterProbe: ViewModifier {
    let id: String
    func body(content: Content) -> some View { OverflowProbe(id: id) { content } }
}

/// The main button of a screen.
struct PrimaryButton: View {
    let title: String
    let id: String
    var enabled = true
    var role: ButtonRole? = nil
    let action: () -> Void

    var body: some View {
        Button(role: role, action: action) {
            Text(title).frame(minWidth: 90)
        }
        .buttonStyle(.borderedProminent)
        .controlSize(.large)
        .keyboardShortcut(.defaultAction)
        .disabled(!enabled)
        .fixedSize()
        .accessibilityIdentifier(id)
    }
}

/// A secondary button in the footer.
struct SecondaryButton: View {
    let title: String
    let id: String
    var enabled = true
    let action: () -> Void

    var body: some View {
        Button(action: action) { Text(title) }
            .controlSize(.large)
            .disabled(!enabled)
            .fixedSize()
            .accessibilityIdentifier(id)
    }
}

/// Decorative illustration: an SF Symbol in a tinted rounded square (placeholder for later device drawings;
/// no device photos, DESIGN §4.3).
struct Illustration: View {
    let symbol: String
    var tone: ScreenTone = .normal

    var body: some View {
        Image(systemName: symbol)
            .symbolRenderingMode(.hierarchical)
            .font(.system(size: 30, weight: .regular))
            .foregroundStyle(tone.color)
            .frame(width: 56, height: 56)
            .background(tone.color.opacity(0.12), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .accessibilityHidden(true)
    }
}

/// Paragraph text that wraps and never truncates.
struct Paragraph: View {
    let text: AttributedString
    var secondary = false

    init(_ s: String, secondary: Bool = false) { self.text = AttributedString(s); self.secondary = secondary }
    init(md: AttributedString, secondary: Bool = false) { self.text = md; self.secondary = secondary }

    var body: some View {
        Text(text)
            .font(secondary ? .callout : .body)
            .foregroundStyle(secondary ? .secondary : .primary)
            .fixedSize(horizontal: false, vertical: true)
            .frame(maxWidth: .infinity, alignment: .leading)
            .textSelection(.enabled)
    }
}

enum BoxKind {
    case info, warning, danger, success

    var color: Color {
        switch self {
        case .info: return .accentColor
        case .warning: return .orange
        case .danger: return .red
        case .success: return .green
        }
    }

    var symbol: String {
        switch self {
        case .info: return "info.circle.fill"
        case .warning: return "exclamationmark.triangle.fill"
        case .danger: return "exclamationmark.octagon.fill"
        case .success: return "checkmark.seal.fill"
        }
    }
}

/// A tinted box for hints, warnings and the red boxes of S16/S21.
struct InfoBox<Extra: View>: View {
    let kind: BoxKind
    var title: String? = nil
    let text: AttributedString
    @ViewBuilder var extra: () -> Extra

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: kind.symbol)
                .foregroundStyle(kind.color)
                .font(.title3)
                .accessibilityHidden(true)
            VStack(alignment: .leading, spacing: 6) {
                if let title {
                    Text(title).font(.headline).fixedSize(horizontal: false, vertical: true)
                }
                Text(text).fixedSize(horizontal: false, vertical: true)
                extra()
            }
            Spacer(minLength: 0)
        }
        .padding(14)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(kind.color.opacity(0.10), in: RoundedRectangle(cornerRadius: 10, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(kind.color.opacity(0.35)))
        // text-only boxes are read as one element; boxes with controls (S20 cleanup buttons) keep each control
        // reachable for VoiceOver and UI tests
        .accessibilityElement(children: Extra.self == EmptyView.self ? .combine : .contain)
    }
}

extension InfoBox where Extra == EmptyView {
    init(kind: BoxKind, title: String? = nil, text: AttributedString) {
        self.init(kind: kind, title: title, text: text) { EmptyView() }
    }

    init(kind: BoxKind, title: String? = nil, _ s: String) {
        self.init(kind: kind, title: title, text: AttributedString(s)) { EmptyView() }
    }
}

/// A numbered instruction (S04, S16).
struct NumberedStep: View {
    let number: Int
    let text: AttributedString

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 10) {
            Text("\(number)")
                .font(.callout.weight(.bold).monospacedDigit())
                .foregroundStyle(.white)
                .frame(width: 24, height: 24)
                .background(Circle().fill(Color.accentColor))
                .accessibilityHidden(true)
            Text(text).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(Text("\(number). ") + Text(text))
    }
}

/// A bullet line (S00 list, S20 list).
struct Bullet: View {
    let text: String
    var symbol = "circle.fill"

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 8) {
            Image(systemName: symbol).font(.system(size: 6)).foregroundStyle(.secondary).accessibilityHidden(true)
            Text(text).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
    }
}

/// Size check for long German texts at the smallest window (DESIGN §13.1 level 5 "Snapshots"): records when the
/// content wants to be wider than the space it gets (fixed-size buttons and rows are the usual culprits). Passive in
/// the app; the snapshot tests switch the registry on and fail on any entry.
struct OverflowProbe<Content: View>: View {
    let id: String
    @ViewBuilder var content: () -> Content

    var body: some View { OverflowProbeLayout(id: id) { content() } }
}

struct OverflowProbeLayout: Layout {
    let id: String

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let size = subviews.first?.sizeThatFits(proposal) ?? .zero
        if let w = proposal.width, w > 0, w < 10_000, size.width > w + 0.5 {
            OverflowRegistry.record(id: id, wanted: size.width, available: w)
        }
        return size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        subviews.first?.place(at: bounds.origin, proposal: ProposedViewSize(width: bounds.width, height: bounds.height))
    }
}

/// Collects overflows while enabled (tests only).
enum OverflowRegistry {
    struct Entry: Equatable, CustomStringConvertible {
        let id: String
        let wanted: CGFloat
        let available: CGFloat
        var description: String { "\(id): wants \(Int(wanted)) pt, has \(Int(available)) pt" }
    }

    private static let lock = NSLock()
    nonisolated(unsafe) private static var enabled = false
    nonisolated(unsafe) private static var entries: [Entry] = []

    static func start() { lock.lock(); enabled = true; entries = []; lock.unlock() }

    static func stop() -> [Entry] {
        lock.lock(); defer { lock.unlock() }
        enabled = false
        return entries
    }

    static func record(id: String, wanted: CGFloat, available: CGFloat) {
        lock.lock(); defer { lock.unlock() }
        guard enabled else { return }
        let e = Entry(id: id, wanted: wanted, available: available)
        if !entries.contains(e) { entries.append(e) }
    }
}
