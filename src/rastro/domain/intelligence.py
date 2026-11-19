"""Optional inference contracts and declarative proposals (AI-001-004, REC-004)."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, create_model, model_validator

from rastro.domain.models import (
    Contract,
    CssRecipe,
    JsonLdRecipe,
    RastroError,
    Recipe,
    StrategyUnion,
    seal_union,
)
from rastro.domain.recipe_nested import NestedMapping


class InferencePolicy(Contract):
    """Per-extraction budgets; concurrency is shared by one Rastro instance/event loop."""

    max_calls: int = Field(default=2, ge=1, le=4)
    max_repair_attempts: int = Field(default=1, ge=0, le=3)
    timeout_seconds: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    max_context_bytes: int = Field(default=32_768, ge=1, le=262_144)
    max_response_bytes: int = Field(default=65_536, ge=1, le=1_048_576)
    max_output_tokens: int = Field(default=2048, ge=1, le=8192)
    context_tokens: int = Field(default=8192, ge=512, le=32_768)
    max_concurrency: int = Field(default=1, ge=1, le=16)
    max_discovery_candidates: int = Field(default=8, ge=1, le=32)


class ModelBudget(Contract):
    """What a model call costs and the most one run may spend on it (COST-002), in USD.

    Without ``cost_per_call`` nothing can be priced, so ``max_cost`` requires it.
    """

    cost_per_call: float | None = Field(default=None, ge=0, le=1000, allow_inf_nan=False)
    max_cost: float | None = Field(default=None, gt=0, le=1_000_000, allow_inf_nan=False)

    @model_validator(mode="after")
    def check_priced(self) -> ModelBudget:
        if self.max_cost is not None and self.cost_per_call is None:
            raise ValueError("max_cost needs cost_per_call")
        return self

    @property
    def allowed_calls(self) -> int | None:
        """Calls the limit pays for; ``None`` when unlimited. Tolerates float rounding."""
        if self.max_cost is None or not self.cost_per_call:
            return None
        return int(self.max_cost / self.cost_per_call + 1e-9)


class RecipeProposal(StrategyUnion):
    """A model can describe a mapping, never records, tools, code, or stored versions.

    Variants are derived from the HTML recipe variants without their version,
    evidence and fallback settings; validation selects one by ``strategy``.
    """

    strategy: Literal["jsonld", "css"]
    # One expression per field, or a nested mapping for list/object fields.
    fields: dict[str, str | NestedMapping] = Field(min_length=1, max_length=100)

    def recipe(self) -> Recipe:
        return Recipe.model_validate(self.model_dump())


_DEFINITION_ONLY = frozenset({"version", "require_evidence", "fallbacks", "pagination"})


def _proposal(variant: type[Recipe]) -> type[RecipeProposal]:
    fields: dict[str, Any] = {
        name: (info.annotation, info)
        for name, info in variant.model_fields.items()
        if name not in _DEFINITION_ONLY
    }
    # Model proposals never use pointer lists; those are for hand-written recipes.
    fields["fields"] = (
        RecipeProposal.model_fields["fields"].annotation,
        RecipeProposal.model_fields["fields"],
    )
    if "item_type" in fields:  # proposals keep the 0.1 single-type form; tuples are hand-written
        fields["item_type"] = (str | None, Field(default=None, min_length=1, max_length=256))
    # A proposal names its strategy explicitly; the variant default only aids construction.
    fields["strategy"] = (variant.model_fields["strategy"].annotation, ...)
    name = variant.__name__.removesuffix("Recipe") + "Proposal"
    return create_model(name, __base__=RecipeProposal, __module__=__name__, **fields)


CssProposal = _proposal(CssRecipe)
JsonLdProposal = _proposal(JsonLdRecipe)
seal_union(RecipeProposal, CssProposal, JsonLdProposal)


def proposal_schema() -> dict[str, Any]:
    """Constrained-decoding schema with each HTML recipe variant's selector rules."""
    variants = [CssProposal.model_json_schema(), JsonLdProposal.model_json_schema()]
    defs: dict[str, Any] = {}
    for variant in variants:  # "$ref"s resolve from the root, so hoist them there
        defs.update(variant.pop("$defs", {}))
    return {"anyOf": variants, **({"$defs": defs} if defs else {})}


class ModelRequest(Contract):
    """Separate trusted instructions from a JSON envelope containing untrusted data."""

    system: str
    content: str
    response_schema: dict[str, Any] | None = None

    @property
    def output_schema(self) -> dict[str, Any]:
        """The JSON Schema the answer must match; recipe proposals by default."""
        return self.response_schema or proposal_schema()

    @property
    def context_bytes(self) -> int:
        envelope = {
            "messages": [
                {"role": "system", "content": self.system},
                {"role": "user", "content": self.content},
            ],
            "format": self.output_schema,
        }
        return len(json.dumps(envelope).encode("utf-8"))


class ModelError(RastroError):
    """A safe category, independent of provider responses and exception messages."""

    def __init__(
        self,
        code: Literal[
            "model_unavailable",
            "model_timeout",
            "model_response_limit",
            "invalid_model_response",
            "model_context_limit",
            "model_budget_exhausted",
        ],
    ) -> None:
        self.code = code
        super().__init__(code)
