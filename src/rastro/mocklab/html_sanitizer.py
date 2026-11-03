"""Rewrite HTML so only structure and the recipe's extracted values survive (MOCK-005).

The rewriter keeps tags and attribute names so selectors replay unchanged. Text,
comments, scripts and styles are replaced or dropped unless the retention plan
names them. Attribute values survive token by token only when a recipe selector
names the token (``.product``, ``[data-kind=card]``) or the value was extracted;
every other token becomes a placeholder. It is stdlib-only and never executes markup.
"""

from __future__ import annotations

import json
from html import escape
from html.parser import HTMLParser

from rastro.domain.json import strict_json
from rastro.mocklab.detectors import scrub_url
from rastro.mocklab.json_scrubber import scrub_json
from rastro.mocklab.retention import RetentionPlan
from rastro.mocklab.seeds import DeterministicStream

_JSONLD = "application/ld+json"
_URL_ATTRIBUTES = frozenset(
    {"action", "background", "cite", "formaction", "href", "itemid", "itemtype", "poster", "src"}
)
_DROPPED = frozenset({"integrity", "nonce", "srcdoc", "srcset", "style"})
_SECRET_INPUTS = frozenset({"hidden", "password"})
# A JSON-LD block that failed to parse must still fail to parse, at the same index.
_INVALID_JSONLD = "{"


class _Rewriter(HTMLParser):
    def __init__(self, plan: RetentionPlan, stream: DeterministicStream) -> None:
        super().__init__(convert_charrefs=True)
        self._plan = plan
        self._stream = stream
        self._out: list[str] = []
        self._raw: str | None = None
        self._script: list[str] = []
        self._jsonld_index = 0

    def result(self) -> str:
        return "".join(self._out)

    def handle_decl(self, decl: str) -> None:
        if decl.lower().startswith("doctype"):
            self._out.append("<!doctype html>")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._out.append(f"<{tag}{self._attributes(tag, attrs)}>")
        if tag in {"script", "style"}:
            kind = dict(attrs).get("type") if tag == "script" else None
            self._raw = kind if kind == _JSONLD else "drop"
            self._script = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._out.append(f"<{tag}{self._attributes(tag, attrs)} />")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            self._close_raw()
        self._out.append(f"</{tag}>")

    def close(self) -> None:
        super().close()
        self._close_raw()

    def handle_data(self, data: str) -> None:
        if self._raw == _JSONLD:
            self._script.append(data)
        elif self._raw is None:
            self._out.append(escape(self._text(data), quote=False))

    def _close_raw(self) -> None:
        # The parser enumerates only JSON-LD blocks with text; empty ones take no index.
        if self._raw == _JSONLD and self._script:
            self._out.append(self._jsonld("".join(self._script)))
            self._jsonld_index += 1
        self._raw = None
        self._script = []

    def _text(self, data: str) -> str:
        stripped = data.strip()
        if not stripped or stripped in self._plan.texts:
            return data
        return self._stream.placeholder(data)

    def _jsonld(self, data: str) -> str:
        retained = self._plan.jsonld_pointers
        if retained is None:
            return ""
        try:
            payload = strict_json(data)
        except (ValueError, RecursionError):
            return _INVALID_JSONLD
        pointers = retained.get(self._jsonld_index, frozenset())
        scrubbed = scrub_json(
            payload, pointers, self._stream, keep_types=self._plan.tokens, linked_data=True
        )
        # Escaping "</" keeps the block from closing its script element early.
        return json.dumps(scrubbed, ensure_ascii=False).replace("</", "<\\/")

    def _attributes(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        secret_input = tag == "input" and (dict(attrs).get("type") or "").lower() in _SECRET_INPUTS
        rendered: list[str] = []
        for name, value in attrs:
            if name.startswith("on") or name in _DROPPED or (secret_input and name == "value"):
                continue
            if value is None:
                rendered.append(f" {name}")
            else:
                kept = self._attribute(tag, name, value)
                rendered.append(f' {name}="{escape(kept)}"')
        return "".join(rendered)

    def _attribute(self, tag: str, name: str, value: str) -> str:
        # Extracted values were already checked by the retention plan.
        if value.strip() in self._plan.texts:
            return value
        if tag == "script" and name == "type" and value == _JSONLD:
            return value
        if name in _URL_ATTRIBUTES:
            return scrub_url(value, self._stream, self._plan.tokens)
        return "".join(
            part if part.isspace() or part in self._plan.tokens else self._stream.placeholder(part)
            for part in _split_tokens(value)
        )


def _split_tokens(value: str) -> list[str]:
    """Split on whitespace, keeping the whitespace runs so layout is preserved."""
    parts: list[str] = []
    current = ""
    for char in value:
        if current and char.isspace() != current[-1].isspace():
            parts.append(current)
            current = ""
        current += char
    if current:
        parts.append(current)
    return parts


def sanitize_html(body: str, plan: RetentionPlan, stream: DeterministicStream) -> str:
    """Return sanitized HTML; comments, processing instructions and scripts are removed."""
    rewriter = _Rewriter(plan, stream)
    rewriter.feed(body)
    rewriter.close()
    return rewriter.result()
