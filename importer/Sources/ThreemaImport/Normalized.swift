// SPDX-License-Identifier: AGPL-3.0-or-later
// Normalized.swift - read-only access to work/<x>/normalized.sqlite (NORMALIZED CONTRACT).
import Foundation
import SQLite3

struct ImportError: Error, CustomStringConvertible {
    let description: String
    /// machine-readable reason for the engine (report "error_code", DESIGN §5.5 code mapping):
    /// "duplicate_1to1" -> E_IMPORT_DUPLICATE_CHAT, "model_incompatible" -> E_THREEMA_MODEL_UNKNOWN, else E_INTERNAL
    let code: String
    init(_ d: String, code: String = "import_failed") { description = d; self.code = code }
}

final class SQLiteDB {
    private var db: OpaquePointer?

    init(path: String) throws {
        guard sqlite3_open_v2(path, &db, SQLITE_OPEN_READONLY, nil) == SQLITE_OK else {
            throw ImportError("cannot open normalized db \(path)")
        }
    }

    deinit { sqlite3_close(db) }

    func tableExists(_ name: String) -> Bool {
        var found = false
        try? query("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", [name]) { _ in found = true }
        return found
    }

    func columns(of table: String) -> Set<String> {
        var cols = Set<String>()
        try? query("PRAGMA table_info(\(table))") { r in if let n = r.text("name") { cols.insert(n) } }
        return cols
    }

    func query(_ sql: String, _ binds: [String] = [], _ each: (Row) throws -> Void) throws {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK, let stmt else {
            throw ImportError("sqlite prepare failed: \(String(cString: sqlite3_errmsg(db)))")
        }
        defer { sqlite3_finalize(stmt) }
        let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
        for (i, b) in binds.enumerated() { sqlite3_bind_text(stmt, Int32(i + 1), b, -1, transient) }
        var names: [String: Int32] = [:]
        for i in 0..<sqlite3_column_count(stmt) { names[String(cString: sqlite3_column_name(stmt, i))] = i }
        let row = Row(stmt: stmt, names: names)
        while true {
            let rc = sqlite3_step(stmt)
            if rc == SQLITE_DONE { break }
            guard rc == SQLITE_ROW else { throw ImportError("sqlite step failed: \(String(cString: sqlite3_errmsg(db)))") }
            try autoreleasepool { try each(row) }
        }
    }
}

struct Row {
    let stmt: OpaquePointer
    let names: [String: Int32]

    private func idx(_ n: String) -> Int32? {
        guard let i = names[n], sqlite3_column_type(stmt, i) != SQLITE_NULL else { return nil }
        return i
    }
    func int(_ n: String) -> Int64? { idx(n).map { sqlite3_column_int64(stmt, $0) } }
    func double(_ n: String) -> Double? { idx(n).map { sqlite3_column_double(stmt, $0) } }
    func text(_ n: String) -> String? {
        guard let i = idx(n), let c = sqlite3_column_text(stmt, i) else { return nil }
        return String(cString: c)
    }
    func blob(_ n: String) -> Data? {
        guard let i = idx(n) else { return nil }
        if sqlite3_column_type(stmt, i) == SQLITE_TEXT {
            // tolerate hex text in BLOB columns
            return text(n).flatMap { Data(hex: $0) }
        }
        let len = Int(sqlite3_column_bytes(stmt, i))
        guard len > 0, let p = sqlite3_column_blob(stmt, i) else { return Data() }
        return Data(bytes: p, count: len)
    }
    func bool(_ n: String) -> Bool { (int(n) ?? 0) != 0 }
}

extension Data {
    init?(hex: String) {
        let s = hex.trimmingCharacters(in: .whitespaces)
        guard s.count % 2 == 0 else { return nil }
        var d = Data(capacity: s.count / 2)
        var it = s.makeIterator()
        while let a = it.next(), let b = it.next() {
            guard let v = UInt8(String([a, b]), radix: 16) else { return nil }
            d.append(v)
        }
        self = d
    }
    var hex: String { map { String(format: "%02x", $0) }.joined() }
}

func dateFromMs(_ ms: Int64?) -> Date? {
    guard let ms else { return nil }
    return Date(timeIntervalSince1970: Double(ms) / 1000.0)
}

// MARK: - contract rows

struct NContact {
    let identity: String
    let publicKey: Data
    let verification: Int
    let firstName, lastName, nickname: String?
    let hidden, archived: Bool
    let lastUpdateMs: Int64?
    let avatarUserPath, avatarContactPath: String?
}

struct NGroup {
    let groupKey: String
    let groupId: Data
    let creator: String
    let isMine: Bool
    let name: String?
    let createdMs, lastUpdateMs: Int64?
    let archived: Bool
    let userState: Int
    let members: [String]
    let avatarPath: String?
}

