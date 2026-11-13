"""Offline adaptive relocation at the parser boundary (SCR-002, REPAIR-001).

Container and field fingerprints from the first success propose a template; CI must validate
all records, history and semantics. No fetcher, adaptive storage or activation is involved.
"""

from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from cssselect import SelectorError
from lxml.etree import LxmlError  # type: ignore[import-untyped]
from pydantic import BaseModel, ValidationError
from scrapling.core.utils._utils import _StorageTools
from scrapling.parser import Selector

from rastro.adapters.scrapling_container import (
    container_evidence,
    container_selector,
    field_supported,
    identifier,
    relocate_matches,
    template_record,
)
from rastro.domain.models import (
    ConfigurationError,
    CssRecipe,
    ExtractionResult,
    Limits,
    Outcome,
    ResourceLimitError,
    Source,
)
from rastro.domain.recipes import DOCUMENT_PREFIX, EXPAND_PREFIX
from rastro.domain.repair import (
    ElementFingerprint,
    RelocationSnapshot,
    RepairCandidate,
    RepairEvidence,
    digest,
    recipe_digest,
)

_SUFFIX = re.compile(r"(.*?)(::(?:text|attr\([^)]+\)))$", re.DOTALL)


class ScraplingRelocation:
    def capture[T: BaseModel](
        self,
        html: str,
        *,
        source: Source,
        recipe: CssRecipe,
        result: ExtractionResult[T],
        limits: Limits,
    ) -> RelocationSnapshot:
        """Capture the exact HTML/selected recipe of a complete successful extraction.

        Callers must supply the matching result, not an earlier run's success.
        Failures raise ConfigurationError; excessive input raises ResourceLimitError.
        """
        if result.status != Outcome.SUCCESS or not result.records:
            raise ConfigurationError("Relocation fingerprints require a successful run")
        if result.source_url != source.evidence_url:
            raise ConfigurationError("Successful result belongs to another source")
        page, records = _records(html, recipe, limits)
        if len(records) != len(result.records):
            raise ConfigurationError("Successful result does not match the snapshot records")
        nodes: dict[str, Selector] = {}
        for field, expression in recipe.fields.items():
            selector, _ = _expression(expression)
            if field not in result.records[0].evidence:
                continue
            matches = _css(records[0], selector)
            if len(matches) != 1:
                raise ConfigurationError("Snapshot fields require unique successful evidence")
            nodes[field] = matches[0]
        if not nodes:
            raise ConfigurationError("Relocation requires observed fields in the first record")
        _mask(page)
        snapshot = RelocationSnapshot(
            recipe_digest=recipe_digest(recipe),
            source_digest=digest(source.model_dump_json()),
            schema_fingerprint=result.schema_fingerprint,
            observed_at=result.completed_at,
            fields={name: _fingerprint(node) for name, node in nodes.items()},
            record=_fingerprint(records[0]),
        )
        if len(snapshot.model_dump_json().encode()) > limits.max_html_bytes:
            raise ResourceLimitError("Relocation snapshot exceeds max_html_bytes")
        return snapshot

    def relocate(
        self, html: str, recipe: CssRecipe, snapshot: RelocationSnapshot, limits: Limits
    ) -> tuple[RepairCandidate, ...]:
        """Propose one mapping, retaining tied matches as ambiguous evidence for CI."""
        try:
            snapshot = RelocationSnapshot.model_validate(snapshot.model_dump())
        except ValidationError:
            raise ConfigurationError("Invalid relocation snapshot") from None
        if snapshot.recipe_digest != recipe_digest(recipe) or not set(snapshot.fields).issubset(
            recipe.fields
        ):
            raise ConfigurationError("Relocation snapshot does not match the recipe")
        page, records = _records(html, recipe, limits)
        record_selector = recipe.record_selector
        relocated: list[Selector] = []
        if not records and snapshot.record is not None and page._root is not None:
            masked = Selector(root=deepcopy(page._root), adaptive=False)
            _mask(masked)
            new, relocated = container_selector(page, masked, snapshot.record)
            if new is not None:
                records = _css(page, new)
                if len(records) > limits.max_records:
                    raise ResourceLimitError("Record count exceeds max_records")
                record_selector = f"{record_selector}, {new}"
        if not records:
            return ()
        # One template record: the most common shape, so a row missing an optional child
        record = template_record(records)  # does not become the template.
        selected = {
            field: _css(record, _expression(expr)[0]) for field, expr in recipe.fields.items()
        }
        names = {
            node: (node.tag, tuple(sorted(set(node.get("class", "").split()))))
            for node in page._root.iter()
        }
        _mask(page)
        evidence = container_evidence(page, relocated, records, snapshot.fields, selected)
        fields = dict(recipe.fields)
        for field, fingerprint in snapshot.fields.items():
            if _expression(recipe.fields[field])[0] == ":scope":
                evidence.append(RepairEvidence(field=field, match_count=1))
                continue
            # An element another field already reads is never a relocation target.
            taken = {n._root for name, nodes in selected.items() if name != field for n in nodes}
            matches = [n for n in relocate_matches(record, fingerprint) if n._root not in taken]
            if relocated and not selected[field] and not field_supported(matches, fingerprint):
                return ()
            evidence.append(RepairEvidence(field=field, match_count=len(matches)))
            paths = [_relative(record, node, names) for node in matches]
            if matches and [node._root for node in matches] != [
                node._root for node in selected[field]
            ]:
                _, suffix = _expression(fields[field])
                fields[field] = ", ".join(
                    [fields[field], *(path + suffix for path in paths if path is not None)]
                )
        if fields == recipe.fields and record_selector == recipe.record_selector:
            return ()
        try:
            candidate = CssRecipe(
                record_selector=record_selector,
                fields=fields,
                version=recipe.version,
                require_evidence=True,
            )
        except ValidationError:
            raise ConfigurationError("Relocation mapping exceeds recipe limits") from None
        return (
            RepairCandidate(
                recipe=candidate,
                origin="scrapling_relocation",
                rationale="Adaptive matches from the last successful snapshot; requires recipe CI",
                evidence=tuple(evidence),
            ),
        )


