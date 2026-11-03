"""Rastro: schema-first extraction with field-level provenance."""

from importlib.metadata import version

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.application.engine import Rastro
from rastro.domain.acquisition import AcquisitionPolicy, EgressProfile
from rastro.domain.intelligence import InferencePolicy, ModelError, ModelRequest, RecipeProposal
from rastro.domain.memory import (
    RecipeConflictError,
    RecipeContext,
    RecipeKey,
    RecipeMetadata,
    RecipeNotFoundError,
    RecipeStorageError,
    StoredRecipe,
)
from rastro.domain.models import (
    ConfigurationError,
    CssRecipe,
    Evidence,
    ExtractionRecord,
    ExtractionResult,
    Issue,
    JsonLdRecipe,
    JsonRecipe,
    Limits,
    Metrics,
    Outcome,
    RastroError,
    Recipe,
    RecipeAttempt,
    Representation,
    ResourceLimitError,
    Source,
    schema_fingerprint,
)
from rastro.ports.model import ModelPort
from rastro.ports.recipes import RecipeRepository

__version__ = version("rastro-engine")

__all__ = [
    "AcquisitionPolicy",
    "ConfigurationError",
    "CssRecipe",
    "EgressProfile",
    "Evidence",
    "ExtractionRecord",
    "ExtractionResult",
    "InferencePolicy",
    "Issue",
    "JsonLdRecipe",
    "JsonRecipe",
    "Limits",
    "Metrics",
    "ModelError",
    "ModelPort",
    "ModelRequest",
    "Outcome",
    "Rastro",
    "RastroError",
    "Recipe",
    "RecipeAttempt",
    "RecipeConflictError",
    "RecipeContext",
    "RecipeKey",
    "RecipeMetadata",
    "RecipeNotFoundError",
    "RecipeProposal",
    "RecipeRepository",
    "RecipeStorageError",
    "Representation",
    "ResourceLimitError",
    "SQLiteRecipeRepository",
    "Source",
    "StoredRecipe",
    "__version__",
    "schema_fingerprint",
]
