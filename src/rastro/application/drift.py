"""Opt-in, execution-scoped capture; extraction data never enters drift persistence.

List and object fields add one ``field.member`` entry per member, with the share of items
that member was filled in, so losing a member is drift even when the item count holds.
"""

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from rastro.domain.drift import MAX_STRUCTURE_FIELDS, FieldStructure, StructuralSnapshot
from rastro.domain.models import ConfigurationError, ExtractionResult, Limits, Recipe
from rastro.ports.drift import StructuralProbe

_CAPTURE: ContextVar[tuple[StructuralProbe, dict[str, StructuralSnapshot]] | None] = ContextVar(
    "drift_capture", default=None
)


@contextmanager
def collect_snapshots(probe: StructuralProbe) -> Iterator[dict[str, StructuralSnapshot]]:
    """Scope capture to this execution, including asyncio tasks and to_thread calls."""
    snapshots: dict[str, StructuralSnapshot] = {}
    token = _CAPTURE.set((probe, snapshots))
    try:
        yield snapshots
    finally:
        _CAPTURE.reset(token)


def capture_snapshot(
    result: ExtractionResult[Any], body: str, recipe: Recipe, limits: Limits
) -> None:
    capture = _CAPTURE.get()
    if capture is not None:
        probe, snapshots = capture
        fields = probe.measure(body, recipe, limits)
        snapshots[result.job_id] = snapshot_result(result, tuple(recipe.fields), fields=fields)


def snapshot_result(
    result: ExtractionResult[Any],
    names: Sequence[str],
    *,
    fields: Mapping[str, FieldStructure] | None = None,
    captured: StructuralSnapshot | None = None,
) -> StructuralSnapshot:
    """Use final run metadata, retaining per-attempt denominators when capture exists."""
    if captured is not None and (
        captured.run_id != result.job_id
        or captured.source_url != result.source_url
        or captured.schema_fingerprint != result.schema_fingerprint
    ):
        raise ConfigurationError("Structural snapshot does not belong to this extraction")
    measured = fields if fields is not None else captured.fields if captured else {}
    fallback = bool(result.recipe_attempts[-1].index) if result.recipe_attempts else None
    total = max(result.metrics.candidates, len(result.records), 1)
    rows = [record.data.model_dump(mode="json") for record in result.records]
    observed = {}
    for name in sorted(names):
        measurement = measured.get(name)
        coverage = sum(name in record.evidence for record in result.records) / total
        invalid = (
            len(
                {
                    issue.record_index
                    for issue in result.issues
                    if issue.code == "validation_error" and issue.field == name
                }
            )
            / total
        )
        observed[name] = FieldStructure(
            selector_match_count=measurement.selector_match_count if measurement else None,
            subtree_fingerprint=measurement.subtree_fingerprint if measurement else None,
            fallback_used=fallback,
            field_coverage=measurement.field_coverage if captured and measurement else coverage,
            validation_failure_rate=(
                measurement.validation_failure_rate if captured and measurement else min(1, invalid)
            ),
        )
    for key, coverage in _member_coverage(
        names, rows, measured, MAX_STRUCTURE_FIELDS - len(observed)
    ):
        measurement = measured.get(key)
        observed[key] = FieldStructure(
            selector_match_count=measurement.selector_match_count if measurement else None,
            subtree_fingerprint=measurement.subtree_fingerprint if measurement else None,
            fallback_used=fallback,
            field_coverage=measurement.field_coverage if captured and measurement else coverage,
            validation_failure_rate=0,
        )
    return StructuralSnapshot(
        source_url=result.source_url,
        run_id=result.job_id,
        observed_at=result.completed_at,
        schema_fingerprint=result.schema_fingerprint,
        locale=result.locale,
        timezone=result.timezone,
        egress=result.metrics.egress,
        status=result.status,
        fields=observed,
    )


def _member_coverage(
    names: Sequence[str],
    rows: Sequence[Mapping[str, Any]],
    measured: Mapping[str, FieldStructure],
    budget: int,
) -> list[tuple[str, float]]:
    """Share of list or object items with each member filled, keyed ``field.member``.

    Members come from the measured structure and from the records themselves, so a
    member that vanished from every item still reads as coverage 0.
    """
    found: list[tuple[str, float]] = []
    for name in sorted(names):
        items = [item for row in rows for item in _as_items(row.get(name))]
        members = {key.split(".", 1)[1] for key in measured if key.startswith(name + ".")}
        members.update(key for item in items for key in item)
        for member in sorted(members):
            filled = sum(item.get(member) not in (None, "") for item in items)
            found.append((f"{name}.{member}", filled / max(1, len(items))))
    return found[: max(0, budget)]


def _as_items(value: object) -> list[Mapping[str, Any]]:
    values = value if isinstance(value, list) else [value]
    return [item for item in values if isinstance(item, dict)]
