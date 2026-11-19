"""Bounded, DTD-free sitemap parsing and entry ordering (N16; SEC-001, SEC-008).

A sitemap is untrusted XML. Any DOCTYPE is refused before an entity can be declared, so
there is no entity expansion and no external resource; expat never touches the network.
Gzip is inflated under the same byte cap. Element names match without their namespace,
and ``<loc>`` / ``<lastmod>`` entries come back in document order.
"""

from __future__ import annotations

import re
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import NoReturn
from xml.parsers import expat

from rastro.domain.source_notes import DiscoveryNote
from rastro.domain.spider import SeedOrder

MAX_SITEMAP_URLS = 50_000  # the sitemap protocol's own per-file limit
_MAX_LOC = 8192
_MAX_LASTMOD = 128
_ROOTS = {"urlset": "url", "sitemapindex": "sitemap"}
_YEAR_RE = re.compile(r"^\d{4}$")
_YEAR_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


class SitemapError(Exception):
    """A safe issue code: ``sitemap_invalid`` or ``sitemap_too_large``."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class SitemapEntry:
    """One ``<url>`` or ``<sitemap>`` entry with its optional raw ``<lastmod>`` text."""

    loc: str
    lastmod: str | None = None


@dataclass(frozen=True)
class Sitemap:
    index: bool  # a <sitemapindex>: its entries are child sitemaps
    entries: tuple[SitemapEntry, ...]

    @property
    def locs(self) -> tuple[str, ...]:
        return tuple(entry.loc for entry in self.entries)


def parse_lastmod(raw: str | None) -> datetime | None:
    """Parse W3C Datetime / ISO 8601 ``<lastmod>`` as UTC, or ``None`` when absent/invalid."""
    if not raw:
        return None
    text = raw.strip()
    if not text or len(text) > _MAX_LASTMOD:
        return None
    if _YEAR_RE.fullmatch(text):
        text = f"{text}-01-01"
    elif _YEAR_MONTH_RE.fullmatch(text):
        text = f"{text}-01"
    try:
        parsed = datetime.fromisoformat(text)
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
    except (ValueError, OverflowError):
        return None


def order_entries(entries: Sequence[SitemapEntry], order: SeedOrder) -> list[SitemapEntry]:
    """Order sitemap entries by ``document``, ``reverse`` or ``lastmod`` (newest first)."""
    if order == "document":
        return list(entries)
    if order == "reverse":
        return list(reversed(entries))
    dated: list[tuple[datetime, SitemapEntry]] = []
    undated: list[SitemapEntry] = []
    for entry in entries:
        stamp = parse_lastmod(entry.lastmod)
        if stamp is not None:
            dated.append((stamp, entry))
        else:
            undated.append(entry)
    dated.sort(key=lambda item: item[0], reverse=True)
    return [entry for _, entry in dated] + undated


def sitemap_discovery_note(
    order: SeedOrder,
    *,
    found: int,
    filtered: int,
    admitted: int,
) -> DiscoveryNote:
    """Summarize sitemap seed discovery counts for the source report."""
    return DiscoveryNote(
        seed="sitemap",
        order=order,
        locs_found=max(0, found),
        locs_filtered=max(0, filtered),
        locs_admitted=max(0, admitted),
    )


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
    return Sitemap(reader.root == "sitemapindex", tuple(reader.entries))


def _refuse(*_: object) -> NoReturn:
    raise SitemapError("sitemap_invalid")


class _Reader:
    def __init__(self) -> None:
        self.root: str | None = None
        self.path: list[str] = []
        self.entries: list[SitemapEntry] = []
        self.field: str | None = None
        self.chunks: list[str] | None = None
        self.size = 0
        self.cur_loc: str | None = None
        self.cur_lastmod: str | None = None

    def start(self, name: str, _attrs: object) -> None:
        local = name.rsplit(" ", 1)[-1]
        if not self.path:
            if local not in _ROOTS:
                raise SitemapError("sitemap_invalid")
            self.root = local
        self.path.append(local)
        if self.root is None:
            return
        item = _ROOTS[self.root]
        if self.path == [self.root, item]:
            self.cur_loc, self.cur_lastmod = None, None
        elif self.path == [self.root, item, "loc"]:
            self.field, self.chunks, self.size = "loc", [], 0
        elif self.path == [self.root, item, "lastmod"]:
            self.field, self.chunks, self.size = "lastmod", [], 0

    def text(self, data: str) -> None:
        if self.chunks is None:
            return
        self.size += len(data)
        if self.field == "loc":
            if self.size > _MAX_LOC:
                raise SitemapError("sitemap_invalid")
            self.chunks.append(data)
        elif self.size <= _MAX_LASTMOD:
            self.chunks.append(data)
        else:
            self.chunks = None  # overlong <lastmod> is ignored rather than failing the sitemap

    def end(self, _name: str) -> None:
        depth = len(self.path)
        self.path.pop()
        if depth == 3 and self.field is not None:
            if self.chunks is not None:
                value = "".join(self.chunks).strip()
                if self.field == "loc" and value:
                    self.cur_loc = value
                elif self.field == "lastmod" and value:
                    self.cur_lastmod = value
            self.chunks, self.field = None, None
            return
        if depth == 2 and self.cur_loc:
            if len(self.entries) >= MAX_SITEMAP_URLS:
                raise SitemapError("sitemap_too_large")
            self.entries.append(SitemapEntry(self.cur_loc, self.cur_lastmod))
            self.cur_loc, self.cur_lastmod = None, None
