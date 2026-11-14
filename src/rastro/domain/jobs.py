"""Versioned declarative job contract (JOB-001, JOB-002, AGENT-001)."""

from __future__ import annotations

import datetime as dt
import ipaddress
import math
import re
import tomllib
from typing import Any, Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from rastro.domain.acquisition import AcquisitionPolicy
from rastro.domain.consistency import ConsistencyRule
from rastro.domain.intelligence import InferencePolicy
from rastro.domain.job_contract import ENV_REFERENCE, RedactedContract
from rastro.domain.job_dataset import DatasetSpec
from rastro.domain.job_fields import JobField, build_job_schema
from rastro.domain.job_providers import JobModelProvider
from rastro.domain.job_sources import (
    FileJobSource,
    JobSource,
    RegionalContext,
    UrlJobSource,
    validate_job_path_syntax,
)
from rastro.domain.json import strict_json
from rastro.domain.memory import RecipeContext
from rastro.domain.models import Limits, Recipe, ResourceLimitError
from rastro.domain.recipe_compare import RepairPolicy
from rastro.domain.selector_policy import check_untrusted_recipe

JOB_SCHEMA_ID = "https://rave-vipx.github.io/rastro/schemas/job.schema.json"
MAX_JOB_BYTES = 262_144


def _canonical_host(hostname: str) -> str:
    host = hostname.lower().rstrip(".")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        return host


def _check_recipe_fields(recipe: Recipe, declared: set[str]) -> None:
    if any(name not in declared for item in (recipe, *recipe.fallbacks) for name in item.fields):
        raise ValueError("Recipe mappings must reference declared fields")


class Job(RedactedContract):
    """Declarative extraction job (`format_version = 1`). Pure data; never executes code."""

    format_version: Literal[1]
    sources: tuple[JobSource, ...] = Field(min_length=1, max_length=1_000)
    fields: tuple[JobField, ...] = Field(min_length=1, max_length=100)
    acquisition: AcquisitionPolicy | None = None
    region: RegionalContext = RegionalContext()
    context: RecipeContext | None = None
    limits: Limits | None = None
    recipe: Recipe | None = None
    recipe_version: StrictInt | None = Field(default=None, ge=1, le=2_147_483_647)
    discover_recipes: StrictBool = False
    discovery_aliases: dict[StrictStr, tuple[StrictStr, ...]] = Field(
        default_factory=dict, max_length=100
    )
    recipe_memory: str | None = Field(default=None, min_length=1, max_length=1024)
    model: JobModelProvider | None = None
    inference_policy: InferencePolicy | None = None
    dataset: DatasetSpec | None = None
    consistency: tuple[ConsistencyRule, ...] = Field(
        default=(), max_length=100, exclude_if=lambda value: not value
    )
    repair: RepairPolicy | None = Field(default=None, exclude_if=lambda value: value is None)

    @field_validator("format_version", mode="before")
    @classmethod
    def check_format_version(cls, value: Any) -> Any:
        if type(value) is not int:
            raise ValueError("format_version must be integer 1")
        return value

    @field_validator("recipe_memory")
    @classmethod
    def check_memory_path(cls, value: str | None) -> str | None:
        return None if value is None else validate_job_path_syntax(value)

    @model_validator(mode="after")
    def check_job_invariants(self) -> Self:
        declared = {item.name for item in self.fields}
        if len(declared) != len(self.fields):
            raise ValueError("Target field names must be unique")
        for key, aliases in self.discovery_aliases.items():
            if key not in declared:
                raise ValueError("discovery_aliases keys must match declared fields")
            if not 1 <= len(aliases) <= 16 or any(not 1 <= len(a) <= 256 for a in aliases):
                raise ValueError("Each field supports between 1 and 16 bounded aliases")
        if (self.context is not None or self.recipe_version is not None) and (
            self.recipe_memory is None
        ):
            raise ValueError("context and recipe_version require recipe_memory")
        if self.recipe is not None:
            _check_recipe_fields(self.recipe, declared)
        if any(rule.field not in declared for rule in self.consistency):
            raise ValueError("Consistency rules must reference declared fields")
        if self.repair is not None:
            for path in self.repair.fixtures:
                validate_job_path_syntax(path)
        if self.dataset is not None:
            self.dataset.check_against([item.name for item in self.fields])
        self._check_env_references()
        self._check_http_policy()
        self._check_source_recipes_and_egress(declared)
        return self

    def _check_env_references(self) -> None:
        profiles = self.acquisition.egress_profiles if self.acquisition else {}
        for profile in profiles.values():
            for name in (profile.username_env, profile.password_env):
                if name is not None and not re.fullmatch(ENV_REFERENCE, name):
                    raise ValueError("Egress credential references must be uppercase env names")

    def _check_http_policy(self) -> None:
        http_sources = [item for item in self.sources if isinstance(item, UrlJobSource)]
        if not http_sources:
            return
        if self.acquisition is None:
            raise ValueError("HTTP sources require an acquisition policy")
        if len(http_sources) > self.acquisition.max_sources:
            raise ValueError("HTTP sources exceed acquisition.max_sources")
        for item in http_sources:
            parts = urlsplit(item.url)
            if _canonical_host(parts.hostname or "") not in self.acquisition.allowed_hosts:
                raise ValueError("HTTP source host must appear in acquisition.allowed_hosts")
            if parts.scheme != "https" and not self.acquisition.allow_http:
                raise ValueError("HTTP URLs require acquisition.allow_http = true")

    def _check_source_recipes_and_egress(self, declared: set[str]) -> None:
        for source in self.sources:
            effective = source.recipe or self.recipe
            if effective is not None:
                if self.recipe_version is not None:
                    raise ValueError("recipe_version selects stored recipes; omit explicit recipe")
                if effective.representation != source.representation:
                    raise ValueError("Recipe strategy must match source representation")
                _check_recipe_fields(effective, declared)
            elif self.recipe_memory is None and not self.discover_recipes and self.model is None:
                raise ValueError(
                    "Sources without a recipe require recipe_memory, discover_recipes, or model"
                )
            egress = (source.region or self.region).egress
            if egress != "direct" and (
                self.acquisition is None or egress not in self.acquisition.egress_profiles
            ):
                raise ValueError("Non-direct egress requires a matching egress_profiles entry")

    def build_schema(self) -> type[BaseModel]:
        return build_job_schema(self.fields)

    def redacted_dump(self) -> dict[str, Any]:
        """JSON-mode dump with every ``*_env`` reference replaced by ``"<redacted>"``."""
        dump = self.model_dump(mode="json", exclude_none=False)
        if self.dataset is None:
            dump.pop("dataset")
        redacted: dict[str, Any] = _redact_env(dump)
        return redacted


