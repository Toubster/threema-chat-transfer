// SPDX-License-Identifier: AGPL-3.0-or-later
import SwiftUI

/// Demo mode (DESIGN §13.1 level 7): the real app with the virtual iPhone (`--fake-device`) or the MockEngine.
/// Every window carries a "DEMO" watermark so screenshots can never be mistaken for a real transfer.
public struct DemoWatermark: View {
    @Environment(\.loc) private var loc

    public init() {}

    public var body: some View {
        GeometryReader { geo in
            Text(loc.t("common.demo"))
                .font(.system(size: min(geo.size.width, geo.size.height) * 0.28, weight: .black, design: .rounded))
                .foregroundStyle(Color.primary.opacity(0.07))
                .rotationEffect(.degrees(-24))
                .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .allowsHitTesting(false)
        .accessibilityElement()
        .accessibilityLabel(loc.t("common.demo.a11y"))
        .accessibilityIdentifier("demo.watermark")
    }
}
