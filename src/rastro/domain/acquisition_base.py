"""Shared building blocks of the acquisition contracts: host names and redacted errors."""

from __future__ import annotations

import ipaddress
import re
from typing import Any, Self

from pydantic import ConfigDict, ModelWrapValidatorHandler, ValidationError, model_validator
from pydantic_core import PydanticCustomError

from rastro.domain.models import Contract

ENVIRONMENT_NAME = r"^[A-Z_][A-Z0-9_]{0,127}$"


def exact_host(host: str) -> str:
    host = host.lower().rstrip(".")
    try:
        return str(ipaddress.ip_address(host))
    except ValueError:
        if len(host) > 253 or not all(
            re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
            for label in host.split(".")
        ):
            raise ValueError("Use exact ASCII/Punycode hostnames or IP addresses") from None
        return host


def _without_input(error: ValidationError, title: str) -> ValidationError:
    """Rebuild errors without input values, which may hold inline proxy credentials."""
    return ValidationError.from_exception_data(
        title,
        [
            {
                "type": PydanticCustomError(item["type"], item["msg"]),
                "loc": item["loc"],
                "input": None,
            }
            for item in error.errors(include_url=False, include_input=False, include_context=False)
        ],
        hide_input=True,
    )


class RedactedContract(Contract):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    @model_validator(mode="wrap")
    @classmethod
    def redact_errors(cls, data: Any, handler: ModelWrapValidatorHandler[Self]) -> Self:
        try:
            return handler(data)
        except ValidationError as error:
            raise _without_input(error, cls.__name__) from None
