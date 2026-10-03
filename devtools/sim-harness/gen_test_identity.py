#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Generate a THROWAWAY Threema identity (random NaCl/X25519 keypair) for the offline simulator harness.

Usage: gen_test_identity.py [--identity XXXXXXXX] [--server-group g] [--out path]
- --identity: 8-char Threema ID string to pose as. For group rendering fidelity use the SAME ID string the converted
  DB uses in ZCONVERSATION.ZGROUPMYIDENTITY (i.e. the real ID) -- the key is random, so it can never authenticate.
- Output JSON: {identity, secretKey(hex,32B), publicKey(hex,32B), serverGroup}. Contains no real key material.
"""
import argparse, json, os, re, secrets, string
from nacl.public import PrivateKey

ap = argparse.ArgumentParser()
ap.add_argument("--identity", default=None)
ap.add_argument("--server-group", default="a")  # MyIdentityStore.serverGroup; any non-empty string works offline
ap.add_argument("--out", default=os.path.join(os.environ.get("TCT_SIM_ROOT") or os.path.expanduser("~/.cache/threema-chat-transfer-sim"),
                                           "sim", "test-identity.json"))
a = ap.parse_args()
ident = a.identity or ("Z" + "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(7)))
assert re.fullmatch(r"[A-Z0-9*]{8}", ident), "identity must be 8 chars [A-Z0-9*]"
sk = PrivateKey.generate()
out = {"identity": ident, "secretKey": bytes(sk).hex(), "publicKey": bytes(sk.public_key).hex(),
       "serverGroup": a.server_group, "note": "throwaway key for offline simulator only"}
os.makedirs(os.path.dirname(a.out), exist_ok=True)
with open(a.out, "w") as f:
    json.dump(out, f, indent=1)
os.chmod(a.out, 0o600)
print(f"wrote {a.out} (identity={ident})")
