#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""bundle_manifest.py RESOURCES -- write RESOURCES/bundle-manifest.json for `tmcore selftest` (phase "bundle",
core/tmcore/steps/status.py): {"schema": 1, "files": {"<path relative to Resources>": "<sha256 hex>"}}.

Covers every file that decides what the engine does: the engine sources and contract schemas, the importer, the
compiled models, the compat lists, the legal texts and the app's engine sandbox profile. The Python runtime and
the wheels are covered by the code signature instead (verify-bundle.sh: codesign --verify --strict --deep) -- hashing
them on every selftest would cost seconds (DESIGN §5.4: selftest < 3 s). Called by stage-resources.sh and again by
sign-adhoc.sh after every Mach-O of the assembled app is signed (signing rewrites threema-import), so the bundle
signature seals the final manifest. Prints counts only. Owner: pack.
"""
import hashlib
import json
import os
import sys

# (directory or file relative to Resources, suffixes; None = every file)
INCLUDE = [
    ("bin/threema-import", None),
    ("models", None),
    ("compat", (".json",)),
    ("core/schema", (".json",)),
    ("core/tmcore", (".py",)),
    ("core/python/lib/python3.13/site-packages/tmcore.pth", None),
    ("legal", None),
    ("engine-sandbox.sb", None),           # app/Resources (LiveEngine); only present in the assembled app
]
REQUIRED = ["bin/threema-import", "models", "compat", "core/schema", "core/tmcore"]


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    res = os.path.realpath(sys.argv[1])
    files: dict[str, str] = {}
    for rel, suffixes in INCLUDE:
        p = os.path.join(res, rel)
        if not os.path.exists(p):
            if rel in REQUIRED:
                sys.exit(f"bundle_manifest: Resources/{rel} missing")
            continue
        if os.path.isfile(p):
            files[rel] = sha256(p)
            continue
        for dirpath, dirnames, names in os.walk(p):
            dirnames[:] = sorted(d for d in dirnames if d != "__pycache__")
            for n in sorted(names):
                fp = os.path.join(dirpath, n)
                if os.path.islink(fp) or (suffixes and not n.endswith(suffixes)):
                    continue
                files[os.path.relpath(fp, res)] = sha256(fp)
    out = os.path.join(res, "bundle-manifest.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"schema": 1, "files": dict(sorted(files.items()))}, f, indent=0, sort_keys=True)
        f.write("\n")
    os.chmod(out, 0o644)
    print(f"[bundle_manifest] {len(files)} files", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
