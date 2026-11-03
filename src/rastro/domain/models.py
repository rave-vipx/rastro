"""Public extraction contracts; independent of parsers, storage, and transports."""

from rastro.domain.recipes import (
    CssRecipe,
    HtmlRecipe,
    JsonLdRecipe,
    JsonRecipe,
    Recipe,
    StrategyUnion,
    seal_union,
)
from rastro.domain.results import (
    Evidence,
    ExtractionRecord,
    ExtractionResult,
    Issue,
    Metrics,
    Outcome,
    RecipeAttempt,
)
from rastro.domain.sources import (
    EGRESS_NAME,
    LANGUAGE_TAG,
    ConfigurationError,
    Contract,
    Limits,
    RastroError,
    Representation,
    ResourceLimitError,
    Source,
    schema_fingerprint,
)

__all__ = [
    "EGRESS_NAME",
    "LANGUAGE_TAG",
    "ConfigurationError",
    "Contract",
    "CssRecipe",
    "Evidence",
    "ExtractionRecord",
    "ExtractionResult",
    "HtmlRecipe",
    "Issue",
    "JsonLdRecipe",
    "JsonRecipe",
    "Limits",
    "Metrics",
    "Outcome",
    "RastroError",
    "Recipe",
    "RecipeAttempt",
    "Representation",
    "ResourceLimitError",
    "Source",
    "StrategyUnion",
    "schema_fingerprint",
    "seal_union",
]
