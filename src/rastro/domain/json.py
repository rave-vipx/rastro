"""Strict JSON decoding and RFC 6901 pointers shared by untrusted structured inputs."""

import json
import math
import re

from rastro.domain.sources import ConfigurationError

MISSING = object()
MAX_INTEGER_DIGITS = 4300
POINTER = re.compile(r"(?:/(?:[^~/]|~[01])*)*")
RELATIVE_POINTER = re.compile(r"(0|[1-9][0-9]*)(/(?:[^~/]|~[01])*)?")
SURROGATE = re.compile("[\ud800-\udfff]")


def strict_json(value: str | bytes, *, repair_text: bool = False) -> object:
    """Decode JSON strictly.

    ``repair_text`` is for page data about to become records: each lone UTF-16 surrogate
    (an escape such as ``\\ud83d`` without its pair) becomes U+FFFD, since it cannot be
    stored as UTF-8. Without it the surrogate is kept, for callers that refuse such input.
    """
    payload = json.loads(
        value,
        parse_float=_finite_float,
        parse_int=_bounded_int,
        parse_constant=_reject_constant,
        object_pairs_hook=_repaired_keys if repair_text else _unique_keys,
    )
    return _clean(payload) if repair_text else payload


def clean_text(value: str) -> str:
    """Replace each lone surrogate with U+FFFD: such text cannot be stored as UTF-8."""
    if not SURROGATE.search(value):
        return value
    return value.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "replace")


def resolve_pointer(
    value: object, pointer: str, *, parents: tuple[object, ...] = (), max_records: int = 1000
) -> object:
    """Resolve a pointer, returning MISSING for invalid or absent paths.

    ``parents`` runs from the document root to the immediate parent. Relative pointers
    climb that context; wildcard array segments return a flat list bounded by
    ``max_records`` (pass ``Limits.max_records`` when extracting).
    """
    relative = RELATIVE_POINTER.fullmatch(pointer)
    if relative is not None:
        levels = relative[1]
        if len(levels) > 9 or int(levels) > len(parents):
            return MISSING
        if int(levels):
            value = parents[-int(levels)]
        pointer = relative[2] or ""
    if POINTER.fullmatch(pointer) is None:
        return MISSING
    values = [value]
    wildcard = False
    for token in pointer.split("/")[1:]:
        key = token.replace("~1", "/").replace("~0", "~")
        matches: list[object] = []
        for current in values:
            if key == "*" and isinstance(current, list):
                wildcard = True
                if len(matches) + len(current) > max_records:
                    raise ConfigurationError("JSON pointer exceeds the record limit")
                matches.extend(current)
            else:
                match = _pointer_member(current, key)
                if match is MISSING:
                    return MISSING
                matches.append(match)
        values = matches
    return values if wildcard else values[0]


def _pointer_member(value: object, key: str) -> object:
    if isinstance(value, dict):
        return value.get(key, MISSING)
    if isinstance(value, list) and key.isascii() and key.isdigit():
        if len(key) > 1 and key.startswith("0"):
            return MISSING
        if len(key) <= 9 and int(key) < len(value):
            return value[int(key)]
    return MISSING


def escape_token(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


def _finite_float(value: str) -> float:
    # float("1e999") is inf and float("1e-999") is 0.0; neither is the observed number.
    result = float(value)
    mantissa = value.lower().split("e")[0]
    if not math.isfinite(result) or (result == 0 and mantissa.strip("-0.")):
        raise ValueError("JSON number is outside the finite float range")
    return result


def _bounded_int(value: str) -> int:
    # Independent of the process-wide int_max_str_digits setting.
    if len(value.lstrip("-")) > MAX_INTEGER_DIGITS:
        raise ValueError("JSON integer has too many digits")
    return int(value)


def _reject_constant(value: str) -> object:
    raise ValueError("Non-finite JSON constants are not allowed")


def _clean(value: object) -> object:
    # Objects are cleaned by _repaired_keys while decoding; strings and lists remain.
    # Iterative and in place: nesting depth is bounded by callers, not by this walk.
    if isinstance(value, str):
        return clean_text(value)
    pending = [value] if isinstance(value, list) else []
    while pending:
        items = pending.pop()
        for index, item in enumerate(items):
            if isinstance(item, str):
                items[index] = clean_text(item)
            elif isinstance(item, list):
                pending.append(item)
    return value


def _repaired_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    return _unique_keys([(clean_text(key), _clean(value)) for key, value in pairs])


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys are ambiguous")
        result[key] = value
    return result
