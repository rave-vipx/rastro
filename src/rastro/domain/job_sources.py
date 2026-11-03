"""Declarative job sources, regional context, and safe path syntax rules."""

from __future__ import annotations

import hashlib
import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal, Self
from urllib.parse import quote

from pydantic import BeforeValidator, Field, field_validator, model_validator

from rastro.domain.job_fields import RedactedContract
from rastro.domain.models import EGRESS_NAME, Recipe, Representation, Source


def validate_job_path_syntax(value: str) -> str:
    """Validate path syntax without touching the filesystem; containment runs at load/run time."""
    if (
        not value
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in value)
        or value == ":memory:"
        or value.lower().startswith("file:")
        or value.startswith("~")
        or value.endswith(("/", "\\"))
    ):
        raise ValueError("Invalid job path")
    segments = re.split(r"[/\\]", value)
    if ".." in segments or segments[-1] in {"", "."}:
        raise ValueError("Job paths cannot traverse parent directories or name a directory")
    return value


class RegionalContext(RedactedContract):
    """Explicit regional settings applied to sources unless overridden per source."""

    locale: str = Field(default="und", min_length=2, max_length=64)
    languages: tuple[str, ...] = Field(default=(), max_length=20)
    timezone: str = Field(default="UTC", min_length=1, max_length=128)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    egress: str = Field(default="direct", pattern=EGRESS_NAME)

    @model_validator(mode="before")
    @classmethod
    def reject_geolocation(cls, data: Any) -> Any:
        if isinstance(data, dict) and "geolocation" in data:
            raise ValueError("Browser geolocation is unavailable without a browser adapter")
        return data

    @model_validator(mode="after")
    def validate_via_source(self) -> Self:
        self.to_source("https://example.com/")
        return self

    def to_source(self, url: str) -> Source:
        return Source(url=url, **self.model_dump())


class UrlJobSource(RedactedContract):
    """Permitted HTTP(S) job source; its host must appear in acquisition.allowed_hosts."""

    kind: Literal["url"] = "url"
    url: str = Field(min_length=1, max_length=8192)
    representation: Representation = "html"
    region: RegionalContext | None = None
    recipe: Recipe | None = None

    @field_validator("url")
    @classmethod
    def check_url(cls, value: str) -> str:
        return Source(url=value).url


class FileJobSource(RedactedContract):
    """Local HTML or JSON file source resolved within the job file's directory by default."""

    kind: Literal["file"] = "file"
    path: str = Field(min_length=1, max_length=1024)
    url: str | None = Field(default=None, min_length=1, max_length=8192)
    representation: Representation = "html"
    region: RegionalContext | None = None
    recipe: Recipe | None = None

    _check_path = field_validator("path")(validate_job_path_syntax)

    @field_validator("url")
    @classmethod
    def check_url(cls, value: str | None) -> str | None:
        return None if value is None else Source(url=value).url

    def resolved_source_url(self) -> str:
        if self.url is not None:
            return self.url
        if PurePosixPath(self.path).is_absolute() or PureWindowsPath(self.path).is_absolute():
            digest = hashlib.sha256(self.path.encode("utf-8")).hexdigest()[:16]
            return f"https://local.invalid/file/{digest}"
        parts = [p for p in self.path.replace("\\", "/").split("/") if p and p != "."]
        return "https://local.invalid/" + "/".join(quote(p, safe="-._~") for p in parts)


def _coerce_source(value: Any) -> Any:
    if isinstance(value, str):
        return {"kind": "url", "url": value}
    if isinstance(value, dict) and "kind" not in value:
        if "path" in value:
            return {"kind": "file", **value}
        if "url" in value:
            return {"kind": "url", **value}
    return value


type JobSource = Annotated[
    Annotated[UrlJobSource | FileJobSource, Field(discriminator="kind")],
    BeforeValidator(_coerce_source, json_schema_input_type=UrlJobSource | FileJobSource | str),
]
