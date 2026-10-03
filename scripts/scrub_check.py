#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""
scrub_check.py -- privacy scan for the public repository (DESIGN §10.3).

Two layers:

  Layer 1  generic patterns that live in this repo and run everywhere (CI, forks, hooks):
           UDIDs, SHA1/old UDIDs, ECIDs, Threema-ID-shaped tokens, e-mail addresses, home paths,
           device names, phone numbers, private IPs, secrets, binary files, images, example counts in docs/,
           the repository account outside a repository link (the account is read from NOTICE: it may appear only
           in https://github.com/<account>/... URLs), hard-coded developer workspace paths ($HOME/<folder>/...).
           With --publish-identity (pre-push, publishing): every commit of --commits must use a GitHub no-reply
           identity whose name is the account handle, and +0000 author/committer dates (no time-zone hint).
  Layer 2  deny list of REAL values that never lives in the repo: a file of HMAC-SHA256 digests
           (key and list are local, ~/.config/tm-scrub/, or the CI secrets SCRUB_DENY_B64 / SCRUB_KEY).
           Every candidate token of a scanned file is normalised, HMAC'd with the same key and compared.

Output names file, line and rule -- never the matched text. Exit 0 = clean, 1 = findings, 2 = usage/setup error.

Examples:
  scripts/scrub_check.py                      # all tracked + untracked (not ignored) files, layer 1 (+2 if available)
  scripts/scrub_check.py --staged             # staged blobs (pre-commit hook)
  scripts/scrub_check.py --require-layer2 --commits origin/main..HEAD   # pre-push
  scripts/scrub_check.py --no-files --publish-identity --commits HEAD   # history to be published
  scripts/scrub_check.py --paths some/dir other/file
  scripts/scrub_check.py --text-stdin release-notes < notes.md
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import struct
import subprocess
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ALLOWLIST_FILE = REPO / "scripts" / "scrub-allowlist.txt"
DENY_DIR = Path(os.environ.get("TM_SCRUB_DIR", Path.home() / ".config" / "tm-scrub"))
DENY_FILE = DENY_DIR / "deny.hmac"
KEY_FILE = DENY_DIR / "key"

# Directories never scanned when walking a tree outside git (build output, venvs, caches).
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".build",
             "build", "DerivedData", "node_modules", ".swiftpm"}

