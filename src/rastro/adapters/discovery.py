"""Conservative JSON-LD and microdata discovery through Scrapling (INT-002)."""

from __future__ import annotations

import re

from pydantic import ValidationError
from scrapling.parser import Selector

from rastro.adapters.scrapling import _objects, _parse_html
from rastro.domain.json import strict_json
from rastro.domain.models import CssRecipe, JsonLdRecipe, Limits, Recipe, ResourceLimitError
from rastro.ports.discovery import AliasMap

_SAFE_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,255}")


class ScraplingDiscovery:
    """Match exact field names and declared aliases; never guess synonyms or fetch URLs."""

    def discover(
        self,
        html: str,
        fields: tuple[str, ...],
        limits: Limits,
        *,
        max_candidates: int,
        aliases: AliasMap | None = None,
    ) -> tuple[Recipe, ...]:
        if len(html.encode()) > limits.max_html_bytes:
            raise ResourceLimitError("HTML input exceeds max_html_bytes")
        if not html.strip():
            return ()
        page = _parse_html(html)
        names = _accepted_names(fields, aliases)
        candidates: list[Recipe] = []

        def add(recipe: Recipe) -> None:
            if recipe not in candidates:
                candidates.append(recipe)

        blocks = page.css('script[type="application/ld+json"]::text').getall()
        if len(blocks) > limits.max_jsonld_blocks:
            raise ResourceLimitError("JSON-LD block count exceeds max_jsonld_blocks")
        examined = 0
        for block in blocks:
            try:
                payload = strict_json(str(block))
            except (ValueError, RecursionError):
                continue
            for item, _ in _objects(payload):
                examined += 1
                if examined > limits.max_records:
                    raise ResourceLimitError("JSON-LD object count exceeds max_records")
                mapping = _unique_paths(item, names)
                if not mapping:
                    continue
                item_type = item.get("@type")
                try:
                    add(
                        JsonLdRecipe(
                            fields={**mapping},
                            item_type=item_type if isinstance(item_type, str) else None,
                        )
                    )
                except ValidationError:
                    continue
                if len(candidates) == max_candidates:
                    return tuple(candidates)

        nodes = page.css("[itemscope]:not([itemprop])")
        if len(nodes) > limits.max_records:
            raise ResourceLimitError("Microdata record count exceeds max_records")
        for node in nodes:
            mapping = _microdata_mapping(node, names)
            if mapping:
                add(CssRecipe(record_selector="[itemscope]:not([itemprop])", fields=mapping))
            if len(candidates) == max_candidates:
                break
        return tuple(candidates)


def _accepted_names(
    fields: tuple[str, ...], aliases: AliasMap | None
) -> dict[str, tuple[str, ...]]:
    """Each field always accepts its own name, followed by any extra exact aliases."""
    if aliases is None:
        return {field: (field,) for field in fields}
    return {field: tuple(dict.fromkeys((field, *aliases.get(field, ())))) for field in fields}


def _microdata_mapping(node: Selector, names: dict[str, tuple[str, ...]]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for field, accepted in names.items():
        matches = [
            (name, match)
            for name in accepted
            if _SAFE_NAME.fullmatch(name)
            for match in node.css(f'[itemprop~="{name}"]')
        ]
        if len(matches) != 1:
            continue
        name, match = matches[0]
        suffix = "::text"
        for attr in ("content", "href", "src", "datetime"):
            if attr in match.attrib:
                suffix = f"::attr({attr})"
                break
        mapping[field] = f'[itemprop~="{name}"]' + suffix
    return mapping


def _unique_paths(item: dict[str, object], names: dict[str, tuple[str, ...]]) -> dict[str, str]:
    owners: dict[str, tuple[str, ...]] = {}
    for field, accepted in names.items():
        for name in accepted:
            owners[name] = (*owners.get(name, ()), field)
    paths: dict[str, list[str]] = {field: [] for field in names}
    pending = [(item, "", 0)]
    examined = 0
    while pending:
        current, prefix, depth = pending.pop()
        for key, value in current.items():
            examined += 1
            if examined > 4096 or depth > 8:
                raise ResourceLimitError("JSON-LD discovery exceeds traversal budget")
            pointer = prefix + "/" + key.replace("~", "~0").replace("/", "~1")
            for field in owners.get(key, ()):
                paths[field].append(pointer)
            if isinstance(value, dict):
                pending.append((value, pointer, depth + 1))
    # More than one matching node is ambiguous; the field is left unmapped.
    return {field: found[0] for field, found in paths.items() if len(found) == 1}
