"""Container scope and exact class-token evidence (REPAIR-001/002, SCR-002)."""

import re
from typing import Any

from scrapling.parser import Selector

from rastro.domain.repair import ElementFingerprint, RepairEvidence, digest

_ROOTS = {"html", "head", "body", *(f"h{digest(t)}" for t in ("html", "head", "body"))}


def relocate_matches(record: Selector, fingerprint: ElementFingerprint) -> list[Selector]:
    """Keep Scrapling's structural ranking, resolving class similarity as sets."""
    data = fingerprint.model_dump()
    saved = data["attributes"].pop("class", ())
    wanted = {saved} if isinstance(saved, str) else set(saved)
    classes = {
        node: node.attrib.pop("class") for node in record._root.iter() if "class" in node.attrib
    }
    texts = {
        node: node.text
        for node in record._root.iter()
        if node.text is not None and node.text != data["text"]
    }
    try:
        for node in texts:
            node.text = None
        matches = list(record.relocate(data, 40, selector_type=True))
    finally:
        for node, value in classes.items():
            node.set("class", value)
        for node, value in texts.items():
            node.text = value
    if not matches or not wanted:
        return matches
    tokens = [set(classes.get(node._root, "").split()) for node in matches]
    scores = [len(wanted & current) / len(wanted | current) for current in tokens]
    return [node for node, score in zip(matches, scores, strict=True) if score == max(scores)]


def container_selector(
    page: Selector, masked: Selector, fingerprint: ElementFingerprint
) -> tuple[str | None, list[Selector]]:
    matches = relocate_matches(masked, fingerprint)
    originals = dict(zip(masked._root.iter(), page._root.iter(), strict=True))
    nodes = [originals[node._root] for node in matches if node._root.tag == fingerprint.tag]
    if not nodes:
        return None, []
    # Preserve every ancestor step; descendant selectors can capture nested advertisements.
    classes = set.intersection(*(set(node.get("class", "").split()) for node in nodes))
    selector = " > ".join(
        [
            *_path(nodes[0]),
            _identifier(nodes[0].tag) + "".join("." + _identifier(c) for c in sorted(classes)),
        ]
    )
    return selector, [Selector(root=node, adaptive=False) for node in nodes]


def container_evidence(
    page: Selector,
    relocated: list[Selector],
    selected: list[Selector],
    fields: dict[str, ElementFingerprint],
    selected_fields: dict[str, list[Selector]],
) -> list[RepairEvidence]:
    if not relocated:
        return []
    retained_text = any(
        node._root.text == fingerprint.text and fingerprint.text is not None
        for field, fingerprint in fields.items()
        for node in selected_fields[field]
    )
    return [
        RepairEvidence(
            field="record_selector",
            match_count=max(len(relocated), len(selected)),
            ambiguous=not homogeneous(page, relocated, selected)
            or (len(selected) == 1 and not retained_text),
        )
    ]


def homogeneous(page: Selector, relocated: list[Selector], selected: list[Selector]) -> bool:
    """Check selected rows and competing same-class containers elsewhere on the page."""
    first = relocated[0]._root
    classes = set(first.get("class", "").split())
    competitors = [
        node
        for node in page._root.iter(first.tag)
        if not classes or classes.intersection(node.get("class", "").split())
    ]
    shape = (_path(first), tuple(child.tag for child in first), classes)
    return all(
        (_path(node), tuple(child.tag for child in node), set(node.get("class", "").split()))
        == shape
        for node in [*(n._root for n in relocated), *(n._root for n in selected), *competitors]
    )


def field_supported(matches: list[Selector], fingerprint: ElementFingerprint) -> bool:
    """Tag/path similarity alone is insufficient evidence for a missing field."""
    saved = fingerprint.attributes
    classes = saved.get("class", ())
    tokens = {classes} if isinstance(classes, str) else set(classes)
    return any(
        node._root.tag == fingerprint.tag
        and (
            bool(fingerprint.text and fingerprint.text == node._root.text)
            or bool(tokens.intersection(node._root.get("class", "").split()))
            or any(key != "class" and node._root.get(key) == value for key, value in saved.items())
        )
        for node in matches
    )


def _path(node: Any) -> tuple[str, ...]:
    parts = tuple(
        _identifier(parent.tag)
        + "".join("." + _identifier(c) for c in sorted(set(parent.get("class", "").split())))
        for parent in reversed(list(node.iterancestors()))
        if parent.tag not in _ROOTS
    )
    return parts or ("body",)


def _identifier(value: str) -> str:
    escaped = re.sub(r"[^a-zA-Z0-9_-]", lambda match: f"\\{ord(match[0]):x} ", value)
    if escaped[0].isdigit() or escaped.startswith("-"):
        escaped = f"\\{ord(escaped[0]):x} " + escaped[1:]
    return escaped
