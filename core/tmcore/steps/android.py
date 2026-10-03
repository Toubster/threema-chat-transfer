# SPDX-License-Identifier: AGPL-3.0-or-later
"""
android-inspect / android-normalize (S05, S06). Owner: coreA (devtools/OWNERSHIP.md).

android-inspect FILE...                (no password; reads only the zip central directories)
    check android_file per file (kind), android_text, android_media; result files[], plan, text_ref, media_refs.
    No file with chats: all incomplete -> E_ANDROID_INCOMPLETE, media only -> E_ANDROID_NO_TEXT; only unknown files
    -> ok with plan "none" (the app shows the file kinds).

android-normalize --plan '{"text_ref":0,"media_refs":[1]}' FILE...     (secrets: android_passwords by argv index)
    read:   file complete + has chats (E_ANDROID_INCOMPLETE / E_ANDROID_NO_TEXT), password per file
            (E_ANDROID_PASSWORD ref), format of the chat backup vs compat/android.json
            (E_ANDROID_FORMAT_NEW / E_ANDROID_FORMAT_UNVERIFIED)
    media:  lib.android_normalize in-process -> <session>/android/ (normalized.sqlite, media/, thumbs/, avatars/)
    verify: lib.verify_normalized; counts for S06; android/missing-senders.json (0600, the only file with Threema IDs
            the UI may show, DESIGN §5.2); notes W_ANDROID_MEDIA_PARTIAL, W_OWN_UNSENT_AS_SENT, W_MISSING_KEY_SENDERS
    engine.json android {normalized_at, own_id (hash), plan, messages, media_present}; phase android_done;
    a previous `prepared` set is dropped (it was built from other chats).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from pathlib import Path

from tmcore.cli import Context, StepResult
from tmcore.lib import android_normalize as an
from tmcore.lib import validate_android as va
from tmcore.lib import verify_normalized as vn
from tmcore.protocol import EngineError, utc_now

# own messages Android never delivered; the importer shows them as sent (importer: own_unsent_state_normalized_to_sent)
OWN_UNSENT_STATES = ("SENDFAILED", "PENDING", "SENDING", "UPLOADING", "TRANSCODING", "FS_KEY_MISMATCH")
MISSING_SENDERS_KEY = "android/missing-senders.json"
MEDIA_PARALLEL_FROM = 200          # media entries; below that one process is faster than a pool


def _iso(ms: int | None) -> str | None:
    if not ms:
        return None
    t = time.gmtime(ms / 1000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", t) + ".%03dZ" % (ms % 1000)


def _log(msg: str) -> None:
    print("[android] " + msg, file=sys.stderr, flush=True)       # stderr = redacted debug log


# ------------------------------------------------------------------------------------------------ inspect
def inspect(ctx: Context) -> StepResult:
    proto = ctx.proto
    proto.phase("inspect", 1, 1)
    files = list(ctx.args.files)
    classified = []
    out_files = []
    for ref, path in enumerate(files):
        proto.check_cancel()
        c = va.classify(path)
        classified.append((ref, c))
        status = "pass" if c["kind"] in ("full", "text", "media") else "fail"
        code = "E_ANDROID_INCOMPLETE" if c["kind"] == "incomplete" else None
        proto.check("android_file", status, code, ref=ref, kind=c["kind"])
        out_files.append({"ref": ref, "kind": c["kind"], "format_version": None, "created_at": _iso(c["created_at_ms"]),
                          "bytes": c["bytes"], "has_media": c["has_media"], "encrypted": c["encrypted"]})
    plan, text_ref, media_refs = va.plan(classified)
    counts = {k: sum(1 for _, c in classified if c["kind"] == k) for k in ("full", "text", "media", "incomplete",
                                                                         "unknown")}
    counts["files"] = len(files)
    if plan == "none":
        incomplete = [r for r, c in classified if c["kind"] == "incomplete"]
        media_only = [r for r, c in classified if c["kind"] == "media"]
        err = (EngineError("E_ANDROID_NO_TEXT", ref=media_only[0]) if media_only
               else EngineError("E_ANDROID_INCOMPLETE", ref=incomplete[0]) if incomplete else None)
        proto.check("android_text", "fail", err.code if err else None)
        if ctx.session:
            ctx.session.write_report("android_inspect", ctx.cmd, err.code if err else "R_OK", counts=counts)
        if err:
            raise err
        return StepResult(data={"files": out_files, "plan": plan, "text_ref": None, "media_refs": []})
    else:
        proto.check("android_text", "pass", ref=text_ref)
        proto.check("android_media", "pass" if media_refs else "warn", files=len(media_refs))
    if ctx.session:
        ctx.session.write_report("android_inspect", ctx.cmd, "R_OK", counts=counts)
    return StepResult(data={"files": out_files, "plan": plan, "text_ref": text_ref, "media_refs": media_refs})


# ------------------------------------------------------------------------------------------------ normalize
def _parse_plan(raw: str, n_files: int) -> tuple[int, list[int]]:
    try:
        plan = json.loads(raw)
    except ValueError:
        raise EngineError("E_PROTOCOL", sub="plan") from None
    if not isinstance(plan, dict) or set(plan) - {"text_ref", "media_refs", "plan"}:
        raise EngineError("E_PROTOCOL", sub="plan")
    text_ref = plan.get("text_ref")
    media_refs = plan.get("media_refs", [])
    ok = (isinstance(text_ref, int) and not isinstance(text_ref, bool) and 0 <= text_ref < n_files
          and isinstance(media_refs, list)
          and all(isinstance(r, int) and not isinstance(r, bool) and 0 <= r < n_files for r in media_refs)
          and len(set(media_refs)) == len(media_refs))
    if not ok:
        raise EngineError("E_PROTOCOL", sub="plan")
    assert isinstance(text_ref, int)
    return text_ref, list(media_refs)


def _format_rules(ctx: Context) -> tuple[set[int], int]:
    try:
        c = ctx.compat("android")
        return set(int(v) for v in c.get("verified_formats", [])), int(c.get("max_known_format", 0))
    except (OSError, ValueError, TypeError):
        return set(), 0          # unreadable allow-list: nothing is verified (fail closed)


def _plan_digest(paths: list[str], text_ref: int, media_refs: list[int]) -> str:
    """Identifies one normalize input (paths, sizes, mtimes) without storing the paths themselves."""
    h = hashlib.sha256(json.dumps([text_ref, media_refs]).encode())
    for p in paths:
        st = os.stat(p)
        h.update(os.fsencode(os.path.abspath(p)) + b"\0" + str((st.st_size, st.st_mtime_ns)).encode() + b"\0")
    return h.hexdigest()


def _prepare_out_dir(out: Path, digest: str) -> None:
    """Fresh android/ for a new input; same input again -> keep the extracted media (resumable extraction)."""
    marker = out / ".input-digest"
    same = marker.is_file() and marker.read_text().strip() == digest
    for child in list(out.iterdir()) if out.is_dir() else []:
        if same and child.name in ("media", "thumbs", ".extract-manifest.jsonl", ".input-digest"):
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    out.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(digest + "\n")


def _counts(db_path: Path) -> tuple[dict, list[str], str]:
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        q = lambda sql, *a: db.execute(sql, a).fetchone()[0]  # noqa: E731
        meta = dict(db.execute("SELECT key, value FROM meta"))
        own = meta.get("own_identity") or ""
        contacts = {r[0] for r in db.execute("SELECT identity FROM contacts")}
        missing = [(s, ck) for s, ck in db.execute(
            "SELECT sender, chat_key FROM messages WHERE sender IS NOT NULL AND is_own = 0")
            if s not in contacts and s != own]
        placeholders = ",".join("?" * len(OWN_UNSENT_STATES))
        c = {
            "chats": q("SELECT count(DISTINCT chat_key) FROM messages WHERE chat_kind = 'contact'"),
            "groups": q('SELECT count(*) FROM "groups"'),
            "messages": q("SELECT count(*) FROM messages"),
            "media_total": q("SELECT count(*) FROM messages WHERE kind = 'file' AND deleted_ms IS NULL"),
            "media_present": q("SELECT count(*) FROM messages WHERE kind = 'file' AND deleted_ms IS NULL "
                               "AND media_path IS NOT NULL"),
            "polls": q("SELECT count(*) FROM ballots"),
            "own_unsent_as_sent": q(f"SELECT count(*) FROM messages WHERE is_own = 1 AND upper(ifnull(state, '')) "
                                    f"IN ({placeholders})", *OWN_UNSENT_STATES),
            "missing_key_senders": len({s for s, _ in missing}),
            "missing_key_messages": len(missing),
            "missing_key_groups": len({ck for _, ck in missing}),
        }
        return c, sorted({s for s, _ in missing}), own
    finally:
        db.close()


def _write_private_json(path: Path, obj) -> None:
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def normalize(ctx: Context) -> StepResult:
    proto, session = ctx.proto, ctx.session
    files = [os.path.abspath(p) for p in ctx.args.files]
    text_ref, media_refs = _parse_plan(ctx.args.plan, len(files))
    refs = [text_ref] + [r for r in media_refs if r != text_ref]
    passwords = {files[r]: ctx.secrets.android(r) for r in refs}       # E_SECRETS_MISSING when one is absent

    # ---- read: completeness, chats, passwords, format --------------------------------------------------------
    proto.phase("read", 1, 3)
    for r in refs:
        c = va.classify(files[r])
        if c["kind"] in ("incomplete", "unknown"):
            proto.check("android_file", "fail", "E_ANDROID_INCOMPLETE" if c["kind"] == "incomplete" else None,
                        ref=r, kind=c["kind"])
            raise EngineError("E_ANDROID_INCOMPLETE", ref=r)
        proto.check("android_file", "pass", ref=r, kind=c["kind"])
        if r == text_ref and not c["has_text"]:
            proto.check("android_text", "fail", "E_ANDROID_NO_TEXT", ref=r)
            raise EngineError("E_ANDROID_NO_TEXT", ref=r)
    for r in refs:
        proto.check_cancel()
        try:
            z, _ = an.open_zip(files[r], an.password_candidates(passwords[files[r]]))
            z.close()
        except an.NormalizeError:
            proto.check("android_password", "fail", "E_ANDROID_PASSWORD", ref=r)
            raise EngineError("E_ANDROID_PASSWORD", ref=r) from None
        proto.check("android_password", "pass", ref=r)
    verified, max_known = _format_rules(ctx)
    version = an.read_format_version(files[text_ref], an.password_candidates(passwords[files[text_ref]]))
    if version is not None and max_known and version > max_known:
        proto.check("android_format", "fail", "E_ANDROID_FORMAT_NEW", ref=text_ref, format_version=version)
        raise EngineError("E_ANDROID_FORMAT_NEW", ref=text_ref, format_version=version)
    if version is None or version not in verified:
        proto.check("android_format", "fail", "E_ANDROID_FORMAT_UNVERIFIED", ref=text_ref, format_version=version)
        raise EngineError("E_ANDROID_FORMAT_UNVERIFIED", ref=text_ref, format_version=version)
    proto.check("android_format", "pass", ref=text_ref, format_version=version)

    # ---- media: normalize in-process ---------------------------------------------------------------------------
    proto.phase("media", 2, 3)
    out = session.path("android")
    _prepare_out_dir(out, _plan_digest(files, text_ref, media_refs))
    last = [0.0]

    def progress(done: int, total: int) -> None:
        now = time.monotonic()
        if done == total or now - last[0] >= 0.5:
            last[0] = now
            proto.progress("media", done, total, unit="files")
        proto.check_cancel()

    media_paths = [files[r] for r in media_refs if r != text_ref]
    jobs = min(4, os.cpu_count() or 1) if sum(va.classify(files[r])["entries"] for r in refs) > MEDIA_PARALLEL_FROM \
        else 1
    try:
        rep = an.normalize(files[text_ref], media_paths, passwords=passwords, out_dir=str(out),
                           no_media=not media_refs, jobs=jobs, log=_log, progress=progress)
    except an.NormalizeError as e:
        if e.kind == "password":
            bad = files.index(e.info["path"]) if e.info.get("path") in files else text_ref
            raise EngineError("E_ANDROID_PASSWORD", ref=bad) from None
        if e.kind == "format_new":
            raise EngineError("E_ANDROID_FORMAT_NEW", ref=text_ref,
                              format_version=e.info.get("format_version")) from None
        if e.kind == "no_settings":
            raise EngineError("E_ANDROID_FORMAT_UNVERIFIED", ref=text_ref, format_version=None) from None
        if e.kind == "no_media_password":
            raise EngineError("E_SECRETS_MISSING", sub="android_passwords") from None
        raise EngineError("E_INTERNAL", sub=e.kind) from None
    finally:
        passwords.clear()
    media_errors = sum((rep.get("media") or {}).get("errors", {}).values()) if isinstance(rep.get("media"), dict) \
        else 0

    # ---- verify: independent checks + counts for S06 -------------------------------------------------------------
    proto.phase("verify", 3, 3)
    res = vn.verify(str(out))
    counts, missing_ids, own = _counts(out / "normalized.sqlite")
    if res["result"] != "PASS" or not own:
        proto.check("android_text", "fail", None, messages=counts["messages"], failed=len(res["fails"]))
        raise EngineError("E_INTERNAL", sub="verify_normalized")
    proto.check("android_text", "pass", chats=counts["chats"], groups=counts["groups"],
                messages=counts["messages"], polls=counts["polls"])
    media_ok = counts["media_present"] == counts["media_total"]
    proto.check("android_media", "pass" if media_ok else "warn", None if media_ok else "W_ANDROID_MEDIA_PARTIAL",
                media_present=counts["media_present"], media_total=counts["media_total"], errors=media_errors)
    _write_private_json(out / "missing-senders.json",
                        {"schema": 1, "identities": missing_ids, "groups": counts["missing_key_groups"],
                         "messages": counts["missing_key_messages"]})
    if not media_ok:
        proto.note("W_ANDROID_MEDIA_PARTIAL", media_present=counts["media_present"], media_total=counts["media_total"])
    if counts["own_unsent_as_sent"]:
        proto.note("W_OWN_UNSENT_AS_SENT", own_unsent_as_sent=counts["own_unsent_as_sent"])
    if counts["missing_key_senders"]:
        proto.note("W_MISSING_KEY_SENDERS", missing_key_groups=counts["missing_key_groups"],
                   missing_key_messages=counts["missing_key_messages"],
                   missing_key_senders=counts["missing_key_senders"])

    own_id = session.hasher.h(own)
    plan_kind = "text_plus_media" if media_paths else "single"
    with session.update_engine() as st:
        st["android"] = {"normalized_at": utc_now(), "own_id": own_id, "plan": plan_kind,
                         "messages": counts["messages"], "media_present": counts["media_present"]}
        st["prepared"] = None
        session.advance_phase(st, "android_done")
    codes = [c for c, cond in (("W_ANDROID_MEDIA_PARTIAL", not media_ok),
                               ("W_OWN_UNSENT_AS_SENT", counts["own_unsent_as_sent"]),
                               ("W_MISSING_KEY_SENDERS", counts["missing_key_senders"])) if cond]
    session.write_report("android_normalize", ctx.cmd, "R_OK", counts={**counts, "media_errors": media_errors,
                                                                       "format_version": version,
                                                                       "verify_checks": res["checks"]},
                         codes=codes)
    data = {**counts, "own_id": own_id}
    if counts["missing_key_senders"]:
        data["missing_senders_file"] = MISSING_SENDERS_KEY
    return StepResult(data=data)
