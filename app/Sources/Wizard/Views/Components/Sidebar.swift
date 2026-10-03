// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// Phases in the sidebar: Start · Android · iPhone vorbereiten · Übertragung · Kontrolle · Fertig (DESIGN §8.1).
struct SidebarView: View {
    @Environment(\.loc) var loc
    let current: SidebarPhase

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(AppInfo.productName)
                .font(.title2.weight(.bold))
                .padding(.bottom, 2)
                .accessibilityAddTraits(.isHeader)
            Text(loc.t("sidebar.title"))
                .font(.caption.weight(.semibold))
                .foregroundStyle(.secondary)
                .textCase(.uppercase)
                .padding(.bottom, 8)
                .accessibilityHidden(true)
            ForEach(Array(SidebarPhase.allCases.enumerated()), id: \.element) { i, p in
                row(index: i, phase: p)
            }
            Spacer()
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 24)
        .frame(width: WindowMetrics.sidebarWidth, alignment: .leading)
        .frame(maxHeight: .infinity, alignment: .top)
        .background(.regularMaterial)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("sidebar")
    }

    private func state(_ p: SidebarPhase) -> String {
        let all = SidebarPhase.allCases
        guard let i = all.firstIndex(of: p), let c = all.firstIndex(of: current) else { return "upcoming" }
        return i < c ? "completed" : (i == c ? "current" : "upcoming")
    }

    private func row(index: Int, phase: SidebarPhase) -> some View {
        let st = state(phase)
        return HStack(spacing: 10) {
            ZStack {
                Circle().fill(st == "upcoming" ? Color.secondary.opacity(0.2) : Color.accentColor)
                if st == "completed" {
                    Image(systemName: "checkmark").font(.caption.weight(.bold)).foregroundStyle(.white)
                } else {
                    Text("\(index + 1)").font(.caption.weight(.bold))
                        .foregroundStyle(st == "upcoming" ? Color.secondary : Color.white)
                }
            }
            .frame(width: 22, height: 22)
            .accessibilityHidden(true)
            Text(loc.t(phase.titleKey))
                .font(st == "current" ? .body.weight(.semibold) : .body)
                .foregroundStyle(st == "upcoming" ? .secondary : .primary)
                .lineLimit(2)
                .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(.vertical, 6)
        .padding(.horizontal, 8)
        .background(st == "current" ? Color.accentColor.opacity(0.12) : .clear, in: RoundedRectangle(cornerRadius: 8))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(loc.t(phase.titleKey))
        .accessibilityValue(loc.t("sidebar.state.\(st)"))
        .accessibilityIdentifier("sidebar.\(phase.rawValue)")
    }
}