def _expression(expression: str) -> tuple[str, str]:
    if expression.startswith(EXPAND_PREFIX):
        raise ConfigurationError("Relocation does not support expand fields")
    if expression.startswith(DOCUMENT_PREFIX):
        raise ConfigurationError("Relocation does not support document-scoped fields")
    match = _SUFFIX.fullmatch(expression)
    if match is None:
        raise ConfigurationError("Relocation requires scalar field selectors")
    selector = re.sub(re.escape(match[2]) + r"\s*,\s*", ", ", match[1])
    if "::" in selector:
        raise ConfigurationError("Relocation requires the same scalar suffix per field")
    return selector or ":scope", match[2]


def _css(page: Selector, expression: str) -> list[Selector]:
    try:
        return list(page.css(expression))
    except SelectorError:
        raise ConfigurationError("Recipe contains an invalid CSS selector") from None


def _records(html: str, recipe: CssRecipe, limits: Limits) -> tuple[Selector, list[Selector]]:
    try:
        size = len(html.encode())
    except UnicodeError:
        raise ConfigurationError("Relocation input must be valid UTF-8") from None
    if size > limits.max_html_bytes:
        raise ResourceLimitError("HTML input exceeds max_html_bytes")
    try:
        page = Selector(html or "<html></html>", adaptive=False, huge_tree=False)
    except (ValueError, LxmlError):
        raise ConfigurationError("Invalid relocation HTML") from None
    if page._root is None:
        return page, []
    for comment in page._root.xpath("//comment() | //processing-instruction()"):
        parent = comment.getparent()
        if parent is not None:
            parent.remove(comment)
    nodes = list(page._root.iter())
    if len(nodes) > 4096:
        raise ResourceLimitError("Relocation exceeds 4096 nodes")
    for node in nodes:
        if len(list(node.iterancestors())) >= 128 or len(node.attrib) > 100:
            raise ResourceLimitError("Relocation exceeds structural budgets")
    records = _css(page, recipe.record_selector)
    if len(records) > limits.max_records:
        raise ResourceLimitError("Record count exceeds max_records")
    return page, records


def _mask(page: Selector) -> None:
    # Hash both sides identically, so Scrapling debug logs cannot expose source content.
    for node in page._root.iter():
        node.tag = "h" + digest(node.tag)
        classes = sorted(set(node.get("class", "").split()))
        if len(classes) > 100:
            raise ResourceLimitError("Relocation exceeds 100 class tokens")
        attributes = {
            key if key in {"class", "id", "href", "src"} else "h" + digest(key): (
                " ".join("h" + digest(c) for c in classes) if key == "class" else "h" + digest(v)
            )
            for key, v in sorted(node.attrib.items())
        }
        node.attrib.clear()
        node.attrib.update(attributes)
        node.text = "h" + digest(node.text.strip()) if node.text and node.text.strip() else None
        node.tail = None


def _fingerprint(node: Selector) -> ElementFingerprint:
    # Scrapling 0.5 has no public serialization helper; keep this dependency in the adapter.
    data = _StorageTools.element_to_dict(node._root)
    if "class" in data["attributes"]:
        data["attributes"]["class"] = tuple(data["attributes"]["class"].split())
    # Parent and sibling similarity would penalize the very node moves we repair.
    return ElementFingerprint.model_validate(
        {key: data[key] for key in ElementFingerprint.model_fields}
    )


def _relative(
    record: Selector, node: Selector, names: dict[Any, tuple[str, tuple[str, ...]]]
) -> str | None:
    """A tag+class child path from the record; None when a step is not unique.

    Positional steps would read a sibling in a record that omits an optional child.
    """
    parts: list[str] = []
    current = node._root
    while current is not record._root:
        parent = current.getparent()
        if parent is None:
            raise ConfigurationError("Relocation escaped the record")
        siblings = [child for child in parent if isinstance(child.tag, str)]
        if sum(names[child] == names[current] for child in siblings) != 1:
            return None
        tag, classes = names[current]
        parts.append(identifier(tag) + "".join("." + identifier(c) for c in classes))
        current = parent
    return " > ".join([":scope", *reversed(parts)])
