"""Bounded, DTD-free sitemap parsing (N16; SEC-001, SEC-008).

A sitemap is untrusted XML. Any DOCTYPE is refused before an entity can be declared, so
there is no entity expansion and no external resource; expat never touches the network.
Gzip is inflated under the same byte cap. Element names match without their namespace,
and ``<loc>`` values come back in document order.
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass
from typing import NoReturn
from xml.parsers import expat

MAX_SITEMAP_URLS = 50_000  # the sitemap protocol's own per-file limit
_MAX_LOC = 8192
_ROOTS = {"urlset": "url", "sitemapindex": "sitemap"}


class SitemapError(Exception):
    """A safe issue code: ``sitemap_invalid`` or ``sitemap_too_large``."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class Sitemap:
    index: bool  # a <sitemapindex>: its locs are child sitemaps
    locs: tuple[str, ...]


def inflate(data: bytes, max_bytes: int) -> bytes:
    """Return ``data``, gunzipped when it carries the gzip magic, never past ``max_bytes``."""
    if not data.startswith(b"\x1f\x8b"):
        return data
    inflater = zlib.decompressobj(wbits=31)
    try:
        out = inflater.decompress(data, max_bytes + 1)
    except zlib.error:
        raise SitemapError("sitemap_invalid") from None
    if len(out) > max_bytes:
        raise SitemapError("sitemap_too_large")
    if not inflater.eof:
        raise SitemapError("sitemap_invalid")  # truncated stream
    return out


def parse_sitemap(data: bytes, max_bytes: int) -> Sitemap:
    """Parse a ``<urlset>`` or ``<sitemapindex>``; refuse DTDs, other roots and overflows."""
    reader = _Reader()
    parser = expat.ParserCreate(namespace_separator=" ")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.StartDoctypeDeclHandler = _refuse
    parser.EntityDeclHandler = _refuse
    parser.ExternalEntityRefHandler = _refuse
    parser.StartElementHandler = reader.start
    parser.EndElementHandler = reader.end
    parser.CharacterDataHandler = reader.text
    try:
        parser.Parse(inflate(data, max_bytes), True)
    except (expat.ExpatError, ValueError):
        raise SitemapError("sitemap_invalid") from None
    return Sitemap(reader.root == "sitemapindex", tuple(reader.locs))


def _refuse(*_: object) -> NoReturn:
    raise SitemapError("sitemap_invalid")


class _Reader:
    def __init__(self) -> None:
        self.root: str | None = None
        self.path: list[str] = []
        self.locs: list[str] = []
        self.chunks: list[str] | None = None
        self.size = 0

    def start(self, name: str, _attrs: object) -> None:
        local = name.rsplit(" ", 1)[-1]
        if not self.path:
            if local not in _ROOTS:
                raise SitemapError("sitemap_invalid")
            self.root = local
        self.path.append(local)
        if self.root and self.path == [self.root, _ROOTS[self.root], "loc"]:
            self.chunks, self.size = [], 0

    def text(self, data: str) -> None:
        if self.chunks is not None:
            self.size += len(data)
            if self.size > _MAX_LOC:
                raise SitemapError("sitemap_invalid")
            self.chunks.append(data)

    def end(self, _name: str) -> None:
        self.path.pop()
        if self.chunks is None or len(self.path) != 2:
            return
        loc = "".join(self.chunks).strip()
        self.chunks = None
        if loc:
            if len(self.locs) >= MAX_SITEMAP_URLS:
                raise SitemapError("sitemap_too_large")
            self.locs.append(loc)
