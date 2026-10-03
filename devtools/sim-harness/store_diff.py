#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""store_diff.py - row-level diff of two ThreemaData.sqlite stores (e.g. importer output vs. the copy taken back from
the simulator/device after the app ran on it). Counts only: no texts, names or identities.

  .venv/bin/python tools/sim/store_diff.py --before DIR --after DIR [--report FILE] [--expect-app-writes]

For every Z table in both stores: rows added / removed (by Z_PK), rows changed and, per changed column, how many rows.
ZMESSAGE changes are classified (review appsafety m6): 'sendFailed 0->1 on incoming file message without blob id and
without data' is what BlobManager.setErrorStateForBlob writes for placeholders (BlobManager.swift:424,920-930).
--expect-app-writes: exit 0 when the ONLY changes are Z_OPT bumps, that placeholder write, ZCONVERSATION
lastMessage/lastUpdate repairs by AppLaunchTasks (none expected for our stores) and Z_PRIMARYKEY/Z_METADATA
bookkeeping; any other change (a message added, a state flipped to unsent, a blob id set, ...) exits 1.
"""
import argparse
import collections
import json
import os
import sqlite3
import sys


def ro(d):
    return sqlite3.connect(f"file:{os.path.join(d, 'ThreemaData.sqlite')}?mode=ro", uri=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--before", required=True)
    ap.add_argument("--after", required=True)
    ap.add_argument("--report")
    ap.add_argument("--expect-app-writes", action="store_true")
    ap.add_argument("--probe", action="store_true", help="the run included THREEMA_SIM_OPEN=probe (explicit sync of "
                    "every file message): also accept sendFailed on own file messages without blob id and data")
    a = ap.parse_args()
    A, B = ro(a.before), ro(a.after)
    ta = {r[0] for r in A.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Z%'")}
    tb = {r[0] for r in B.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Z%'")}
    out = collections.OrderedDict()
    out["tables_only_before"] = sorted(ta - tb)
    out["tables_only_after"] = sorted(tb - ta)
    unexpected = []
    per = {}
    read_marked, nonces_added = [0], [0]
    ent = {e: n for e, n in A.execute("SELECT Z_ENT, Z_NAME FROM Z_PRIMARYKEY")}
    for t in sorted(ta & tb):
        if t in ("Z_METADATA", "Z_MODELCACHE"):
            continue
        cols = [r[1] for r in A.execute(f'PRAGMA table_info("{t}")')]
        colsb = [r[1] for r in B.execute(f'PRAGMA table_info("{t}")')]
        if cols != colsb:
            per[t] = {"schema_changed": True}
            unexpected.append(f"{t}:schema")
            continue
        key = "Z_PK" if "Z_PK" in cols else ("Z_ENT" if t == "Z_PRIMARYKEY" else None)
        if key is None:     # many-to-many join tables: compare as sets
            sa = set(A.execute(f'SELECT * FROM "{t}"')); sb = set(B.execute(f'SELECT * FROM "{t}"'))
            d = {"added": len(sb - sa), "removed": len(sa - sb)}
            if d["added"] or d["removed"]:
                per[t] = d
                unexpected.append(f"{t}:rows")
            continue
        ki = cols.index(key)
        ra = {r[ki]: r for r in A.execute(f'SELECT * FROM "{t}"')}
        rb = {r[ki]: r for r in B.execute(f'SELECT * FROM "{t}"')}
        added, removed = set(rb) - set(ra), set(ra) - set(rb)
        colchg = collections.Counter()
        classes = collections.Counter()
        changed = 0
        idx = {c: i for i, c in enumerate(cols)}
        for k in set(ra) & set(rb):
            x, y = ra[k], rb[k]
            if x == y:
                continue
            changed += 1
            diff = [c for i, c in enumerate(cols) if x[i] != y[i]]
            for c in diff:
                colchg[c] += 1
            rest = [c for c in diff if c != "Z_OPT"]
            if t == "Z_PRIMARYKEY":
                if rest != ["Z_MAX"] and rest:
                    unexpected.append(f"{t}:{','.join(rest)}")
                continue
            if not rest:
                classes["z_opt_only"] += 1
                continue
            if t == "ZMESSAGE" and set(rest) <= {"ZSENDFAILED", "ZPROGRESS"} and "ZSENDFAILED" in rest:
                ename = ent.get(y[idx["Z_ENT"]])
                incoming = not y[idx["ZISOWN"]]
                no_blob = y[idx["ZBLOBID"]] is None if "ZBLOBID" in idx else False
                no_data = y[idx["ZDATA"]] is None if "ZDATA" in idx else False
                if x[idx["ZSENDFAILED"]] in (0, None) and y[idx["ZSENDFAILED"]] == 1 and incoming and no_blob and \
                        no_data and ename == "FileMessage":
                    classes["incoming_placeholder_blobError_set (BlobManager noID)"] += 1
                    continue
                if x[idx["ZSENDFAILED"]] in (0, None) and y[idx["ZSENDFAILED"]] == 1 and not incoming and no_blob and \
                        no_data and ename == "FileMessage" and a.probe:
                    # only reachable through the EXPLICIT syncBlobs path (user tap / media browser), which the online
                    # probe drives for every file message: noData / noEncryptionKey -> setErrorStateForBlob
                    classes["own_placeholder_or_deleted_blobError_set (explicit syncBlobs only)"] += 1
                    continue
            if t == "ZMESSAGE" and set(rest) <= {"ZREAD", "ZREADDATE"} and not y[idx["ZISOWN"]] \
                    and x[idx["ZREAD"]] in (0, None) and y[idx["ZREAD"]] == 1:
                # an incoming message that was UNREAD before the launch was read by opening its chat. Imported rows are
                # never unread (read=1, verify_import incoming_read_1), so this can only be pre-existing iPhone data;
                # the app then sends a read receipt for it (one outgoing message -> one new ZNONCE row).
                classes["preexisting_unread_marked_read (chat opened)"] += 1
                read_marked[0] += 1
                continue
            if t == "ZCONVERSATION" and rest == ["ZUNREADMESSAGECOUNT"] and \
                    (y[idx["ZUNREADMESSAGECOUNT"]] or 0) < (x[idx["ZUNREADMESSAGECOUNT"]] or 0):
                classes["unread_count_decreased (chat opened)"] += 1
                continue
            if t == "ZCONVERSATION" and set(rest) <= {"ZLASTMESSAGE", "ZLASTUPDATE"}:
                classes["conversation_lastMessage_or_lastUpdate"] += 1
                unexpected.append(f"{t}:{','.join(sorted(rest))}")
                continue
            classes["other:" + ",".join(sorted(rest))] += 1
            unexpected.append(f"{t}:{','.join(sorted(rest))}")
        if t == "ZNONCE" and added and not removed:
            nonces_added[0] = len(added)        # judged after all tables (needs read_marked)
        elif added or removed:
            unexpected.append(f"{t}:rows_added_or_removed")
        if added or removed or changed:
            per[t] = {"rows_before": len(ra), "rows_after": len(rb), "added": len(added), "removed": len(removed),
                      "changed": changed, "changed_columns": dict(colchg), "classes": dict(classes)}
    if nonces_added[0]:
        # every outgoing message stores one nonce; allowed only as receipts for pre-existing unread messages
        out["nonces_added_vs_read_receipts"] = {"nonces_added": nonces_added[0], "messages_marked_read": read_marked[0]}
        if not (read_marked[0] >= 1 and nonces_added[0] <= read_marked[0]):
            unexpected.append("ZNONCE:rows_added_without_matching_read")
    out["tables"] = per
    out["unexpected"] = sorted(collections.Counter(unexpected).items())
    out["result"] = "ONLY_EXPECTED_APP_WRITES" if not unexpected else "UNEXPECTED_CHANGES"
    s = json.dumps(out, indent=1)
    if a.report:
        with open(a.report, "w") as f:
            f.write(s + "\n")
    print(s)
    return 0 if (not a.expect_app_writes or not unexpected) else 1


if __name__ == "__main__":
    sys.exit(main())