struct NMessage {
    let uid: String
    let chatKind: String
    let chatKey: String
    let apiId: Data?
    let msgId: Data
    let isOwn: Bool
    let sender: String?
    let kind: String
    let createdMs, postedMs, deliveredMs, readMs, modifiedMs, editedMs, deletedMs: Int64?
    let state: String?
    let isRead: Bool
    let starred: Bool
    let text: String?
    let quotedApiId: Data?
    let fileMime, fileName: String?
    let fileSize: Int64?
    let fileRender: Int64?
    let fileCaption: String?
    let fileBlobId, fileKey: Data?
    let fileThumbMime, fileMetaJson, mediaPath, mediaSha256, thumbPath: String?
    let locLat, locLon, locAcc: Double?
    let locName, locAddress: String?
    let ballotRef, ballotDataType: Int64?
    let callStatus, callReason, callDurationS, callId: Int64?
    let gstatusType: Int64?
    let gstatusIdentity, gstatusName: String?

    init(_ r: Row) {
        uid = r.text("uid") ?? ""
        chatKind = r.text("chat_kind") ?? ""
        chatKey = r.text("chat_key") ?? ""
        apiId = r.blob("api_id")
        msgId = r.blob("msg_id") ?? Data()
        isOwn = r.bool("is_own")
        sender = r.text("sender")
        kind = r.text("kind") ?? ""
        createdMs = r.int("created_ms"); postedMs = r.int("posted_ms"); deliveredMs = r.int("delivered_ms")
        readMs = r.int("read_ms"); modifiedMs = r.int("modified_ms"); editedMs = r.int("edited_ms")
        deletedMs = r.int("deleted_ms")
        state = r.text("state")
        isRead = r.bool("is_read")
        starred = r.bool("starred")
        text = r.text("text")
        quotedApiId = r.blob("quoted_api_id")
        fileMime = r.text("file_mime"); fileName = r.text("file_name"); fileSize = r.int("file_size")
        fileRender = r.int("file_render"); fileCaption = r.text("file_caption")
        fileBlobId = r.blob("file_blob_id"); fileKey = r.blob("file_key")
        fileThumbMime = r.text("file_thumb_mime"); fileMetaJson = r.text("file_meta_json")
        mediaPath = r.text("media_path"); mediaSha256 = r.text("media_sha256"); thumbPath = r.text("thumb_path")
        locLat = r.double("loc_lat"); locLon = r.double("loc_lon"); locAcc = r.double("loc_acc")
        locName = r.text("loc_name"); locAddress = r.text("loc_address")
        ballotRef = r.int("ballot_ref"); ballotDataType = r.int("ballot_data_type")
        callStatus = r.int("call_status"); callReason = r.int("call_reason")
        callDurationS = r.int("call_duration_s"); callId = r.int("call_id")
        gstatusType = r.int("gstatus_type"); gstatusIdentity = r.text("gstatus_identity"); gstatusName = r.text("gstatus_name")
    }
}

struct NReaction {
    let chatKind, chatKey: String
    let targetMsgId: Data
    let sender: String?
    let emoji: String
    let reactedMs: Int64?
    let source: String?
}

struct NBallot {
    let refId: Int64
    let apiId: Data?
    let creator: String?
    let chatKind, chatKey: String?
    let title, state, assessment, btype, choiceType: String?
    let createdMs, modifiedMs: Int64?
}

struct NBallotChoice { let ballotRef: Int64; let choiceId: Int64; let name: String?; let orderPos: Int64?; let voteCount: Int64?; let createdMs, modifiedMs: Int64? }
struct NBallotVote { let ballotRef: Int64; let choiceId: Int64; let identity: String; let choice: Int64; let createdMs, modifiedMs: Int64? }

final class Normalized {
    let db: SQLiteDB
    let path: String
    private(set) var meta: [String: String] = [:]

    init(path: String) throws {
        self.path = path
        db = try SQLiteDB(path: path)
        for t in ["meta", "contacts", "groups", "messages", "reactions", "ballots", "ballot_choices", "ballot_votes", "nonces"] {
            guard db.tableExists(t) else { throw ImportError("normalized db lacks table '\(t)' (contract violation)") }
        }
        try db.query("SELECT key, value FROM meta") { r in
            if let k = r.text("key") { meta[k] = r.text("value") }
        }
        let required: [String: [String]] = [
            "contacts": ["identity", "public_key", "verification", "first_name", "last_name", "nickname", "hidden", "archived",
                         "last_update_ms", "avatar_user_path", "avatar_contact_path"],
            "groups": ["group_key", "group_id", "creator", "is_mine", "name", "created_ms", "last_update_ms", "archived",
                       "user_state", "members_json", "avatar_path"],
            "messages": ["uid", "chat_kind", "chat_key", "api_id", "msg_id", "is_own", "sender", "kind", "created_ms",
                         "posted_ms", "delivered_ms", "read_ms", "modified_ms", "edited_ms", "deleted_ms", "state", "is_read",
                         "starred", "text", "quoted_api_id", "file_mime", "file_name", "file_size", "file_render",
                         "file_caption", "file_blob_id", "file_key", "file_thumb_mime", "file_meta_json", "media_path",
                         "media_sha256", "thumb_path", "loc_lat", "loc_lon", "loc_acc", "loc_name", "loc_address",
                         "ballot_ref", "ballot_data_type", "call_status", "call_reason", "call_duration_s", "call_id",
                         "gstatus_type", "gstatus_identity", "gstatus_name"],
            "reactions": ["chat_kind", "chat_key", "target_msg_id", "sender", "emoji", "reacted_ms", "source"],
            "nonces": ["kind", "hash"],
        ]
        for (t, cols) in required {
            let have = db.columns(of: t)
            let missing = cols.filter { !have.contains($0) }
            if !missing.isEmpty { throw ImportError("normalized table \(t) lacks columns \(missing)") }
        }
    }

