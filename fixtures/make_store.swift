// SPDX-License-Identifier: AGPL-3.0-or-later
// make_store.swift - SYNTHETIC Threema iOS Core Data stores (model V56) for fixtures and the virtual iPhone.
// Replaces the private ref/sample-store + ref/empty-store: everything is created here from model/V56 with Core Data.
//
//   make_store <momd> <out-dir> empty                 empty V56 store (no rows), WAL checkpointed, no -wal/-shm
//   make_store <momd> <out-dir> seed <spec.json>      seeded store, left in WAL mode with a live -wal
//
// spec (all values synthetic, identities start with ZZ):
//   {"own": ID,                                       own identity (groupMyIdentity of every group conversation)
//    "contacts": [{"identity","publicKey"(hex 64),"firstName"?,"lastName"?,"hidden"?,"lastUpdateMs"?}],
//    "groups":   [{"groupId"(hex 16),"creator"(ID; own ID for own groups),"name"?,"members":[IDs incl. own]}],
//    "oneToOne": [ID, ...]                             empty 1:1 conversations (like a Threema Safe restore)
//    "duplicateConversations": [ID, ...]               a SECOND 1:1 conversation for that contact (duplicate_chat)
//    "messages": [{"chat":"contact:<ID>"|"group:<hex>","id"(hex 16),"text","dateMs","isOwn"}],
//    "fileMessage": {"chat":"contact:<ID>","bytes":N}  one FileMessage whose FileData is > 100 KB (_EXTERNAL_DATA)}
//
// Shapes follow importer/Tests/safe_seed.swift (Threema Safe restore) and the store options of the Threema app
// (auto migration + inferred mapping). Prints one line "ok <counts-json>"; never prints identities or texts.
import CoreData
import Foundation

final class PassThrough: ValueTransformer {
    override class func transformedValueClass() -> AnyClass { NSData.self }
    override class func allowsReverseTransformation() -> Bool { true }
    override func transformedValue(_ value: Any?) -> Any? { value }
    override func reverseTransformedValue(_ value: Any?) -> Any? { value }
}
ValueTransformer.setValueTransformer(PassThrough(), forName: NSValueTransformerName("GroupDeliveryReceiptValueTransformer"))

extension Data {
    init(hex: String) {
        var d = Data(); var it = hex.makeIterator()
        while let a = it.next(), let b = it.next() { d.append(UInt8(String([a, b]), radix: 16)!) }
        self = d
    }
}

func die(_ msg: String) -> Never {
    FileHandle.standardError.write(("make_store: " + msg + "\n").data(using: .utf8)!)
    exit(2)
}

let args = CommandLine.arguments
guard args.count >= 4, let model = NSManagedObjectModel(contentsOf: URL(fileURLWithPath: args[1])) else {
    die("usage: make_store <momd> <out-dir> empty | seed <spec.json>")
}
let out = URL(fileURLWithPath: args[2], isDirectory: true)
let mode = args[3]
try? FileManager.default.removeItem(at: out)
try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
let storeURL = out.appendingPathComponent("ThreemaData.sqlite")
let psc = NSPersistentStoreCoordinator(managedObjectModel: model)
let options: [AnyHashable: Any] = [NSMigratePersistentStoresAutomaticallyOption: true,
                                   NSInferMappingModelAutomaticallyOption: true]
let store: NSPersistentStore
do {
    store = try psc.addPersistentStore(ofType: NSSQLiteStoreType, configurationName: nil, at: storeURL, options: options)
} catch { die("addPersistentStore failed: \(type(of: error))") }

var counts: [String: Int] = [:]
func inc(_ k: String, _ n: Int = 1) { counts[k, default: 0] += n }

if mode == "empty" {
    // checkpoint: removing the store from the coordinator folds the WAL into the main file
    try? psc.remove(store)
    for s in ["-wal", "-shm"] { try? FileManager.default.removeItem(atPath: storeURL.path + s) }
    print("ok {}")
    exit(0)
}
guard mode == "seed", args.count == 5,
      let specData = FileManager.default.contents(atPath: args[4]),
      let spec = try? JSONSerialization.jsonObject(with: specData) as? [String: Any],
      let own = spec["own"] as? String else { die("seed needs a readable spec with 'own'") }

let ctx = NSManagedObjectContext(concurrencyType: .privateQueueConcurrencyType)
ctx.persistentStoreCoordinator = psc
let base = Date(timeIntervalSince1970: 1_788_000_000)     // fixed: deterministic fixtures
func date(_ v: Any?) -> Date? { (v as? NSNumber).map { Date(timeIntervalSince1970: $0.doubleValue / 1000) } }
var saveError: Error?

