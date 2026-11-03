"""Bounded deterministic recipe discovery, independent of application validation."""

from collections.abc import Mapping
from typing import Protocol

from rastro.domain.models import Limits, Recipe

# Extra accepted source names per model field; the field name is always accepted.
type AliasMap = Mapping[str, tuple[str, ...]]


class RecipeDiscovery(Protocol):
    def discover(
        self,
        html: str,
        fields: tuple[str, ...],
        limits: Limits,
        *,
        max_candidates: int,
        aliases: AliasMap | None = None,
    ) -> tuple[Recipe, ...]:
        """Propose JSON-LD mappings before DOM mappings; never fetch more documents.

        ``aliases`` maps a model field to additional exact source names; matching
        stays case-sensitive, and a field matched by more than one source node is
        omitted as ambiguous.
        """
        ...
