# model/ — Threema iOS Core Data model (AGPL-3.0-only)

`V56/ThreemaData.momd/` is the Core Data model of **Threema for iOS**, https://github.com/threema-ch/threema-ios,
tag `7.4b74051`, commit `35b749536bbb` (full hash in `V56/SOURCE`), compiled with `momc` from
`ThreemaFramework/Persistence/Model/ThreemaData/ThreemaData.xcdatamodeld` (current version `ThreemaDataV56`).
Copyright (c) Threema AG, licensed under the GNU Affero General Public License **version 3 only** (no "or later"),
so this folder is `AGPL-3.0-only`; the app as a whole is distributed under AGPL-3.0 (NOTICE). The model is a schema
description only — it contains no data of anyone.

It is committed pre-compiled because `momc` needs a full Xcode; `build-momd.sh` proves that it is exactly what the
pinned source compiles to:

```
model/build-momd.sh [--model V56] [--source DIR] [--verify]       # make model-verify [MOMD_SOURCE=DIR]
```

1. clones threema-ios at the pinned tag (shallow, github.com only) or uses `--source DIR`; the checkout must be at
   `COMMIT` of `V56/SOURCE` with the `.xcdatamodeld` unmodified;
2. compiles it with `xcrun momc`;
3. same file set; every `*.mom` and `VersionInfo.plist` **byte-identical** (sha256) to the committed files;
4. `ThreemaDataV56.omo` (momc's optimized model; its hash-table order changes from run to run, so it is not
   byte-reproducible) equal through a canonical dump (`tools/model_dump.swift`: entities, attributes, relationships,
   version hashes) — built `.omo` = committed `.omo` = committed `ThreemaDataV56.mom`;
5. model identity = `compat/threema-ios.json`: `version_hashes_sha256` = sha256 of
   `json.dumps(NSStoreModelVersionHashes, sort_keys=True)`, version identifier `ThremaDataV56` (sic, Threema's spelling).

Output: names, digests and OK/FAIL only. `--verify` exits 1 on any mismatch (CI job `importer`). The committed
files' sha256 digests are also listed in `scripts/scrub-allowlist.txt` (binary allow-list of the scrub check).

Verified locally with Xcode 27.0 (27A266a): all five checks OK. A different Xcode may emit different `.mom` bytes; then
step 3 fails while steps 4–5 still prove the same model — record the Xcode version in `V56/SOURCE` (`COMPILED_WITH`)
when the model is rebuilt.

**A new Threema model (V57, …)** never replaces V56 in place: new folder `model/V57/` with its own `SOURCE`, a new
entry in `compat/threema-ios.json`, an importer mapping, tests — and both models ship (DESIGN §6.3).
