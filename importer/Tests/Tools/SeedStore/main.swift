// SPDX-License-Identifier: AGPL-3.0-or-later
// seed_store.swift - builds a SYNTHETIC "Safe-restored iPhone" store for importer tests:
//   seed_store <momd> <empty-store-dir> <out-dir> <spec.json>
// spec: {"own": ID, "contacts":[{"identity","publicKey"(hex),"firstName"}], "groups":[{"groupId"(hex),"creator"|null,
//        "members":[ids]}], "messages":[{"contact":ID,"id"(hex),"text","dateMs","isOwn"}],
//        "duplicateConversations":[ID] (optional: a second 1:1 conversation for that contact)}
// The store is left in WAL mode with a non-empty -wal (like a live iPhone store) - the importer must cope with that.
import CoreData
import Foundation

final class Stub: ValueTransformer {
    override class func transformedValueClass() -> AnyClass { NSData.self }
    override class func allowsReverseTransformation() -> Bool { true }
    override func transformedValue(_ value: Any?) -> Any? { value }
    override func reverseTransformedValue(_ value: Any?) -> Any? { value }
}
ValueTransformer.setValueTransformer(Stub(), forName: NSValueTransformerName("GroupDeliveryReceiptValueTransformer"))

extension Data {
    init(hex: String) {
        var d = Data(); var it = hex.makeIterator()
        while let a = it.next(), let b = it.next() { d.append(UInt8(String([a, b]), radix: 16)!) }
        self = d
    }
}

let a = CommandLine.arguments
guard a.count == 5, let model = NSManagedObjectModel(contentsOf: URL(fileURLWithPath: a[1])) else {
    fatalError("usage: seed_store <momd> <empty-store-dir> <out-dir> <spec.json>")
}
let out = URL(fileURLWithPath: a[3], isDirectory: true)
try? FileManager.default.removeItem(at: out)
try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
try FileManager.default.copyItem(at: URL(fileURLWithPath: a[2]).appendingPathComponent("ThreemaData.sqlite"),
                                  to: out.appendingPathComponent("ThreemaData.sqlite"))
let spec = try JSONSerialization.jsonObject(with: Data(contentsOf: URL(fileURLWithPath: a[4]))) as! [String: Any]
let own = spec["own"] as! String
let psc = NSPersistentStoreCoordinator(managedObjectModel: model)
let store = try psc.addPersistentStore(ofType: NSSQLiteStoreType, configurationName: nil,
                                       at: out.appendingPathComponent("ThreemaData.sqlite"), options: nil)
let ctx = NSManagedObjectContext(concurrencyType: .privateQueueConcurrencyType)
ctx.persistentStoreCoordinator = psc
ctx.performAndWait {
    var contacts: [String: NSManagedObject] = [:]
    for c in spec["contacts"] as! [[String: Any]] {
        let o = NSEntityDescription.insertNewObject(forEntityName: "Contact", into: ctx)
        o.setValue(c["identity"], forKey: "identity")
        o.setValue(Data(hex: c["publicKey"] as! String), forKey: "publicKey")
        o.setValue(c["firstName"], forKey: "firstName")
        o.setValue(NSNumber(value: Int16(0)), forKey: "verificationLevel")
        o.setValue(NSNumber(value: Int16(0)), forKey: "state")
        contacts[c["identity"] as! String] = o
    }
    for g in spec["groups"] as! [[String: Any]] {
        let gid = Data(hex: g["groupId"] as! String)
        let creator = g["creator"] as? String
        let ge = NSEntityDescription.insertNewObject(forEntityName: "Group", into: ctx)
        ge.setValue(gid, forKey: "groupId")
        ge.setValue(creator, forKey: "groupCreator")
        ge.setValue(NSNumber(value: Int16(0)), forKey: "state")
        let conv = NSEntityDescription.insertNewObject(forEntityName: "Conversation", into: ctx)
        conv.setValue(gid, forKey: "groupId")
        conv.setValue("Seeded group", forKey: "groupName")
        conv.setValue(own, forKey: "groupMyIdentity")
        conv.setValue(creator.flatMap { contacts[$0] }, forKey: "contact")
        for m in g["members"] as? [String] ?? [] { if let c = contacts[m] { conv.mutableSetValue(forKey: "members").add(c) } }
        // Safe-restored group without history: no lastUpdate / lastMessage
    }
    var convs: [String: NSManagedObject] = [:]
    for m in spec["messages"] as! [[String: Any]] {
        let who = m["contact"] as! String
        let conv = convs[who] ?? {
            let c = NSEntityDescription.insertNewObject(forEntityName: "Conversation", into: ctx)
            c.setValue(contacts[who], forKey: "contact")
            convs[who] = c
            return c
        }()
        let msg = NSEntityDescription.insertNewObject(forEntityName: "TextMessage", into: ctx)
        let date = Date(timeIntervalSince1970: (m["dateMs"] as! Double) / 1000)
        msg.setValue(Data(hex: m["id"] as! String), forKey: "id")
        msg.setValue(m["text"], forKey: "text")
        msg.setValue(date, forKey: "date")
        msg.setValue(date, forKey: "remoteSentDate")
        let isOwn = m["isOwn"] as! Bool
        msg.setValue(isOwn, forKey: "isOwn")
        msg.setValue(true, forKey: "sent")
        msg.setValue(true, forKey: "delivered")
        msg.setValue(!isOwn ? false : true, forKey: "read")   // one unread incoming -> unreadMessageCount 1
        msg.setValue(false, forKey: "userack")
        msg.setValue(conv, forKey: "conversation")
        if (conv.value(forKey: "lastUpdate") as? Date ?? .distantPast) < date {
            conv.setValue(msg, forKey: "lastMessage")
            conv.setValue(date, forKey: "lastUpdate")
        }
        conv.setValue(NSNumber(value: Int32(1)), forKey: "unreadMessageCount")
    }
    // review m8: optional extra (duplicate) 1:1 conversations for the same contact
    for who in spec["duplicateConversations"] as? [String] ?? [] {
        guard let c = contacts[who] else { continue }
        let dup = NSEntityDescription.insertNewObject(forEntityName: "Conversation", into: ctx)
        dup.setValue(c, forKey: "contact")
    }
    try! ctx.save()
}
_ = store
// no psc.remove -> the WAL stays (process exits without checkpoint of the last frames on purpose)
let wal = out.appendingPathComponent("ThreemaData.sqlite-wal").path
let size = (try? FileManager.default.attributesOfItem(atPath: wal)[.size] as? Int) ?? 0
print("seeded \(out.path) wal_bytes=\(size)")
exit(0)
