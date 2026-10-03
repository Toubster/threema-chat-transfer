// SPDX-License-Identifier: AGPL-3.0-or-later
// Store.swift - copy / open (no migration) / fold WAL for ThreemaData.sqlite.
import CoreData
import Foundation

/// Pass-through stand-in for ThreemaFramework's GroupDeliveryReceiptValueTransformer (Message.groupDeliveryReceipts).
/// The importer never sets that attribute; existing values are only read back as opaque Data.
final class GroupDeliveryReceiptValueTransformerStub: ValueTransformer {
    override class func transformedValueClass() -> AnyClass { NSData.self }
    override class func allowsReverseTransformation() -> Bool { true }
    override func transformedValue(_ value: Any?) -> Any? { value }
    override func reverseTransformedValue(_ value: Any?) -> Any? { value }
}

enum StoreFiles {
    static let sqlite = "ThreemaData.sqlite"
    static let support = ".ThreemaData_SUPPORT"
    static let members = [sqlite, sqlite + "-wal", sqlite + "-shm", support]

    static func registerTransformer() {
        ValueTransformer.setValueTransformer(GroupDeliveryReceiptValueTransformerStub(),
                                             forName: NSValueTransformerName("GroupDeliveryReceiptValueTransformer"))
    }

    /// Copies ThreemaData.sqlite[-wal/-shm] + .ThreemaData_SUPPORT from `from` into `to` (created if needed).
    static func copyStore(from: URL, to: URL, overwrite: Bool) throws {
        let fm = FileManager.default
        guard fm.fileExists(atPath: from.appendingPathComponent(sqlite).path) else {
            throw ImportError("store-in has no \(sqlite)")
        }
        if from.standardizedFileURL.resolvingSymlinksInPath() == to.standardizedFileURL.resolvingSymlinksInPath() {
            throw ImportError("store-out must differ from store-in (the input store is never modified)")
        }
        try fm.createDirectory(at: to, withIntermediateDirectories: true)
        for m in members where fm.fileExists(atPath: to.appendingPathComponent(m).path) {
            guard overwrite else { throw ImportError("store-out already contains \(m); pass --overwrite") }
            try fm.removeItem(at: to.appendingPathComponent(m))
        }
        for m in members where fm.fileExists(atPath: from.appendingPathComponent(m).path) {
            try fm.copyItem(at: from.appendingPathComponent(m), to: to.appendingPathComponent(m))
        }
    }

    static func loadModel(_ momd: URL) throws -> NSManagedObjectModel {
        guard let model = NSManagedObjectModel(contentsOf: momd) else { throw ImportError("cannot load model \(momd.path)") }
        return model
    }

    /// Opens the store WITHOUT migration. Aborts if the store metadata does not match the model exactly.
    static func open(storeDir: URL, model: NSManagedObjectModel, pragmas: [String: String]? = nil,
                     readOnly: Bool = false) throws -> (NSPersistentStoreCoordinator, [String: Any]) {
        let url = storeDir.appendingPathComponent(sqlite)
        let md = try NSPersistentStoreCoordinator.metadataForPersistentStore(ofType: NSSQLiteStoreType, at: url, options: nil)
        guard model.isConfiguration(withName: nil, compatibleWithStoreMetadata: md) else {
            let ids = (md["NSStoreModelVersionIdentifiers"] as? [Any]).map { "\($0)" } ?? "?"
            throw ImportError("""
                store metadata is NOT compatible with the model (store version identifiers \(ids), model \(model.versionIdentifiers)). \
                Refusing to migrate; use the momd of the exact installed app version.
                """, code: "model_incompatible")
        }
        let psc = NSPersistentStoreCoordinator(managedObjectModel: model)
        var opts: [AnyHashable: Any] = [
            NSMigratePersistentStoresAutomaticallyOption: false,
            NSInferMappingModelAutomaticallyOption: false,
        ]
        if let pragmas { opts[NSSQLitePragmasOption] = pragmas }
        if readOnly { opts[NSReadOnlyPersistentStoreOption] = true }
        _ = try psc.addPersistentStore(ofType: NSSQLiteStoreType, configurationName: nil, at: url, options: opts)
        return (psc, md)
    }

    static func close(_ psc: NSPersistentStoreCoordinator) throws {
        for s in psc.persistentStores { try psc.remove(s) }
    }

    /// Re-open with journal_mode=DELETE so the WAL is checkpointed into the main file and -wal/-shm disappear.
    static func foldWAL(storeDir: URL, model: NSManagedObjectModel) throws -> Bool {
        let (psc, _) = try open(storeDir: storeDir, model: model, pragmas: ["journal_mode": "DELETE"])
        try close(psc)
        let fm = FileManager.default
        let url = storeDir.appendingPathComponent(sqlite)
        let walSize = ((try? fm.attributesOfItem(atPath: url.path + "-wal"))?[.size] as? Int) ?? 0
        if walSize > 0 { return false }   // never drop a non-empty WAL
        // Empty leftovers are harmless for SQLite but must not travel into the backup.
        for s in ["-wal", "-shm"] where fm.fileExists(atPath: url.path + s) { try? fm.removeItem(atPath: url.path + s) }
        return !fm.fileExists(atPath: url.path + "-wal") && !fm.fileExists(atPath: url.path + "-shm")
    }
}
