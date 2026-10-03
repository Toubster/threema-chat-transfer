// SPDX-License-Identifier: AGPL-3.0-or-later
// make_empty_store.swift - test helper: an EMPTY Threema store for a compiled model, created by Core Data itself.
//   make_empty_store <momd> <out-dir>      -> <out-dir>/ThreemaData.sqlite (journal_mode=DELETE, no -wal/-shm)
// Synthetic: no rows except the Core Data metadata. Replaces the proof of concept's private reference store.
import CoreData
import Foundation

final class Stub: ValueTransformer {
    override class func transformedValueClass() -> AnyClass { NSData.self }
    override class func allowsReverseTransformation() -> Bool { true }
    override func transformedValue(_ value: Any?) -> Any? { value }
    override func reverseTransformedValue(_ value: Any?) -> Any? { value }
}
ValueTransformer.setValueTransformer(Stub(), forName: NSValueTransformerName("GroupDeliveryReceiptValueTransformer"))

let a = CommandLine.arguments
guard a.count == 3, let model = NSManagedObjectModel(contentsOf: URL(fileURLWithPath: a[1])) else {
    FileHandle.standardError.write("usage: make_empty_store <momd> <out-dir>\n".data(using: .utf8)!)
    exit(2)
}
let out = URL(fileURLWithPath: a[2], isDirectory: true)
try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true)
let url = out.appendingPathComponent("ThreemaData.sqlite")
if FileManager.default.fileExists(atPath: url.path) {
    FileHandle.standardError.write("refusing to overwrite an existing store\n".data(using: .utf8)!)
    exit(1)
}
let psc = NSPersistentStoreCoordinator(managedObjectModel: model)
let store = try psc.addPersistentStore(ofType: NSSQLiteStoreType, configurationName: nil, at: url,
                                       options: [NSSQLitePragmasOption: ["journal_mode": "DELETE"]])
try psc.remove(store)
print("ok")
