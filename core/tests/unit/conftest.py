# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Ported tests from the proof of concept that still import the old tools/ layout. Each entry is removed by its owner
(devtools/OWNERSHIP.md) when the test runs against tmcore.lib. Never add new entries.
"""
collect_ignore: list[str] = []   # every ported suite runs against tmcore.lib
