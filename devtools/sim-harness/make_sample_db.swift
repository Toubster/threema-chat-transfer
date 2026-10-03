// SPDX-License-Identifier: AGPL-3.0-or-later
// Creates a SYNTHETIC Threema iOS store (no personal data) with the compiled Core Data model, to smoke-test the
// simulator harness and as a reference for how rows must look when written through Core Data itself.
//
//   xcrun swift make_sample_db.swift <ThreemaDataV56.mom> <out-dir> <own-identity>
//
// Optional env SAMPLE_MSGS=N: messages per 1:1 chat (default 3; e.g. 60 to exercise THREEMA_SIM_SCROLL_UP/PAGES).
// Writes <out-dir>/ThreemaData.sqlite (journal_mode=DELETE => single file; the app re-opens it in WAL mode).
// Using Core Data (not raw SQL) guarantees Z_METADATA / Z_PRIMARYKEY / Z_ENT and model hashes match V56, so
// DatabaseManager.storeRequiresMigration() == .none.
import CoreData
import Foundation

let args = CommandLine.arguments
guard args.count == 4, let model = NSManagedObjectModel(contentsOf: URL(fileURLWithPath: args[1])) else {
    fatalError("usage: make_sample_db.swift <ThreemaDataV56.mom> <out-dir> <own-identity>")
}
let outDir = URL(fileURLWithPath: args[2], isDirectory: true)
let myID = args[3]
try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
let storeURL = outDir.appendingPathComponent("ThreemaData.sqlite")
for s in ["", "-wal", "-shm"] { try? FileManager.default.removeItem(atPath: storeURL.path + s) }

let psc = NSPersistentStoreCoordinator(managedObjectModel: model)
try psc.addPersistentStore(
    ofType: NSSQLiteStoreType, configurationName: nil, at: storeURL,
    options: [NSSQLitePragmasOption: ["journal_mode": "DELETE"]]
)
let ctx = NSManagedObjectContext(concurrencyType: .privateQueueConcurrencyType)
ctx.persistentStoreCoordinator = psc

func rnd(_ n: Int) -> Data { Data((0..<n).map { _ in UInt8.random(in: 0...255) }) }

ctx.performAndWait {
    func insert(_ entity: String) -> NSManagedObject {
        NSEntityDescription.insertNewObject(forEntityName: entity, into: ctx)
    }
    func contact(_ id: String, _ nick: String) -> NSManagedObject {
        let c = insert("Contact")
        c.setValue(id, forKey: "identity")
        c.setValue(rnd(32), forKey: "publicKey")
        c.setValue(nick, forKey: "publicNickname")
        c.setValue(Int16(0), forKey: "state")            // ContactState.active
        c.setValue(Int16(0), forKey: "verificationLevel")
        c.setValue(Int64(1), forKey: "featureMask")
        c.setValue(Date(), forKey: "createdAt")
        return c
    }
    func text(_ conv: NSManagedObject, _ body: String, own: Bool, sender: NSManagedObject?, at date: Date) -> NSManagedObject {
        let m = insert("TextMessage")
        m.setValue(rnd(8), forKey: "id")                  // 8-byte message ID
        m.setValue(body, forKey: "text")
        m.setValue(own, forKey: "isOwn")
        m.setValue(date, forKey: "date")
        m.setValue(own ? nil : date, forKey: "remoteSentDate")
        m.setValue(true, forKey: "delivered")
        m.setValue(true, forKey: "read")
        m.setValue(true, forKey: "sent")
        m.setValue(false, forKey: "userack")
        m.setValue(conv, forKey: "conversation")
        m.setValue(sender, forKey: "sender")              // nil for own msgs; in groups = member contact
        return m
    }
    let now = Date()
    let a = contact("TESTAAAA", "Sample A"), b = contact("TESTBBBB", "Sample B")
    for (i, c) in [a, b].enumerated() {
        let conv = insert("Conversation")
        conv.setValue(c, forKey: "contact")
        var last: NSManagedObject?
        let n = Int(ProcessInfo.processInfo.environment["SAMPLE_MSGS"] ?? "") ?? 3
        for k in 0..<n {
            let d = now.addingTimeInterval(Double(-3600 * (10 - i) - 60 * n + 60 * k))
            last = text(conv, "Synthetic message \(k + 1)", own: k % 2 == 1, sender: k % 2 == 1 ? nil : c, at: d)
        }
        conv.setValue(last, forKey: "lastMessage")
        conv.setValue(last?.value(forKey: "date"), forKey: "lastUpdate")   // chat list predicate: lastUpdate != nil
    }
    // group created by A, me + B members
    let gid = rnd(8)
    let g = insert("Group")
    g.setValue(gid, forKey: "groupId")
    g.setValue("TESTAAAA", forKey: "groupCreator")      // nil if I am the creator
    g.setValue(Int16(0), forKey: "state")                // GroupState.active
    let gc = insert("Conversation")
    gc.setValue(gid, forKey: "groupId")
    gc.setValue("Sample group", forKey: "groupName")
    gc.setValue(myID, forKey: "groupMyIdentity")        // must equal the simulator/phone identity
    gc.setValue(a, forKey: "contact")                   // creator contact (nil if own group)
    gc.mutableSetValue(forKey: "members").addObjects(from: [a, b])
    let gm = text(gc, "Synthetic group message", own: false, sender: b, at: now)
    gc.setValue(gm, forKey: "lastMessage")
    gc.setValue(now, forKey: "lastUpdate")
    do { try ctx.save() } catch { fatalError("save failed: \(error)") }
}
let md = try NSPersistentStoreCoordinator.metadataForPersistentStore(ofType: NSSQLiteStoreType, at: storeURL, options: nil)
print("wrote \(storeURL.path); compatible with model: \(model.isConfiguration(withName: nil, compatibleWithStoreMetadata: md))")
