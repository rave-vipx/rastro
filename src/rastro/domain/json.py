"""Strict JSON decoding shared by untrusted structured inputs."""

import json


def strict_json(value: str | bytes) -> object:
    return json.loads(value, parse_constant=_reject_constant, object_pairs_hook=_unique_keys)


def _reject_constant(value: str) -> object:
    raise ValueError("Non-finite JSON constants are not allowed")


def _unique_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON keys are ambiguous")
        result[key] = value
    return result
