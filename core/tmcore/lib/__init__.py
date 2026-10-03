# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Domain logic ported from the private proof-of-concept (assembled by the private devtools/assemble_public.py,
DESIGN §4.3). The modules still carry their original CLIs; the pending "Umbau" per module is listed in
devtools/OWNERSHIP.md (owner: coreA). Steps call these modules in-process with in-memory passwords.
"""
