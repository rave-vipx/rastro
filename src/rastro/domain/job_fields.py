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
    SerializerFunctionWrapHandler,
    StrictBool,
    StrictInt,
    TypeAdapter,
    WrapValidator,
    create_model,
    field_validator,
    model_serializer,
    model_validator,
)
from pydantic_core import SchemaError

from rastro.domain.job_contract import RedactedContract
from rastro.domain.job_field_values import (
    bounded_list,
    localized_date,
    null_placeholder,
    recover_field,
    relative_url,
    strip_text,
)
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
    "list",
    "object",
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
    null_values: tuple[Annotated[str, Field(min_length=1, max_length=64)], ...] = Field(
        default=(),
        max_length=16,
        description="Text placeholders rejected after stripping whitespace and case folding.",
    )

    @model_serializer(mode="wrap")
    def _omit_empty_placeholders(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        # Keeps the stored form of jobs without placeholders byte-identical.
        data: dict[str, Any] = handler(self)
        if not self.null_values:
            data.pop("null_values", None)
        return data

    @field_validator("name")
    @classmethod
    def check_name(cls, value: str) -> str:
        if keyword.iskeyword(value) or value.startswith("model_") or hasattr(BaseModel, value):
            raise ValueError("Field name is reserved by Python or Pydantic")
        return value

    def _wrap_required(self, annotation: Any) -> tuple[Any, Any]:
        # Blank text is stripped before validation, so constraints see the real value and
        # a blank required value is a validation issue (QUAL-004).
        blank = BeforeValidator(partial(strip_text, required=self.required))
        if self.null_values:
            annotation = Annotated[
                annotation,
                BeforeValidator(
                    partial(
                        null_placeholder,
                        values=frozenset(v.strip().casefold() for v in self.null_values),
                    )
                ),
            ]
        if self.required:
            return (Annotated[annotation, blank], ...)
        return (Annotated[annotation | None, blank], None)


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
        target = Annotated[
            datetime,
            BeforeValidator(partial(localized_date, want_time=True, localized=self.localized)),
        ]
        return self._wrap_required(target)


class UrlField(_FieldBase):
    type: Literal["url"] = "url"

    def field_definition(self) -> tuple[Any, Any]:
        return self._wrap_required(Annotated[AnyHttpUrl, BeforeValidator(relative_url)])


type ScalarField = (
    StringField
    | IntegerField
    | NumberField
    | BooleanField
    | DecimalField
    | DateField
    | DateTimeField
    | UrlField
)


class ObjectField(_FieldBase):
    type: Literal["object"] = "object"
    members: tuple[Annotated[ScalarField, Field(discriminator="type")], ...] = Field(
        min_length=1,
        max_length=32,
        description="Uniquely named scalar members of an object.",
    )

    @model_validator(mode="after")
    def check_members(self) -> Self:
        if len({member.name for member in self.members}) != len(self.members):
            raise ValueError("Object member names must be unique")
        return self

    def field_definition(self) -> tuple[Any, Any]:
        definitions: dict[str, Any] = {}
        for member in sorted(self.members, key=lambda m: m.name):
            annotation, default = member.field_definition()
            if not member.required:
                annotation = Annotated[
                    annotation,
                    WrapValidator(
                        partial(
                            recover_field,
                            code="member_nulled",
                            name=member.name,
                        )
                    ),
                ]
            definitions[member.name] = annotation, default
        target = create_model("JobObject", __module__=__name__, **definitions)
        return self._wrap_required(target)


class ListField(_FieldBase):
    type: Literal["list"] = "list"
    item: Annotated[
        StringField
        | IntegerField
        | NumberField
        | BooleanField
        | DecimalField
        | DateField
        | DateTimeField
        | UrlField
        | ObjectField,
        Field(discriminator="type"),
    ] = Field(
        description="Scalar or object item contract; its name is ignored.",
    )
    min_items: StrictInt = Field(default=0, ge=0, le=1000, description="Minimum valid items.")
    max_items: StrictInt = Field(default=100, ge=1, le=1000, description="Maximum input items.")

    @model_validator(mode="after")
    def check_item_limits(self) -> Self:
        if self.min_items > self.max_items:
            raise ValueError("min_items must not exceed max_items")
        return self

    def field_definition(self) -> tuple[Any, Any]:
        annotation, _ = self.item.field_definition()
        item = Annotated[
            annotation,  # type: ignore[valid-type]
            WrapValidator(
                partial(
                    recover_field,
                    code="list_item_dropped",
                    name=self.name,
                )
            ),
        ]
        target = Annotated[
            list[item],
            Field(min_length=self.min_items, max_length=self.max_items),
            BeforeValidator(partial(bounded_list, maximum=self.max_items)),
        ]
        return self._wrap_required(target)


# Spelled out: a discriminated union nested in another reports a doubled tag in error paths.
type JobField = Annotated[
    StringField
    | IntegerField
    | NumberField
    | BooleanField
    | DecimalField
    | DateField
    | DateTimeField
    | UrlField
    | ListField
    | ObjectField,
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
