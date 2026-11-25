"""Declarative link reading and URL rules for the spider.

Links come from a CSS expression or from one of the recipe's own fields, never from a
callback. A URL is normalised (fragment dropped, host lowercased, the query kept) and its
host and path alone are what a report may say about it.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit

from pydantic import BaseModel

from rastro.application.sitemap import SitemapEntry, sitemap_discovery_note
from rastro.domain.linear_regex import linear_search
from rastro.domain.models import ConfigurationError, ExtractionResult, Limits, ResourceLimitError
from rastro.domain.provenance_url import canonical_page_url
from rastro.domain.source_notes import DiscoveryNote, SourceNotes
from rastro.domain.spider import SpiderPolicy
from rastro.domain.spider_report import SpiderOutcome
from rastro.ports.parser import DocumentParser

MAX_URL = 8192
_DEFAULT_PORTS = {"http": 80, "https": 443}
_SEED_ENTRIES: ContextVar[tuple[int, list[SitemapEntry], list[SourceNotes]]] = ContextVar(
    "seed_entries", default=(-1, [], [])
)


@dataclass(frozen=True)
class Link:
    """A normalised absolute URL, or the reason it is unusable."""

    url: str
    host: str
    path: str  # whole, for allow/deny; ``report_path`` shortens it for the report
    refusal: SpiderOutcome | None = None


_INVALID = Link("", "", "", "invalid_url")


def describe_url(url: str) -> Link:
    """Normalise ``url`` leniently: the job's own start URL keeps whatever it already had."""
    try:
        parts = urlsplit(url.strip())
        host = (parts.hostname or "").lower().rstrip(".")
        port = parts.port
    except ValueError:
        return _INVALID
    if parts.scheme not in _DEFAULT_PORTS or not host or parts.username or parts.password:
        return _INVALID
    name = f"[{host}]" if ":" in host else host
    netloc = name if port in (None, _DEFAULT_PORTS[parts.scheme]) else f"{name}:{port}"
    path = parts.path or "/"
    return Link(urlunsplit((parts.scheme, netloc, path, parts.query, "")), host, tidy_path(path))


def report_path(link: Link) -> str:
    return link.path[:256]


def classify_link(base: str, href: str) -> Link:
    """Resolve ``href`` against ``base``; refuse anything that is not a plain http(s) URL."""
    text = href.strip()
    if not text or len(text) > MAX_URL or any(ord(char) < 32 for char in text):
        return _INVALID
    try:
        link = describe_url(urljoin(base, text))
    except ValueError:
        return _INVALID
    if link.refusal:
        return link
    path = urlsplit(link.url).path
    if "%2f" in path.lower() or "%5c" in path.lower():
        return _INVALID
    if {".", ".."} & set(unquote(path).split("/")):
        return _INVALID
    return link


def tidy_path(path: str) -> str:
    """Drop ``;params`` (session ids live there) and repeated slashes, as most servers do."""
    segments = [segment.split(";", 1)[0] for segment in path.split("/")]
    return "/" + "/".join(segment for segment in segments if segment)


def path_matches(path: str, prefixes: tuple[str, ...]) -> bool:
    """Whole-segment prefix match: ``/item`` covers ``/item`` and ``/item/1``, not ``/items``."""
    decoded = tidy_path(unquote(path)).casefold()
    for prefix in prefixes:
        root = prefix.rstrip("/").casefold()
        if decoded == root or decoded.startswith(root + "/"):
            return True
    return False


def pattern_matches(path: str, pattern: str) -> bool:
    """Linear-time regex match against the tidied, percent-decoded path."""
    return linear_search(pattern, tidy_path(unquote(path)))


