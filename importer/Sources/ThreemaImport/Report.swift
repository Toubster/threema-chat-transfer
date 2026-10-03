// SPDX-License-Identifier: AGPL-3.0-or-later
// Report.swift - counters only (no texts, names or identities).
import Foundation

final class Report {
    var counters: [String: [String: Int]] = [:]   // entity -> {inserted, existing, skipped:<reason>, ...}
    var info: [String: Any] = [:]
    var warnings: [String] = []

    func inc(_ entity: String, _ key: String, by n: Int = 1) {
        counters[entity, default: [:]][key, default: 0] += n
    }
    func inserted(_ entity: String, by n: Int = 1) { inc(entity, "inserted", by: n) }
    func existing(_ entity: String) { inc(entity, "existing") }
    func skipped(_ entity: String, _ reason: String) { inc(entity, "skipped:" + reason) }
    func get(_ entity: String, _ key: String) -> Int { counters[entity]?[key] ?? 0 }
    func warn(_ w: String) { if !warnings.contains(w) { warnings.append(w) } }

    func json() -> Data {
        var root: [String: Any] = info
        var ents: [String: Any] = [:]
        for (e, kv) in counters {
            var inserted = 0, existing = 0
            var skipped: [String: Int] = [:]
            var other: [String: Int] = [:]
            for (k, v) in kv {
                if k == "inserted" { inserted = v }
                else if k == "existing" { existing = v }
                else if k.hasPrefix("skipped:") { skipped[String(k.dropFirst(8))] = v }
                else { other[k] = v }
            }
            var o: [String: Any] = ["inserted": inserted, "existing": existing,
                                    "skipped": skipped, "skipped_total": skipped.values.reduce(0, +)]
            if !other.isEmpty { o["details"] = other }
            ents[e] = o
        }
        root["entities"] = ents
        root["warnings"] = warnings
        return (try? JSONSerialization.data(withJSONObject: root, options: [.prettyPrinted, .sortedKeys])) ?? Data()
    }
}
