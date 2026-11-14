"""Machine-readable `rastro job` error objects (AGENT-001, AGENT-003).

Every job configuration or usage failure becomes one JSON object on stderr:
``{"error": <stable code>, "message": <safe text>, "location": <field path or null>}``,
plus an optional ``"hint"``.
Messages are Rastro's own text or redacted Pydantic messages; they never contain job values,
page text or environment-variable contents.
"""

from __future__ import annotations

import json
import re
import tomllib
from typing import Any

from pydantic import ValidationError
from pydantic_core import SchemaError

from rastro.application.errors import missing_extra_error
from rastro.domain.dataset import DatasetNotFoundError, DatasetStorageError
from rastro.domain.models import RastroError, ResourceLimitError

__all__ = ["UsageError", "classify", "missing_extra_error"]

MAX_MESSAGE = 300
_TOML_POSITION = re.compile(r"\((at (?:line \d+, column \d+|end of document))\)$")
_TAG_ERRORS = {"union_tag_invalid", "union_tag_not_found"}


class UsageError(Exception):
    """Raised instead of argparse's default exit so usage errors can be reported as JSON."""


def _location(loc: tuple[int | str, ...]) -> str | None:
    parts = [str(item) if len(str(item)) <= 64 else "<key>" for item in loc]
    return ".".join(parts) or None


def _validation_payload(error: ValidationError) -> tuple[str, str | None]:
    items = error.errors(include_url=False, include_input=False, include_context=False)
    if not items:
        return "Job definition is invalid", None
    first = items[0]
    if first["type"] in _TAG_ERRORS:
        message = "Unknown or missing discriminator value; use a documented type/kind/provider"
    else:
        message = first["msg"].removeprefix("Value error, ")
    if len(items) > 1:
        message = f"{message} (and {len(items) - 1} more validation errors)"
    cause = error.errors(include_url=False, include_input=False)[0].get("ctx", {}).get("error")
    location = getattr(cause, "location", None)
    return message, location if isinstance(location, str) else _location(first["loc"])


def classify(error: BaseException) -> dict[str, Any]:
    """Map an exception to the documented error object without echoing raw input."""
    code, message, location = "configuration_error", "Job configuration is invalid", None
    if isinstance(error, UsageError):
        code, message = "usage_error", str(error)
    elif isinstance(error, ValidationError):
        code = "validation_error"
        message, location = _validation_payload(error)
    elif isinstance(error, SchemaError):
        code, message = "schema_error", "Target fields could not be compiled into a record model"
    elif isinstance(error, tomllib.TOMLDecodeError | json.JSONDecodeError):
        code, message = "syntax_error", f"Job file is not valid {_format_name(error)}"
        if isinstance(error, json.JSONDecodeError):
            message += f" (line {error.lineno}, column {error.colno})"
        elif position := _TOML_POSITION.search(str(error)):
            message += f" ({position.group(1)})"
    elif isinstance(error, UnicodeDecodeError):
        code, message = "syntax_error", "Job file must be UTF-8 text"
    elif isinstance(error, ResourceLimitError):
        code, message = "resource_limit", str(error)
    elif isinstance(error, DatasetStorageError) and not isinstance(error, DatasetNotFoundError):
        code, message = "storage_error", str(error) or "Dataset operation failed"
        location = error.location
    elif isinstance(error, RastroError):
        message, location = str(error) or message, error.location
    elif isinstance(error, OSError):
        code, message = "file_error", f"File could not be read: {error.strerror or 'I/O error'}"
    elif isinstance(error, ValueError):
        code, message = "invalid_job", str(error) or "Job definition is invalid"
    else:
        code, message = "internal_error", f"Unexpected {type(error).__name__}"
    payload: dict[str, Any] = {
        "error": code,
        "message": message[:MAX_MESSAGE],
        "location": location,
    }
    if isinstance(error, RastroError) and error.hint:
        payload["hint"] = error.hint[:MAX_MESSAGE]
    return payload


def _format_name(error: Exception) -> str:
    return "JSON" if isinstance(error, json.JSONDecodeError) else "TOML"