ctx.performAndWait {
    func new(_ e: String) -> NSManagedObject { NSEntityDescription.insertNewObject(forEntityName: e, into: ctx) }
    var contacts: [String: NSManagedObject] = [:]
    var oneToOne: [String: NSManagedObject] = [:]
    var groups: [String: NSManagedObject] = [:]

    func conversation(for c: NSManagedObject, lastUpdate: Date?) -> NSManagedObject {
        let conv = new("Conversation")
        conv.setValue(c, forKey: "contact")
        conv.setValue(lastUpdate, forKey: "lastUpdate")
        conv.setValue(NSNumber(value: Int16(0)), forKey: "category")
        conv.setValue(NSNumber(value: Int16(0)), forKey: "visibility")
        conv.setValue(NSNumber(value: Int32(0)), forKey: "unreadMessageCount")
        conv.setValue(NSNumber(value: false), forKey: "marked")
        return conv
    }

    for c in spec["contacts"] as? [[String: Any]] ?? [] {
        guard let id = c["identity"] as? String, id != own, let pk = c["publicKey"] as? String else { continue }
        let o = new("Contact")
        o.setValue(id, forKey: "identity")
        o.setValue(Data(hex: pk), forKey: "publicKey")
        o.setValue(NSNumber(value: Int16(0)), forKey: "verificationLevel")
        o.setValue(c["firstName"] as? String, forKey: "firstName")
        o.setValue(c["lastName"] as? String, forKey: "lastName")
        let initial = String((c["lastName"] as? String ?? c["firstName"] as? String ?? id).prefix(1)).uppercased()
        o.setValue(initial.isEmpty ? "#" : initial, forKey: "sortInitial")
        o.setValue(NSNumber(value: Int32(Int(initial.unicodeScalars.first?.value ?? 91) - 65).clamped(0, 26)),
                   forKey: "sortIndex")
        o.setValue(NSNumber(value: Int16((c["hidden"] as? Bool ?? false) ? 1 : 0)), forKey: "hidden")
        o.setValue(NSNumber(value: Int16(0)), forKey: "readReceipts")
        o.setValue(NSNumber(value: Int16(0)), forKey: "typingIndicators")
        o.setValue(NSNumber(value: Int16(0)), forKey: "workContact")
        o.setValue(NSNumber(value: Int64(255)), forKey: "featureMask")
        o.setValue(NSNumber(value: Int16(0)), forKey: "state")
        o.setValue(NSNumber(value: Int16(0)), forKey: "forwardSecurityState")
        contacts[id] = o
        inc("contacts")
    }
    for id in spec["oneToOne"] as? [String] ?? [] {
        guard let c = contacts[id] else { continue }
        oneToOne[id] = conversation(for: c, lastUpdate: base)
        inc("conversations_1to1")
    }
    for id in spec["duplicateConversations"] as? [String] ?? [] {
        guard let c = contacts[id] else { continue }
        if oneToOne[id] == nil { oneToOne[id] = conversation(for: c, lastUpdate: base); inc("conversations_1to1") }
        _ = conversation(for: c, lastUpdate: base.addingTimeInterval(60))
        inc("conversations_duplicate")
    }
    for g in spec["groups"] as? [[String: Any]] ?? [] {
        guard let hex = g["groupId"] as? String, let creator = g["creator"] as? String else { continue }
        let gid = Data(hex: hex)
        let isOwnGroup = creator == own
        if !isOwnGroup && contacts[creator] == nil { inc("groups_skipped_creator_missing"); continue }
        let ge = new("Group")
        ge.setValue(gid, forKey: "groupId")
        ge.setValue(NSNumber(value: Int16(0)), forKey: "state")
        ge.setValue(isOwnGroup ? nil : creator, forKey: "groupCreator")
        ge.setValue(base, forKey: "lastPeriodicSync")
        let conv = new("Conversation")
        conv.setValue(gid, forKey: "groupId")
        conv.setValue(own, forKey: "groupMyIdentity")
        conv.setValue(isOwnGroup ? nil : contacts[creator], forKey: "contact")
        conv.setValue(g["name"] as? String, forKey: "groupName")
        conv.setValue(NSNumber(value: Int16(0)), forKey: "category")
        conv.setValue(NSNumber(value: Int16(0)), forKey: "visibility")
        conv.setValue(NSNumber(value: Int32(0)), forKey: "unreadMessageCount")
        conv.setValue(NSNumber(value: false), forKey: "marked")
        conv.setValue(base, forKey: "lastUpdate")
        let mset = conv.mutableSetValue(forKey: "members")
        var all = Set((g["members"] as? [String] ?? []).map { $0.uppercased() })
        all.insert(creator)
        for m in all.sorted() where m != own { if let c = contacts[m] { mset.add(c) } }
        groups[hex.lowercased()] = conv
        inc("groups")
    }
    func chat(_ key: String) -> NSManagedObject? {
        if key.hasPrefix("contact:") {
            let id = String(key.dropFirst(8))
            if let cv = oneToOne[id] { return cv }
            guard let c = contacts[id] else { return nil }
            let cv = conversation(for: c, lastUpdate: base)
            oneToOne[id] = cv
            return cv
        }
        if key.hasPrefix("group:") { return groups[String(key.dropFirst(6)).lowercased()] }
        return nil
    }
    for m in spec["messages"] as? [[String: Any]] ?? [] {
        guard let key = m["chat"] as? String, let conv = chat(key), let idHex = m["id"] as? String else {
            inc("messages_skipped"); continue
        }
        let d = date(m["dateMs"]) ?? base
        let isOwn = m["isOwn"] as? Bool ?? false
        let msg = new("TextMessage")
        msg.setValue(Data(hex: idHex), forKey: "id")
        msg.setValue(m["text"] as? String ?? "", forKey: "text")
        msg.setValue(d, forKey: "date"); msg.setValue(d, forKey: "remoteSentDate")
        msg.setValue(isOwn, forKey: "isOwn"); msg.setValue(true, forKey: "sent"); msg.setValue(true, forKey: "delivered")
        msg.setValue(true, forKey: "read"); msg.setValue(false, forKey: "userack")
        if !isOwn, key.hasPrefix("contact:"), let c = contacts[String(key.dropFirst(8))] { msg.setValue(c, forKey: "sender") }
        msg.setValue(conv, forKey: "conversation")
        conv.setValue(d, forKey: "lastUpdate")
        conv.setValue(msg, forKey: "lastMessage")
        inc("messages")
    }
    if let fm = spec["fileMessage"] as? [String: Any], let key = fm["chat"] as? String, let conv = chat(key) {
        let n = (fm["bytes"] as? Int) ?? 300_000
        var gen = SystemRandomNumberGenerator()
        let msg = new("FileMessage")
        msg.setValue(Data((0..<8).map { _ in UInt8.random(in: 0...255, using: &gen) }), forKey: "id")
        msg.setValue(base, forKey: "date"); msg.setValue(base, forKey: "remoteSentDate")
        msg.setValue(true, forKey: "isOwn"); msg.setValue(true, forKey: "delivered"); msg.setValue(true, forKey: "read")
        msg.setValue(true, forKey: "sent"); msg.setValue(false, forKey: "userack")
        msg.setValue(true, forKey: "dataAvailable")
        msg.setValue("application/octet-stream", forKey: "mimeType")
        msg.setValue("fixture.bin", forKey: "fileName")
        msg.setValue(NSNumber(value: n), forKey: "fileSize")
        msg.setValue(NSNumber(value: 0), forKey: "type")
        // a real sent file has its blob id and key (Core Data invariants checked by verify_import)
        msg.setValue(Data((0..<16).map { _ in UInt8.random(in: 0...255, using: &gen) }), forKey: "blobId")
        msg.setValue(Data((0..<32).map { _ in UInt8.random(in: 0...255, using: &gen) }), forKey: "encryptionKey")
        msg.setValue(conv, forKey: "conversation")
        let fdata = new("FileData")
        fdata.setValue(Data((0..<n).map { _ in UInt8.random(in: 0...255, using: &gen) }), forKey: "data")
        msg.setValue(fdata, forKey: "data")
        inc("file_messages")
    }
    do { try ctx.save() } catch { saveError = error }
}
if let saveError { die("save failed: \(type(of: saveError)) \((saveError as NSError).code)") }
_ = store
// no psc.remove: the -wal stays, like the store of a running app
let cj = (try? JSONSerialization.data(withJSONObject: counts, options: [.sortedKeys])).flatMap { String(data: $0, encoding: .utf8) } ?? "{}"
print("ok \(cj)")
exit(0)

extension Comparable {
    func clamped(_ lo: Self, _ hi: Self) -> Self { min(max(self, lo), hi) }
}
