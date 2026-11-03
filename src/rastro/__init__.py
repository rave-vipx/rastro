"""Rastro: schema-first extraction with field-level provenance."""

from importlib import import_module
from importlib.metadata import version
from typing import TYPE_CHECKING

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.application.engine import Rastro
from rastro.application.jobs import load_job, run_job, run_job_async, run_job_file
from rastro.domain.acquisition import AcquisitionPolicy, EgressProfile
from rastro.domain.intelligence import InferencePolicy, ModelError, ModelRequest, RecipeProposal
from rastro.domain.job_fields import (
    BooleanField,
    DateField,
    DateTimeField,
    DecimalField,
    IntegerField,
    JobField,
    JobFieldType,
    NumberField,
    StringField,
    UrlField,
    build_job_schema,
)
from rastro.domain.job_providers import (
    JobModelProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
)
from rastro.domain.job_results import (
    JobExitCode,
    JobRecordData,
    JobRunResult,
    JobRunStatus,
    JobSummary,
    job_run_json_schema,
)
from rastro.domain.jobs import (
    FileJobSource,
    Job,
    JobSource,
    RegionalContext,
    UrlJobSource,
    job_json_schema,
    parse_job,
)
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
    "BooleanField",
    "ConfigurationError",
    "CssRecipe",
    "DateField",
    "DateTimeField",
    "DecimalField",
    "EgressProfile",
    "Evidence",
    "ExtractionRecord",
    "ExtractionResult",
    "FileJobSource",
    "InferencePolicy",
    "IntegerField",
    "Issue",
    "Job",
    "JobExitCode",
    "JobField",
    "JobFieldType",
    "JobModelProvider",
    "JobRecordData",
    "JobRunResult",
    "JobRunStatus",
    "JobSource",
    "JobSummary",
    "JsonLdRecipe",
    "JsonRecipe",
    "Limits",
    "Metrics",
    "ModelError",
    "ModelPort",
    "ModelRequest",
    "NumberField",
    "OllamaProvider",
    "OpenAICompatibleProvider",
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
    "RegionalContext",
    "Representation",
    "ResourceLimitError",
    "SQLiteRecipeRepository",
    "Source",
    "StoredRecipe",
    "StringField",
    "UrlField",
    "UrlJobSource",
    "__version__",
    "build_job_schema",
    "job_json_schema",
    "job_run_json_schema",
    "load_job",
    "parse_job",
    "run_job",
    "run_job_async",
    "run_job_file",
    "schema_fingerprint",
]
