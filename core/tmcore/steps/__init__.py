# SPDX-License-Identifier: AGPL-3.0-or-later
"""
Steps: one function per command, signature (ctx: tmcore.cli.Context) -> tmcore.cli.StepResult.

A step emits phase/progress/check/note/retry/prompt/device events through ctx.proto (or tmcore.protocol), raises
tmcore.protocol.EngineError(code, **data) to fail, polls ctx.proto.check_cancel() at safe points and wraps device
writes in `with ctx.proto.critical():`. It never prints, never writes stdout, never emits 'hello' or 'result'.
Ownership: devtools/OWNERSHIP.md.
"""
