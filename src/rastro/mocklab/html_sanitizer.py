"""Rewrite HTML so only structure and the recipe's extracted values survive (MOCK-005).

The rewriter keeps tags and attribute names so selectors replay unchanged. Text,
attribute values, comments, scripts and styles are replaced, dropped or scrubbed
unless the retention plan names them. It is stdlib-only and never executes markup.
"""

from __future__ import annotations

import json
from html import escape
from html.parser import HTMLParser

from rastro.domain.json import strict_json
from rastro.mocklab.detectors import contains_secret, scrub_url
from rastro.mocklab.retention import RetentionPlan, scrub_json
from rastro.mocklab.seeds import DeterministicStream

_JSONLD = "application/ld+json"
_STRUCTURAL = frozenset(
    {
        "charset",
        "checked",
        "class",
        "colspan",
        "dir",
        "disabled",
        "for",
        "hidden",
        "id",
        "itemprop",
        "itemscope",
        "itemtype",
        "lang",
        "method",
        "name",
        "rel",
        "role",
        "rowspan",
        "selected",
        "type",
    }
)
_URL_ATTRIBUTES = frozenset(
    {"action", "background", "cite", "formaction", "href", "poster", "src", "srcset"}
)
_DROPPED = frozenset({"integrity", "nonce", "srcdoc", "style"})
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

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._out.append(f"<{tag}{self._attributes(tag, attrs)} />")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"}:
            if self._raw == _JSONLD:
                self._jsonld_index += 1
            self._raw = None
        self._out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self._raw == _JSONLD:
            self._out.append(self._jsonld(data))
        elif self._raw is None:
            self._out.append(escape(self._text(data), quote=False))

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
        scrubbed = scrub_json(payload, pointers, self._stream, keep_keys=_LINKED_DATA_KEYS)
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
                rendered.append(f' {name}="{escape(self._attribute(name, value))}"')
        return "".join(rendered)

    def _quoted_in_selectors(self, value: str) -> bool:
        """True when a recipe selector compares an attribute with exactly this value."""
        text = self._plan.selector_text
        return bool(value) and any(
            form in text for form in (f'"{value}"', f"'{value}'", f"={value}]")
        )

    def _attribute(self, name: str, value: str) -> str:
        if contains_secret(value):
            return scrub_url(value, self._stream) if name in _URL_ATTRIBUTES else ""
        if value.strip() in self._plan.texts:
            return value
        if name in _STRUCTURAL or self._quoted_in_selectors(value):
            return value
        if name in _URL_ATTRIBUTES:
            return scrub_url(value, self._stream)
        return self._stream.placeholder(value)


_LINKED_DATA_KEYS = frozenset({"@context", "@type"})


def sanitize_html(body: str, plan: RetentionPlan, stream: DeterministicStream) -> str:
    """Return sanitized HTML; comments, processing instructions and scripts are removed."""
    rewriter = _Rewriter(plan, stream)
    rewriter.feed(body)
    rewriter.close()
    return rewriter.result()