class LinkHarvest:
    """Links from each fetched document, in expression order then document order."""

    def __init__(self, spider: SpiderPolicy, parser: DocumentParser, limits: Limits) -> None:
        self._spider, self._parser, self._limits = spider, parser, limits
        self.hrefs: list[str] = []
        self.sitemaps: list[SitemapEntry] = []  # a sitemap index's child sitemaps
        self.seed_entries: list[SitemapEntry] = []  # <url> entries from a sitemap seed
        self.discovery: DiscoveryNote = DiscoveryNote(
            seed=spider.seed,
            order=spider.seed_order,
        )
        self._root_notes: list[SourceNotes] | None = None
        self._known: set[str] = set()  # a scrolled page is added once per step
        self.failed = False
        self.page_url: str | None = None

    @property
    def spider(self) -> SpiderPolicy:
        return self._spider

    def bind_root_notes(self, notes: SourceNotes, *, first: bool) -> SourceNotes:
        """Bind or update the root seed page's ``SourceNotes`` across child sitemaps."""
        scope_id, entries, holder = _SEED_ENTRIES.get()
        if first or not holder:
            holder = [notes]
            _SEED_ENTRIES.set((scope_id, entries, holder))
        else:
            object.__setattr__(holder[0], "discovery", self.discovery)
        self._root_notes = holder
        return holder[0]

    def record_discovery(
        self,
        *,
        found: int,
        filtered: int,
        admitted: int,
    ) -> DiscoveryNote:
        """Record or update discovery counts on this harvest."""
        self.discovery = (
            sitemap_discovery_note(
                self._spider.seed_order,
                found=found,
                filtered=filtered,
                admitted=admitted,
            )
            if self._spider.seed == "sitemap"
            else DiscoveryNote(
                seed=self._spider.seed,
                order=self._spider.seed_order,
                locs_found=found,
                locs_filtered=filtered,
                locs_admitted=admitted,
            )
        )
        return self.discovery

    def record_seed_entries(
        self, scope_id: int, entries: tuple[SitemapEntry, ...], *, reset: bool
    ) -> list[SitemapEntry]:
        """Accumulate urlset entries across one sitemap seed crawl."""
        current_id, accumulated, holder = _SEED_ENTRIES.get()
        if reset or current_id != scope_id:
            accumulated = []
            holder = []
            _SEED_ENTRIES.set((scope_id, accumulated, holder))
        accumulated.extend(entries)
        return list(accumulated)

    def add[T: BaseModel](
        self, url: str, body: str | None, result: ExtractionResult[T], representation: str
    ) -> None:
        if self.page_url is None:
            self.page_url = canonical_page_url(url)
        if not (self._spider.links or self._spider.link_field):
            return  # a sitemap seed without links: detail pages are leaves
        try:
            found = (
                self._from_field(result)
                if self._spider.link_field
                else self._from_html(body, representation)
            )
        except (ConfigurationError, ResourceLimitError):
            self.failed = True
            return
        for href in (_absolute(url, value) for value in found):
            if href not in self._known:
                self._known.add(href)
                self.hrefs.append(href)
        self.record_discovery(found=len(self.hrefs), filtered=0, admitted=len(self.hrefs))

    def _from_field[T: BaseModel](self, result: ExtractionResult[T]) -> list[str]:
        name = self._spider.link_field
        assert name is not None
        values = (record.data.model_dump(mode="json").get(name) for record in result.records)
        return [value for value in values if isinstance(value, str) and value.strip()]

    def _from_html(self, body: str | None, representation: str) -> list[str]:
        if not body or not body.strip():
            return []
        if representation != "html":
            raise ConfigurationError("links reads HTML; use link_field for JSON sources")
        found: list[str] = []
        for expression in self._spider.link_expressions():
            parsed = self._parser.parse(body, self._spider.link_probe(expression), self._limits)
            values = (candidate.values.get("link") for candidate in parsed.candidates)
            found.extend(value for value in values if isinstance(value, str) and value.strip())
        return found


def _absolute(base: str, href: str) -> str:
    try:
        return urljoin(base, href.strip())
    except ValueError:
        return href
