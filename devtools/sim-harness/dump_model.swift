// SPDX-License-Identifier: AGPL-3.0-or-later
// xcrun swift dump_model.swift <path/to/ThreemaDataV56.mom>  -> prints entities, attributes (type, optional, default), relationships
import CoreData
import Foundation
let url = URL(fileURLWithPath: CommandLine.arguments[1])
guard let model = NSManagedObjectModel(contentsOf: url) else { fatalError("cannot load model") }
func t(_ a: NSAttributeDescription) -> String {
    switch a.attributeType {
    case .integer16AttributeType: return "int16"; case .integer32AttributeType: return "int32"
    case .integer64AttributeType: return "int64"; case .doubleAttributeType: return "double"
    case .floatAttributeType: return "float"; case .stringAttributeType: return "string"
    case .booleanAttributeType: return "bool"; case .dateAttributeType: return "date"
    case .binaryDataAttributeType: return a.allowsExternalBinaryDataStorage ? "binary(ext)" : "binary"
    case .transformableAttributeType: return "transformable(\(a.valueTransformerName ?? "-"))"
    case .UUIDAttributeType: return "uuid"; case .decimalAttributeType: return "decimal"
    default: return "other(\(a.attributeType.rawValue))" }
}
print("model version hashes identifiers:", model.versionIdentifiers)
for e in model.entities.sorted(by: { $0.name! < $1.name! }) {
    print("\nENTITY \(e.name!)  class=\(e.managedObjectClassName ?? "-") super=\(e.superentity?.name ?? "-") abstract=\(e.isAbstract)")
    for (n, a) in e.attributesByName.sorted(by: { $0.key < $1.key }) where a.entity == e {
        print("  attr \(n): \(t(a))\(a.isOptional ? "?" : " REQUIRED")\(a.defaultValue.map { " default=\($0)" } ?? "")")
    }
    for (n, r) in e.relationshipsByName.sorted(by: { $0.key < $1.key }) where r.entity == e {
        print("  rel  \(n) -> \(r.destinationEntity?.name ?? "?") \(r.isToMany ? "[*]" : "[1]")\(r.isOptional ? "?" : " REQUIRED") inverse=\(r.inverseRelationship?.name ?? "-") delete=\(r.deleteRule.rawValue)")
    }
}
