// SPDX-License-Identifier: AGPL-3.0-or-later
import Foundation

// Typed `result.data` of the commands the wizard reads (events.v1.json#/$defs/result_ok_<command>).
// Every field the app does not strictly need is optional so a newer engine with extra/missing optional fields
// still decodes.

public struct HostCheckResult: Decodable, Equatable, Sendable {
    public let macos: String
    public let arch: String
    public let fs: String
    public let freeBytes: Int64
    public let needBytes: Int64?
    public let power: String
    public let batteryPct: Int?
    public let filevault: Bool

    enum CodingKeys: String, CodingKey {
        case macos, arch, fs, freeBytes = "free_bytes", needBytes = "need_bytes", power, batteryPct = "battery_pct"
        case filevault
    }
}

public struct DeviceStatus: Decodable, Equatable, Sendable {
    public struct Threema: Decodable, Equatable, Sendable {
        public let installed: Bool
        public let variant: String
        public let version: String?
    }
    public let device: String
    public let productType: String
    public let iosVersion: String
    public let iosBuild: String
    public let compat: String
    public let threema: Threema
    public let encryption: String
    public let findMy: String
    public let freeBytes: Int64
    public let photosBytesEstimate: Int64?
    public let batteryPct: Int?
    public let charging: Bool
    public let managed: Bool

    enum CodingKeys: String, CodingKey {
        case device, productType = "product_type", iosVersion = "ios_version", iosBuild = "ios_build", compat
        case threema, encryption, findMy = "find_my", freeBytes = "free_bytes"
        case photosBytesEstimate = "photos_bytes_estimate", batteryPct = "battery_pct", charging, managed
    }

    public var isVerified: Bool { compat == "verified" }
}

public struct AndroidInspectResult: Decodable, Equatable, Sendable {
    public struct File: Decodable, Equatable, Sendable {
        public let ref: Int
        public let kind: String
        public let formatVersion: Int?
        public let createdAt: String?
        public let bytes: Int64
        public let hasMedia: Bool
        public let encrypted: Bool?

        enum CodingKeys: String, CodingKey {
            case ref, kind, formatVersion = "format_version", createdAt = "created_at", bytes
            case hasMedia = "has_media", encrypted
        }
    }
    public let files: [File]
    public let plan: String
    public let textRef: Int?
    public let mediaRefs: [Int]

    enum CodingKeys: String, CodingKey { case files, plan, textRef = "text_ref", mediaRefs = "media_refs" }

    /// The refs whose password is needed (text file + media files, unique, in order).
    public var refsNeedingPassword: [Int] {
        var out: [Int] = []
        for r in [textRef].compactMap({ $0 }) + mediaRefs where !out.contains(r) {
            if files.first(where: { $0.ref == r })?.encrypted ?? true { out.append(r) }
        }
        return out
    }

    /// Plan argument of `android-normalize` (compact JSON, keys in a fixed order).
    public var planArgument: String {
        let media = mediaRefs.map(String.init).joined(separator: ",")
        let text = textRef.map(String.init) ?? "null"
        return "{\"text_ref\":\(text),\"media_refs\":[\(media)]}"
    }
}

public struct AndroidNormalizeResult: Decodable, Equatable, Sendable {
    public let chats: Int
    public let groups: Int
    public let messages: Int
    public let mediaPresent: Int
    public let mediaTotal: Int
    public let polls: Int
    public let ownUnsentAsSent: Int
    public let missingKeySenders: Int
    public let missingKeyMessages: Int
    public let missingKeyGroups: Int?
    public let missingSendersFile: String?
    public let ownId: String

    enum CodingKeys: String, CodingKey {
        case chats, groups, messages, mediaPresent = "media_present", mediaTotal = "media_total", polls
        case ownUnsentAsSent = "own_unsent_as_sent", missingKeySenders = "missing_key_senders"
        case missingKeyMessages = "missing_key_messages", missingKeyGroups = "missing_key_groups"
        case missingSendersFile = "missing_senders_file", ownId = "own_id"
    }

    public var mediaPercent: Int {
        guard mediaTotal > 0 else { return 100 }
        return Int((Double(mediaPresent) / Double(mediaTotal) * 100).rounded(.down))
    }
}

public struct BackupResult: Decodable, Equatable, Sendable {
    public struct Threema: Decodable, Equatable, Sendable {
        public let setupOk: Bool
        public let retentionOk: Bool
        public let model: String
        public let appVersion: String?
        enum CodingKeys: String, CodingKey {
            case setupOk = "setup_ok", retentionOk = "retention_ok", model, appVersion = "app_version"
        }
    }
    public let role: String
    public let bytes: Int64
    public let files: Int
    public let passwordOk: Bool
    public let airplane: Bool
    public let threema: Threema
    public let idMatch: Bool?
    public let photosBytes: Int64
    public let finishedAt: String
    public let freshUntil: String?
    public let iosBuild: String?

    enum CodingKeys: String, CodingKey {
        case role, bytes, files, passwordOk = "password_ok", airplane, threema, idMatch = "id_match"
        case photosBytes = "photos_bytes", finishedAt = "finished_at", freshUntil = "fresh_until", iosBuild = "ios_build"
    }
}

public struct PrepareResult: Decodable, Equatable, Sendable {
    public let messages: Int
    public let media: Int
    public let skipped: Int
    public let payloadBytes: Int64
    public let iphoneRequiredBytes: Int64
    public let freshUntil: String

    enum CodingKeys: String, CodingKey {
        case messages, media, skipped, payloadBytes = "payload_bytes"
        case iphoneRequiredBytes = "iphone_required_bytes", freshUntil = "fresh_until"
    }
}

public struct PostcheckResult: Decodable, Equatable, Sendable {
    public struct Area: Decodable, Equatable, Sendable {
        public let area: String
        public let severity: String
    }
    public let verdict: String
    public let notes: [String]
    public let areas: [Area]
    public let threemaOk: Bool

    enum CodingKeys: String, CodingKey { case verdict, notes, areas, threemaOk = "threema_ok" }

    public var verdictValue: Verdict? { Verdict(rawValue: verdict) }
}

public struct SessionStatusResult: Decodable, Equatable, Sendable {
    public let phase: String
    public let resumeAt: String
    public let restoreSentAt: String?
    public let freshUntil: String?
    public let verdict: String?

    enum CodingKeys: String, CodingKey {
        case phase, resumeAt = "resume_at", restoreSentAt = "restore_sent_at", freshUntil = "fresh_until", verdict
    }

    public init(phase: String, resumeAt: String, restoreSentAt: String?, freshUntil: String?, verdict: String?) {
        self.phase = phase
        self.resumeAt = resumeAt
        self.restoreSentAt = restoreSentAt
        self.freshUntil = freshUntil
        self.verdict = verdict
    }
}

public struct CleanupResult: Decodable, Equatable, Sendable {
    public let freedBytes: Int64
    public let what: String?
    enum CodingKeys: String, CodingKey { case freedBytes = "freed_bytes", what }
}

public struct DiagReportResult: Decodable, Equatable, Sendable {
    public let file: String
    public let bytes: Int64
}

extension Verdict {
    /// Green verdicts lead to S19; there is no "yellow" (DESIGN §7).
    public var isGreen: Bool { self == .ok || self == .okWithNotes }
}
