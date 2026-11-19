"""Validate one record so a bad optional field costs that field, not the record."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError
from pydantic_core import ErrorDetails

from rastro.domain.models import Issue
from rastro.domain.safe_messages import safe_message

__all__ = ["IsolatedRecord", "count_nulled", "validate_isolated"]

_PLACEHOLDER = "null_placeholder"


@dataclass(frozen=True)
class IsolatedRecord[T: BaseModel]:
    """``data`` is None when the record is rejected; ``issues`` then explain why."""

    data: T | None
    issues: tuple[Issue, ...] = ()
    nulled: tuple[str, ...] = ()


def validate_isolated[T: BaseModel](
    schema: type[T],
    values: Mapping[str, Any],
    *,
    context: Mapping[str, Any],
    record_index: int,
) -> IsolatedRecord[T]:
    """Null optional fields that fail validation; reject the record if a required one does."""
    pending = dict(values)
    nulled: list[str] = []
    notes: list[Issue] = []
    for _ in range(len(values) + 1):
        field_issues: list[Issue] = []
        try:
            data = schema.model_validate(
                pending, by_name=True, context={**context, "field_issues": field_issues}
            )
        except ValidationError as error:
            details = error.errors(include_url=False, include_context=False, include_input=False)
            failing = _optional_failures(schema, details)
            if failing is None:
                return IsolatedRecord(None, _rejection(details, record_index))
            for name, detail in failing.items():
                del pending[name]
                nulled.append(name)
                notes.extend(_null_notes(name, detail, record_index))
            continue
        extra = tuple(_indexed(issue, record_index) for issue in field_issues)
        return IsolatedRecord(data, (*notes, *extra), tuple(nulled))
    return IsolatedRecord(None, (Issue(code="validation_error", record_index=record_index),))


def count_nulled(issues: Iterable[Issue]) -> dict[str, int]:
    """Per-field count of ``field_nulled`` issues, ready for a source's notes."""
    counts = Counter(i.field for i in issues if i.code == "field_nulled" and i.field)
    return dict(sorted(counts.items()))


def _optional_failures(
    schema: type[BaseModel], details: list[ErrorDetails]
) -> dict[str, ErrorDetails] | None:
    """The failing top-level optional fields, or None when any error cannot be nulled."""
    failing: dict[str, ErrorDetails] = {}
    for detail in details:
        name = str(detail["loc"][0]) if detail["loc"] else None
        field = schema.model_fields.get(name) if name else None
        if field is None or field.is_required():
            return None
        failing.setdefault(str(name), detail)
    return failing


def _null_notes(name: str, detail: ErrorDetails, record_index: int) -> list[Issue]:
    notes = []
    if detail["type"] == _PLACEHOLDER:
        notes.append(Issue(code=_PLACEHOLDER, record_index=record_index, field=name))
    notes.append(
        Issue(
            code="field_nulled",
            record_index=record_index,
            field=name,
            message=safe_message(detail["type"], detail["msg"]),
        )
    )
    return notes


def _rejection(details: list[ErrorDetails], record_index: int) -> tuple[Issue, ...]:
    issues = []
    for detail in details:
        # Validator messages can contain secrets; only expose a stable category.
        field = str(detail["loc"][0]) if detail["loc"] else None
        issues.append(
            Issue(
                code="validation_error",
                record_index=record_index,
                field=field,
                message=safe_message(detail["type"], detail["msg"]),
            )
        )
    return tuple(issues)


def _indexed(issue: Issue, record_index: int) -> Issue:
    return issue.model_copy(update={"record_index": record_index})
