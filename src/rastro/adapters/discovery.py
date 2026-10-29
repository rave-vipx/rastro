"""Conservative JSON-LD and microdata discovery through Scrapling (INT-002)."""

from __future__ import annotations

import re

from pydantic import ValidationError
from scrapling.parser import Selector

from rastro.adapters.scrapling import _objects
from rastro.domain.json import strict_json
from rastro.domain.models import Limits, Recipe, ResourceLimitError


class ScraplingDiscovery:
    """Match exact field names; never guess synonyms, generate combinations, or fetch URLs."""

    def discover(
        self, html: str, fields: tuple[str, ...], limits: Limits, *, max_candidates: int
    ) -> tuple[Recipe, ...]:
        if len(html.encode()) > limits.max_html_bytes:
            raise ResourceLimitError("HTML input exceeds max_html_bytes")
        if not html.strip():
            return ()
        page = Selector(html, adaptive=False, huge_tree=False)
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
                mapping = _unique_paths(item, fields)
                if not mapping:
                    continue
                item_type = item.get("@type")
                try:
                    add(
                        Recipe(
                            strategy="jsonld",
                            fields=mapping,
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
            mapping = {}
            for name in fields:
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,255}", name):
                    continue
                selector = f'[itemprop~="{name}"]'
                matches = node.css(selector)
                if len(matches) != 1:
                    continue
                suffix = "::text"
                for attr in ("content", "href", "src", "datetime"):
                    if attr in matches[0].attrib:
                        suffix = f"::attr({attr})"
                        break
                mapping[name] = selector + suffix
            if mapping:
                add(
                    Recipe(
                        strategy="css",
                        record_selector="[itemscope]:not([itemprop])",
                        fields=mapping,
                    )
                )
            if len(candidates) == max_candidates:
                break
        return tuple(candidates)


def _unique_paths(item: dict[str, object], fields: tuple[str, ...]) -> dict[str, str]:
    paths: dict[str, list[str]] = {name: [] for name in fields}
    pending = [(item, "", 0)]
    examined = 0
    while pending:
        current, prefix, depth = pending.pop()
        for key, value in current.items():
            examined += 1
            if examined > 4096 or depth > 8:
                raise ResourceLimitError("JSON-LD discovery exceeds traversal budget")
            pointer = prefix + "/" + key.replace("~", "~0").replace("/", "~1")
            if key in paths:
                paths[key].append(pointer)
            if isinstance(value, dict):
                pending.append((value, pointer, depth + 1))
    return {name: found[0] for name, found in paths.items() if len(found) == 1}
