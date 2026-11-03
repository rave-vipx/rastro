"""Accepted discovery source names from Pydantic aliases and an explicit map (INT-002)."""

from __future__ import annotations

from collections.abc import Mapping

from pydantic import AliasChoices
from pydantic.fields import FieldInfo

from rastro.domain.models import ConfigurationError
from rastro.ports.discovery import AliasMap

MAX_ALIASES_PER_FIELD = 16
MAX_ALIAS_LENGTH = 256


def discovery_aliases(
    fields: Mapping[str, FieldInfo], explicit: AliasMap | None = None
) -> dict[str, tuple[str, ...]]:
    """Return extra exact source names per field; the model field name always matches.

    Declared ``Field(alias=...)`` and string ``validation_alias`` values, including
    ``AliasChoices``, combine with the caller's per-field map. ``AliasPath``
    selectors are not source names. Matching stays exact and case-sensitive.
    """
    if explicit:
        unknown = set(explicit) - set(fields)
        if unknown:
            raise ConfigurationError("Discovery aliases reference undeclared schema fields")
    resolved: dict[str, tuple[str, ...]] = {}
    for name, field in fields.items():
        extra = (*_declared(field), *_explicit(explicit, name))
        unique = tuple(item for item in dict.fromkeys(extra) if item != name)
        if len(unique) > MAX_ALIASES_PER_FIELD or any(
            not 0 < len(item) <= MAX_ALIAS_LENGTH for item in unique
        ):
            raise ConfigurationError("Discovery aliases must be bounded strings")
        if unique:
            resolved[name] = unique
    return resolved


def _declared(field: FieldInfo) -> tuple[str, ...]:
    alias = field.validation_alias
    if isinstance(alias, AliasChoices):
        return tuple(choice for choice in alias.choices if isinstance(choice, str))
    return (alias,) if isinstance(alias, str) else ()


def _explicit(explicit: AliasMap | None, name: str) -> tuple[str, ...]:
    if explicit is None:
        return ()
    values = explicit.get(name, ())
    if not isinstance(values, (list, tuple)) or any(not isinstance(item, str) for item in values):
        raise ConfigurationError("Discovery aliases must be sequences of strings")
    return tuple(values)