    func contacts() throws -> [NContact] {
        var out: [NContact] = []
        try db.query("SELECT * FROM contacts ORDER BY identity") { r in
            out.append(NContact(identity: r.text("identity") ?? "", publicKey: r.blob("public_key") ?? Data(),
                                verification: Int(r.int("verification") ?? 0), firstName: r.text("first_name"),
                                lastName: r.text("last_name"), nickname: r.text("nickname"), hidden: r.bool("hidden"),
                                archived: r.bool("archived"), lastUpdateMs: r.int("last_update_ms"),
                                avatarUserPath: r.text("avatar_user_path"), avatarContactPath: r.text("avatar_contact_path")))
        }
        return out
    }

    func groups() throws -> [NGroup] {
        var out: [NGroup] = []
        try db.query("SELECT * FROM groups ORDER BY group_key") { r in
            var members: [String] = []
            if let j = r.text("members_json"), let d = j.data(using: .utf8),
               let arr = try? JSONSerialization.jsonObject(with: d) as? [String] {
                members = arr
            }
            out.append(NGroup(groupKey: r.text("group_key") ?? "", groupId: r.blob("group_id") ?? Data(),
                              creator: r.text("creator") ?? "", isMine: r.bool("is_mine"), name: r.text("name"),
                              createdMs: r.int("created_ms"), lastUpdateMs: r.int("last_update_ms"),
                              archived: r.bool("archived"), userState: Int(r.int("user_state") ?? 0), members: members,
                              avatarPath: r.text("avatar_path")))
        }
        return out
    }

    func messageCount() throws -> Int {
        var n = 0
        try db.query("SELECT count(*) AS n FROM messages") { r in n = Int(r.int("n") ?? 0) }
        return n
    }

    /// Streams messages grouped by chat, oldest first (date, posted, uid) - same order iOS uses for display.
    func forEachMessage(_ body: (NMessage) throws -> Void) throws {
        try db.query("""
            SELECT * FROM messages
            ORDER BY chat_kind, chat_key, created_ms IS NULL, created_ms, posted_ms, uid
            """) { r in try body(NMessage(r)) }
    }

    func reactions() throws -> [NReaction] {
        var out: [NReaction] = []
        try db.query("SELECT * FROM reactions ORDER BY chat_kind, chat_key, reacted_ms") { r in
            guard let t = r.blob("target_msg_id"), let e = r.text("emoji") else { return }
            out.append(NReaction(chatKind: r.text("chat_kind") ?? "", chatKey: r.text("chat_key") ?? "", targetMsgId: t,
                                 sender: r.text("sender"), emoji: e, reactedMs: r.int("reacted_ms"), source: r.text("source")))
        }
        return out
    }

    func ballots() throws -> [NBallot] {
        var out: [NBallot] = []
        try db.query("SELECT * FROM ballots ORDER BY ref_id") { r in
            out.append(NBallot(refId: r.int("ref_id") ?? 0, apiId: r.blob("api_id"), creator: r.text("creator"),
                               chatKind: r.text("chat_kind"), chatKey: r.text("chat_key"), title: r.text("title"),
                               state: r.text("state"), assessment: r.text("assessment"), btype: r.text("btype"),
                               choiceType: r.text("choice_type"), createdMs: r.int("created_ms"), modifiedMs: r.int("modified_ms")))
        }
        return out
    }

    func ballotChoices() throws -> [NBallotChoice] {
        var out: [NBallotChoice] = []
        try db.query("SELECT * FROM ballot_choices ORDER BY ballot_ref, order_pos, choice_id") { r in
            out.append(NBallotChoice(ballotRef: r.int("ballot_ref") ?? -1, choiceId: r.int("choice_id") ?? 0, name: r.text("name"),
                                     orderPos: r.int("order_pos"), voteCount: r.int("vote_count"),
                                     createdMs: r.int("created_ms"), modifiedMs: r.int("modified_ms")))
        }
        return out
    }

    func ballotVotes() throws -> [NBallotVote] {
        var out: [NBallotVote] = []
        try db.query("SELECT * FROM ballot_votes ORDER BY ballot_ref, choice_id, identity") { r in
            guard let ident = r.text("identity") else { return }
            out.append(NBallotVote(ballotRef: r.int("ballot_ref") ?? -1, choiceId: r.int("choice_id") ?? 0, identity: ident,
                                   choice: r.int("choice") ?? 0, createdMs: r.int("created_ms"), modifiedMs: r.int("modified_ms")))
        }
        return out
    }

    func forEachNonce(_ body: (String, Data) throws -> Void) throws {
        try db.query("SELECT kind, hash FROM nonces") { r in
            if let h = r.blob("hash") { try body(r.text("kind") ?? "", h) }
        }
    }
}
