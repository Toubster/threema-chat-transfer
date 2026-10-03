// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation

/// Number/date formatting for placeholders. Bytes are shown as decimal GB (like Finder and iOS settings).
public enum Formatters {
    /// `{x} GB` placeholders: one decimal below 100 GB, none above; anything above 0 but below 0.1 GB is "< 0,1"
    /// (never "0 GB" for data that exists, e.g. a text-only Android backup).
    public static func gigabytes(_ bytes: Int64, lang: AppLanguage) -> String {
        var gb = Double(bytes) / 1_000_000_000
        var prefix = ""
        if bytes > 0 && gb < 0.05 {
            gb = 0.1
            prefix = "< "
        }
        let f = NumberFormatter()
        f.locale = lang.locale
        f.numberStyle = .decimal
        f.minimumFractionDigits = 0
        f.maximumFractionDigits = gb >= 100 ? 0 : 1
        f.usesGroupingSeparator = true
        return prefix + (f.string(from: NSNumber(value: gb)) ?? String(format: "%.1f", gb))
    }

    public static func count(_ n: Int, lang: AppLanguage) -> String {
        let f = NumberFormatter()
        f.locale = lang.locale
        f.numberStyle = .decimal
        f.usesGroupingSeparator = n >= 10_000
        return f.string(from: NSNumber(value: n)) ?? String(n)
    }

    public static func time(_ date: Date, lang: AppLanguage, timeZone: TimeZone = .current) -> String {
        let f = DateFormatter()
        f.locale = lang.locale
        f.timeZone = timeZone
        f.dateFormat = "HH:mm"
        return f.string(from: date)
    }

    public static func date(_ date: Date, lang: AppLanguage, timeZone: TimeZone = .current) -> String {
        let f = DateFormatter()
        f.locale = lang.locale
        f.timeZone = timeZone
        f.dateStyle = .long
        f.timeStyle = .none
        return f.string(from: date)
    }

    // MARK: RFC 3339 (protocol timestamps, always UTC with "Z")

    private static func iso(fractional: Bool) -> ISO8601DateFormatter {
        let f = ISO8601DateFormatter()
        f.formatOptions = fractional ? [.withInternetDateTime, .withFractionalSeconds] : [.withInternetDateTime]
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }

    public static func parseTimestamp(_ s: String?) -> Date? {
        guard let s else { return nil }
        return iso(fractional: true).date(from: s) ?? iso(fractional: false).date(from: s)
    }

    public static func timestamp(_ d: Date) -> String { iso(fractional: true).string(from: d) }

    /// Rough duration estimate for S12/S18 texts ({min}): ~30 MB/s over USB, at least 2 minutes.
    public static func backupMinutes(estimatedBytes: Int64?) -> Int {
        let bytes = Double(estimatedBytes ?? 6_000_000_000) + 2_000_000_000
        let minutes = Int((bytes / 30_000_000 / 60).rounded(.up))
        return min(max(minutes, 2), 60)
    }
}
