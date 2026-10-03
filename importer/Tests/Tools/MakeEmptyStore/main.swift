// SPDX-License-Identifier: AGPL-3.0-or-later
// make-empty-store - test tool: an EMPTY Threema iOS Core Data store for a compiled model, the way
// ThreemaFramework/Persistence/DatabaseManager.swift creates one (plain NSPersistentStoreCoordinator,
// NSSQLiteStoreType, automatic + inferred migration). No rows, no data of any kind.
//
//   make-empty-store <ThreemaData.momd | ThreemaDataVnn.mom> <out-dir>     -> <out-dir>/ThreemaData.sqlite
//
// Replaces the private ref/empty-store (never copied, DESIGN §4.3): the importer checks build their input store
// with this tool from model/V56, and a V55 store from ThreemaDataV55.mom for the "incompatible model" check.
import CoreData
import Foundation

final class GroupDeliveryReceiptValueTransformerStub: ValueTransformer {
    override class func transformedValueClass() -> AnyClass { NSData.self }
    override class func allowsReverseTransformation() -> Bool { true }
    override func transformedValue(_ value: Any?) -> Any? { value }
    override func reverseTransformedValue(_ value: Any?) -> Any? { value }
}

func die(_ msg: String, _ code: Int32 = 1) -> Never {
    FileHandle.standardError.write(("make-empty-store: " + msg + "\n").data(using: .utf8)!)
    exit(code)
}

let args = CommandLine.arguments
guard args.count == 3 else { die("usage: make-empty-store <momd|mom> <out-dir>", 2) }
ValueTransformer.setValueTransformer(GroupDeliveryReceiptValueTransformerStub(),
                                     forName: NSValueTransformerName("GroupDeliveryReceiptValueTransformer"))
guard let model = NSManagedObjectModel(contentsOf: URL(fileURLWithPath: args[1])) else { die("cannot load model") }
let dir = URL(fileURLWithPath: args[2], isDirectory: true)
let store = dir.appendingPathComponent("ThreemaData.sqlite")
let fm = FileManager.default
if fm.fileExists(atPath: dir.path) { die("refusing to overwrite an existing directory") }
do { try fm.createDirectory(at: dir, withIntermediateDirectories: true) } catch { die("mkdir: \(error)") }

let psc = NSPersistentStoreCoordinator(managedObjectModel: model)
let opts: [AnyHashable: Any] = [NSMigratePersistentStoresAutomaticallyOption: true,
                                NSInferMappingModelAutomaticallyOption: true]
do {
    let s = try psc.addPersistentStore(ofType: NSSQLiteStoreType, configurationName: nil, at: store, options: opts)
    try psc.remove(s)
} catch { die("create store: \(error)") }
let ids = model.versionIdentifiers.map { "\($0)" }.sorted().joined(separator: ",")
print("ok entities=\(model.entities.count) version_identifiers=\(ids)")
