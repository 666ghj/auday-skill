"""Privacy-safe progress on stderr; stdout remains available for JSON results."""

import os
import sys
from time import monotonic

_last_stage = None
_last_emitted_at = 0.0
_MIN_INTERVAL_SECONDS = 1.0


def report(stage, completed=None, total=None, unit=None, force=False):
    """Report a fixed, non-sensitive stage label and optional measured counts.

    Callers must never include transcript text, credentials, paths, or source IDs
    in stage/unit. Unknown-duration inference should use start/complete labels,
    not estimated percentages. Repeated counts are throttled; stage changes and
    measured completion are always emitted. AUDAY_PROGRESS=0 disables output.
    """
    global _last_stage, _last_emitted_at
    if os.environ.get("AUDAY_PROGRESS") == "0":
        return
    counts = (
        type(completed) is int
        and type(total) is int
        and 0 <= completed <= total
    )
    finished = counts and completed == total
    current = monotonic()
    if (
        not force
        and stage == _last_stage
        and not finished
        and current - _last_emitted_at < _MIN_INTERVAL_SECONDS
    ):
        return
    detail = ""
    if counts:
        suffix = f" {unit}" if unit else ""
        detail = f": {completed}/{total}{suffix}"
        if total > 0:
            detail += f" ({completed * 100 // total}%)"
    print(f"[Auday] {stage}{detail}", file=sys.stderr, flush=True)
    _last_stage, _last_emitted_at = stage, current