# ----------------------------------------------------------------------------------------------------------------
# Layer 1 rules
# ----------------------------------------------------------------------------------------------------------------
RX_UDID_NEW = re.compile(r"\b[0-9A-F]{8}-[0-9A-F]{16}\b", re.I)
RX_SHA1 = re.compile(r"\b[0-9a-f]{40}\b", re.I)
RX_ECID = re.compile(r"(?i)\becid\b.{0,20}\b(0x)?[0-9a-f]{11,16}\b")
# 8 characters from [0-9A-Z*], not glued to other letters or digits ("_" counts as a separator: avatar_<ID>).
RX_TID = re.compile(r"(?<![0-9A-Za-z*])[0-9A-Z*]{8}(?![0-9A-Za-z*])")
RX_EMAIL = re.compile(r"(?<![\w.%+-])[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b")
# character classes like [U] keep this source file from matching its own rules
RX_HOME = re.compile(r"/[U]sers/(?!Shared/|runner/)([^/\s\"']+)|/[h]ome/([^/\s\"']+)|[C]:\\\\[U]sers\\\\|[C]:\\[U]sers\\")
RX_DEVNAME_EN = re.compile(r"(?i)\b\w+['\u2019]s (iPhone|iPad|Mac(Book)?)\b")
# German genitive without apostrophe ("<Name>s iPhone"); articles/adjectives ("das iPhone", "ein anderes iPhone") are
# not names and are skipped (refinement of DESIGN §10.3, which would flag every "das iPhone").
RX_DEVNAME_DE = re.compile(r"\b(?!(?i:das|was|bis|als|aus|ins|ans|aufs|fürs|ums|this|his|its|is|has|does|plus|yours|thus|\w+es)\s)\w+s (iPhone|iPad)\b")
RX_PHONE = re.compile(r"\+\d{2}[\d \-]{7,}")
RX_PRIVATE_IP = re.compile(r"(?<![\d.])192\.168\.|(?<![\d.])10\.\d+\.\d+\.\d+|(?<![\d.])172\.(1[6-9]|2\d|3[01])\.")
SECRET_RULES = [
    ("secret_private_key", re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*?PRIVATE KEY(?: BLOCK)?-----")),
    ("secret_aws", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("secret_github", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})\b")),
    ("secret_slack", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("secret_google", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
    ("secret_openai_anthropic", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_\-]{20,}")),
    ("secret_jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}")),
    ("secret_telegram", re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b")),
    ("secret_password_literal", re.compile(r"(?i)password\s*[:=]\s*[\"'](?![a-z][a-z0-9]*(?:_[a-z0-9]+)+[\"'])[^\"']{4,}")),
]
# The canonical repository URL in NOTICE names the account; the account must not appear anywhere else.
RX_REPO_URL = re.compile(r"https://github\.com/([A-Za-z0-9-]+)/threema-chat-transfer\b")
# Hard-coded developer workspace below the home folder ("$HOME/<folder>/...", "~/<folder>/..."): reveals a private
# folder layout. Standard locations stay allowed (Library, .config, .cache, .local, Desktop, Downloads, Documents).
RX_HOME_WORKSPACE = re.compile(r"(?:\$HOME|\$\{HOME\}|(?<![\w/.~])~)/(?!Library/|\.config/|\.cache/|\.local/|Desktop/|"
                               r"Downloads/|Documents/|\.ssh/|\.gnupg/)[A-Za-z0-9_.-]+/")
RX_NOREPLY = re.compile(r"^(?:\d+\+)?([A-Za-z0-9-]+)@users\.noreply\.github\.com$", re.I)
ALLOWED_PHONE = "+41 00 000 00 00"
ALLOWED_EMAIL_DOMAINS_BUILTIN = ("example.com", "example.org", "example.net", "users.noreply.github.com")
# Count nouns for the docs/ example-number rule (§10.4).
RX_DOC_COUNT = re.compile(
    r"(?<![\w.,'\u2019])(\d{1,3}(?:[ \u00a0\u202f'\u2019.,]\d{3})+|\d+)\s*"
    r"(Nachrichten|Nachricht|messages?|Medien|media|Chats?|chats?|Gruppen|groups?|Umfragen|polls?|Dateien|files?|"
    r"Kontakte|contacts?|Zeilen|rows?|Einträge|entries|Konversationen|conversations?)\b")
CANONICAL_COUNTS = {"18", "4", "2", "12345", "1234"}
TEXT_EXT_BINARY_SNIFF = 8192
IMAGE_EXT = {".png", ".jpg", ".jpeg"}


@dataclass
class Allowlist:
    ids: set = field(default_factory=set)
    email_domains: set = field(default_factory=set)
    emails: set = field(default_factory=set)            # exact public no-reply addresses (commit trailers)
    binaries: dict = field(default_factory=dict)        # sha256 -> path (informational)
    password_files: set = field(default_factory=set)     # repo-relative paths where the password rule is waived
    account: str = ""                                    # repository account (NOTICE), lower case; "" = rule off

    @classmethod
    def load(cls, path: Path = ALLOWLIST_FILE) -> "Allowlist":
        al = cls()
        al.password_files.add("fixtures/canaries.json")  # the canary password (§10.3), nothing else
        if not path.exists():
            return al
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            kind, _, rest = line.partition(":")
            rest = rest.strip()
            if kind == "id":
                al.ids.add(rest)
            elif kind == "email-domain":
                al.email_domains.add(rest.lower())
            elif kind == "email":
                al.emails.add(rest.lower())
            elif kind == "sha256":
                digest, _, p = rest.partition(" ")
                al.binaries[digest.strip().lower()] = p.strip()
            else:
                raise SystemExit(f"scrub-allowlist.txt: unknown entry kind {kind!r}")
        return al


@dataclass
class Finding:
    path: str
    line: int
    rule: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.rule}"


def _is_tid_candidate(tok: str) -> bool:
    if tok.startswith("*"):
        return len(tok) == 8 and re.fullmatch(r"\*[0-9A-Z]{7}", tok) is not None
    if "*" in tok:
        return False
    return any(c.isdigit() for c in tok) and any(c.isalpha() for c in tok)


def _tid_allowed(tok: str, al: Allowlist) -> bool:
    return tok.startswith("ZZ") or tok.startswith("*ZZ") or tok in al.ids


def _email_allowed(addr: str, al: Allowlist) -> bool:
    if addr.lower() in al.emails:
        return True
    dom = addr.rsplit("@", 1)[1].lower()
    for allowed in ALLOWED_EMAIL_DOMAINS_BUILTIN + tuple(al.email_domains):
        if dom == allowed or dom.endswith("." + allowed):
            return True
    return False


def repo_account(root: Path) -> str:
    try:
        m = RX_REPO_URL.search((root / "NOTICE").read_text(encoding="utf-8"))
    except OSError:
        return ""
    return m.group(1).lower() if m else ""


def _account_outside_url(text: str, account: str) -> bool:
    """True when the account handle occurs as its own token anywhere but right after "github.com/" (a link)"""
    low = text.lower()
    i = low.find(account)
    while i >= 0:
        j = i + len(account)
        glued = (i > 0 and low[i - 1].isalnum()) or (j < len(low) and (low[j].isalnum() or low[j] == "_"))
        in_link = low[max(0, i - 11):i] == "github.com/"
        if not glued and not in_link:
            return True
        i = low.find(account, i + 1)
    return False


def _home_allowed(m: re.Match) -> bool:
    seg = m.group(1) or m.group(2)
    if seg is None:  # Windows profile path -- always a finding
        return False
    # placeholders and regex/glob fragments, never a real name
    return seg[0] in "<[({$\\*." or seg in {"USER", "USERNAME", "you", "name", "example"}


def scan_text_layer1(rel: str, text: str, al: Allowlist) -> list[Finding]:
    out: list[Finding] = []
    in_docs = rel.startswith("docs/") and rel.endswith(".md")
    for no, line in enumerate(text.splitlines(), 1):
        if RX_UDID_NEW.search(line):
            out.append(Finding(rel, no, "udid_new"))
        if RX_SHA1.search(line) and "scrub:ok sha1" not in line:
            out.append(Finding(rel, no, "udid_old_or_sha1"))
        if RX_ECID.search(line):
            out.append(Finding(rel, no, "ecid"))
        for m in RX_TID.finditer(line):
            tok = m.group(0)
            if _is_tid_candidate(tok) and not _tid_allowed(tok, al):
                out.append(Finding(rel, no, "threema_id"))
                break
        for m in RX_EMAIL.finditer(line):
            if not _email_allowed(m.group(0), al):
                out.append(Finding(rel, no, "email"))
                break
        for m in RX_HOME.finditer(line):
            if not _home_allowed(m):
                out.append(Finding(rel, no, "home_path"))
                break
        if RX_DEVNAME_EN.search(line) or RX_DEVNAME_DE.search(line):
            out.append(Finding(rel, no, "device_name"))
        for m in RX_PHONE.finditer(line):
            if m.group(0).strip() != ALLOWED_PHONE:
                out.append(Finding(rel, no, "phone"))
                break
        if RX_PRIVATE_IP.search(line):
            out.append(Finding(rel, no, "private_ip"))
        if al.account and _account_outside_url(line, al.account):
            out.append(Finding(rel, no, "account_outside_repo_url"))
        if RX_HOME_WORKSPACE.search(line) and "scrub:ok workspace" not in line:
            out.append(Finding(rel, no, "home_workspace_path"))
        for name, rx in SECRET_RULES:
            if rx.search(line):
                if name == "secret_password_literal" and rel in al.password_files:
                    continue
                out.append(Finding(rel, no, name))
        if in_docs:
            for m in RX_DOC_COUNT.finditer(line):
                digits = re.sub(r"\D", "", m.group(1))
                if digits not in CANONICAL_COUNTS and int(digits) >= 100:
                    out.append(Finding(rel, no, "docs_example_count"))
                    break
    return out


# ----------------------------------------------------------------------------------------------------------------
# Binary files and images
# ----------------------------------------------------------------------------------------------------------------
def is_binary(data: bytes) -> bool:
    head = data[:TEXT_EXT_BINARY_SNIFF]
    if b"\x00" in head:
        return True
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def _png_text_chunks(data: bytes) -> list[bytes]:
    chunks, pos = [], 8
    while pos + 8 <= len(data):
        length, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if ctype in (b"tEXt", b"iTXt", b"zTXt"):
            chunks.append(body)
        if ctype == b"eXIf":
            chunks.append(b"EXIF:" + body)
        if ctype == b"iCCP":
            chunks.append(b"ICCP:")
        pos += 12 + length
    return chunks


def _exif_has_gps_or_serial(exif: bytes) -> bool:
    # minimal TIFF walk: look for GPS IFD pointer (0x8825) or serial-number tags (0xA431, 0xC62F) in IFD0/ExifIFD
    if len(exif) < 8:
        return False
    endian = "<" if exif[:2] == b"II" else ">"
    try:
        def ifd_tags(off: int) -> list[tuple[int, int]]:
            n = struct.unpack(endian + "H", exif[off:off + 2])[0]
            tags = []
            for i in range(n):
                e = off + 2 + 12 * i
                tag, _typ, _cnt, val = struct.unpack(endian + "HHII", exif[e:e + 12])
                tags.append((tag, val))
            return tags
        first = struct.unpack(endian + "I", exif[4:8])[0]
        tags = ifd_tags(first)
        sub = [v for t, v in tags if t == 0x8769]
        all_tags = [t for t, _ in tags] + ([t for t, _ in ifd_tags(sub[0])] if sub else [])
        return any(t in (0x8825, 0xA431, 0xC62F) for t in all_tags)
    except (struct.error, IndexError):
        return True  # unparsable EXIF: fail closed


def scan_image(rel: str, data: bytes) -> list[Finding]:
    out: list[Finding] = []
    markers = (b"tm-demo=1", b"tm-reviewed=1")
    exif_blobs: list[bytes] = []
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        texts = _png_text_chunks(data)
        has_marker = any(m in c for c in texts for m in markers)
        exif_blobs = [c[5:] for c in texts if c.startswith(b"EXIF:")]
        if any(c == b"ICCP:" for c in texts):
            # the display profile of the capturing Mac names the display model (.github/scripts/mark_png.py)
            out.append(Finding(rel, 0, "image_icc_profile"))
    elif data.startswith(b"\xff\xd8"):
        has_marker = any(m in data for m in markers)  # XMP packet or COM segment
        pos = 2
        while pos + 4 <= len(data) and data[pos] == 0xFF:
            marker, seglen = data[pos + 1], struct.unpack(">H", data[pos + 2:pos + 4])[0]
            if marker == 0xE1 and data[pos + 4:pos + 10] == b"Exif\x00\x00":
                exif_blobs.append(data[pos + 10:pos + 2 + seglen])
            if marker == 0xDA:
                break
            pos += 2 + seglen
    else:
        return [Finding(rel, 0, "image_unknown_format")]
    if not has_marker:
        out.append(Finding(rel, 0, "image_without_marker"))
    if any(_exif_has_gps_or_serial(b) for b in exif_blobs):
        out.append(Finding(rel, 0, "image_exif_gps_or_serial"))
    return out


# ----------------------------------------------------------------------------------------------------------------
# Layer 2: HMAC deny list. The token normalisation below is the contract with devtools/make_denylist.py
# (private repo), which imports these functions so both sides always agree.
# ----------------------------------------------------------------------------------------------------------------
TOKEN_VERSION = 1
RX_WORD = re.compile(r"[\w*](?:[\w.@+*'\u2019\-]*[\w*])?")
RX_SPLIT = re.compile(r"[.@+'\u2019\-]+")
RX_ALPHA = re.compile(r"[^\W\d_]+")
RX_NUMBER = re.compile(r"(?<![\w])(\d{1,3}(?:[ \u00a0\u202f'\u2019.,]\d{3})+|\d+)(?![\w])")
PHRASE_MAX = 4


def norm(s: str) -> str:
    return unicodedata.normalize("NFKC", s).replace("\u2019", "'").casefold()


def word_key(w: str) -> str:
    return "w:" + norm(w)


def phrase_key(words: list[str]) -> str:
    return "p:" + " ".join(norm(w) for w in words)


def number_key(n: int) -> str:
    return f"n:{n}"


def candidate_keys(line: str) -> set[str]:
    keys: set[str] = set()
    for m in RX_WORD.finditer(line):
        tok = m.group(0)
        keys.add(word_key(tok))
        for sub in RX_SPLIT.split(tok):
            if sub:
                keys.add(word_key(sub))
    alpha = RX_ALPHA.findall(line)
    for n in range(2, PHRASE_MAX + 1):
        for i in range(len(alpha) - n + 1):
            keys.add(phrase_key(alpha[i:i + n]))
    for m in RX_NUMBER.finditer(line):
        digits = re.sub(r"\D", "", m.group(1))
        if digits and len(digits) <= 18 and int(digits) >= 100:
            keys.add(number_key(int(digits)))
    return keys


def key_class(k: str) -> str:
    return {"w": "word", "p": "phrase", "n": "number"}.get(k[:1], "token")


def hmac_hex(key: bytes, token_key: str) -> str:
    return hmac.new(key, token_key.encode("utf-8"), hashlib.sha256).hexdigest()


@dataclass
class DenyList:
    key: bytes
    digests: set

    @classmethod
    def load(cls) -> "DenyList | None":
        env_deny, env_key = os.environ.get("SCRUB_DENY_B64"), os.environ.get("SCRUB_KEY")
        if env_deny and env_key:
            text = base64.b64decode(env_deny).decode("ascii")
            key = base64.b64decode(env_key) if not re.fullmatch(r"[0-9a-f]{64}", env_key) else bytes.fromhex(env_key)
        elif DENY_FILE.exists() and KEY_FILE.exists():
            text = DENY_FILE.read_text(encoding="ascii")
            key = bytes.fromhex(KEY_FILE.read_text(encoding="ascii").strip())
        else:
            return None
        digests = {ln.strip() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")}
        return cls(key, digests)


def scan_text_layer2(rel: str, text: str, deny: DenyList) -> list[Finding]:
    out: list[Finding] = []
    for no, line in enumerate(text.splitlines(), 1):
        hit = None
        for k in candidate_keys(line):
            if hmac_hex(deny.key, k) in deny.digests:
                hit = key_class(k)
                break
        if hit:
            out.append(Finding(rel, no, f"deny_{hit}"))
    return out


def scan_bytes_layer2_extra(rel: str, data: bytes, deny: DenyList) -> list[Finding]:
    """Binary files: look for deny-listed WORDS/PHRASES hidden as ASCII, UTF-16LE or UTF-16BE strings (plists,
    archives, compiled models). Numbers are ignored here: random bytes produce digit runs all the time."""
    for enc in ("latin-1", "utf-16-le", "utf-16-be"):
        text = data.decode(enc, errors="ignore")
        for chunk in re.findall(r"[A-Za-z0-9*@.+'\- ]{4,}", text):
            for k in candidate_keys(chunk):
                if k.startswith("n:") or sum(ch.isalpha() for ch in k[2:]) < 4:
                    continue
                if hmac_hex(deny.key, k) in deny.digests:
                    return [Finding(rel, 0, f"deny_in_binary_{enc}")]
    return []


# ----------------------------------------------------------------------------------------------------------------
# Driver
# ----------------------------------------------------------------------------------------------------------------
def scan_blob(rel: str, data: bytes, al: Allowlist, deny: DenyList | None, layer: str) -> list[Finding]:
    findings: list[Finding] = []
    ext = Path(rel).suffix.lower()
    if is_binary(data):
        if layer in ("1", "all"):
            digest = hashlib.sha256(data).hexdigest()
            if digest not in al.binaries:
                findings.append(Finding(rel, 0, "binary_not_allowlisted"))
            if ext in IMAGE_EXT:
                findings += scan_image(rel, data)
            if al.account and any(_account_outside_url(data.decode(enc, errors="ignore"), al.account)
                                  for enc in ("latin-1", "utf-16-le")):
                findings.append(Finding(rel, 0, "account_outside_repo_url"))
        if deny and layer in ("2", "all"):
            findings += scan_bytes_layer2_extra(rel, data, deny)
        return findings
    text = data.decode("utf-8")
    if layer in ("1", "all"):
        findings += scan_text_layer1(rel, text, al)
    if deny and layer in ("2", "all"):
        findings += scan_text_layer2(rel, text, deny)
    return findings


def _git(*args: str, root: Path) -> bytes:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True).stdout


def iter_worktree(root: Path):
    try:
        out = _git("ls-files", "-z", "--cached", "--others", "--exclude-standard", root=root)
        for rel in sorted(set(p for p in out.decode("utf-8").split("\0") if p)):
            p = root / rel
            if p.is_file() and not p.is_symlink():
                yield rel, p.read_bytes()
    except (subprocess.CalledProcessError, FileNotFoundError):
        yield from iter_paths(root, [root])


def iter_paths(root: Path, paths: list[Path]):
    for base in paths:
        base = base.resolve()
        if base.is_file():
            yield _rel(root, base), base.read_bytes()
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
            for fn in sorted(filenames):
                p = Path(dirpath) / fn
                if p.is_file() and not p.is_symlink():
                    yield _rel(root, p), p.read_bytes()


def _rel(root: Path, p: Path) -> str:
    try:
        return p.relative_to(root.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def iter_staged(root: Path):
    out = _git("diff", "--cached", "--name-only", "-z", "--diff-filter=ACMR", root=root)
    for rel in [p for p in out.decode("utf-8").split("\0") if p]:
        yield rel, _git("show", f":{rel}", root=root)


def iter_commit_messages(root: Path, rng: str):
    out = _git("log", "--format=%H%x00%B%x01", rng, root=root).decode("utf-8", "replace")
    for i, entry in enumerate(e for e in out.split("\x01") if e.strip()):
        _sha, _, body = entry.strip("\n").partition("\x00")
        yield f"commit-message[{i}]", body.encode("utf-8")


def scan_commit_identities(root: Path, rng: str, al: Allowlist) -> list[Finding]:
    """--publish-identity: no-reply identity whose name is the handle, and UTC dates, for every commit of `rng`"""
    out: list[Finding] = []
    fmt = "%an%x00%ae%x00%cn%x00%ce%x00%ai%x00%ci%x01"
    log = _git("log", f"--format={fmt}", rng, root=root).decode("utf-8", "replace")
    for i, entry in enumerate(e.strip("\n") for e in log.split("\x01") if e.strip()):
        an, ae, cn, ce, ad, cd = entry.split("\x00")
        rel = f"commit-identity[{i}]"
        for name, email in ((an, ae), (cn, ce)):
            m = RX_NOREPLY.match(email)
            if email.lower() not in al.emails and not (m and m.group(1).lower() == name.lower()):
                out.append(Finding(rel, 0, "commit_identity"))
                break
        if not (ad.endswith("+0000") and cd.endswith("+0000")):
            out.append(Finding(rel, 0, "commit_timezone"))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=REPO)
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--paths", nargs="+", type=Path, help="scan these files/directories instead of the work tree")
    src.add_argument("--staged", action="store_true", help="scan staged blobs (pre-commit)")
    src.add_argument("--text-stdin", metavar="NAME", help="scan stdin as one text file called NAME")
    src.add_argument("--no-files", action="store_true", help="scan nothing but --commits")
    ap.add_argument("--commits", metavar="RANGE", help="also scan commit messages of this range")
    ap.add_argument("--publish-identity", action="store_true",
                    help="with --commits: require no-reply identities (name = handle) and +0000 dates")
    ap.add_argument("--layer", choices=("1", "2", "all"), default="all")
    ap.add_argument("--require-layer2", action="store_true", help="fail (exit 2) when no deny list is available")
    ap.add_argument("--json", action="store_true", help="print a counts-only JSON summary on stdout")
    ap.add_argument("--max-findings", type=int, default=200)
    a = ap.parse_args(argv)

    root = a.root.resolve()
    al = Allowlist.load(root / "scripts" / "scrub-allowlist.txt") if (root / "scripts").exists() else Allowlist.load()
    al.account = repo_account(root) or repo_account(REPO)
    deny = DenyList.load() if a.layer in ("2", "all") else None
    if a.layer == "2" and deny is None or a.require_layer2 and deny is None:
        print("scrub_check: layer 2 requested but no deny list (~/.config/tm-scrub/{deny.hmac,key} or "
              "SCRUB_DENY_B64/SCRUB_KEY)", file=sys.stderr)
        return 2

    if a.staged:
        blobs = iter_staged(root)
    elif a.text_stdin:
        blobs = iter([(a.text_stdin, sys.stdin.buffer.read())])
    elif a.paths:
        blobs = iter_paths(root, a.paths)
    elif a.no_files:
        blobs = iter([])
    else:
        blobs = iter_worktree(root)

    findings: list[Finding] = []
    files = 0
    for rel, data in blobs:
        files += 1
        findings += scan_blob(rel, data, al, deny, a.layer)
    if a.commits:
        for rel, data in iter_commit_messages(root, a.commits):
            files += 1
            findings += scan_blob(rel, data, al, deny, a.layer)
        if a.publish_identity:
            findings += scan_commit_identities(root, a.commits, al)
    elif a.publish_identity:
        print("scrub_check: --publish-identity needs --commits", file=sys.stderr)
        return 2

    for f in findings[: a.max_findings]:
        print(f, file=sys.stderr)
    if len(findings) > a.max_findings:
        print(f"... {len(findings) - a.max_findings} more", file=sys.stderr)
    summary = {"files": files, "findings": len(findings), "layer1": a.layer in ("1", "all"),
               "layer2": deny is not None, "rules": {}}
    for f in findings:
        summary["rules"][f.rule] = summary["rules"].get(f.rule, 0) + 1
    if a.json:
        print(json.dumps(summary, sort_keys=True))
    else:
        state = "CLEAN" if not findings else "FINDINGS"
        l2 = "on" if deny is not None else "off (no deny list)"
        print(f"scrub_check: {state} files={files} findings={len(findings)} layer2={l2}", file=sys.stderr)
    return 0 if not findings else 1


if __name__ == "__main__":
    sys.exit(main())
