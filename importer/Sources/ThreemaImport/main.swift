// SPDX-License-Identifier: AGPL-3.0-or-later
// threema-import - Android (normalized.sqlite) -> Threema iOS 7.4 ThreemaData.sqlite (Core Data model V56).
//
//   threema-import --normalized FILE --work-dir DIR --store-in DIR --store-out DIR --momd PATH
//                  [--own-identity ID] [--master-data-only] [--dry-run] [--report FILE] [--no-nonces]
//                  [--overwrite] [--app-prefs group.ch.threema.plist] [--batch N] [--no-hash-check]
//                  [--fill-missing-avatars]
//   threema-import verify --store DIR --momd PATH [--load-media] [--report FILE]
//   threema-import --version
//
// No overrides of safety stops (DESIGN §2.3): a target store with duplicate 1:1 conversations or an incompatible
// model always aborts (exit 1, report "error_code"). --own-identity exists for the maintainer's simulator harness
// only; the engine never passes it.
// Never touches --store-in; writes a self-contained store (journal_mode=DELETE, no -wal/-shm) to --store-out.
// Output (stdout/report) contains counts only - no message texts, names or identities.
import CoreData
import Foundation

func fail(_ msg: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write(("ERROR: " + msg + "\n").data(using: .utf8)!)
    exit(code)
}

let usage = """
usage: threema-import --normalized FILE --work-dir DIR --store-in DIR --store-out DIR --momd PATH
                      [--own-identity ID] [--master-data-only] [--dry-run] [--report FILE] [--no-nonces]
                      [--overwrite] [--app-prefs PLIST] [--batch N] [--no-hash-check]
                      [--fill-missing-avatars]
       threema-import verify --store DIR --momd PATH [--load-media] [--report FILE]
       threema-import --version
"""

StoreFiles.registerTransformer()
var args = Array(CommandLine.arguments.dropFirst())
if args.isEmpty || args.contains("-h") || args.contains("--help") { print(usage); exit(args.isEmpty ? 2 : 0) }
if args == ["--version"] {
    // one JSON line: the engine's selftest compares importer_version with its own engine_version (DESIGN §5.7)
    print("{\"importer_version\":\"\(importerVersion)\",\"mappings\":[\(importerMappings.map { "\"\($0)\"" }.joined(separator: ","))]}")
    exit(0)
}

func value(_ flag: String) -> String? {
    guard let i = args.firstIndex(of: flag), i + 1 < args.count else { return nil }
    return args[i + 1]
}
func url(_ flag: String, dir: Bool = false) -> URL? {
    value(flag).map { URL(fileURLWithPath: ($0 as NSString).expandingTildeInPath, isDirectory: dir).standardizedFileURL }
}
func writeJSON(_ obj: Any, to: URL?) {
    let d = (try? JSONSerialization.data(withJSONObject: obj, options: [.prettyPrinted, .sortedKeys])) ?? Data()
    if let to { try? d.write(to: to) }
    FileHandle.standardOutput.write(d)
    print("")
}

if args.first == "verify" {
    guard let store = url("--store", dir: true), let momd = url("--momd", dir: true) else { fail(usage, code: 2) }
    do {
        let res = try Verify.run(storeDir: store, momd: momd, loadMedia: args.contains("--load-media"))
        writeJSON(res, to: url("--report"))
        exit(0)
    } catch { fail("verify: \(error)") }
}

let known: Set<String> = ["--normalized", "--work-dir", "--store-in", "--store-out", "--momd", "--own-identity",
                          "--master-data-only", "--dry-run", "--report", "--no-nonces", "--overwrite", "--app-prefs",
                          "--batch", "--no-hash-check", "--fill-missing-avatars"]
let withValue: Set<String> = ["--normalized", "--work-dir", "--store-in", "--store-out", "--momd", "--own-identity",
                              "--report", "--app-prefs", "--batch"]
