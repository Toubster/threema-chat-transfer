#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""third_party_licenses.py SITE_PACKAGES PYTHON_LICENSE OUT -- writes THIRD_PARTY_LICENSES for the bundle
(DESIGN §11.1) and enforces packaging/licenses.txt:

  * every *.dist-info in SITE_PACKAGES (tmcore excluded, it is this project) must be listed in licenses.txt,
  * every SPDX identifier used there must be on the ALLOW line,
  * the licence files shipped in each dist-info are reproduced verbatim, CPython's LICENSE.txt too.

Exit 1 on an unlisted distribution or a licence outside the allowlist. Prints counts only. Owner: pack.
"""
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
LICENSES = os.path.join(os.path.dirname(HERE), "licenses.txt")


def canon(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def load_map():
    allow, dists, runtime = set(), {}, []
    for raw in open(LICENSES, encoding="utf-8"):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("ALLOW "):
            allow = set(line.split()[1:])
        elif line.startswith("@runtime "):
            parts = re.split(r"\s{2,}", line[len("@runtime "):].strip())
            runtime.append((parts[0], parts[1]))
        else:
            name, expr = re.split(r"\s+", line, maxsplit=1)
            dists[canon(name)] = expr.strip()
    return allow, dists, runtime


def ids(expr: str):
    return [t for t in re.split(r"[\s()]+", expr) if t and t not in ("AND", "OR", "WITH")]


def main() -> int:
    site, pylicense, out = sys.argv[1:4]
    allow, curated, runtime = load_map()
    errors = []
    found = []
    for d in sorted(os.listdir(site)):
        if not d.endswith(".dist-info"):
            continue
        meta = open(os.path.join(site, d, "METADATA"), encoding="utf-8", errors="replace").read()
        name = re.search(r"^Name:\s*(.+)$", meta, re.M).group(1).strip()
        ver = re.search(r"^Version:\s*(.+)$", meta, re.M).group(1).strip()
        if canon(name) == "tmcore":
            continue
        expr = curated.get(canon(name))
        if expr is None:
            errors.append(f"not in licenses.txt: {name}")
            continue
        bad = [i for i in ids(expr) if i not in allow]
        if bad:
            errors.append(f"licence not allowed for {name}: {bad}")
        files = []
        for root, _dirs, fnames in os.walk(os.path.join(site, d)):
            for f in sorted(fnames):
                if re.match(r"(?i)(licen[cs]e|copying|notice|authors)", f):
                    files.append(os.path.join(root, f))
        found.append((name, ver, expr, files))
    for comp, expr in runtime:
        bad = [i for i in ids(expr) if i not in allow]
        if bad:
            errors.append(f"licence not allowed for runtime component {comp}: {bad}")
    if errors:
        for e in errors:
            print("third_party_licenses: " + e, file=sys.stderr)
        return 1
    with open(out, "w", encoding="utf-8") as o:
        o.write("THIRD-PARTY LICENSES\n====================\n\n"
                "This application bundles the following third-party software. The application as a whole is\n"
                "distributed under the GNU Affero General Public License v3 (see LICENSE and NOTICE).\n\n")
        o.write("Python packages (site-packages of the bundled runtime)\n")
        for name, ver, expr, _ in found:
            o.write(f"  {name} {ver}: {expr}\n")
        o.write("\nPython runtime (python-build-standalone, statically linked components)\n")
        for comp, expr in runtime:
            o.write(f"  {comp}: {expr}\n")
        o.write("\n\n==== CPython ====\n\n" + open(pylicense, encoding="utf-8", errors="replace").read())
        for name, ver, expr, files in found:
            for f in files:
                o.write(f"\n\n==== {name} {ver} -- {os.path.basename(f)} ====\n\n")
                o.write(open(f, encoding="utf-8", errors="replace").read())
    print(f"third_party_licenses: {len(found)} packages, {len(runtime)} runtime components, "
          f"{sum(len(x[3]) for x in found)} licence files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
