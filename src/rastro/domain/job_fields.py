"""Declarative target field contracts and deterministic Pydantic model generation."""

from __future__ import annotations

import keyword
import math
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from functools import partial
from typing import Annotated, Any, Literal, Self

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    BeforeValidator,
    Field,
    StrictBool,
    StrictInt,
    TypeAdapter,
    create_model,
    field_validator,
    model_validator,
)
from pydantic_core import SchemaError

from rastro.domain.job_contract import RedactedContract
from rastro.domain.job_field_values import localized_date, relative_url
from rastro.domain.models import ConfigurationError

type JobFieldType = Literal[
    "string",
    "integer",
    "number",
    "boolean",
    "decimal",
    "date",
    "datetime",
    "url",
]
_FIELD_NAME = r"^[A-Za-z][A-Za-z0-9_]{0,63}$"


def _reject_bool_bound(value: Any) -> Any:
    if isinstance(value, bool):
        raise ValueError("Numeric bounds cannot be booleans")
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Numeric bounds must be finite")
    return value


class _FieldBase(RedactedContract):
    name: str = Field(min_length=1, max_length=64, pattern=_FIELD_NAME)
    required: StrictBool = True

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        if keyword.iskeyword(value) or value.startswith("model_") or hasattr(BaseModel, value):
            raise ValueError("Field name is reserved by Python or Pydantic")
        return value

    def _wrap_required(self, annotation: Any) -> tuple[Any, Any]:
        return (annotation, ...) if self.required else (annotation | None, None)


class StringField(_FieldBase):
    type: Literal["string"] = "string"
    min_length: StrictInt | None = Field(default=None, ge=0, le=1_000_000)
    max_length: StrictInt | None = Field(default=None, ge=1, le=1_000_000)
    pattern: str | None = Field(default=None, min_length=1, max_length=1024)

    @field_validator("pattern")
    @classmethod
    def check_pattern(cls, value: str | None) -> str | None:
        if value is not None:
            # Compile with Pydantic's own regex engine (no look-around or backreferences),
            # so a pattern accepted here can always build the dynamic record model.
            try:
                TypeAdapter(Annotated[str, Field(pattern=value)])
            except SchemaError:
                raise ValueError(
                    "Invalid or unsupported regular expression pattern "
                    "(look-around and backreferences are not supported)"
                ) from None
        return value

    @model_validator(mode="after")
    def check_lengths(self) -> Self:
        if (
            self.min_length is not None
            and self.max_length is not None
            and self.min_length > self.max_length
        ):
            raise ValueError("min_length must not exceed max_length")
        return self

    def field_definition(self) -> tuple[Any, Any]:
        constraints: dict[str, Any] = {}
        if self.min_length is not None:
            constraints["min_length"] = self.min_length
        if self.max_length is not None:
            constraints["max_length"] = self.max_length
        if self.pattern is not None:
            constraints["pattern"] = self.pattern
        target: Any = Annotated[str, Field(**constraints)] if constraints else str
        return self._wrap_required(target)


class IntegerField(_FieldBase):
    type: Literal["integer"] = "integer"
    ge: StrictInt | None = None
    le: StrictInt | None = None

    @model_validator(mode="after")
    def check_bounds(self) -> Self:
        if self.ge is not None and self.le is not None and self.ge > self.le:
            raise ValueError("ge must not exceed le")
        return self

    def field_definition(self) -> tuple[Any, Any]:
        constraints: dict[str, Any] = {}
        if self.ge is not None:
            constraints["ge"] = self.ge
        if self.le is not None:
            constraints["le"] = self.le
        target: Any = Annotated[int, Field(**constraints)] if constraints else int
        return self._wrap_required(target)


class NumberField(_FieldBase):
    type: Literal["number"] = "number"
    ge: float | None = Field(default=None, allow_inf_nan=False)
    le: float | None = Field(default=None, allow_inf_nan=False)
    _no_bool = field_validator("ge", "le", mode="before")(_reject_bool_bound)

    @model_validator(mode="after")
    def check_bounds(self) -> Self:
        if self.ge is not None and self.le is not None and self.ge > self.le:
            raise ValueError("ge must not exceed le")
        return self

    def field_definition(self) -> tuple[Any, Any]:
        constraints: dict[str, Any] = {"allow_inf_nan": False}
        if self.ge is not None:
            constraints["ge"] = self.ge
        if self.le is not None:
            constraints["le"] = self.le
        return self._wrap_required(Annotated[float, Field(**constraints)])


class BooleanField(_FieldBase):
    type: Literal["boolean"] = "boolean"

    def field_definition(self) -> tuple[Any, Any]:
        return self._wrap_required(bool)


class DecimalField(_FieldBase):
    type: Literal["decimal"] = "decimal"
    ge: Decimal | None = Field(default=None, allow_inf_nan=False)
    le: Decimal | None = Field(default=None, allow_inf_nan=False)
    _no_bool = field_validator("ge", "le", mode="before")(_reject_bool_bound)

    @model_validator(mode="after")
    def check_bounds(self) -> Self:
        if self.ge is not None and self.le is not None and self.ge > self.le:
            raise ValueError("ge must not exceed le")
        return self

    def field_definition(self) -> tuple[Any, Any]:
        constraints: dict[str, Any] = {"allow_inf_nan": False}
        if self.ge is not None:
            constraints["ge"] = self.ge
        if self.le is not None:
            constraints["le"] = self.le
        return self._wrap_required(Annotated[Decimal, Field(**constraints)])


class DateField(_FieldBase):
    type: Literal["date"] = "date"
    localized: StrictBool = False

    def field_definition(self) -> tuple[Any, Any]:
        target = Annotated[date, BeforeValidator(partial(localized_date, want_time=False))]
        return self._wrap_required(target if self.localized else date)


class DateTimeField(_FieldBase):
    type: Literal["datetime"] = "datetime"
    localized: StrictBool = False

    def field_definition(self) -> tuple[Any, Any]:
        target = Annotated[datetime, BeforeValidator(partial(localized_date, want_time=True))]
        return self._wrap_required(target if self.localized else datetime)


class UrlField(_FieldBase):
    type: Literal["url"] = "url"

    def field_definition(self) -> tuple[Any, Any]:
        return self._wrap_required(Annotated[AnyHttpUrl, BeforeValidator(relative_url)])


type JobField = Annotated[
    StringField
    | IntegerField
    | NumberField
    | BooleanField
    | DecimalField
    | DateField
    | DateTimeField
    | UrlField,
    Field(discriminator="type"),
]


def build_job_schema(fields: Sequence[JobField]) -> type[BaseModel]:
    """Create a deterministic Pydantic model so schema_fingerprint stays identical across runs."""
    if not 1 <= len(fields) <= 100:
        raise ConfigurationError("Jobs must declare between 1 and 100 target fields")
    names = [item.name for item in fields]
    if len(set(names)) != len(names):
        raise ConfigurationError("Target field names must be unique")
    ordered: dict[str, Any] = {
        item.name: item.field_definition() for item in sorted(fields, key=lambda f: f.name)
    }
    return create_model("JobRecord", __module__=__name__, **ordered)
