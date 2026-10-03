// SPDX-License-Identifier: AGPL-3.0-or-later
// Verify.swift - `threema-import verify`: re-open a store read-only with Core Data and check the invariants the
// importer promises (counts only, no content).
import CoreData
import Foundation

enum Verify {
    static func run(storeDir: URL, momd: URL, loadMedia: Bool) throws -> [String: Any] {
        let model = try StoreFiles.loadModel(momd)
        let url = storeDir.appendingPathComponent(StoreFiles.sqlite)
        let fm = FileManager.default
        var out: [String: Any] = [:]
        out["wal_present"] = fm.fileExists(atPath: url.path + "-wal")
        out["shm_present"] = fm.fileExists(atPath: url.path + "-shm")
        // A read-only open never writes -wal/-shm into a DELETE-mode file.
        let walPresent = fm.fileExists(atPath: url.path + "-wal")
        let (psc, md) = try StoreFiles.open(storeDir: storeDir, model: model,
                                            pragmas: walPresent ? nil : ["journal_mode": "DELETE"], readOnly: true)
        defer { try? StoreFiles.close(psc) }
        out["model_compatible"] = true
        out["store_version_identifiers"] = (md["NSStoreModelVersionIdentifiers"] as? [Any])?.map { "\($0)" } ?? []
        let ctx = NSManagedObjectContext(concurrencyType: .privateQueueConcurrencyType)
        ctx.persistentStoreCoordinator = psc
        var failure: Error?
        ctx.performAndWait {
            do {
                func count(_ e: String, _ p: String? = nil, _ args: [Any] = []) throws -> Int {
                    let f = NSFetchRequest<NSManagedObject>(entityName: e)
                    if let p { f.predicate = NSPredicate(format: p, argumentArray: args) }
                    return try ctx.count(for: f)
                }
                var counts: [String: Int] = [:]
                for e in ["Contact", "Conversation", "Group", "Message", "TextMessage", "FileMessage", "LocationMessage",
                          "BallotMessage", "SystemMessage", "ImageMessage", "VideoMessage", "AudioMessage", "FileData",
                          "ImageData", "MessageReaction", "MessageMarkers", "Ballot", "BallotChoice", "BallotResult", "Nonce"] {
                    counts[e] = try count(e)
                }
                out["counts"] = counts
                var inv: [String: Int] = [:]
                inv["own_not_sent"] = try count("Message", "isOwn == YES AND sent == NO")
                inv["send_failed"] = try count("Message", "sendFailed == YES")
                inv["incoming_unread"] = try count("Message", "isOwn == NO AND read == NO")
                inv["incoming_not_delivered"] = try count("Message", "isOwn == NO AND delivered == NO")
                inv["own_file_with_data_without_blobId"] = try count("FileMessage", "isOwn == YES AND dataAvailable == YES AND blobId == nil")
                inv["own_file_thumb_without_blobThumbnailId"] = try count("FileMessage", "isOwn == YES AND thumbnail != nil AND blobThumbnailId == nil")
                inv["own_file_blobThumbnailId_without_thumb"] = try count("FileMessage", "isOwn == YES AND thumbnail == nil AND blobThumbnailId != nil")
                inv["own_file_blobId_without_data"] = try count("FileMessage", "isOwn == YES AND blobId != nil AND dataAvailable == NO")
                inv["incoming_file_blobId_without_data"] = try count("FileMessage", "isOwn == NO AND blobId != nil AND dataAvailable == NO")
                inv["file_dataAvailable_without_data"] = try count("FileMessage", "dataAvailable == YES AND data == nil")
                inv["file_data_without_dataAvailable"] = try count("FileMessage", "dataAvailable == NO AND data != nil")
                inv["file_without_key_not_deleted"] = try count("FileMessage", "encryptionKey == nil AND deletedAt == nil")
                inv["conversation_listed"] = try count("Conversation", "lastUpdate != nil")
                inv["conversation_group_without_myIdentity"] = try count("Conversation", "groupId != nil AND groupMyIdentity == nil")
                inv["conversation_unread_nonzero"] = try count("Conversation", "unreadMessageCount != 0")

                // per-message checks: id length, (conversation,id) duplicates
                let mf = NSFetchRequest<NSDictionary>(entityName: "Message")
                mf.resultType = .dictionaryResultType
                mf.propertiesToFetch = ["id", "conversation"]
                var seen = Set<String>(), dup = 0, badLen = 0
                for d in try ctx.fetch(mf) {
                    guard let id = d["id"] as? Data, let c = d["conversation"] as? NSManagedObjectID else { badLen += 1; continue }
                    if id.count != 8 { badLen += 1 }
                    if !seen.insert(c.uriRepresentation().absoluteString + id.hex).inserted { dup += 1 }
                }
                inv["message_id_not_8_bytes"] = badLen
                inv["message_duplicate_conversation_id"] = dup

                // conversations: lastMessage belongs to conversation, has messages but no lastMessage
                var lmForeign = 0, hasMsgsNoLast = 0, listedWithoutLast = 0
                for c in try ctx.fetch(NSFetchRequest<NSManagedObject>(entityName: "Conversation")) {
                    let last = c.value(forKey: "lastMessage") as? NSManagedObject
                    if let last, (last.value(forKey: "conversation") as? NSManagedObject)?.objectID != c.objectID { lmForeign += 1 }
                    if last == nil {
                        if try count("Message", "conversation == %@", [c]) > 0 { hasMsgsNoLast += 1 }
                        if c.value(forKey: "lastUpdate") != nil { listedWithoutLast += 1 }
                    }
                }
                inv["conversation_lastMessage_foreign"] = lmForeign
                inv["conversation_has_messages_but_no_lastMessage"] = hasMsgsNoLast
                // normal iOS state (EntityCreator.conversationEntity sets lastUpdate = .now) -> info only
                out["info"] = ["conversation_listed_without_lastMessage": listedWithoutLast]

                // reactions uniqueness (creator, reaction, message)
                let rf = NSFetchRequest<NSManagedObject>(entityName: "MessageReaction")
                var rseen = Set<String>(), rdup = 0
                for r in try ctx.fetch(rf) {
                    let m = (r.value(forKey: "message") as? NSManagedObject)?.objectID.uriRepresentation().absoluteString ?? "-"
                    let c = (r.value(forKey: "creator") as? NSManagedObject)?.objectID.uriRepresentation().absoluteString ?? "me"
                    if !rseen.insert("\(m)|\(c)|\(r.value(forKey: "reaction") ?? "")").inserted { rdup += 1 }
                }
                inv["reaction_duplicates"] = rdup
                ctx.reset()

                // system message type histogram, file render histogram
                var sys: [String: Int] = [:]
                for s in try ctx.fetch(NSFetchRequest<NSManagedObject>(entityName: "SystemMessage")) {
                    sys["\((s.value(forKey: "type") as? NSNumber)?.intValue ?? -1)", default: 0] += 1
                }
                out["system_message_types"] = sys
                ctx.reset()

                // file messages: json decodes like FileMessageEntity.FileMessageJSON, media readable
                var files: [String: Int] = [:]
                var bytes = 0, unreadable = 0, badJSON = 0, thumbsBad = 0
                let ff = NSFetchRequest<NSManagedObject>(entityName: "FileMessage")
                ff.fetchBatchSize = 200
                var n = 0
                for f in try ctx.fetch(ff) {
                    autoreleasepool {
                        let mime = (f.value(forKey: "mimeType") as? String) ?? ""
                        let t = (f.value(forKey: "type") as? NSNumber)?.intValue ?? -1
                        files["\(mime.split(separator: "/").first ?? "-")/type\(t)/data\((f.value(forKey: "dataAvailable") as? NSNumber)?.intValue ?? 0)", default: 0] += 1
                        if let j = f.value(forKey: "json") as? String, !j.isEmpty {
                            if !decodesLikeIOS(j) { badJSON += 1 }
                        }
                        if let th = f.value(forKey: "thumbnail") as? NSManagedObject {
                            let w = (th.value(forKey: "width") as? NSNumber)?.intValue ?? 0
                            let h = (th.value(forKey: "height") as? NSNumber)?.intValue ?? 0
                            if w <= 0 || h <= 0 || (th.value(forKey: "data") as? Data)?.isEmpty != false { thumbsBad += 1 }
                        }
                        if loadMedia, let fd = f.value(forKey: "data") as? NSManagedObject {
                            if let d = fd.value(forKey: "data") as? Data, !d.isEmpty { bytes += d.count } else { unreadable += 1 }
                        }
                    }
                    n += 1
                    if n % 200 == 0 { ctx.reset() }
                }
                inv["file_json_not_decodable"] = badJSON
                inv["thumbnail_bad"] = thumbsBad
                if loadMedia {
                    inv["file_data_unreadable"] = unreadable
                    out["file_data_bytes"] = bytes
                }
                out["file_messages"] = files
                out["invariants"] = inv
            } catch { failure = error }
        }
        if let failure { throw failure }
        let ext = storeDir.appendingPathComponent(StoreFiles.support).appendingPathComponent("_EXTERNAL_DATA")
        out["external_data_files"] = (try? fm.contentsOfDirectory(atPath: ext.path).count) ?? 0
        return out
    }

    /// Mirrors FileMessageEntity.FileMessageJSON / FileMessageMetadataJSON (FileMessageEntity.swift:20-45).
    struct MetaJSON: Codable { var h: Int?; var w: Int?; var d: Double? }
    struct FileJSON: Codable { var c: String?; var p: String?; var d: String?; var x: MetaJSON? }
    static func decodesLikeIOS(_ s: String) -> Bool {
        (try? JSONDecoder().decode(FileJSON.self, from: Data(s.utf8))) != nil
    }
}
