"""Optional drift capabilities; existing extraction and dataset ports stay unchanged."""

from typing import Protocol, runtime_checkable

from rastro.domain.drift import DriftAssessment, FieldStructure, StructuralSnapshot
from rastro.domain.health import OpenDrift
from rastro.domain.models import Limits, Recipe


class StructuralProbe(Protocol):
    def measure(
        self, document: str, recipe: Recipe, limits: Limits
    ) -> dict[str, FieldStructure]: ...


@runtime_checkable
class DriftMonitor(Protocol):
    def assess(self, dataset: str, snapshot: StructuralSnapshot) -> DriftAssessment:
        """Compare with the last successful run and persist this observation and assessment."""
        ...


@runtime_checkable
class OpenDriftReader(Protocol):
    def open_drift(self, dataset: str) -> tuple[OpenDrift, ...]:
        """Unacknowledged elevated or high drift per source; never creates storage."""
        ...
