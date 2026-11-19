"""Declarative target field contracts and deterministic Pydantic model generation."""

from __future__ import annotations

from collections.abc import Sequence
from functools import partial
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    Field,
    StrictInt,
    WrapValidator,
    create_model,
    model_validator,
)

from rastro.domain.job_field_scalars import (
    BooleanField,
    DateField,
    DateTimeField,
    DecimalField,
    IntegerField,
    JobFieldType,
    NumberField,
    StringField,
    UrlField,
    _FieldBase,
)
from rastro.domain.job_field_values import bounded_list, recover_field
from rastro.domain.models import ConfigurationError

__all__ = [
    "BooleanField",
    "DateField",
    "DateTimeField",
    "DecimalField",
    "IntegerField",
    "JobField",
    "JobFieldType",
    "ListField",
    "NumberField",
    "ObjectField",
    "ScalarField",
    "StringField",
    "UrlField",
    "build_job_schema",
]

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
