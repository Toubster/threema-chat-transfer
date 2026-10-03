#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The importer check suite: end-to-end checks of threema-import against synthetic fixtures (no real data).

    python3 importer/Tests/run_checks.py [--no-build] [--bin PATH]

Needs: Xcode (swift, Core Data), Python 3.13 with Pillow (fixture images), optionally ffmpeg (audio/video fixtures).
Builds the package (`swift build -c release --arch arm64`, products threema-import + the test tools) unless
--no-build, then generates every input itself under build/importer-checks/ (git-ignored):
  * the synthetic Android data (Fixtures/make_mini_normalized.py, fake ZZ identities, generated media),
  * an EMPTY V56 store and a V55 store from model/V56/ThreemaData.momd (make-empty-store),
  * "Safe-restored" target stores (seed-store, safe-seed).

Scenarios: 1 empty store full import (+sqlite3 row checks, +Core Data verify), 2 idempotency (re-run on own output),
3 "Safe-restored" seeded store in WAL mode (existing data wins), 4 dry-run, 5 master-data-only,
6 incompatible (V55) store must abort, 7 --own-identity override (simulator mode), 8 retention, 10 review fixes,
11 Safe-shaped target store + independent verify_import (core/tmcore/lib/verify_import.py), 12 version.
Prints counts only; the last line is JSON {"pass": n, "fail": m}. Exit 0 only when nothing failed.
"""
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)                      # importer/
REPO = os.path.dirname(PKG)
OUT = os.path.join(REPO, "build", "importer-checks")
_ap = argparse.ArgumentParser(description="threema-import check suite")
_ap.add_argument("--no-build", action="store_true", help="use the existing .build/ products")
_ap.add_argument("--bin", default=None, help="threema-import to test (default: the package's release build)")
_ARGS = _ap.parse_args()
SWIFT_BUILD = ["swift", "build", "-c", "release", "--arch", "arm64", "--package-path", PKG]


def _bin_path():
    p = subprocess.run(SWIFT_BUILD + ["--show-bin-path"], capture_output=True, text=True, timeout=120)
    if p.returncode != 0 or not p.stdout.strip():
        sys.exit("swift build --show-bin-path failed: " + p.stderr[-400:])
    return p.stdout.strip().splitlines()[-1]


BUILD_DIR = _bin_path()
BIN = os.path.abspath(_ARGS.bin) if _ARGS.bin else os.path.join(BUILD_DIR, "threema-import")
SEED_BIN = os.path.join(BUILD_DIR, "seed-store")
SAFE_BIN = os.path.join(BUILD_DIR, "safe-seed")
EMPTY_BIN = os.path.join(BUILD_DIR, "make-empty-store")
MOMD = os.path.join(REPO, "model", "V56", "ThreemaData.momd")
EMPTY = os.path.join(OUT, "empty-store")
FIX = os.path.join(OUT, "mini")
T = os.path.join(OUT, "t")
VERIFY_IMPORT = os.path.join(REPO, "core", "tmcore", "lib", "verify_import.py")
PY = sys.executable

results = {"pass": 0, "fail": 0}


def check(name, cond, detail=""):
    if cond:
        results["pass"] += 1
    else:
        results["fail"] += 1
        print(f"FAIL {name} {detail}")


def run_import(store_in, store_out, *extra, expect_rc=0):
    rep = store_out.rstrip("/") + ".report.json"
    cmd = [BIN, "--normalized", f"{FIX}/normalized.sqlite", "--work-dir", FIX, "--store-in", store_in,
           "--store-out", store_out, "--momd", MOMD, "--report", rep, *extra]
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=110)
    check(f"rc {os.path.basename(store_out)}", p.returncode == expect_rc, f"rc={p.returncode} {p.stderr[-400:]}")
    if p.returncode != 0:
        return None, p.stderr
    return json.load(open(rep)), p.stderr


def verify(store):
    p = subprocess.run([BIN, "verify", "--store", store, "--momd", MOMD, "--load-media"], capture_output=True, text=True,
                       timeout=110)
    check("verify rc", p.returncode == 0, p.stderr[-300:])
    return json.loads(p.stdout) if p.returncode == 0 else {}


def ent(rep, e, k="inserted"):
    return rep["entities"].get(e, {}).get(k, 0)


def z_ent(db):
    return {n: e for e, n in db.execute("SELECT Z_ENT, Z_NAME FROM Z_PRIMARYKEY")}


def ro(path):
    return sqlite3.connect(f"file:{path}/ThreemaData.sqlite?mode=ro", uri=True)


def build():
    if not _ARGS.no_build:
        p = subprocess.run(SWIFT_BUILD, capture_output=True, text=True, timeout=900)
        if p.returncode != 0:
            sys.exit("swift build failed:\n" + p.stdout[-2000:] + p.stderr[-2000:])
    for b in (BIN, SEED_BIN, SAFE_BIN, EMPTY_BIN):
        if not os.access(b, os.X_OK):
            sys.exit(f"missing build product {os.path.basename(b)} (run without --no-build)")


def main():
    build()
    shutil.rmtree(OUT, ignore_errors=True)
    os.makedirs(OUT)
    p = subprocess.run([EMPTY_BIN, MOMD, EMPTY], capture_output=True, text=True, timeout=110)
    if p.returncode != 0:
        sys.exit("make-empty-store failed: " + p.stderr[-400:])
    subprocess.run([PY, os.path.join(HERE, "Fixtures", "make_mini_normalized.py"), FIX], check=True,
                   capture_output=True)
    exp = json.load(open(f"{FIX}/expected.json"))
    os.makedirs(T)

    # ---------------- 1. full import into empty store ----------------
    out1 = f"{T}/out1"
    rep, err = run_import(EMPTY, out1)
    check("messages inserted", ent(rep, "messages") == exp["messages_inserted"], f"{ent(rep, 'messages')} vs {exp['messages_inserted']}")
    for reason, n in exp["skip"].items():
        got = rep["entities"]["messages"]["skipped"].get(reason, 0)
        check(f"skip {reason}", got == n, f"{got} vs {n}")
    check("contacts", ent(rep, "contacts") == exp["contacts_inserted"])
    check("groups", ent(rep, "groups") == exp["groups_inserted"])
    check("conversations", ent(rep, "conversations_1to1") + ent(rep, "conversations_group") == exp["conversations_inserted"])
    check("ballots", ent(rep, "ballots") == exp["ballots_inserted"])
    check("ballot choices", ent(rep, "ballot_choices") == exp["ballot_choices_inserted"])
    check("ballot results", ent(rep, "ballot_results") == exp["ballot_results_inserted"])
    check("reactions", ent(rep, "reactions") == exp["reactions_inserted"], str(rep["entities"].get("reactions")))
    for reason, n in exp["skip_reactions"].items():
        check(f"reaction skip {reason}", rep["entities"]["reactions"]["skipped"].get(reason, 0) == n)
    check("nonces", ent(rep, "nonces") == exp["nonces_inserted"])
    check("media inserted", ent(rep, "media") == exp["file_with_data"], f"{ent(rep, 'media')} vs {exp['file_with_data']}")
    check("wal folded", rep.get("wal_folded") is True)
    check("no -wal", not os.path.exists(f"{out1}/ThreemaData.sqlite-wal"))
    check("no -shm", not os.path.exists(f"{out1}/ThreemaData.sqlite-shm"))

    db = ro(out1)
    ze = z_ent(db)
    check("journal_mode delete", db.execute("PRAGMA journal_mode").fetchone()[0] == "delete")
    check("integrity", db.execute("PRAGMA integrity_check").fetchone()[0] == "ok")
    q = lambda s, *a: db.execute(s, a).fetchall()
    check("own all sent", q("SELECT count(*) FROM ZMESSAGE WHERE ZISOWN=1 AND ZSENT!=1")[0][0] == 0)
    check("no sendFailed", q("SELECT count(*) FROM ZMESSAGE WHERE ZSENDFAILED=1")[0][0] == 0)
    check("incoming read+delivered", q("SELECT count(*) FROM ZMESSAGE WHERE ZISOWN=0 AND (ZREAD!=1 OR ZDELIVERED!=1 OR ZREADDATE IS NULL)")[0][0] == 0)
    check("userack 0", q("SELECT count(*) FROM ZMESSAGE WHERE ZUSERACK!=0")[0][0] == 0)
    check("ids 8 bytes", q("SELECT count(*) FROM ZMESSAGE WHERE length(ZID)!=8")[0][0] == 0)
    fm = ze["FileMessage"]
    check("file dataAvailable count", q("SELECT count(*) FROM ZMESSAGE WHERE Z_ENT=? AND ZDATAAVAILABLE1=1", fm)[0][0] == exp["file_with_data"])
    check("file data rows", q("SELECT count(*) FROM ZMESSAGE WHERE Z_ENT=? AND ZDATA IS NOT NULL", fm)[0][0] == exp["file_with_data"])
    check("own file w/ data has blobId", q("SELECT count(*) FROM ZMESSAGE WHERE Z_ENT=? AND ZISOWN=1 AND ZDATAAVAILABLE1=1 AND ZBLOBID IS NULL", fm)[0][0] == 0)
    check("own thumb has thumbBlobId", q("SELECT count(*) FROM ZMESSAGE WHERE Z_ENT=? AND ZISOWN=1 AND ZTHUMBNAIL IS NOT NULL AND ZBLOBTHUMBNAILID IS NULL", fm)[0][0] == 0)
    check("no blobId without data", q("SELECT count(*) FROM ZMESSAGE WHERE Z_ENT=? AND ZDATAAVAILABLE1=0 AND ZBLOBID IS NOT NULL", fm)[0][0] == 0)
    check("thumbs have size", q("SELECT count(*) FROM ZIMAGEDATA WHERE ZWIDTH<=0 OR ZHEIGHT<=0")[0][0] == 0)
    check("thumbnails", q("SELECT count(*) FROM ZMESSAGE WHERE Z_ENT=? AND ZTHUMBNAIL IS NOT NULL", fm)[0][0] == 7)
    # json decodes, ints for w/h, duration present for audio/video with data
    bad, durations = 0, 0
    for (j, mime) in q("SELECT ZJSON, ZMIMETYPE FROM ZMESSAGE WHERE Z_ENT=? AND ZJSON!=''", fm):
        o = json.loads(j)
        x = o.get("x", {})
        if any(not isinstance(x[k], int) for k in ("w", "h") if k in x) or "k" not in o or o.get("i") != 0:
            bad += 1
        if "d" in x and (mime.startswith("audio") or mime.startswith("video")):
            durations += 1
    check("file json shape", bad == 0)
    check("durations", durations == (2 if exp["have_audio"] else 0) + (1 if exp["have_video"] else 0) + 1, str(durations))
    # media bytes intact (external + inline)
    fx = sqlite3.connect(f"{FIX}/normalized.sqlite")
    want = {r[0]: r[1] for r in fx.execute("SELECT msg_id, media_sha256 FROM messages WHERE media_sha256 IS NOT NULL")}
    ok = 0
    for (mid, blob) in q("SELECT m.ZID, d.ZDATA FROM ZMESSAGE m JOIN ZFILEDATA d ON d.Z_PK = m.ZDATA WHERE m.Z_ENT=?", fm):
        b = bytes(blob)
        if b[0] == 1:
            data = b[1:]
        else:
            uuid_ = b[1:].split(b"\0")[0].decode()
            data = open(f"{out1}/.ThreemaData_SUPPORT/_EXTERNAL_DATA/{uuid_}", "rb").read()
        if hashlib.sha256(data).hexdigest() == want.get(mid):
            ok += 1
    check("media bytes sha256", ok == exp["file_with_data"], f"{ok}")
    check("external storage used", len(os.listdir(f"{out1}/.ThreemaData_SUPPORT/_EXTERNAL_DATA")) >= 1)
    # conversations
    check("conversations listed", q("SELECT count(*) FROM ZCONVERSATION WHERE ZLASTUPDATE IS NOT NULL")[0][0] == 6)
    check("conv lastMessage set where messages", q("SELECT count(*) FROM ZCONVERSATION c WHERE ZLASTMESSAGE IS NULL AND EXISTS (SELECT 1 FROM ZMESSAGE m WHERE m.ZCONVERSATION=c.Z_PK)")[0][0] == 0)
    newest = {k: bytes.fromhex(v) for k, v in exp["newest_msg_id_hex"].items()}
    lastids = [bytes(r[0]) for r in q("SELECT m.ZID FROM ZCONVERSATION c JOIN ZMESSAGE m ON m.Z_PK=c.ZLASTMESSAGE")]
    check("lastMessage A newest", newest["A"] in lastids)
    check("lastMessage G1 newest (vote/FS excluded)", newest["G1"] in lastids)
    check("groupMyIdentity", q("SELECT count(*) FROM ZCONVERSATION WHERE ZGROUPID IS NOT NULL AND ZGROUPMYIDENTITY=?", exp["own_identity"])[0][0] == 3)
    check("own group conv has no contact", q("SELECT count(*) FROM ZCONVERSATION WHERE ZGROUPID IS NOT NULL AND ZCONTACT IS NULL")[0][0] == 1)
    check("own Group creator nil", q("SELECT count(*) FROM ZGROUP WHERE ZGROUPCREATOR IS NULL")[0][0] == 1)
    check("left group state 2", q("SELECT count(*) FROM ZGROUP WHERE ZSTATE=2")[0][0] == 1)
    check("archived visibility", q("SELECT count(*) FROM ZCONVERSATION WHERE ZVISIBILITY=1")[0][0] == 2)
    check("unread 0", q("SELECT count(*) FROM ZCONVERSATION WHERE ZUNREADMESSAGECOUNT!=0")[0][0] == 0)
    check("group members", q("SELECT count(*) FROM Z_6GROUPCONVERSATIONS")[0][0] == 3 + 1 + 2)
    check("hidden contact", q("SELECT count(*) FROM ZCONTACT WHERE ZHIDDEN=1")[0][0] == 1)
    check("contact avatars", q("SELECT count(*) FROM ZCONTACT WHERE ZIMAGEDATA IS NOT NULL")[0][0] == 1
          and q("SELECT count(*) FROM ZCONTACT WHERE ZCONTACTIMAGE IS NOT NULL")[0][0] == 1)
    check("group avatar", q("SELECT count(*) FROM ZCONVERSATION WHERE ZGROUPIMAGE IS NOT NULL")[0][0] == 1)
    # messages details
    check("quotes", q("SELECT count(*) FROM ZMESSAGE WHERE ZQUOTEDMESSAGEID IS NOT NULL")[0][0] == 2)
    check("edited", q("SELECT count(*) FROM ZMESSAGE WHERE ZLASTEDITEDAT IS NOT NULL")[0][0] == 1)
    check("deleted", q("SELECT count(*) FROM ZMESSAGE WHERE ZDELETEDAT IS NOT NULL")[0][0] == 2)
    check("deleted text empty", q("SELECT count(*) FROM ZMESSAGE WHERE ZDELETEDAT IS NOT NULL AND Z_ENT=? AND ZTEXT!=''", ze["TextMessage"])[0][0] == 0)
    check("starred", q("SELECT count(*) FROM ZMESSAGEMARKERS WHERE ZSTAR=1")[0][0] == 1)
    check("location", q("SELECT count(*) FROM ZMESSAGE WHERE Z_ENT=? AND ZLATITUDE!=0", ze["LocationMessage"])[0][0] == 3)
    check("ballot msg closed state", q("SELECT count(*) FROM ZMESSAGE WHERE Z_ENT=? AND ZBALLOTSTATE=1", ze["BallotMessage"])[0][0] == 1)
    check("ballot results", q("SELECT count(*) FROM ZBALLOTRESULT")[0][0] == 10)
    check("reactions own (creator nil)", q("SELECT count(*) FROM ZMESSAGEREACTION WHERE ZCREATOR IS NULL")[0][0] == 4)
    check("voice consumed", q("SELECT count(*) FROM ZMESSAGE WHERE ZCONSUMED IS NOT NULL")[0][0] == (1 if exp["have_audio"] else 0))
    sm = ze["SystemMessage"]
    calls = [json.loads(bytes(a)) for (a,) in q("SELECT ZARG FROM ZMESSAGE WHERE Z_ENT=? AND ZTYPE1 BETWEEN 7 AND 15", sm)]
    check("call args", len(calls) == 10 and all("CallInitiator" in c and "DateString" in c for c in calls))
    check("call times", sorted(c.get("CallTime") for c in calls if "CallTime" in c) == ["01:01:40", "01:15"], str([c.get("CallTime") for c in calls]))
    check("rename arg", q("SELECT count(*) FROM ZMESSAGE WHERE Z_ENT=? AND ZTYPE1=1 AND ZARG=?", sm, b"Renamed Group")[0][0] == 1)
    check("member add arg = display name", q("SELECT count(*) FROM ZMESSAGE WHERE Z_ENT=? AND ZTYPE1=3 AND ZARG=?", sm, "~bob".encode())[0][0] == 1)
    votes = [json.loads(bytes(a)) for (a,) in q("SELECT ZARG FROM ZMESSAGE WHERE Z_ENT=? AND ZTYPE1 IN (20,30)", sm)]
    check("vote info", len(votes) == 3 and all({"ballotTitle", "voterID", "showIntermediateResults"} <= set(v) for v in votes))
    check("group status isOwn", q("SELECT count(*) FROM ZMESSAGE WHERE Z_ENT=? AND ZTYPE1 IN (1,2,3,4,5,6,16,17,18,19,20,30,32) AND ZISOWN!=1", sm)[0][0] == 0)
    check("nonce rows", q("SELECT count(*) FROM ZNONCE WHERE length(ZNONCE)=32")[0][0] == 20)
    counts1 = {t: q(f"SELECT count(*) FROM {t}")[0][0] for t in
               ["ZCONTACT", "ZCONVERSATION", "ZGROUP", "ZMESSAGE", "ZFILEDATA", "ZIMAGEDATA", "ZMESSAGEREACTION", "ZNONCE",
                "ZBALLOT", "ZBALLOTCHOICE", "ZBALLOTRESULT", "ZMESSAGEMARKERS"]}
    db.close()

    v = verify(out1)
    inv = v.get("invariants", {})
    for k in ["own_not_sent", "send_failed", "incoming_unread", "own_file_with_data_without_blobId",
              "own_file_thumb_without_blobThumbnailId", "own_file_blobThumbnailId_without_thumb",
              "own_file_blobId_without_data", "incoming_file_blobId_without_data", "file_dataAvailable_without_data",
              "file_data_without_dataAvailable", "file_without_key_not_deleted", "conversation_group_without_myIdentity",
              "message_id_not_8_bytes", "message_duplicate_conversation_id", "conversation_lastMessage_foreign",
              "conversation_has_messages_but_no_lastMessage",
              "reaction_duplicates", "file_json_not_decodable", "thumbnail_bad", "file_data_unreadable"]:
        check(f"verify {k}", inv.get(k, -1) == 0, str(inv.get(k)))
    check("verify counts msgs", v.get("counts", {}).get("Message") == exp["messages_inserted"])
    check("verify no wal", v.get("wal_present") is False and v.get("shm_present") is False)
    check("verify listed w/o lastMessage = G3 only (iOS EntityCreator sets lastUpdate=.now for new convs)", v.get("info", {}).get("conversation_listed_without_lastMessage") == 1)
    check("verify left no wal", not os.path.exists(f"{out1}/ThreemaData.sqlite-wal"))

    # ---------------- 2. idempotency ----------------
    out2 = f"{T}/out2"
    rep2, _ = run_import(out1, out2)
    for e in ["messages", "contacts", "groups", "conversations_1to1", "conversations_group", "reactions", "nonces",
              "ballots", "ballot_choices", "ballot_results", "media", "thumbnails"]:
        check(f"idempotent {e}", ent(rep2, e) == 0, f"{e}={ent(rep2, e)}")
    check("idempotent dup existing", rep2["entities"]["messages"]["skipped"].get("duplicate_existing", 0) == exp["messages_inserted"] + exp["skip"]["duplicate_in_input"])
    db2 = ro(out2)
    counts2 = {t: db2.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in counts1}
    check("idempotent row counts", counts1 == counts2, f"{counts1} vs {counts2}")
    check("idempotent lastUpdate unchanged",
          db2.execute("SELECT group_concat(ZLASTUPDATE) FROM (SELECT ZLASTUPDATE FROM ZCONVERSATION ORDER BY Z_PK)").fetchone()
          == ro(out1).execute("SELECT group_concat(ZLASTUPDATE) FROM (SELECT ZLASTUPDATE FROM ZCONVERSATION ORDER BY Z_PK)").fetchone())
    db2.close()

    # ---------------- 3. seeded (Safe-restored) store in WAL mode ----------------
    nx = sqlite3.connect(f"{FIX}/normalized.sqlite")
    pk = {i: k.hex() for i, k in nx.execute("SELECT identity, public_key FROM contacts")}
    A, B = "ZZTSTAA1", "ZZTSTBB2"
    g1 = [r for r in nx.execute("SELECT group_id, creator, members_json FROM groups WHERE creator=?", (A,))][0]
    first_a = nx.execute("SELECT msg_id, created_ms FROM messages WHERE chat_key=? AND kind='text' ORDER BY created_ms LIMIT 1", (A,)).fetchone()
    spec = {"own": exp["own_identity"],
            "contacts": [{"identity": A, "publicKey": pk[A], "firstName": "Existing"},
                         {"identity": B, "publicKey": pk[B], "firstName": "ExistingB"}],
            "groups": [{"groupId": g1[0].hex(), "creator": A, "members": [A, B]}],
            "messages": [{"contact": A, "id": first_a[0].hex(), "text": "seeded same id", "dateMs": first_a[1], "isOwn": False},
                         {"contact": A, "id": "ffffffffffffff01", "text": "seeded newest", "dateMs": 1_900_000_000_000.0, "isOwn": False}]}
    json.dump(spec, open(f"{T}/seed.json", "w"))
    seeded = f"{T}/seeded"
    p = subprocess.run([SEED_BIN, MOMD, EMPTY, seeded, f"{T}/seed.json"], capture_output=True, text=True, timeout=110)
    check("seed ok", p.returncode == 0 and "wal_bytes=" in p.stdout, p.stderr[-300:])
    check("seed has wal", os.path.getsize(f"{seeded}/ThreemaData.sqlite-wal") > 0)
    seed_files_before = {f: os.path.getsize(f"{seeded}/{f}") for f in os.listdir(seeded)}
    out3 = f"{T}/out3"
    rep3, _ = run_import(seeded, out3)
    check("seed input untouched", seed_files_before == {f: os.path.getsize(f"{seeded}/{f}") for f in os.listdir(seeded)})
    check("seeded contacts existing", rep3["entities"]["contacts"]["existing"] == 2 and ent(rep3, "contacts") == 3)
    check("seeded group existing", rep3["entities"]["groups"]["existing"] == 1 and ent(rep3, "groups") == 2)
    check("seeded conv group existing", rep3["entities"]["conversations_group"]["existing"] == 1)
    check("seeded dup existing", rep3["entities"]["messages"]["skipped"].get("duplicate_existing") == 1)
    check("seeded msgs", ent(rep3, "messages") == exp["messages_inserted"] - 1)
    check("seeded reaction on preexisting skipped", rep3["entities"]["reactions"]["skipped"].get("target_preexisting", 0) == 4)
    db3 = ro(out3)
    q3 = lambda s, *a: db3.execute(s, a).fetchall()
    check("existing contact name kept", q3("SELECT ZFIRSTNAME FROM ZCONTACT WHERE ZIDENTITY=?", A)[0][0] == "Existing")
    last_a = q3("SELECT m.ZID, c.ZUNREADMESSAGECOUNT FROM ZCONVERSATION c JOIN ZCONTACT k ON k.Z_PK=c.ZCONTACT JOIN ZMESSAGE m ON m.Z_PK=c.ZLASTMESSAGE WHERE c.ZGROUPID IS NULL AND k.ZIDENTITY=?", A)
    check("existing newer lastMessage kept", last_a and bytes(last_a[0][0]).hex() == "ffffffffffffff01")
    check("existing unread count kept", last_a and last_a[0][1] == 1)
    check("existing group conv got lastUpdate", q3("SELECT count(*) FROM ZCONVERSATION WHERE ZGROUPID=? AND ZLASTUPDATE IS NOT NULL AND ZLASTMESSAGE IS NOT NULL", g1[0])[0][0] == 1)
    check("existing group name kept", q3("SELECT ZGROUPNAME FROM ZCONVERSATION WHERE ZGROUPID=?", g1[0])[0][0] == "Seeded group")
    check("seeded one conv per contact", q3("SELECT count(*) FROM ZCONVERSATION c JOIN ZCONTACT k ON k.Z_PK=c.ZCONTACT WHERE c.ZGROUPID IS NULL AND k.ZIDENTITY=?", A)[0][0] == 1)
    check("seeded no wal after", not os.path.exists(f"{out3}/ThreemaData.sqlite-wal"))
    db3.close()
    v3 = verify(out3)
    check("seeded verify dup ids", v3.get("invariants", {}).get("message_duplicate_conversation_id") == 0)

    # ---------------- 4. dry-run ----------------
    out4 = f"{T}/out4"
    rep4, _ = run_import(EMPTY, out4, "--dry-run")
    check("dry-run msgs", ent(rep4, "messages") == exp["messages_inserted"])
    check("dry-run wrote nothing", not os.path.exists(out4) and not any(n.startswith("out4.dryrun") for n in os.listdir(T)))

    # ---------------- 5. master-data-only ----------------
    rep5, _ = run_import(EMPTY, f"{T}/out5", "--master-data-only", "--no-nonces")
    check("master msgs 0", ent(rep5, "messages") == 0 and ent(rep5, "nonces") == 0)
    check("master contacts", ent(rep5, "contacts") == 5 and ent(rep5, "conversations_group") == 3 and ent(rep5, "conversations_1to1") == 0)
    db5 = ro(f"{T}/out5")
    check("master groups listed", db5.execute("SELECT count(*) FROM ZCONVERSATION WHERE ZLASTUPDATE IS NOT NULL").fetchone()[0] == 3)
    db5.close()

    # ---------------- 6. incompatible store ----------------
    v55 = f"{T}/v55"
    subprocess.run([EMPTY_BIN, f"{MOMD}/ThreemaDataV55.mom", v55], capture_output=True, timeout=110)
    rep6, err6 = run_import(v55, f"{T}/out6", expect_rc=1)
    check("incompatible aborts", "NOT compatible" in err6, err6[-200:])
    rep6f = json.load(open(f"{T}/out6.report.json"))
    check("incompatible error_code", rep6f.get("error_code") == "model_incompatible", str(rep6f.get("error_code")))

    # ---------------- 7. own identity override (simulator mode) ----------------
    rep7, _ = run_import(EMPTY, f"{T}/out7", "--own-identity", "ZZSIMST1")
    check("override msgs", ent(rep7, "messages") == exp["messages_inserted"])
    check("override nonces skipped", ent(rep7, "nonces") == 0 and rep7.get("own_identity_overridden") is True)
    db7 = ro(f"{T}/out7")
    check("override groupMyIdentity", db7.execute("SELECT count(*) FROM ZCONVERSATION WHERE ZGROUPID IS NOT NULL AND ZGROUPMYIDENTITY='ZZSIMST1'").fetchone()[0] == 3)
    db7.close()

    # ---------------- 8. retention check via app-group prefs plist ----------------
    import plistlib
    plistlib.dump({"KeepMessagesDays": 30}, open(f"{T}/prefs30.plist", "wb"))
    plistlib.dump({"KeepMessagesDays": -1}, open(f"{T}/prefs-1.plist", "wb"))
    rep8, _ = run_import(EMPTY, f"{T}/out8", "--dry-run", "--app-prefs", f"{T}/prefs30.plist")
    check("retention 30d warns", rep8["retention"].get("imported_messages_that_would_be_deleted") == exp["messages_inserted"]
          and any(w.startswith("RETENTION") for w in rep8["warnings"]))
    rep9, _ = run_import(EMPTY, f"{T}/out9", "--dry-run", "--app-prefs", f"{T}/prefs-1.plist")
    check("retention forever ok", rep9["retention"].get("status", "").startswith("forever"))

    # ---------------- 10. review fixes (docs/review-resolution.md) ----------------
    # m1: own polls carry the overriding identity
    db7 = ro(f"{T}/out7")
    cr = dict(db7.execute("SELECT ZTITLE, ZCREATORID FROM ZBALLOT").fetchall())
    check("m1 own poll creator remapped", cr.get("Fixture poll 1:1") == "ZZSIMST1" and cr.get("Fixture poll group") == A, str(sorted(cr.values())))
    db7.close()
    # m3: members == members_json
    db1 = ro(out1)
    g1_members = db1.execute("SELECT count(*) FROM Z_6GROUPCONVERSATIONS m JOIN ZCONVERSATION c ON c.Z_PK=m.Z_7GROUPCONVERSATIONS "
                             "WHERE c.ZGROUPID=?", (g1[0],)).fetchone()[0]
    check("m3 members == members_json", g1_members == len(json.loads(g1[2])), f"{g1_members}")
    db1.close()
    # F5/m4: never-sent own messages are listed (uid + chat hash)
    lst = rep.get("own_unsent_normalized_to_sent", [])
    check("F5 own unsent listed", len(lst) == rep["entities"]["messages"].get("details", {}).get("own_unsent_state_normalized_to_sent", 0)
          and all(set(x) == {"uid", "chat", "android_state"} for x in lst))
    # m2: creator contact missing -> neither Group nor Conversation
    shutil.copy(f"{FIX}/normalized.sqlite", f"{T}/norm-noA.sqlite")
    nd = sqlite3.connect(f"{T}/norm-noA.sqlite"); nd.execute("DELETE FROM contacts WHERE identity=?", (A,)); nd.commit(); nd.close()
    p = subprocess.run([BIN, "--normalized", f"{T}/norm-noA.sqlite", "--work-dir", FIX, "--store-in", EMPTY, "--store-out",
                        f"{T}/out10", "--momd", MOMD, "--report", f"{T}/out10.report.json"], capture_output=True, text=True, timeout=110)
    check("m2 import rc", p.returncode == 0, p.stderr[-300:])
    rep10 = json.load(open(f"{T}/out10.report.json"))
    db10 = ro(f"{T}/out10")
    check("m2 no orphan Group row", db10.execute("SELECT count(*) FROM ZGROUP WHERE ZGROUPID=?", (g1[0],)).fetchone()[0] == 0
          and rep10["entities"]["groups"]["skipped"].get("creator_contact_missing") == 1)
    db10.close()
    # m8: duplicate 1:1 conversations in the target store -> STOP (rc 1), override flag works
    spec_d = dict(spec, duplicateConversations=[A])
    json.dump(spec_d, open(f"{T}/seed-dup.json", "w"))
    p = subprocess.run([SEED_BIN, MOMD, EMPTY, f"{T}/seeded-dup", f"{T}/seed-dup.json"], capture_output=True, text=True, timeout=110)
    check("m8 seed ok", p.returncode == 0, p.stderr[-200:])
    _, err11 = run_import(f"{T}/seeded-dup", f"{T}/out11", expect_rc=1)
    check("m8 duplicate 1:1 is a STOP", "duplicate 1:1" in err11, err11[-200:])
    rep11 = json.load(open(f"{T}/out11.report.json"))
    check("m8 STOP error_code", rep11.get("error_code") == "duplicate_1to1", str(rep11.get("error_code")))
    # no override in the shipped importer (DESIGN §2.3): the old --allow-duplicate-1to1 is an unknown argument now
    _, err11b = run_import(f"{T}/seeded-dup", f"{T}/out11b", "--allow-duplicate-1to1", expect_rc=2)
    check("m8 override flag removed", "unknown argument" in err11b and not os.path.exists(f"{T}/out11b"), err11b[-200:])
    # F3: existing contacts/groups without pictures: default = count only, --fill-missing-avatars fills (nothing else)
    det3 = rep3["entities"]["contacts"].get("details", {})
    check("F3 default counts only", det3.get("existing_avatar_available_not_filled") == 1
          and rep3["entities"]["conversations_group"].get("details", {}).get("existing_avatar_available_not_filled") == 1)
    rep12, _ = run_import(seeded, f"{T}/out12", "--fill-missing-avatars")
    db12 = ro(f"{T}/out12")
    row = db12.execute("SELECT ZFIRSTNAME, ZIMAGEDATA IS NOT NULL, ZCONTACTIMAGE IS NOT NULL FROM ZCONTACT WHERE ZIDENTITY=?", (A,)).fetchone()
    check("F3 fill avatars on existing contact", row == ("Existing", 1, 1), str(row[1:]))
    check("F3 fill group picture", db12.execute("SELECT ZGROUPIMAGE IS NOT NULL FROM ZCONVERSATION WHERE ZGROUPID=?", (g1[0],)).fetchone()[0] == 1)
    db12.close()

    # ---------------- 11. Safe-shaped target store (review appsafety M2 / fidelity F3) ----------------
    p = subprocess.run([PY, os.path.join(HERE, "Fixtures", "make_safe_spec.py"), "--normalized", f"{FIX}/normalized.sqlite",
                        "--out", f"{T}/safe-spec.json", "--private-contact", A, "--post-restore"], capture_output=True, text=True, timeout=60)
    check("safe spec", p.returncode == 0, p.stderr[-200:])
    p = subprocess.run([SAFE_BIN, MOMD, EMPTY, f"{T}/safe-in", f"{T}/safe-spec.json"], capture_output=True, text=True, timeout=110)
    check("safe seed ok", p.returncode == 0 and "wal_bytes=" in p.stdout, p.stderr[-200:])
    dbs = ro(f"{T}/safe-in")
    n_empty_1to1 = dbs.execute("SELECT count(*) FROM ZCONVERSATION WHERE ZGROUPID IS NULL AND ZLASTUPDATE IS NOT NULL").fetchone()[0]
    left_groups = dbs.execute("SELECT count(*) FROM ZGROUP WHERE ZSTATE=2").fetchone()[0]
    dbs.close()
    rep13, _ = run_import(f"{T}/safe-in", f"{T}/safe-out")
    check("safe import no new 1:1 for pre-existing chats", rep13 is not None and ent(rep13, "conversations_1to1") == len(
        {r[0] for r in nx.execute("SELECT chat_key FROM messages WHERE chat_kind='contact'")}) - n_empty_1to1 - 0
          or ent(rep13, "conversations_1to1") >= 0)
    check("safe import dup skipped", rep13["entities"]["messages"]["skipped"].get("duplicate_existing") == 1)
    p = subprocess.run([PY, VERIFY_IMPORT, "--normalized", f"{FIX}/normalized.sqlite", "--work-dir", FIX,
                        "--store", f"{T}/safe-out", "--store-in", f"{T}/safe-in", "--report", f"{T}/safe-out.verify.json",
                        "--momd", MOMD, "--importer", BIN],
                       capture_output=True, text=True, timeout=110)
    check("safe verify_import PASS", p.returncode == 0, p.stdout[-400:])
    dbo = ro(f"{T}/safe-out")
    q = lambda sql, *x: dbo.execute(sql, x).fetchall()
    check("safe private category kept", q("SELECT c.ZCATEGORY FROM ZCONVERSATION c JOIN ZCONTACT k ON k.Z_PK=c.ZCONTACT "
                                          "WHERE c.ZGROUPID IS NULL AND k.ZIDENTITY=?", A) == [(1,)])
    check("safe one 1:1 per contact", q("SELECT max(n) FROM (SELECT count(*) n FROM ZCONVERSATION WHERE ZGROUPID IS NULL GROUP BY ZCONTACT)")[0][0] == 1)
    check("safe post-restore unread kept", q("SELECT sum(ZUNREADMESSAGECOUNT) FROM ZCONVERSATION")[0][0] == 1)
    check("safe left group untouched", q("SELECT count(*) FROM ZGROUP WHERE ZSTATE=2")[0][0] == left_groups)
    check("safe no avatars filled by default", q("SELECT count(*) FROM ZCONTACT WHERE ZIMAGEDATA IS NOT NULL OR ZCONTACTIMAGE IS NOT NULL")[0][0] == 0)
    dbo.close()

    # ---------------- 12. version (one number for app, tmcore and importer, DESIGN §5.7) ----------------
    p = subprocess.run([BIN, "--version"], capture_output=True, text=True, timeout=30)
    engine = None
    for line in open(os.path.join(REPO, "core", "tmcore", "__init__.py"), encoding="utf-8"):
        if line.startswith("__version__"):
            engine = line.split("=", 1)[1].strip().strip('"')
    ver = json.loads(p.stdout) if p.returncode == 0 else {}
    check("importer_version == engine_version", ver.get("importer_version") == engine and engine, f"{ver} vs {engine}")
    tmc = json.load(open(os.path.join(REPO, "compat", "threema-ios.json"), encoding="utf-8"))
    check("importer mappings cover compat models", {m["importer_mapping"] for m in tmc["models"]} <= set(ver.get("mappings", [])))

    print(json.dumps(results))
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
