"""Bounded deterministic recipe discovery, independent of application validation."""

from typing import Protocol

from rastro.domain.models import Limits, Recipe


class RecipeDiscovery(Protocol):
    def discover(
        self, html: str, fields: tuple[str, ...], limits: Limits, *, max_candidates: int
    ) -> tuple[Recipe, ...]:
        """Propose JSON-LD mappings before DOM mappings; never fetch more documents."""
        ...