var i = 0
while i < args.count {
    guard known.contains(args[i]) else { fail("unknown argument \(args[i])\n" + usage, code: 2) }
    i += withValue.contains(args[i]) ? 2 : 1
}

var opt = Options()
guard let n = url("--normalized"), let w = url("--work-dir", dir: true), let si = url("--store-in", dir: true),
      let so = url("--store-out", dir: true), let m = url("--momd", dir: true) else { fail(usage, code: 2) }
opt.normalized = n; opt.workDir = w; opt.storeIn = si; opt.storeOut = so; opt.momd = m
opt.ownIdentity = value("--own-identity")
opt.masterDataOnly = args.contains("--master-data-only")
opt.dryRun = args.contains("--dry-run")
opt.report = url("--report")
opt.noNonces = args.contains("--no-nonces")
opt.overwrite = args.contains("--overwrite")
opt.appPrefs = url("--app-prefs")
opt.verifyMediaHash = !args.contains("--no-hash-check")
opt.fillMissingAvatars = args.contains("--fill-missing-avatars")
if let b = value("--batch"), let bi = Int(b), bi > 0 { opt.batchSize = bi }
if let o = opt.ownIdentity, o.count != 8 { fail("--own-identity must be 8 characters") }

let started = Date()
let report = Report()
report.info["tool"] = "threema-import \(importerVersion)"
report.info["mode"] = opt.dryRun ? "dry-run" : (opt.masterDataOnly ? "master-data-only" : "full")

// Dry-run works on a throw-away copy next to store-out and deletes it afterwards.
let target: URL = opt.dryRun
    ? opt.storeOut.deletingLastPathComponent().appendingPathComponent(opt.storeOut.lastPathComponent + ".dryrun-\(getpid())")
    : opt.storeOut

do {
    let norm = try Normalized(path: opt.normalized.path)
    report.info["normalized_format_version"] = norm.meta["format_version"] ?? NSNull()
    let model = try StoreFiles.loadModel(opt.momd)
    report.info["model_version_identifiers"] = Array(model.versionIdentifiers).map { "\($0)" }
    try StoreFiles.copyStore(from: opt.storeIn, to: target, overwrite: opt.overwrite || opt.dryRun)
    let psc: NSPersistentStoreCoordinator
    do {
        (psc, _) = try StoreFiles.open(storeDir: target, model: model)
    } catch {
        if opt.dryRun { try? FileManager.default.removeItem(at: target) }
        throw error
    }
    report.info["store_model_compatible"] = true
    let importer = try Importer(opt: opt, norm: norm, report: report, psc: psc)
    report.info["own_identity_overridden"] = importer.ownOverridden
    if importer.ownOverridden {
        report.warn("--own-identity differs from normalized meta.own_identity (test mode): nonces skipped")
    }
    try importer.run()
    try StoreFiles.close(psc)
    if opt.dryRun {
        try? FileManager.default.removeItem(at: target)
        report.info["store_out_written"] = false
        report.warn("dry-run: no media bytes loaded, no thumbnails generated, no sha256 check")
    } else {
        let folded = try StoreFiles.foldWAL(storeDir: target, model: model)
        report.info["wal_folded"] = folded
        report.info["store_out_written"] = true
        if !folded { report.warn("WAL could not be folded - do NOT inject this store") }
        let ext = target.appendingPathComponent(StoreFiles.support).appendingPathComponent("_EXTERNAL_DATA")
        report.info["external_data_files"] = (try? FileManager.default.contentsOfDirectory(atPath: ext.path).count) ?? 0
    }
} catch {
    report.info["error"] = "\(error)"
    report.info["error_code"] = (error as? ImportError)?.code ?? "import_failed"
    report.info["duration_s"] = Date().timeIntervalSince(started)
    if let r = opt.report { try? report.json().write(to: r) }
    fail("\(error)")
}
report.info["duration_s"] = (Date().timeIntervalSince(started) * 10).rounded() / 10
let data = report.json()
if let r = opt.report { try data.write(to: r) }
FileHandle.standardOutput.write(data)
print("")
