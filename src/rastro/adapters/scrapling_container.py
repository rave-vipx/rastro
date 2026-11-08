"""Generic container selectors for masked Scrapling matches (REPAIR-001, SCR-002)."""

import re

from scrapling.parser import Selector

from rastro.domain.repair import ElementFingerprint


def container_selector(
    page: Selector, masked: Selector, fingerprint: ElementFingerprint
) -> tuple[str | None, int]:
    matches = masked.relocate(fingerprint.model_dump(), 40, selector_type=True)
    originals = dict(zip(masked._root.iter(), page._root.iter(), strict=True))
    # Keep the saved tag; a similarly scored banner must never become a record.
    nodes = [originals[node._root] for node in matches if node._root.tag == fingerprint.tag]
    if not nodes:
        return None, len(matches)
    tag = _identifier(nodes[0].tag)
    classes = set.intersection(*(set(node.get("class", "").split()) for node in nodes))
    if classes:
        return tag + "".join("." + _identifier(name) for name in sorted(classes)), len(matches)
    paths = []
    for node in nodes:
        parts = []
        for parent in reversed(list(node.iterancestors())):
            siblings = list(parent.itersiblings(preceding=True, tag=parent.tag))
            parts.append(f"{_identifier(parent.tag)}:nth-of-type({len(siblings) + 1})")
        paths.append(parts)
    shared = []
    for ancestors in zip(*paths, strict=False):
        if len(set(ancestors)) != 1:
            break
        shared.append(ancestors[0])
    return (" > ".join(shared) + " " + tag).strip(), len(matches)


def _identifier(value: str) -> str:
    escaped = re.sub(r"[^a-zA-Z0-9_-]", lambda match: f"\\{ord(match[0]):x} ", value)
    if escaped[0].isdigit() or escaped.startswith("-"):
        escaped = f"\\{ord(escaped[0]):x} " + escaped[1:]
    return escaped
