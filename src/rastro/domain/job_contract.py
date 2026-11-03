"""Strict, redacted base contract shared by declarative job models (JOB-002)."""

from __future__ import annotations

import types
from decimal import Decimal
from typing import Annotated, Any, Self, Union, get_args, get_origin

from pydantic import (
    BaseModel,
    ConfigDict,
    ModelWrapValidatorHandler,
    ValidationError,
    model_validator,
)
from pydantic_core import PydanticCustomError

from rastro.domain.models import Contract

ENV_REFERENCE = r"^[A-Z_][A-Z0-9_]{0,127}$"
"""Uppercase environment-variable names: the only way a job may reference a secret."""


def _scalar_kind(annotation: Any) -> Any:
    """Unwrap ``Annotated`` and ``X | None`` to the single concrete type, if there is one."""
    while get_origin(annotation) is Annotated:
        annotation = get_args(annotation)[0]
    if get_origin(annotation) in (Union, types.UnionType):
        options = [item for item in get_args(annotation) if item is not type(None)]
        return _scalar_kind(options[0]) if len(options) == 1 else None
    return annotation


def _strict_value(annotation: Any, value: Any, name: str) -> None:
    kind = _scalar_kind(annotation)
    if value is None or kind is None:
        return
    if kind is bool and not isinstance(value, bool):
        raise ValueError(f"{name} must be a boolean, not a string or number")
    if kind is int and (isinstance(value, bool) or not isinstance(value, int)):
        raise ValueError(f"{name} must be an integer")
    if kind is float and (isinstance(value, bool) or not isinstance(value, (int, float))):
        raise ValueError(f"{name} must be a number")
    if kind is Decimal and (isinstance(value, bool) or not isinstance(value, (int, float, str))):
        raise ValueError(f"{name} must be a number or decimal string")
    if get_origin(kind) is dict and isinstance(value, dict):
        for item in value.values():
            _strict_value(get_args(kind)[1], item, name)
    elif (
        isinstance(kind, type)
        and issubclass(kind, BaseModel)
        and not issubclass(kind, RedactedContract)
    ):
        check_strict_scalars(kind, value)


def check_strict_scalars(model: type[BaseModel], data: Any) -> None:
    """Reject lax coercions ("yes" -> True, "3" -> 3) in raw input for ``model``'s fields.

    Nested ``RedactedContract`` models check themselves; other nested contracts such as
    ``AcquisitionPolicy`` are checked recursively so job files cannot rely on coercion.
    """
    if not isinstance(data, dict):
        return
    for name, info in model.model_fields.items():
        if name in data:
            _strict_value(info.annotation, data[name], name)


class RedactedContract(Contract):
    """Forbid unknown keys and lax scalar coercion, and strip input values from errors."""

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    @model_validator(mode="before")
    @classmethod
    def reject_lax_scalars(cls, data: Any) -> Any:
        check_strict_scalars(cls, data)
        return data

    @model_validator(mode="wrap")
    @classmethod
    def redact_errors(cls, data: Any, handler: ModelWrapValidatorHandler[Self]) -> Self:
        try:
            return handler(data)
        except ValidationError as error:
            raise ValidationError.from_exception_data(
                cls.__name__,
                [
                    {
                        "type": PydanticCustomError(item["type"], item["msg"]),
                        "loc": item["loc"],
                        "input": None,
                    }
                    for item in error.errors(
                        include_url=False, include_input=False, include_context=False
                    )
                ],
                hide_input=True,
            ) from None
