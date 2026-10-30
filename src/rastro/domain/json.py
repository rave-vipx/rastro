"""Strict JSON decoding and RFC 6901 pointers shared by untrusted structured inputs."""

import json
import math
import re

MISSING = object()
MAX_INTEGER_DIGITS = 4300
POINTER = re.compile(r"(?:/(?:[^~/]|~[01])*)*")


def strict_json(value: str | bytes) -> object:
    return json.loads(
        value,
        parse_float=_finite_float,
        parse_int=_bounded_int,
        parse_constant=_reject_constant,
        object_pairs_hook=_unique_keys,
    )


def resolve_pointer(value: object, pointer: str) -> object:
    """Resolve an RFC 6901 pointer, returning MISSING instead of raising."""
    for token in pointer.split("/")[1:]:
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict):
            value = value.get(key, MISSING)
        elif isinstance(value, list) and key.isascii() and key.isdigit():
            if len(key) > 1 and key.startswith("0"):
                return MISSING
            # Avoid parsing an unbounded integer from an untrusted pointer.
            if len(key) > 9 or int(key) >= len(value):
                return MISSING
            value = value[int(key)]
        else:
            return MISSING
    return value


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


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys are ambiguous")
        result[key] = value
    return result
