// SPDX-License-Identifier: AGPL-3.0-or-later
// model_dump - canonical JSON description of a compiled Core Data model (owner: pack, used by model/build-momd.sh).
//
//   model_dump <ThreemaData.momd | ThreemaDataVnn.mom | ThreemaDataVnn.omo>
//
// Prints one JSON object (keys sorted):
//   version_identifiers       NSManagedObjectModel.versionIdentifiers (Threema: "ThremaDataV56", sic)
//   version_hashes            entity -> base64(entityVersionHash), exactly what a store records in
//                             NSStoreModelVersionHashes; compat/threema-ios.json "version_hashes_sha256" is
//                             sha256(json.dumps(version_hashes, sort_keys=True)) (computed by build-momd.sh)
//   entities                  entity -> {parent, abstract, class, attributes{name: [type, optional, transient,
//                             default]}, relationships{name: [destination, to_many, optional, delete_rule, inverse]}}
// The .omo (optimized model) momc writes is not byte-reproducible (hash-table order differs from run to run), so
// build-momd.sh compares .omo files through this dump instead of byte for byte.
import CoreData
import Foundation

final class Stub: ValueTransformer {
    override class func transformedValueClass() -> AnyClass { NSData.self }
    override class func allowsReverseTransformation() -> Bool { true }
    override func transformedValue(_ value: Any?) -> Any? { value }
    override func reverseTransformedValue(_ value: Any?) -> Any? { value }
}
ValueTransformer.setValueTransformer(Stub(), forName: NSValueTransformerName("GroupDeliveryReceiptValueTransformer"))

guard CommandLine.arguments.count == 2,
      let model = NSManagedObjectModel(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1])) else {
    FileHandle.standardError.write("usage: model_dump <momd|mom|omo> (model not loadable)\n".data(using: .utf8)!)
    exit(2)
}

func str(_ v: Any?) -> Any { v.map { "\($0)" } ?? NSNull() }

var hashes: [String: String] = [:]
for (name, data) in model.entityVersionHashesByName { hashes[name] = data.base64EncodedString() }
var entities: [String: Any] = [:]
for e in model.entities {
    var attrs: [String: Any] = [:]
    for (n, a) in e.attributesByName {
        attrs[n] = [Int(a.attributeType.rawValue), a.isOptional, a.isTransient, str(a.defaultValue),
                    a.valueTransformerName ?? NSNull(), a.allowsExternalBinaryDataStorage]
    }
    var rels: [String: Any] = [:]
    for (n, r) in e.relationshipsByName {
        rels[n] = [r.destinationEntity?.name ?? NSNull(), r.isToMany, r.isOptional, Int(r.deleteRule.rawValue),
                   r.inverseRelationship?.name ?? NSNull(), r.isOrdered]
    }
    let parent: Any = e.superentity?.name ?? NSNull()
    entities[e.name ?? "?"] = ["parent": parent, "abstract": e.isAbstract,
                               "class": e.managedObjectClassName as Any, "attributes": attrs,
                               "relationships": rels]
}
let out: [String: Any] = ["version_identifiers": model.versionIdentifiers.map { "\($0)" }.sorted(),
                          "version_hashes": hashes, "entities": entities]
let data = try! JSONSerialization.data(withJSONObject: out, options: [.sortedKeys])
FileHandle.standardOutput.write(data)
print("")
