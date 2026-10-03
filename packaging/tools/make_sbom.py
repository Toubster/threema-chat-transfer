#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""make_sbom.py RESOURCES OUT.json -- CycloneDX 1.5 SBOM of an assembled app (DESIGN §11.1, release.yml).

Components, all read from the bundle itself (nothing assumed):
  * the app (metadata.component): name, version (core/tmcore/__init__.py), AGPL-3.0-or-later
  * tmcore and threema-import (versions from the bundle, sha256 of the importer binary)
  * every distribution in the runtime's site-packages: name, version, SPDX licence (packaging/licenses.txt), purl,
    sha256 of the wheel/sdist that was installed (build/cache, as pinned in core/requirements.lock /
    packaging/sdist-vendor.lock)
  * the Python runtime (python-build-standalone, packaging/python.lock: URL + sha256) and its statically linked
    components (licences from licenses.txt; OpenSSL/SQLite versions asked from the bundled interpreter)
  * every Threema iOS model of compat/threema-ios.json (AGPL-3.0-only, source commit, tree digest)
Deterministic: serialNumber = uuid5 over the component list, timestamp = SOURCE_DATE_EPOCH (else HEAD's commit time).
Prints counts only. Owner: pack.
"""
from __future__ import annotations

import datetime as dt
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
REPO = os.path.dirname(PKG)
BUILD = os.environ.get("TM_BUILD_DIR", os.path.join(REPO, "build"))
APP_NAME = os.environ.get("TM_APP_NAME", "Chat Transfer for Threema")


def canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def kv(path: str) -> dict[str, str]:
    out = {}
    for line in open(path, encoding="utf-8"):
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.rstrip("\n").split("=", 1)
            out[k.strip()] = re.sub(r"\s+#.*$", "", v).strip()
    return out


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def tree_sha256(root: str) -> str:
    h = hashlib.sha256()
    for dirpath, dirs, files in os.walk(root):
        dirs.sort()
        for n in sorted(files):
            p = os.path.join(dirpath, n)
            h.update(os.path.relpath(p, root).encode() + b"\0" + bytes.fromhex(sha256_file(p)))
    return h.hexdigest()


def licences() -> tuple[dict[str, str], list[tuple[str, str]]]:
    dists, runtime = {}, []
    for raw in open(os.path.join(PKG, "licenses.txt"), encoding="utf-8"):
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("ALLOW "):
            continue
        if line.startswith("@runtime "):
            parts = re.split(r"\s{2,}", line[len("@runtime "):].strip())
            runtime.append((parts[0], parts[1]))
        else:
            n, e = re.split(r"\s+", line, maxsplit=1)
            dists[canon(n)] = e.strip()
    return dists, runtime


def lic(expr: str) -> list[dict]:
    return [{"expression": expr}] if (" AND " in expr or " OR " in expr or "LicenseRef-" in expr) \
        else [{"license": {"id": expr}}]


def main() -> int:
    res, out = os.path.realpath(sys.argv[1]), sys.argv[2]
    site = os.path.join(res, "core", "python", "lib", "python3.13", "site-packages")
    py = os.path.join(res, "core", "python", "bin", "python3")
    version = re.search(r'^__version__ = "([^"]+)"', open(os.path.join(res, "core", "tmcore", "__init__.py")).read(),
                        re.M).group(1)
    dist_lic, runtime_lic = licences()
    comps: list[dict] = []
    errors: list[str] = []

    # --- engine + importer ---
    comps.append({"type": "application", "bom-ref": "tmcore", "name": "tmcore", "version": version,
                  "licenses": lic("AGPL-3.0-or-later"), "description": "migration engine (Python)"})
    imp = os.path.join(res, "bin", "threema-import")
    comps.append({"type": "application", "bom-ref": "threema-import", "name": "threema-import", "version": version,
                  "licenses": lic("AGPL-3.0-or-later"), "hashes": [{"alg": "SHA-256", "content": sha256_file(imp)}],
                  "description": "Core Data importer (Swift, arm64)"})

    # --- wheels / vendored sdists ---
    caches: dict[tuple[str, str], str] = {}
    for f in glob.glob(os.path.join(BUILD, "cache", "wheels", "*.whl")) + glob.glob(os.path.join(BUILD, "cache", "*.zip")):
        parts = re.sub(r"\.(whl|zip)$", "", os.path.basename(f)).split("-")
        if len(parts) > 1:
            caches[(canon(parts[0]), parts[1])] = f
    for d in sorted(os.listdir(site)):
        if not d.endswith(".dist-info"):
            continue
        meta = open(os.path.join(site, d, "METADATA"), encoding="utf-8", errors="replace").read()
        name = re.search(r"^Name:\s*(.+)$", meta, re.M).group(1).strip()
        ver = re.search(r"^Version:\s*(.+)$", meta, re.M).group(1).strip()
        if canon(name) not in dist_lic:
            errors.append(f"not in licenses.txt: {name}")
            continue
        c = {"type": "library", "bom-ref": f"pypi:{canon(name)}", "name": name, "version": ver,
             "purl": f"pkg:pypi/{canon(name)}@{ver}", "licenses": lic(dist_lic[canon(name)])}
        if (canon(name), ver) in caches:
            c["hashes"] = [{"alg": "SHA-256", "content": sha256_file(caches[(canon(name), ver)])}]
        else:
            errors.append(f"installed file of {name} {ver} not in build/cache (build-core.sh first)")
        comps.append(c)

    # --- runtime ---
    pbs = kv(os.path.join(PKG, "python.lock"))
    ver_json = subprocess.run([py, "-I", "-B", "-c", "import ssl, sqlite3, json; print(json.dumps("
                               "{'OpenSSL': ssl.OPENSSL_VERSION.split()[1], 'SQLite': sqlite3.sqlite_version}))"],
                              capture_output=True, text=True, timeout=60, env={"PATH": "/usr/bin:/bin"})
    rt_versions = json.loads(ver_json.stdout) if ver_json.returncode == 0 else {}
    sub = []
    for comp, expr in runtime_lic:
        if comp == "CPython":
            continue
        key = comp.split()[0]
        s = {"type": "library", "bom-ref": f"runtime:{canon(key)}", "name": comp, "licenses": lic(expr)}
        if key in rt_versions:
            s["version"] = rt_versions[key]
        sub.append(s)
    comps.append({"type": "framework", "bom-ref": "cpython", "name": "CPython (python-build-standalone)",
                  "version": pbs["PBS_PYTHON"], "licenses": lic("PSF-2.0"),
                  "purl": f"pkg:generic/python-build-standalone@{pbs['PBS_RELEASE']}?file=" + pbs["PBS_URL"].rsplit("/", 1)[1],
                  "hashes": [{"alg": "SHA-256", "content": pbs["PBS_SHA256"]}],
                  "externalReferences": [{"type": "distribution", "url": pbs["PBS_URL"]}],
                  "components": sub})

    # --- models ---
    for m in json.load(open(os.path.join(res, "compat", "threema-ios.json"), encoding="utf-8"))["models"]:
        src = kv(os.path.join(REPO, "model", m["id"], "SOURCE"))
        comps.append({"type": "data", "bom-ref": f"model:{m['id']}", "name": f"Threema iOS Core Data model {m['id']}",
                      "version": src.get("TAG", ""), "licenses": lic("AGPL-3.0-only"),
                      "copyright": "Copyright (c) Threema AG",
                      "hashes": [{"alg": "SHA-256", "content": tree_sha256(os.path.join(res, m["momd"]))}],
                      "externalReferences": [{"type": "vcs", "url": src["REPO_URL"],
                                              "comment": f"commit {src['COMMIT']}"}]})
    if errors:
        for e in errors:
            print("make_sbom: " + e, file=sys.stderr)
        return 1

    epoch = os.environ.get("SOURCE_DATE_EPOCH")
    if not epoch:
        p = subprocess.run(["git", "-C", REPO, "log", "-1", "--format=%ct"], capture_output=True, text=True)
        epoch = p.stdout.strip() or "0"
    ts = dt.datetime.fromtimestamp(int(epoch), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = json.dumps(comps, sort_keys=True)
    bom = {
        "bomFormat": "CycloneDX", "specVersion": "1.5", "version": 1,
        "serialNumber": f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'threema-chat-transfer-sbom:' + version + ':' + body)}",
        "metadata": {"timestamp": ts,
                     "tools": {"components": [{"type": "application", "name": "packaging/tools/make_sbom.py"}]},
                     "component": {"type": "application", "bom-ref": "app", "name": APP_NAME, "version": version,
                                   "licenses": lic("AGPL-3.0-or-later")}},
        "components": comps,
        "dependencies": [{"ref": "app", "dependsOn": sorted(c["bom-ref"] for c in comps)}],
    }
    with open(out, "w", encoding="utf-8") as f:
        json.dump(bom, f, indent=1, sort_keys=True)
        f.write("\n")
    hashed = sum(1 for c in comps if "hashes" in c)
    print(f"[make_sbom] {len(comps)} components ({hashed} with sha256, {len(sub)} runtime parts)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
