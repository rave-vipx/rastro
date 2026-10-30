"""Rastro: schema-first extraction with field-level provenance."""

from importlib import import_module
from importlib.metadata import version
from typing import TYPE_CHECKING

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.application.engine import Rastro
from rastro.domain.acquisition import AcquisitionPolicy, EgressProfile
from rastro.domain.intelligence import InferencePolicy, ModelError, ModelRequest, RecipeProposal
from rastro.domain.memory import (
    RecipeConflictError,
    RecipeContext,
    RecipeKey,
    RecipeNotFoundError,
    RecipeStorageError,
    StoredRecipe,
)
from rastro.domain.models import (
    ConfigurationError,
    Evidence,
    ExtractionRecord,
    ExtractionResult,
    Issue,
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

if TYPE_CHECKING:
    from rastro.adapters.ollama import OllamaModel as OllamaModel
    from rastro.adapters.openai_compatible import OpenAICompatibleModel as OpenAICompatibleModel

__version__ = version("rastro-engine")

# Model providers need optional HTTPX; import them lazily so the core install stays light.
# They are omitted from __all__ so a star import never requires an extra.
_PROVIDERS = {
    "OllamaModel": ("rastro.adapters.ollama", "ollama"),
    "OpenAICompatibleModel": ("rastro.adapters.openai_compatible", "openai"),
}


def __getattr__(name: str) -> object:
    if name not in _PROVIDERS:
        raise AttributeError(f"module 'rastro' has no attribute {name!r}")
    module, extra = _PROVIDERS[name]
    try:
        return getattr(import_module(module), name)
    except ModuleNotFoundError as error:
        if error.name != "httpx":
            raise
        raise ImportError(f"{name} requires: pip install 'rastro-engine[{extra}]'") from None


__all__ = [
    "AcquisitionPolicy",
    "ConfigurationError",
    "EgressProfile",
    "Evidence",
    "ExtractionRecord",
    "ExtractionResult",
    "InferencePolicy",
    "Issue",
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
