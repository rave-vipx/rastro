"""Rastro: schema-first extraction with field-level provenance."""

from importlib.metadata import version

from rastro.application.engine import Rastro
from rastro.domain.acquisition import AcquisitionPolicy
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
    ResourceLimitError,
    Source,
    schema_fingerprint,
)

__version__ = version("rastro-engine")

__all__ = [
    "AcquisitionPolicy",
    "ConfigurationError",
    "Evidence",
    "ExtractionRecord",
    "ExtractionResult",
    "Issue",
    "Limits",
    "Metrics",
    "Outcome",
    "Rastro",
    "RastroError",
    "Recipe",
    "ResourceLimitError",
    "Source",
    "__version__",
    "schema_fingerprint",
]
