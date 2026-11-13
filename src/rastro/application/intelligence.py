"""Bounded cold-context learning; a candidate never enters memory before validation."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal

import structlog
from pydantic import BaseModel

from rastro.application.extraction import extract_document, validate_recipe
from rastro.application.field_aliases import discovery_aliases as resolve_discovery_aliases
from rastro.application.memory import PreparedRecipe
from rastro.application.outcomes import stamp_learning
from rastro.application.proposals import propose_with_model
from rastro.domain.intelligence import InferencePolicy
from rastro.domain.models import (
    ConfigurationError,
    ExtractionResult,
    Limits,
    Outcome,
    Recipe,
    RecipeAttempt,
    Representation,
    ResourceLimitError,
    Source,
)
from rastro.ports.discovery import AliasMap, RecipeDiscovery
from rastro.ports.model import ModelPort
from rastro.ports.parser import DocumentParser
from rastro.ports.recipes import RecipeRepository

_LOG = structlog.wrap_logger(
    logging.getLogger("rastro"), processors=[structlog.processors.JSONRenderer()]
)


def with_version(recipe: Recipe, version: int) -> Recipe:
    """Relabel a proposal and its fallbacks with the version learning will store."""
    if recipe.version == version:
        return recipe
    data = recipe.model_dump()
    data["version"] = version
    data["fallbacks"] = [{**item, "version": version} for item in data["fallbacks"]]
    return Recipe.model_validate(data)


@dataclass
class _LearningSession[T: BaseModel]:
    html: str
    source: Source
    schema: type[T]
    parser: DocumentParser
    limits: Limits
    representation: Representation
    required: set[str]
    version: int = 1
    attempts: list[RecipeAttempt] = field(default_factory=list)
    results: list[ExtractionResult[T]] = field(default_factory=list)
    selected: Recipe | None = None

    async def evaluate(
        self, candidate: Recipe, origin: Literal["discovery", "model"]
    ) -> tuple[bool, str]:
        validated = validate_recipe(
            self.schema, with_version(candidate, self.version), self.representation
        ).model_copy(update={"require_evidence": True})
        if not self.required.issubset(validated.fields):
            self.attempts.append(
                RecipeAttempt(
                    index=len(self.attempts), origin=origin, status=Outcome.INSUFFICIENT_EVIDENCE
                )
            )
            return False, "missing_required_mapping"
        result = await asyncio.to_thread(
            extract_document,
            self.html,
            source=self.source,
            schema=self.schema,
            recipe=validated,
            parser=self.parser,
            limits=self.limits,
        )
        self.results.append(result)
        self.attempts.append(
            RecipeAttempt(index=len(self.attempts), status=result.status, origin=origin)
        )
        if result.status == Outcome.SUCCESS:
            self.selected = validated
        # Never hide an observed partial dataset by choosing another strategy.
        return bool(result.records), "candidate_validation_failed"


class RecipeLearner:
    def __init__(
        self,
        *,
        model: ModelPort | None,
        discovery: RecipeDiscovery,
        policy: InferencePolicy,
        repository: RecipeRepository | None,
        json_discovery: RecipeDiscovery | None = None,
        discovery_aliases: AliasMap | None = None,
    ) -> None:
        self._model = model
        self._discovery = discovery
        self._json_discovery = json_discovery
        self._policy = policy
        self._repository = repository
        self._discovery_aliases = dict(discovery_aliases) if discovery_aliases is not None else None
        self._slots = asyncio.Semaphore(policy.max_concurrency)

    async def extract[T: BaseModel](
        self,
        html: str,
        *,
        source: Source,
        schema: type[T],
        prepared: PreparedRecipe,
        parser: DocumentParser,
        limits: Limits,
        representation: Representation = "html",
    ) -> ExtractionResult[T]:
        """Discover before inference. JSON uses structured discovery only; models propose HTML."""
        candidates, _, required = await self._discover(
            html, schema=schema, limits=limits, representation=representation
        )
        session = _LearningSession(
            html=html,
            source=source,
            schema=schema,
            parser=parser,
            limits=limits,
            representation=representation,
            required=required,
            version=prepared.learn_version,
        )
        complete, failure = False, "no_valid_recipe"
        for candidate in candidates:
            complete, failure = await session.evaluate(candidate, "discovery")
            if complete:
                break
        calls, inference_seconds = 0, 0.0
        if not complete and self._model is not None and representation == "html":
            complete, failure, calls, inference_seconds = await propose_with_model(
                model=self._model,
                policy=self._policy,
                slots=self._slots,
                html=html,
                schema=schema,
                failure=failure,
                attempts=session.attempts,
                evaluate=session.evaluate,
                limits=limits,
            )
        storage_seconds = await self._persist_learned(prepared, session.selected)
        result = stamp_learning(
            results=session.results,
            attempts=session.attempts,
            selected=session.selected,
            complete=complete,
            failure=failure,
            source=source,
            schema=schema,
            input_bytes=len(html.encode()),
            started=prepared.started,
            started_at=prepared.started_at,
            calls=calls,
            inference_seconds=inference_seconds,
            storage_seconds=storage_seconds,
        )
        _LOG.info(
            "recipe_learning_completed",
            job_id=result.job_id,
            status=result.status.value,
            inference_calls=calls,
            inference_seconds=inference_seconds,
            recipe_attempts=len(session.attempts),
            total_seconds=result.metrics.total_seconds,
        )
        return result

    async def _discover(
        self,
        html: str,
        *,
        schema: type[BaseModel],
        limits: Limits,
        representation: Representation,
    ) -> tuple[tuple[Recipe, ...], tuple[str, ...], set[str]]:
        fields = schema.model_fields
        if not fields or len(fields) > 100 or any(len(name) > 256 for name in fields):
            raise ConfigurationError("Discovery requires 1-100 bounded fields")
        if len(html.encode()) > limits.max_input_bytes(representation):
            raise ResourceLimitError(
                f"{representation.upper()} input exceeds max_{representation}_bytes"
            )
        discovery = self._json_discovery if representation == "json" else self._discovery
        if discovery is None:
            raise ConfigurationError("Structured discovery is not configured")
        field_names = tuple(fields)
        candidates = await asyncio.to_thread(
            discovery.discover,
            html,
            field_names,
            limits,
            max_candidates=self._policy.max_discovery_candidates,
            aliases=resolve_discovery_aliases(fields, self._discovery_aliases),
        )
        if len(candidates) > self._policy.max_discovery_candidates:
            raise ResourceLimitError("Discovery returned too many candidates")
        required = {name for name, info in fields.items() if info.is_required()}
        return tuple(candidates), field_names, required

    async def _persist_learned(self, prepared: PreparedRecipe, selected: Recipe | None) -> float:
        storage_seconds = prepared.storage_seconds
        if selected is not None and self._repository is not None:
            assert prepared.key is not None
            storage_start = perf_counter()
            await asyncio.to_thread(
                self._repository.save_success,
                prepared.key,
                selected,
                selected_index=0,
                at=datetime.now(UTC),
            )
            storage_seconds += perf_counter() - storage_start
        return storage_seconds
