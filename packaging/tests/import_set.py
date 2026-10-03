# SPDX-License-Identifier: AGPL-3.0-or-later
"""import_set.py -- run by the BUNDLED interpreter (`python3 -I -B import_set.py`) from packaging/tests/check-core.sh.

Imports every module the engine uses (tmcore and its libraries, pymobiledevice3 services, backup crypto) and checks
that the interpreter only sees the bundle: sys.path, the modules' files and the dist versions. Prints one JSON line
with counts and versions (no paths). Exit 1 on any failure.
"""
import importlib
import importlib.metadata as md
import json
import os
import sys

BUNDLE = os.path.realpath(sys.argv[1])
MODULES = [
    # engine
    "tmcore", "tmcore.cli", "tmcore.protocol", "tmcore.session", "tmcore.secrets", "tmcore.netguard", "tmcore.redact",
    "tmcore.hashing", "tmcore.lib.android_normalize", "tmcore.lib.backup_pipeline", "tmcore.lib.iosbackup_rw",
    "tmcore.lib.backup_diff", "tmcore.lib.verify_import", "tmcore.lib.verify_normalized", "tmcore.lib.validate_android",
    "tmcore.verdict", "tmcore.guards", "tmcore.steps.host", "tmcore.steps.android", "tmcore.steps.device",
    "tmcore.steps.encryption", "tmcore.steps.backup", "tmcore.steps.prepare", "tmcore.steps.restore",
    "tmcore.steps.postcheck", "tmcore.steps.rollback", "tmcore.steps.status", "tmcore.steps.diag",
    "tmcore.steps.cleanup", "tmcore.steps.iphone", "tmcore.fake.device",
    # device (DESIGN §3.1: usbmux/lockdown/mobilebackup2/afc + status reads)
    "pymobiledevice3.usbmux", "pymobiledevice3.lockdown", "pymobiledevice3.exceptions",
    "pymobiledevice3.services.mobilebackup2", "pymobiledevice3.services.afc",
    "pymobiledevice3.services.installation_proxy", "pymobiledevice3.services.mobile_config",
    "pymobiledevice3.services.diagnostics",
    # backups and Android archives
    "pyiosbackup", "pyiosbackup.entry", "pyiosbackup.manifest_dbs.sqlite3", "iphone_backup_decrypt",
    "iphone_backup_decrypt.utils", "cryptography.hazmat.primitives.keywrap", "cryptography.hazmat.primitives.ciphers",
    "pyzipper", "Crypto.Cipher.AES", "Cryptodome.Cipher.AES",
    # stdlib parts the engine relies on
    "sqlite3", "ssl", "plistlib", "hmac", "socket", "lzma", "zlib", "ctypes",
]
DISTS = ["pymobiledevice3", "cryptography", "pyiosbackup", "iphone_backup_decrypt", "pyzipper", "construct"]

fail = []
for m in MODULES:
    try:
        importlib.import_module(m)
    except Exception as e:  # noqa: BLE001
        fail.append(f"{m}: {type(e).__name__}")

outside_path = [p for p in sys.path if p and not os.path.realpath(p).startswith(BUNDLE)]
outside_mod = sorted({name.split(".")[0] for name, mod in list(sys.modules.items()) if name != "__main__"
                      if getattr(mod, "__file__", None) and not os.path.realpath(mod.__file__).startswith(BUNDLE)})
versions = {}
for d in DISTS:
    try:
        versions[d] = md.version(d)
    except md.PackageNotFoundError:
        fail.append(f"dist {d} missing")
flags = {"isolated": bool(sys.flags.isolated), "dont_write_bytecode": bool(sys.dont_write_bytecode),
         "no_user_site": bool(sys.flags.no_user_site)}
ok = not fail and not outside_path and not outside_mod and all(flags.values())
print(json.dumps({"ok": ok, "modules": len(MODULES), "loaded": len(sys.modules), "failed": fail,
                  "outside_sys_path": len(outside_path), "outside_modules": outside_mod, "flags": flags,
                  "python": sys.version.split()[0], "versions": versions}, sort_keys=True))
sys.exit(0 if ok else 1)
