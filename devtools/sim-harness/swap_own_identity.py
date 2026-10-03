#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""swap_own_identity.py - make an imported store runnable in the simulator with a THROWAWAY identity
(runbook C.6, mandatory since review appsafety M2). Never touches the input store.

  .venv/bin/python tools/sim/swap_own_identity.py --store DIR --out DIR --own ID --identity-json sim/<throwaway>.json

Clones DIR (APFS clone, cp -c) to OUT and rewrites every IDENTITY column that holds the own identity to the
throwaway identity: ZCONVERSATION.ZGROUPMYIDENTITY, ZBALLOT.ZCREATORID (review appsafety m1: own polls must stay
own), ZBALLOTRESULT.ZPARTICIPANTID. Free text (message bodies, quotes, mentions) is NOT rewritten; the number of such
rows is reported (they stay inside the simulator device, which sim-gallery.sh erases before the next run).
Prints counts only.
"""
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys

COLUMNS = [("ZCONVERSATION", "ZGROUPMYIDENTITY"), ("ZBALLOT", "ZCREATORID"), ("ZBALLOTRESULT", "ZPARTICIPANTID")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--own", required=True, help="own identity in the store (e.g. normalized meta.own_identity)")
    ap.add_argument("--identity-json", required=True)
    a = ap.parse_args()
    ident = json.load(open(a.identity_json))
    if "throwaway key" not in ident.get("note", ""):
        sys.exit("identity JSON is not a gen_test_identity.py throwaway identity")
    new = ident["identity"]
    if len(a.own) != 8 or len(new) != 8 or new == a.own:
        sys.exit("bad identities")
    if os.path.exists(a.out):
        shutil.rmtree(a.out)
    subprocess.run(["cp", "-c", "-R", a.store, a.out], check=True)
    for f in ("ThreemaData.sqlite-wal", "ThreemaData.sqlite-shm"):
        if os.path.exists(os.path.join(a.out, f)) and os.path.getsize(os.path.join(a.out, f)) > 0 and f.endswith("wal"):
            sys.exit("store has a non-empty WAL - use the importer output (WAL folded)")
    db = sqlite3.connect(os.path.join(a.out, "ThreemaData.sqlite"))
    res = {}
    for t, c in COLUMNS:
        n = db.execute(f"UPDATE {t} SET {c}=? WHERE {c}=?", (new, a.own)).rowcount
        res[f"{t}.{c}"] = n
    db.commit()
    # residue check over every text/blob column (counts only)
    residue = {}
    like = f"%{a.own}%"
    for (t,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Z%'"):
        for _, col, typ, *_ in db.execute(f'PRAGMA table_info("{t}")'):
            if typ.upper() in ("VARCHAR", "TEXT", "BLOB"):
                try:
                    n = db.execute(f'SELECT count(*) FROM "{t}" WHERE CAST("{col}" AS TEXT) LIKE ?', (like,)).fetchone()[0]
                except sqlite3.OperationalError:
                    continue
                if n:
                    residue[f"{t}.{col}"] = n
    db.close()
    print(json.dumps({"rewritten": res, "own_id_residue_rows": residue}))


if __name__ == "__main__":
    main()
