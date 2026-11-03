"""Bounded JSON decoding, pointer navigation and stable serialization for mutations."""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import Field

from rastro.domain.json import escape_token, resolve_pointer, strict_json
from rastro.domain.sources import Limits, ResourceLimitError
from rastro.mocklab.mutations.contracts import SkipReason

_TOKEN = r"/(?:[^~/]|~[01])*"
type Pointer = Annotated[str, Field(max_length=1024, pattern=rf"^(?:{_TOKEN})*$")]
type FieldPointer = Annotated[str, Field(max_length=1024, pattern=rf"^(?:{_TOKEN})+$")]
"""A non-root pointer, resolved relative to each record."""

type Record = dict[str, object]


def decode(document: str) -> tuple[object, SkipReason | None]:
    """Decode strict JSON within ``Limits``; the reason is set when nothing can be mutated."""
    limits = Limits()
    if len(document.encode()) > limits.max_json_bytes:
        raise ResourceLimitError("JSON input exceeds max_json_bytes")
    if not document.strip():
        return None, "empty_document"
    try:
        payload = strict_json(document)
    except ValueError:
        return None, "invalid_document"
    except RecursionError:
        raise ResourceLimitError("JSON nesting exceeds max_json_depth") from None
    _check_size(payload, limits)
    return payload, None


def _check_size(payload: object, limits: Limits) -> None:
    nodes, pending = 0, [(payload, 0)]
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if nodes > limits.max_json_nodes or depth > limits.max_json_depth:
            raise ResourceLimitError("JSON input exceeds max_json_nodes or max_json_depth")
        if isinstance(value, dict):
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)


def encode(payload: object) -> str:
    """Compact, key-order-preserving output: equal trees give equal bytes."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def records(payload: object, pointer: str) -> list[tuple[str, Record]] | None:
    """Object records at ``pointer``: the object itself or the objects of an array."""
    target = resolve_pointer(payload, pointer)
    if isinstance(target, dict):
        return [(pointer, target)]
    if isinstance(target, list):
        return [(f"{pointer}/{i}", item) for i, item in enumerate(target) if isinstance(item, dict)]
    return None


def field_slot(record: Record, field: str) -> tuple[Record, str] | None:
    """The object holding the last token of ``field`` and that key, when present."""
    slash = field.rfind("/")
    parent = resolve_pointer(record, field[:slash])
    key = field[slash + 1 :].replace("~1", "/").replace("~0", "~")
    if isinstance(parent, dict) and key in parent:
        return parent, key
    return None


def sibling(field: str, key: str) -> str:
    """``field`` with its last token replaced by ``key``."""
    return f"{field[: field.rfind('/')]}/{escape_token(key)}"
