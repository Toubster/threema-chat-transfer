# SPDX-License-Identifier: AGPL-3.0-or-later
"""Negative tests for tmcore.lib.verify_import: every tampering of an imported store must be detected.
The fixture is synthetic: the importer's mini normalized fixture imported into an empty V56 store (tests/support).
Each test copies the store, applies one targeted change with sqlite3 and expects exit code 1 and the named failure in
the JSON report. The untouched copy must PASS.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

from tests import support

FIX = STORE = TMP = None


@pytest.fixture(scope="module", autouse=True)
def fixture_store(tmp_path_factory):
    global FIX, STORE, TMP
    base = tmp_path_factory.mktemp("verify-import")
    FIX = str(base / "fixture")
    support.make_mini_normalized(base / "fixture")
    STORE = os.path.join(FIX, "store-out")
    p = subprocess.run([str(support.importer()), "--normalized", os.path.join(FIX, "normalized.sqlite"), "--work-dir",
                        FIX, "--store-in", str(support.empty_store()), "--store-out", STORE, "--momd",
                        str(support.momd())], capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stderr[-2000:]
    TMP = str(base / "tampered")
    os.environ["TMCORE_IMPORTER"] = str(support.importer())
    yield


def run_verify(store, *extra):
    rep = store + ".verify.json"
    p = subprocess.run([sys.executable, "-m", "tmcore.lib.verify_import", "--normalized",
                        os.path.join(FIX, "normalized.sqlite"), "--work-dir", FIX, "--store", store, "--report", rep,
                        "--momd", str(support.momd()), *extra], capture_output=True, text=True, timeout=110,
                       cwd=support.CORE, env=support.tmcore_env(TMCORE_IMPORTER=str(support.importer())))
    return p.returncode, json.load(open(rep))


def tampered(name, sql=None, fn=None):
    d = os.path.join(TMP, name)
    if os.path.exists(d):
        shutil.rmtree(d)
    shutil.copytree(STORE, d)
    if sql:
        db = sqlite3.connect(os.path.join(d, "ThreemaData.sqlite"))
        db.executescript(sql)
        db.commit()
        db.close()
    if fn:
        fn(d)
    return d


def ent(name):
    db = sqlite3.connect(f"file:{STORE}/ThreemaData.sqlite?mode=ro", uri=True)
    try:
        return db.execute("SELECT Z_ENT FROM Z_PRIMARYKEY WHERE Z_NAME=?", (name,)).fetchone()[0]
    finally:
        db.close()


def test_untouched_passes():
    rc, rep = run_verify(tampered("clean"))
    assert rc == 0, rep["failures"]
    assert rep["result"] == "PASS"


def corrupt_external(d):
    """Flip one byte of an external FILE DATA blob (the synthetic fixture also has external thumbnails/avatars)."""
    ext = os.path.join(d, ".ThreemaData_SUPPORT", "_EXTERNAL_DATA")
    db = sqlite3.connect(f"file:{os.path.join(d, 'ThreemaData.sqlite')}?mode=ro", uri=True)
    refs = [bytes(v[1:37]).decode() for (v,) in db.execute(
        "SELECT ZDATA FROM ZFILEDATA WHERE substr(ZDATA,1,1)=X'02' AND length(ZDATA)=38 ORDER BY Z_PK")]
    db.close()
    assert refs, "fixture has no external FileData blob"
    f = os.path.join(ext, refs[0])
    with open(f, "r+b") as h:
        b = h.read(1)
        h.seek(0)
        h.write(bytes([b[0] ^ 0xFF]))


CASES = [
    ("text", "UPDATE ZMESSAGE SET ZTEXT = ZTEXT || 'x' WHERE Z_PK = (SELECT MIN(Z_PK) FROM ZMESSAGE WHERE Z_ENT={T} AND ZTEXT != '')", None, "text_equal"),
    ("date", "UPDATE ZMESSAGE SET ZDATE = ZDATE + 0.002 WHERE Z_PK = (SELECT MIN(Z_PK) FROM ZMESSAGE)", None, "date_ms"),
    ("remote_date", "UPDATE ZMESSAGE SET ZREMOTESENTDATE = ZREMOTESENTDATE - 1 WHERE Z_PK = (SELECT MAX(Z_PK) FROM ZMESSAGE)", None, "remoteSentDate_ms"),
    ("missing_msg", "DELETE FROM ZMESSAGEREACTION WHERE ZMESSAGE = (SELECT MAX(Z_PK) FROM ZMESSAGE WHERE Z_ENT={T}); DELETE FROM ZMESSAGE WHERE Z_PK = (SELECT MAX(Z_PK) FROM ZMESSAGE WHERE Z_ENT={T})", None, "message_present_exactly_once"),
    ("dup_msg", "INSERT INTO ZMESSAGE SELECT (SELECT Z_MAX+1 FROM Z_PRIMARYKEY WHERE Z_NAME='Message'), Z_ENT, Z_OPT, ZDELIVERED, ZFLAGS, ZFORWARDSECURITYMODE, ZISCREATEDFROMWEB, ZISOWN, ZPROPERTY2, ZREAD, ZSENDFAILED, ZSENT, ZUSERACK, ZCONVERSATION, ZDISTRIBUTIONLISTMESSAGE, Z14_DISTRIBUTIONLISTMESSAGE, NULL, ZSENDER, ZAUDIOSIZE, ZDATAAVAILABLE, ZAUDIO, ZBALLOTSTATE, ZBALLOT, ZDATAAVAILABLE1, ZFILESIZE, ZORIGIN, ZTYPE, ZDATA, ZTHUMBNAIL, ZDATAAVAILABLE2, ZIMAGESIZE, ZIMAGE, ZTHUMBNAIL1, ZTYPE1, ZDATAAVAILABLE3, ZVIDEOSIZE, ZTHUMBNAIL2, ZVIDEO, ZDATE, ZDELETEDAT, ZDELIVERYDATE, ZLASTEDITEDAT, ZREADDATE, ZREMOTESENTDATE, ZUSERACKDATE, ZDURATION, ZCONSUMED, ZACCURACY, ZLATITUDE, ZLONGITUDE, ZDURATION1, ZPROPERTY1, ZWEBREQUESTID, ZCAPTION, ZFILENAME, ZJSON, ZMIMETYPE, ZPOIADDRESS, ZPOINAME, ZTEXT, ZGROUPDELIVERYRECEIPTS, ZID, ZAUDIOBLOBID, ZENCRYPTIONKEY, ZBLOBID, ZBLOBTHUMBNAILID, ZENCRYPTIONKEY1, ZENCRYPTIONKEY2, ZIMAGEBLOBID, ZIMAGENONCE, ZARG, ZQUOTEDMESSAGEID, ZENCRYPTIONKEY3, ZVIDEOBLOBID FROM ZMESSAGE WHERE Z_ENT={T} LIMIT 1; UPDATE Z_PRIMARYKEY SET Z_MAX = Z_MAX + 1 WHERE Z_NAME='Message'", None, "message_present_exactly_once"),
    ("direction", "UPDATE ZMESSAGE SET ZISOWN = 1 - ZISOWN WHERE Z_PK = (SELECT MIN(Z_PK) FROM ZMESSAGE WHERE Z_ENT={T})", None, "direction_isOwn"),
    ("send_failed", "UPDATE ZMESSAGE SET ZSENDFAILED = 1 WHERE Z_PK = (SELECT MIN(Z_PK) FROM ZMESSAGE WHERE ZISOWN = 1)", None, "sendFailed_0"),
    ("unread", "UPDATE ZMESSAGE SET ZREAD = 0 WHERE Z_PK = (SELECT MIN(Z_PK) FROM ZMESSAGE WHERE ZISOWN = 0)", None, "incoming_read_1"),
    ("unread_count", "UPDATE ZCONVERSATION SET ZUNREADMESSAGECOUNT = 1 WHERE Z_PK = (SELECT MIN(ZCONVERSATION) FROM ZMESSAGE)", None, "unreadMessageCount_0"),
    ("quote", "UPDATE ZMESSAGE SET ZQUOTEDMESSAGEID = X'0102030405060708' WHERE Z_PK = (SELECT MIN(Z_PK) FROM ZMESSAGE WHERE ZQUOTEDMESSAGEID IS NOT NULL)", None, "quote_id"),
    ("sys_type", "UPDATE ZMESSAGE SET ZTYPE1 = 13 WHERE Z_PK = (SELECT MIN(Z_PK) FROM ZMESSAGE WHERE Z_ENT={S} AND ZTYPE1 = 11)", None, "system_type"),
    ("location", "UPDATE ZMESSAGE SET ZLATITUDE = ZLATITUDE + 0.000001 WHERE Z_PK = (SELECT MIN(Z_PK) FROM ZMESSAGE WHERE Z_ENT={L} AND ZLATITUDE != 0)", None, "location_lat_lon"),
    ("mime", "UPDATE ZMESSAGE SET ZMIMETYPE = 'image/png' WHERE Z_PK = (SELECT MIN(Z_PK) FROM ZMESSAGE WHERE ZMIMETYPE = 'image/jpeg')", None, "file_mime"),
    ("file_size", "UPDATE ZMESSAGE SET ZFILESIZE = ZFILESIZE + 1 WHERE Z_PK = (SELECT MIN(Z_PK) FROM ZMESSAGE WHERE ZDATA IS NOT NULL)", None, "file_size"),
    ("inline_media", "UPDATE ZFILEDATA SET ZDATA = CAST(X'01' || CASE WHEN substr(ZDATA,2,1) = X'00' THEN X'01' ELSE X'00' END || substr(ZDATA, 3) AS BLOB) WHERE Z_PK = (SELECT MIN(f.Z_PK) FROM ZFILEDATA f JOIN ZMESSAGE m ON m.ZDATA = f.Z_PK WHERE substr(f.ZDATA,1,1) = X'01')", None, "filedata_sha256_vs_normalized"),
    ("external_media", None, corrupt_external, "filedata_sha256_vs_normalized"),
    ("reaction_missing", "DELETE FROM ZMESSAGEREACTION WHERE Z_PK = (SELECT MIN(Z_PK) FROM ZMESSAGEREACTION)", None, "reactions_missing"),
    ("reaction_creator", "UPDATE ZMESSAGEREACTION SET ZCREATOR = NULL WHERE Z_PK = (SELECT MIN(Z_PK) FROM ZMESSAGEREACTION WHERE ZCREATOR IS NOT NULL)", None, "reactions_missing"),
    ("last_update", "UPDATE ZCONVERSATION SET ZLASTUPDATE = NULL WHERE Z_PK = (SELECT MIN(ZCONVERSATION) FROM ZMESSAGE)", None, "conversation_with_messages_has_lastUpdate"),
    ("z_max", "UPDATE Z_PRIMARYKEY SET Z_MAX = 1 WHERE Z_NAME = 'Message'", None, "z_primarykey_max_ge_max_pk"),
    ("blob_placeholder", "UPDATE ZMESSAGE SET ZBLOBID = X'00112233445566778899AABBCCDDEEFF' WHERE Z_PK = (SELECT MIN(Z_PK) FROM ZMESSAGE WHERE Z_ENT={F} AND ZDATAAVAILABLE1 = 0 AND ZDELETEDAT IS NULL)", None, "placeholder_blobId_nil"),
    ("star", "UPDATE ZMESSAGEMARKERS SET ZSTAR = 0", None, "starred"),
    ("nonce", "DELETE FROM ZNONCE WHERE Z_PK = (SELECT MIN(Z_PK) FROM ZNONCE)", None, "nonces_present"),
    ("public_key", "UPDATE ZCONTACT SET ZPUBLICKEY = zeroblob(32) WHERE Z_PK = (SELECT MIN(Z_PK) FROM ZCONTACT)", None, "contact_public_key"),
]


@pytest.mark.parametrize("name,sql,fn,expect", CASES, ids=[c[0] for c in CASES])
def test_tamper_detected(name, sql, fn, expect):
    if sql:
        sql = sql.replace("{T}", str(ent("TextMessage"))).replace("{S}", str(ent("SystemMessage"))) \
                 .replace("{L}", str(ent("LocationMessage"))).replace("{F}", str(ent("FileMessage")))
    d = tampered(name, sql, fn)
    rc, rep = run_verify(d, "--no-coredata")
    assert rc == 1, f"tampering '{name}' not detected"
    assert expect in rep["failures"], (expect, rep["failures"])


def test_corrupt_db_detected():
    def trash(d):
        p = os.path.join(d, "ThreemaData.sqlite")
        size = os.path.getsize(p)
        with open(p, "r+b") as h:          # overwrite a page in the middle (not the header)
            h.seek((size // 4096 // 2) * 4096)
            h.write(os.urandom(4096))
    d = tampered("corrupt", fn=trash)
    p = subprocess.run([sys.executable, "-m", "tmcore.lib.verify_import", "--normalized",
                        os.path.join(FIX, "normalized.sqlite"), "--work-dir", FIX, "--store", d, "--momd",
                        str(support.momd())], capture_output=True, text=True, timeout=110, cwd=support.CORE,
                       env=support.tmcore_env(TMCORE_IMPORTER=str(support.importer())))
    assert p.returncode != 0


# ---- review fixes (docs/review-resolution.md) -------------------------------------------------------------------
def test_ballot_creator_checked():                                     # appsafety m1
    d = tampered("ballot_creator", "UPDATE ZBALLOT SET ZCREATORID='ZZZZZZZZ' WHERE Z_PK=(SELECT min(Z_PK) FROM ZBALLOT);")
    rc, rep = run_verify(d, "--no-coredata")
    assert rc == 1 and "ballot_creator" in rep["failures"]


def test_group_members_checked():                                      # appsafety m3
    d = tampered("group_member", "DELETE FROM Z_6GROUPCONVERSATIONS WHERE rowid=(SELECT min(rowid) FROM Z_6GROUPCONVERSATIONS);")
    rc, rep = run_verify(d, "--no-coredata")
    assert rc == 1 and "group_members_equal_members_json" in rep["failures"]


def _placeholder_sql(own):
    fe = ent("FileMessage")
    return (f"UPDATE ZMESSAGE SET ZSENDFAILED=1 WHERE Z_PK=(SELECT min(Z_PK) FROM ZMESSAGE WHERE Z_ENT={fe} "
            f"AND ZISOWN={own} AND ZBLOBID IS NULL AND ZDATA IS NULL);")


def test_post_launch_tolerates_only_app_writes():                      # appsafety m6 / M1
    d = tampered("pl_incoming_placeholder", _placeholder_sql(0))
    rc, rep = run_verify(d, "--no-coredata")
    assert rc == 1 and "sendFailed_0" in rep["failures"]               # strict mode: detected
    rc, rep = run_verify(d, "--no-coredata", "--post-launch")
    assert rc == 0 and rep["post_launch_tolerated"] == {"incoming_placeholder_blobError": 1}
    d2 = tampered("pl_own_placeholder", _placeholder_sql(1))
    rc, rep = run_verify(d2, "--no-coredata", "--post-launch")
    assert rc == 1 and "sendFailed_0" in rep["failures"]               # own rows stay strict without --probe
    rc, rep = run_verify(d2, "--no-coredata", "--post-launch", "--probe")
    assert rc == 0
    d3 = tampered("pl_own_text", "UPDATE ZMESSAGE SET ZSENDFAILED=1 WHERE Z_PK=(SELECT min(Z_PK) FROM ZMESSAGE "
                                 f"WHERE Z_ENT={ent('TextMessage')} AND ZISOWN=1);")
    rc, rep = run_verify(d3, "--no-coredata", "--post-launch", "--probe")
    assert rc == 1 and "sendFailed_0" in rep["failures"]
