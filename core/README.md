# core/ — tmcore engine (owners: coreA, coreB)

Python 3.13 engine, one process per command, JSON-lines events on stdout (protocol v1).

- Contract: `schema/` (frozen), described in `docs/ENGINE-PROTOCOL.md`.
- Run from this folder: `python3 -m tmcore version`.
- Tests: `python3 -m pytest` (dev dependencies: pytest, jsonschema).
- Ownership: `devtools/OWNERSHIP.md`.
