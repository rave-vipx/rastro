"""Metric definitions and Wilson score intervals for the AI repair benchmark (QUAL-001).

* ``correct``: the executor served exactly the expected records after the breakage.
* ``wrong``: it served anything else without refusing: a silent error. For Rastro this
  is a wrong promotion or a load that succeeded with wrong values.
* ``refused``: it served nothing new and reported why (quarantine, rejection, no
  candidate, failed validation). Safe, but not a recovery.
* ``model_calls``: inference calls Rastro reports for the repair.
"""

from __future__ import annotations

from collections.abc import Iterable
from math import sqrt
from typing import Any

from benchmarks.ai_repair.executors import Verdict

VERDICTS = ("correct", "wrong", "refused")
Z95 = 1.959963984540054


def wilson(successes: int, total: int, z: float = Z95) -> tuple[float, float]:
    """The 95% Wilson score interval for a binomial proportion; (0, 1) when empty."""
    if not 0 <= successes <= total:
        raise ValueError("successes must be between 0 and total")
    if total == 0:
        return 0.0, 1.0
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def summarize(verdicts: Iterable[Verdict]) -> dict[str, Any]:
    """Counts, rates with 95% intervals, per-kind counts and total model calls."""
    items = list(verdicts)
    total = len(items)
    summary: dict[str, Any] = {"cases": total, "model_calls": sum(v.model_calls for v in items)}
    for name in VERDICTS:
        count = sum(v.verdict == name for v in items)
        low, high = wilson(count, total)
        summary[name] = {
            "count": count,
            "rate": round(count / total, 3) if total else None,
            "wilson95": [round(low, 3), round(high, 3)],
        }
    summary["by_kind"] = {
        kind: {name: sum(v.verdict == name and v.kind == kind for v in items) for name in VERDICTS}
        for kind in ("structural", "trap")
    }
    return summary
