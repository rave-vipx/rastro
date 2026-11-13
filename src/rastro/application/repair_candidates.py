"""Bounded deterministic proposal collection, with no persistence or activation (REPAIR-001).

Relocation, declared fallbacks and discovery only; a configured model is consulted
afterwards, and only when none of these would pass (``application/model_repair``).
All proposals still require recipe CI.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from pydantic import BaseModel, ValidationError

from rastro.application.extraction import validate_recipe
from rastro.application.field_aliases import discovery_aliases
from rastro.domain.intelligence import InferencePolicy
from rastro.domain.models import (
    ConfigurationError,
    CssRecipe,
    Limits,
    Recipe,
    ResourceLimitError,
    Source,
    schema_fingerprint,
)
from rastro.domain.repair import (
    RelocationSnapshot,
    RepairCandidate,
    RepairCandidates,
    digest,
)
from rastro.ports.discovery import AliasMap, RecipeDiscovery


class ElementRelocator(Protocol):
    """Optional parser capability, separate from the existing DocumentParser port."""

    def relocate(
        self, html: str, recipe: CssRecipe, snapshot: RelocationSnapshot, limits: Limits
    ) -> tuple[RepairCandidate, ...]: ...


class RepairCandidateGenerator:
    def __init__(
        self,
        *,
        discovery: RecipeDiscovery,
        relocation: ElementRelocator | None = None,
        policy: InferencePolicy | None = None,
        aliases: AliasMap | None = None,
    ) -> None:
        self._discovery = discovery
        self._relocation = relocation
        self._policy = policy or InferencePolicy()
        self._aliases = aliases

    async def generate(
        self,
        html: str,
        *,
        source: Source,
        schema: type[BaseModel],
        recipe: Recipe,
        limits: Limits,
        snapshot: RelocationSnapshot | None = None,
    ) -> RepairCandidates:
        """Collect relocation, fallback and discovery proposals.

        Supply representation-appropriate discovery. Snapshot reuse is bound to
        source, schema and recipe; the adapter checks the recipe identity.
        Versions are unchanged: the lifecycle caller assigns fresh versions.
        """
        recipe = _check_input(html, recipe, schema, limits)
        candidates: list[RepairCandidate] = []
        if snapshot is not None:
            if self._relocation is None or not isinstance(recipe, CssRecipe):
                raise ConfigurationError("A CSS relocation adapter is required for snapshots")
            if snapshot.source_digest != digest(source.model_dump_json()) or (
                snapshot.schema_fingerprint != schema_fingerprint(schema)
            ):
                raise ConfigurationError("Relocation snapshot belongs to another source or schema")
            relocated = await asyncio.to_thread(
                self._relocation.relocate, html, recipe, snapshot, limits
            )
            self._extend(candidates, relocated, schema, recipe)
        fallbacks = tuple(
            RepairCandidate(
                recipe=item, origin="fallback", rationale="Declared fallback; requires recipe CI"
            )
            for item in recipe.fallbacks[: self._policy.max_discovery_candidates]
        )
        self._extend(candidates, fallbacks, schema, recipe)
        discovered = await asyncio.to_thread(
            self._discovery.discover,
            html,
            tuple(schema.model_fields),
            limits,
            max_candidates=self._policy.max_discovery_candidates,
            aliases=discovery_aliases(schema.model_fields, self._aliases),
        )
        self._extend(
            candidates,
            tuple(
                RepairCandidate(
                    recipe=item,
                    origin="discovery",
                    rationale="Deterministic discovery; requires recipe CI",
                )
                for item in discovered
            ),
            schema,
            recipe,
        )
        return RepairCandidates(candidates=tuple(candidates))

    def _extend(
        self,
        target: list[RepairCandidate],
        incoming: tuple[RepairCandidate, ...],
        schema: type[BaseModel],
        original: Recipe,
    ) -> None:
        maximum = self._policy.max_discovery_candidates
        if len(incoming) > maximum:
            raise ResourceLimitError("Repair provider returned too many candidates")
        baseline = original.model_copy(update={"require_evidence": True, "fallbacks": ()})
        for item in incoming:
            try:
                data = item.recipe.model_dump()
                data.update(require_evidence=True, fallbacks=(), version=original.version)
                recipe = validate_recipe(
                    schema, Recipe.model_validate(data), original.representation
                )
                candidate = RepairCandidate.model_validate({**item.model_dump(), "recipe": recipe})
            except ValidationError:
                raise ConfigurationError("Invalid repair candidate") from None
            if recipe == baseline:
                continue
            if all(existing.recipe != recipe for existing in target) and len(target) < maximum:
                target.append(candidate)


def _check_input(html: str, recipe: Recipe, schema: type[BaseModel], limits: Limits) -> Recipe:
    try:
        size = len(html.encode())
        recipe = validate_recipe(schema, recipe, recipe.representation)
    except (UnicodeError, ValidationError):
        raise ConfigurationError("Invalid repair input") from None
    if size > limits.max_input_bytes(recipe.representation):
        raise ResourceLimitError("Repair input exceeds document byte budget")
    if len(schema.model_fields) > 100 or any(len(name) > 256 for name in schema.model_fields):
        raise ConfigurationError("Repair requires 1-100 bounded schema fields")
    return recipe
