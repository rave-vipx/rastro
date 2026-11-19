"""Capability catalog entries."""

from __future__ import annotations

from rastro.capabilities_extract import Capability

CRAWL_CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        task="Follow next-page links",
        use='`[recipe.pagination] next = "a[rel=next]::attr(href)"`, `max_pages = 5`',
        limits="1 to 50 pages, same host, one request budget per source",
        instead_of="a loop that guesses page numbers or URL templates",
        docs="docs/jobs.md#pagination",
    ),
    Capability(
        task="Follow links from a listing to detail pages",
        use='`[spider] mode = "spider"` with `links = "a.item::attr(href)"` or `link_field`, '
        '`max_depth`, `max_pages`, `allow`, `deny`, `allow_pattern`; `seed = "sitemap"` starts '
        'from a sitemap.xml (or .xml.gz) source with `seed_order = "document" | "reverse" | '
        '"lastmod"` applied before `max_pages`',
        cli=("rastro job estimate",),
        limits="Depth 1 to 4, up to 100 pages per start URL, same host by default, robots.txt, "
        "shared budget; caps end as partial success; sitemaps parsed without DTDs or entities, "
        "one index level, plain HTTP only",
        instead_of="hand-built link following, detail-page fetching or your own sitemap parser",
        docs="docs/jobs.md#spider",
    ),
    Capability(
        task="Load more records by scrolling",
        use='`[scroll] max_steps = 10` with `render = "browser"`',
        limits="1 to 30 steps; stops when a step adds no record; never enables stealth",
        instead_of="your own infinite-scroll script",
        docs="docs/jobs.md#scroll",
    ),
)
