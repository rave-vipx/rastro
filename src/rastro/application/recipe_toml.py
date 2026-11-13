"""Replace one recipe's tables in a TOML job file and keep every other line as it was.

The recipe is located by its table headers (``[sources.recipe]``, ``[recipe]`` and
their subtables) and rendered again in canonical form. Comments inside the replaced
tables are lost; everything outside them is kept byte for byte. Callers must reparse
and verify the result: this module only edits text.
"""

from __future__ import annotations

import re
from typing import Any

from rastro.domain.models import ConfigurationError, Recipe

_HEADER = re.compile(r"^[ \t]*(\[\[?)([^\[\]#]+)(\]\]?)[ \t]*(?:#.*)?$")
_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")


def recipe_table(recipe: Recipe) -> dict[str, Any]:
    """The recipe as job-file data: defaults left out, ``strategy`` and ``version`` kept."""
    data: dict[str, Any] = recipe.model_dump(mode="json", exclude_defaults=True)
    data = {"version": recipe.version, "strategy": recipe.strategy, **data}
    if recipe.fallbacks:
        data["fallbacks"] = [
            {"strategy": item.strategy, **item.model_dump(mode="json", exclude_defaults=True)}
            for item in recipe.fallbacks
        ]
    return data


def replace_recipe(text: str, recipe: Recipe, *, source: int | None) -> str:
    """Return ``text`` with the recipe of ``sources[source]`` (or the job recipe) replaced."""
    lines = text.splitlines(keepends=True)
    prefix = ("recipe",) if source is None else ("sources", "recipe")
    start, end = _region(lines, prefix, source)
    rendered = _render(".".join(prefix), recipe_table(recipe), array=False)
    return "".join([*lines[:start], *rendered, *lines[end:]])


def add_source_recipe(text: str, recipe: Recipe, *, source: int) -> str:
    """Return ``text`` with a new ``[sources.recipe]`` block at the end of that source."""
    lines = text.splitlines(keepends=True)
    end = _source_end(lines, source)
    rendered = _render("sources.recipe", recipe_table(recipe), array=False)
    before = lines[:end]
    if before and not before[-1].endswith("\n"):
        before[-1] += "\n"
    return "".join([*before, "\n", *rendered, *lines[end:]])


def _source_end(lines: list[str], source: int) -> int:
    """End of the ``[[sources]]`` block ``source`` and its subtables; comments stay after."""
    sources_seen, start, end, in_string = -1, None, len(lines), False
    for number, line in enumerate(lines):
        if (line.count('"""') + line.count("'''")) % 2:
            in_string = not in_string
        match = None if in_string else _HEADER.match(line.rstrip("\r\n"))
        if match is None:
            continue
        path = tuple(part.strip() for part in match.group(2).split("."))
        is_source = match.group(1) == "[[" and path == ("sources",)
        if is_source:
            sources_seen += 1
        if start is None and is_source and sources_seen == source:
            start = number
        elif start is not None and (is_source or path[0] != "sources" or len(path) == 1):
            end = number
            break
    if start is None:
        raise ConfigurationError(
            "The source is not written as a [[sources]] table; update the recipe by hand"
        )
    while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip()[:1] == "#"):
        end -= 1
    return end


def _region(lines: list[str], prefix: tuple[str, ...], source: int | None) -> tuple[int, int]:
    """Line span of the one contiguous run of recipe tables; trailing comments stay outside."""
    spans: list[list[int]] = []
    sources_seen, inside, in_string = -1, False, False
    for number, line in enumerate(lines):
        if (line.count('"""') + line.count("'''")) % 2:
            in_string = not in_string
        match = None if in_string else _HEADER.match(line.rstrip("\r\n"))
        if match is None:
            continue
        path = tuple(part.strip() for part in match.group(2).split("."))
        if match.group(1) == "[[" and path == ("sources",):
            sources_seen += 1
        wanted = path[: len(prefix)] == prefix and (source is None or sources_seen == source)
        if wanted and not inside:
            spans.append([number, len(lines)])
        elif inside and not wanted:
            spans[-1][1] = number
        inside = wanted
    if len(spans) != 1:
        raise ConfigurationError(
            "The recipe is not written as one block of TOML tables; update it by hand"
        )
    start, end = spans[0]
    while end > start + 1 and (not lines[end - 1].strip() or lines[end - 1].lstrip()[:1] == "#"):
        end -= 1
    return start, end


def _render(name: str, table: dict[str, Any], *, array: bool) -> list[str]:
    out = [f"[[{name}]]\n" if array else f"[{name}]\n"]
    nested: list[str] = []
    for key, value in table.items():
        child = f"{name}.{_key(key)}"
        if isinstance(value, dict):
            nested += ["\n", *_render(child, value, array=False)]
        elif isinstance(value, list) and value and all(isinstance(v, dict) for v in value):
            for item in value:
                nested += ["\n", *_render(child, item, array=True)]
        else:
            out.append(f"{_key(key)} = {_value(value)}\n")
    return out + nested


def _key(key: str) -> str:
    return key if _BARE_KEY.fullmatch(key) else _string(key)


def _value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _string(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return "[" + ", ".join(_string(item) for item in value) + "]"
    raise ConfigurationError("The recipe has a value this TOML writer does not support")


def _string(value: str) -> str:
    """A TOML basic string; control characters and DEL are escaped as TOML requires."""
    escaped = []
    for char in value:
        if char in '"\\':
            escaped.append("\\" + char)
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            escaped.append(f"\\u{ord(char):04x}")
        else:
            escaped.append(char)
    return '"' + "".join(escaped) + '"'
