// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation

/// One stdout line of `tmcore` (schema `core/schema/events.v1.json`). The app is a tolerant consumer: unknown fields
/// are ignored, unknown event types decode as `.unknown` and unknown codes are kept as raw strings (DESIGN §5.7).
public struct EngineEvent: Decodable, Equatable, Sendable {
    public let v: Int
    public let seq: Int
    public let ts: String
    public let cmd: String
    public let type: String

    // hello
    public let engineVersion: String?
    public let `protocol`: Int?
    public let pymobiledevice3: String?
    public let importerVersion: String?
    public let models: [String]?
    public let compatDigest: String?
    public let fakeDevice: Bool?
    // phase / progress
    public let phase: String?
    public let index: Int?
    public let count: Int?
    public let pct: Double?
    public let done: Double?
    public let total: Double?
    public let unit: String?
    public let etaS: Double?
    // check / note / result
    public let id: String?
    public let status: String?
    public let code: String?
    public let data: JSONValue?
    // prompt
    public let kind: String?
    public let active: Bool?
    // retry
    public let reason: String?
    public let attempt: Int?
    public let max: Int?
    public let waitS: Double?
    // device
    public let state: String?
    public let device: String?
    public let productType: String?
    // critical
    public let on: Bool?
    // result
    public let ok: Bool?
    public let retryable: Bool?
    public let deviceModified: String?

    enum CodingKeys: String, CodingKey {
        case v, seq, ts, cmd, type
        case engineVersion = "engine_version", `protocol`, pymobiledevice3, importerVersion = "importer_version"
        case models, compatDigest = "compat_digest", fakeDevice = "fake_device"
        case phase, index, count, pct, done, total, unit, etaS = "eta_s"
        case id, status, code, data, kind, active, reason, attempt, max, waitS = "wait_s"
        case state, device, productType = "product_type", on, ok, retryable, deviceModified = "device_modified"
    }

    public var eventType: EventType? { EventType(rawValue: type) }
    public var command: EngineCommand? { EngineCommand(rawValue: cmd) }

    /// Parses one stdout line. Returns nil for lines that are not a JSON object with the envelope fields.
    public static func parse(_ line: String) -> EngineEvent? {
        guard let data = line.data(using: .utf8) else { return nil }
        return try? JSONDecoder().decode(EngineEvent.self, from: data)
    }
}

/// The final `result` event of a command (exactly one per process).
public struct ResultEvent: Equatable, Sendable {
    public let command: String
    public let ok: Bool
    public let codeRaw: String
    public let retryable: Bool
    public let deviceModified: String
    public let data: JSONValue

    public var code: EngineCode? { EngineCode(rawValue: codeRaw) }

    public init(command: String, ok: Bool, codeRaw: String, retryable: Bool, deviceModified: String, data: JSONValue) {
        self.command = command
        self.ok = ok
        self.codeRaw = codeRaw
        self.retryable = retryable
        self.deviceModified = deviceModified
        self.data = data
    }

    init?(_ e: EngineEvent) {
        guard e.type == EventType.result.rawValue, let ok = e.ok, let code = e.code else { return nil }
        self.init(command: e.cmd, ok: ok, codeRaw: code, retryable: e.retryable ?? false,
                  deviceModified: e.deviceModified ?? "unknown", data: e.data ?? .object([:]))
    }
}

/// A check row (`check` event) as the UI shows it.
public struct CheckRow: Equatable, Identifiable, Sendable {
    public enum Status: String, Sendable { case pass, warn, fail, skip, running, pending }
    public let id: String
    public var status: Status
    public var code: String?
    public var data: JSONValue?
}

/// A `note` event (W_/N_ code) shown as a hint line.
public struct NoteItem: Equatable, Identifiable, Sendable {
    public var id: String { code }
    public let code: String
    public let data: JSONValue?
}