def _redact_env(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "<redacted>" if key.endswith("_env") and item is not None else _redact_env(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_env(item) for item in value]
    return value


def _check_toml_scalars(value: object) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Non-finite numbers are not allowed")
    if isinstance(value, (dt.date, dt.time)):
        raise ValueError("Use strings for dates and times in job definitions")
    if isinstance(value, dict):
        for item in value.values():
            _check_toml_scalars(item)
    elif isinstance(value, list):
        for item in value:
            _check_toml_scalars(item)


def parse_job(raw: str | bytes, *, format: Literal["toml", "json"]) -> Job:
    """Parse and validate a TOML or JSON job definition without touching the filesystem."""
    encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(encoded) > MAX_JOB_BYTES:
        raise ResourceLimitError("Job definition exceeds 256 KiB")
    text = raw if isinstance(raw, str) else raw.decode("utf-8")
    if text.startswith("\ufeff"):
        raise ValueError("UTF-8 byte-order mark is not allowed")
    if format == "json":
        parsed = strict_json(text)
    elif format == "toml":
        parsed = tomllib.loads(text)
        _check_toml_scalars(parsed)
    else:
        raise ValueError("Job format must be 'toml' or 'json'")
    if not isinstance(parsed, dict):
        raise ValueError("Job definition must be a top-level object")
    job = Job.model_validate(parsed)
    # A job file may come from someone else (SECURITY.md): its recipes follow the same
    # selector cost policy as bundles, MCP and model proposals (security audit H3).
    for recipe in (job.recipe, *(source.recipe for source in job.sources)):
        if recipe is not None:
            check_untrusted_recipe(recipe)
    return job


def job_json_schema() -> dict[str, Any]:
    """Return the published JSON Schema (Draft 2020-12) for declarative jobs."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": JOB_SCHEMA_ID,
        **Job.model_json_schema(),
    }


__all__ = [
    "JOB_SCHEMA_ID",
    "MAX_JOB_BYTES",
    "FileJobSource",
    "Job",
    "JobSource",
    "RegionalContext",
    "UrlJobSource",
    "job_json_schema",
    "parse_job",
    "validate_job_path_syntax",
]
