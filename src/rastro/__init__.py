"""Rastro: schema-first extraction with field-level provenance."""

from importlib import import_module
from importlib.metadata import version
from typing import TYPE_CHECKING

from rastro.adapters.sqlite import SQLiteRecipeRepository
from rastro.application.engine import Rastro
from rastro.application.job_dataset import load_job_file
from rastro.application.job_maintenance import acknowledge_job_drift, forget_job_source
from rastro.application.job_repair import promote_job_recipe, repair_job_source
from rastro.application.jobs import load_job, run_job, run_job_async, run_job_file
from rastro.application.recipe_apply import apply_job_recipe
from rastro.application.suggest import suggest
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
from rastro.domain.job_load import JobLoadResult, LoadedSource
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
    RecipeSelection,
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
from rastro.domain.lifecycle import (
    LifecycleAction,
    LifecycleTransitionError,
    RecipeState,
    VersionStatus,
)
from rastro.domain.lifecycle_history import LifecycleEvent, LifecycleHistory
from rastro.domain.memory import (
    RecipeConflictError,
    RecipeContext,
    RecipeKey,
    RecipeMetadata,
    RecipeNotFoundError,
    RecipeStorageError,
    StoredRecipe,
)
from rastro.domain.model_selectors import css
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
from rastro.domain.recipe_compare import (
    CandidateEvaluation,
    RecipeComparison,
    RepairReport,
    SourceRepair,
)
from rastro.domain.results import RepairProposal, SelfRepair
from rastro.domain.suggest import FieldConfidence, Suggestion, SuggestionError
from rastro.mocklab import (
    FixtureBundle,
    MockLabError,
    SanitizationError,
    SyntheticLabel,
    build_fixture,
    load_fixture,
    verify_fixture,
)
from rastro.ports.model import ModelPort
from rastro.ports.recipes import RecipeLifecycle, RecipeRepository
from rastro.scrape import scrape, scrape_async

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
    "CandidateEvaluation",
    "ConfigurationError",
    "CssRecipe",
    "DateField",
    "DateTimeField",
    "DecimalField",
    "EgressProfile",
    "Evidence",
    "ExtractionRecord",
    "ExtractionResult",
    "FieldConfidence",
    "FileJobSource",
    "FixtureBundle",
    "InferencePolicy",
    "IntegerField",
    "Issue",
    "Job",
    "JobExitCode",
    "JobField",
    "JobFieldType",
    "JobLoadResult",
    "JobModelProvider",
    "JobRecordData",
    "JobRunResult",
    "JobRunStatus",
    "JobSource",
    "JobSummary",
    "JsonLdRecipe",
    "JsonRecipe",
    "LifecycleAction",
    "LifecycleEvent",
    "LifecycleHistory",
    "LifecycleTransitionError",
    "Limits",
    "LoadedSource",
    "Metrics",
    "MockLabError",
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
    "RecipeComparison",
    "RecipeConflictError",
    "RecipeContext",
    "RecipeKey",
    "RecipeLifecycle",
    "RecipeMetadata",
    "RecipeNotFoundError",
    "RecipeProposal",
    "RecipeRepository",
    "RecipeSelection",
    "RecipeState",
    "RecipeStorageError",
    "RegionalContext",
    "RepairProposal",
    "RepairReport",
    "Representation",
    "ResourceLimitError",
    "SQLiteRecipeRepository",
    "SanitizationError",
    "SelfRepair",
    "Source",
    "SourceRepair",
    "StoredRecipe",
    "StringField",
    "Suggestion",
    "SuggestionError",
    "SyntheticLabel",
    "UrlField",
    "UrlJobSource",
    "VersionStatus",
    "__version__",
    "acknowledge_job_drift",
    "apply_job_recipe",
    "build_fixture",
    "build_job_schema",
    "css",
    "forget_job_source",
    "job_json_schema",
    "job_run_json_schema",
    "load_fixture",
    "load_job",
    "load_job_file",
    "parse_job",
    "promote_job_recipe",
    "repair_job_source",
    "run_job",
    "run_job_async",
    "run_job_file",
    "schema_fingerprint",
    "scrape",
    "scrape_async",
    "suggest",
    "verify_fixture",
]
