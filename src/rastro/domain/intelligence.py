"""Optional inference contracts and declarative proposals (AI-001-004, REC-004)."""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import Field, create_model

from rastro.domain.models import (
    Contract,
    CssRecipe,
    JsonLdRecipe,
    RastroError,
    Recipe,
    StrategyUnion,
    seal_union,
)


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


class RecipeProposal(StrategyUnion):
    """A model can describe a mapping, never records, tools, code, or stored versions.

    Variants are derived from the HTML recipe variants without their version,
    evidence and fallback settings; validation selects one by ``strategy``.
    """

    strategy: Literal["jsonld", "css"]
    fields: dict[str, str] = Field(min_length=1, max_length=100)

    def recipe(self) -> Recipe:
        return Recipe.model_validate(self.model_dump())


_DEFINITION_ONLY = frozenset({"version", "require_evidence", "fallbacks"})


def _proposal(variant: type[Recipe]) -> type[RecipeProposal]:
    fields: dict[str, Any] = {
        name: (info.annotation, info)
        for name, info in variant.model_fields.items()
        if name not in _DEFINITION_ONLY
    }
    # A proposal names its strategy explicitly; the variant default only aids construction.
    fields["strategy"] = (variant.model_fields["strategy"].annotation, ...)
    name = variant.__name__.removesuffix("Recipe") + "Proposal"
    return create_model(name, __base__=RecipeProposal, __module__=__name__, **fields)


CssProposal = _proposal(CssRecipe)
JsonLdProposal = _proposal(JsonLdRecipe)
seal_union(RecipeProposal, CssProposal, JsonLdProposal)


def proposal_schema() -> dict[str, Any]:
    """Constrained-decoding schema with each HTML recipe variant's selector rules."""
    return {"anyOf": [CssProposal.model_json_schema(), JsonLdProposal.model_json_schema()]}


class ModelRequest(Contract):
    """Separate trusted instructions from a JSON envelope containing untrusted data."""

    system: str
    content: str

    @property
    def context_bytes(self) -> int:
        envelope = {
            "messages": [
                {"role": "system", "content": self.system},
                {"role": "user", "content": self.content},
            ],
            "format": proposal_schema(),
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
        ],
    ) -> None:
        self.code = code
        super().__init__(code)
