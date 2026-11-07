"""Bounded proposal collection, with no persistence or activation (REPAIR-001).

The composition layer passes a model only from the job's configured provider,
then explicitly enables it for this call. All proposals still require recipe CI.
"""

from __future__ import annotations

import asyncio
from typing import Literal, Protocol

from pydantic import BaseModel, ValidationError

from rastro.application.extraction import validate_recipe
from rastro.application.field_aliases import discovery_aliases
from rastro.application.proposals import propose_with_model
from rastro.domain.intelligence import InferencePolicy
from rastro.domain.models import (
    ConfigurationError,
    CssRecipe,
    Issue,
    Limits,
    Recipe,
    RecipeAttempt,
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
from rastro.ports.model import ModelPort


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
        model: ModelPort | None = None,
        policy: InferencePolicy | None = None,
        aliases: AliasMap | None = None,
    ) -> None:
        self._discovery = discovery
        self._relocation = relocation
        self._model = model
        self._policy = policy or InferencePolicy()
        self._aliases = aliases
        self._slots = asyncio.Semaphore(self._policy.max_concurrency)

    async def generate(
        self,
        html: str,
        *,
        source: Source,
        schema: type[BaseModel],
        recipe: Recipe,
        limits: Limits,
        snapshot: RelocationSnapshot | None = None,
        model_enabled: bool = False,
    ) -> RepairCandidates:
        """Collect relocation, fallbacks, discovery, then optional model proposals.

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
        return await self._with_model(html, schema, recipe, candidates, model_enabled)

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
            if all(existing.recipe != recipe for existing in target) and len(target) < maximum:
                target.append(candidate)

    async def _with_model(
        self,
        html: str,
        schema: type[BaseModel],
        recipe: Recipe,
        candidates: list[RepairCandidate],
        enabled: bool,
    ) -> RepairCandidates:
        if not enabled or recipe.representation != "html":
            return RepairCandidates(candidates=tuple(candidates))
        if self._model is None:
            raise ConfigurationError("Model repair requires a configured job model")
        if len(candidates) == self._policy.max_discovery_candidates:
            return RepairCandidates(candidates=tuple(candidates))

        async def collect(
            proposal: Recipe, origin: Literal["discovery", "model"]
        ) -> tuple[bool, str]:
            self._extend(
                candidates,
                (
                    RepairCandidate(
                        recipe=proposal,
                        origin=origin,
                        rationale="Model proposal; requires recipe CI",
                    ),
                ),
                schema,
                recipe,
            )
            return True, "candidate_collected"

        attempts: list[RecipeAttempt] = []
        complete, failure, calls, _ = await propose_with_model(
            model=self._model,
            policy=self._policy,
            slots=self._slots,
            html=html,
            fields=tuple(schema.model_fields),
            required={name for name, field in schema.model_fields.items() if field.is_required()},
            failure="recipe_repair",
            attempts=attempts,
            evaluate=collect,
        )
        return RepairCandidates(
            candidates=tuple(candidates),
            inference_calls=calls,
            issues=() if complete else (Issue(code=failure),),
        )


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
