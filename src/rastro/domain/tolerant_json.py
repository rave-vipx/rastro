"""Tolerant JSON for page data. Config and job files never use it; they stay strict."""

from json import JSONDecodeError

from rastro.domain.json import strict_json

BOM = "﻿"


def tolerant_json(text: str | bytes) -> tuple[object, bool]:
    """Decode page JSON, returning ``(payload, repaired)``.

    Strict decoding is tried first. If it fails on syntax, a leading BOM is removed, raw
    control characters inside strings are escaped and trailing commas before ``}`` or ``]``
    are dropped. Duplicate keys, NaN and broken structure are still refused (ValueError).
    """
    if isinstance(text, bytes):
        text = text.decode("utf-8-sig")
    try:
        return strict_json(text, repair_text=True), False
    except JSONDecodeError:
        pass
    fixed = repaired_text(text)
    return strict_json(fixed, repair_text=True), True


def repaired_text(text: str) -> str:
    """Return ``text`` with the repairs ``tolerant_json`` applies (unchanged if none apply)."""
    out: list[str] = []
    in_string = False
    after_backslash = False
    comma = -1  # index in ``out`` of a comma followed only by whitespace so far
    for char in text.removeprefix(BOM):
        if in_string:
            if after_backslash:
                after_backslash = False
            elif char == "\\":
                after_backslash = True
            elif char == '"':
                in_string = False
            elif ord(char) < 32:
                char = f"\\u{ord(char):04x}"
        elif char == '"':
            in_string = True
            comma = -1
        elif char == ",":
            comma = len(out)
        elif char in "}]":
            if comma >= 0:
                out[comma] = ""
            comma = -1
        elif char not in " \t\r\n":
            comma = -1
        out.append(char)
    return "".join(out)
