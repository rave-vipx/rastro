"""Execution-scoped successful samples; raw bodies never leave this scope by default."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from rastro.domain.models import ExtractionResult, Limits, Outcome, Recipe, Source


@dataclass(frozen=True)
class SuccessfulSample:
    body: str
    source: Source
    recipe: Recipe
    result: ExtractionResult[Any]
    limits: Limits


_SAMPLES: ContextVar[dict[str, SuccessfulSample] | None] = ContextVar(
    "success_samples", default=None
)


@contextmanager
def collect_successes() -> Iterator[dict[str, SuccessfulSample]]:
    samples: dict[str, SuccessfulSample] = {}
    token = _SAMPLES.set(samples)
    try:
        yield samples
    finally:
        _SAMPLES.reset(token)


def capture_success(
    result: ExtractionResult[Any], body: str, recipe: Recipe, source: Source, limits: Limits
) -> None:
    samples = _SAMPLES.get()
    if samples is not None and result.status == Outcome.SUCCESS and result.records:
        samples[result.job_id] = SuccessfulSample(body, source, recipe, result, limits)
