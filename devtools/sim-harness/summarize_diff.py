#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Print a one-line summary of a store_diff.py report (counts only)."""
import json, sys
d = json.load(open(sys.argv[1]))
print("STORE_DIFF_SUMMARY", d["result"], json.dumps({t: {"changed": v.get("changed"), "added": v.get("added"),
      "removed": v.get("removed"), "classes": v.get("classes")} for t, v in d["tables"].items()}), "unexpected", d["unexpected"])
