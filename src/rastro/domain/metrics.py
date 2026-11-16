"""Per-source run metrics and their aggregates (OBS-003..007, SYNC-007 report only).

A row holds counts and timings only: no query strings, no record content. Remote latency
(``network_seconds``) and local work (``processing_seconds``) are kept apart (OBS-005).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import Field, field_validator

from rastro.domain.results import ExtractionResult, Outcome
from rastro.domain.sources import Contract

RETENTION_ROWS = 10_000
SUCCESS = frozenset({Outcome.SUCCESS.value, Outcome.UNCHANGED.value})
type Grouping = Literal["source", "host", "strategy", "day"]


class RunMetric(Contract):
    """One source in one run. ``source_url`` is stored without query, fragment or userinfo."""

    run_id: str = Field(min_length=1, max_length=64)
    source_url: str = Field(max_length=8192)
    host: str = Field(max_length=253)
    strategy: str = Field(max_length=32)
    started_at: datetime
    finished_at: datetime
    outcome: str = Field(max_length=64)
    http_status: int | None = None
    requests: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    bytes: int = Field(default=0, ge=0)
    network_seconds: float = Field(default=0, ge=0)
    processing_seconds: float = Field(default=0, ge=0)
    render_seconds: float = Field(default=0, ge=0)
    model_calls: int = Field(default=0, ge=0)
    model_tokens: int = Field(default=0, ge=0)
    records: int = Field(default=0, ge=0)
    required_fields_ok: int = Field(default=0, ge=0)
    fields_validated: int = Field(default=0, ge=0)

    @field_validator("source_url")
    @classmethod
    def _strip_url(cls, value: str) -> str:
        parts = urlsplit(value)
        host = parts.hostname or ""
        netloc = f"{host}:{parts.port}" if parts.port else host
        return urlunsplit((parts.scheme, netloc, parts.path, "", ""))

    @property
    def total_seconds(self) -> float:
        return max(0.0, (self.finished_at - self.started_at).total_seconds())


def metric_from_result(
    run_id: str, result: ExtractionResult[Any], required: Sequence[str]
) -> RunMetric:
    """The row for one source result; ``required`` names the job's required fields."""
    m = result.metrics
    attempts = result.recipe_attempts
    return RunMetric(
        run_id=run_id,
        source_url=result.source_url,
        host=urlsplit(result.source_url).hostname or "",
        strategy=attempts[-1].origin if attempts else "explicit",
        started_at=result.started_at,
        finished_at=result.completed_at,
        outcome=result.status.value,
        http_status=result.http_status,
        requests=m.request_count,
        retries=m.retry_count,
        bytes=m.bytes_received,
        network_seconds=m.network_seconds,
        processing_seconds=max(0.0, m.total_seconds - m.network_seconds - m.inference_seconds),
        model_calls=m.inference_calls,
        records=len(result.records),
        required_fields_ok=sum(all(n in r.evidence for n in required) for r in result.records),
        fields_validated=m.fields_validated,
    )


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile (``q`` in 0..100) of a non-empty sequence."""
    ordered = sorted(values)
    position = (len(ordered) - 1) * q / 100
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def parse_since(text: str, now: datetime) -> datetime:
    """``7d`` or ``12h`` back from ``now``; anything else is rejected by the caller."""
    unit = text[-1:]
    amount = text[:-1]
    if unit not in {"d", "h"} or not amount.isascii() or not amount.isdigit() or int(amount) < 1:
        raise ValueError(text)
    return now - timedelta(**{"days" if unit == "d" else "hours": min(int(amount), 36_500)})


_KEYS: dict[str, Callable[[RunMetric], str]] = {
    "source": lambda r: r.source_url,
    "host": lambda r: r.host,
    "strategy": lambda r: r.strategy,
    "day": lambda r: r.finished_at.astimezone(UTC).date().isoformat(),
}


def _spread(values: list[float]) -> dict[str, float]:
    return {"p50": percentile(values, 50), "p95": percentile(values, 95)}


def _group(key: str, rows: list[RunMetric], now: datetime) -> dict[str, Any]:
    runs = len(rows)
    requests = sum(r.requests for r in rows)
    records = sum(r.records for r in rows)
    seconds = sum(r.total_seconds for r in rows)
    ok = [r for r in rows if r.outcome in SUCCESS]
    errors = [r for r in rows if r.outcome not in SUCCESS and r.outcome != "partial_success"]
    wins = sorted(r.finished_at for r in ok)
    days: dict[str, list[RunMetric]] = defaultdict(list)
    for r in rows:
        days[_KEYS["day"](r)].append(r)
    return {
        "key": key,
        "runs": runs,
        "pages": requests,
        "records": records,
        "success_rate": len(ok) / runs,
        "error_rate": len(errors) / runs,
        "retry_rate": sum(r.retries for r in rows) / requests if requests else 0.0,
        "total_seconds": _spread([r.total_seconds for r in rows]),
        "network_seconds": _spread([r.network_seconds for r in rows]),
        "processing_seconds": _spread([r.processing_seconds for r in rows]),
        "records_per_minute": records / (seconds / 60) if seconds else 0.0,
        "model_calls": sum(r.model_calls for r in rows),
        "freshness_seconds": (now - wins[-1]).total_seconds() if wins else None,
        "coverage": sum(r.required_fields_ok for r in rows) / records if records else None,
        # SYNC-007, report only: how often successful runs actually happened.
        "observed_interval_seconds": (
            percentile([(b - a).total_seconds() for a, b in pairwise(wins)], 50)
            if len(wins) > 1
            else None
        ),
        "trend": [
            {
                "day": day,
                "runs": len(items),
                "records": sum(r.records for r in items),
                "success_rate": sum(r.outcome in SUCCESS for r in items) / len(items),
                "p95_total_seconds": percentile([r.total_seconds for r in items], 95),
            }
            for day, items in sorted(days.items())
        ],
    }


def summarize(rows: Iterable[RunMetric], by: Grouping, now: datetime) -> list[dict[str, Any]]:
    grouped: dict[str, list[RunMetric]] = defaultdict(list)
    for row in rows:
        grouped[_KEYS[by](row)].append(row)
    return [_group(key, grouped[key], now) for key in sorted(grouped)]
