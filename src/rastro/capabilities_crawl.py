"""Capability catalog entries."""

from __future__ import annotations

from rastro.capabilities_extract import Capability, _names

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
        python=_names("estimate_crawl CrawlEstimate"),
        cli=("rastro job estimate",),
        limits="Depth 1 to 4, up to 100 pages per start URL, same host by default, robots.txt, "
        "shared budget; caps end as partial success; sitemaps parsed without DTDs or entities, "
        "one index level, plain HTTP only",
        instead_of="hand-built link following, detail-page fetching or your own sitemap parser",
        docs="docs/jobs.md#spider",
    ),
    Capability(
        task="Crawl a whole site without a page cap and resume where it stopped",
        use='`[spider] max_pages = "unbounded"` with `time_budget_seconds`; the crawl saves a '
        "checkpoint next to the job and resumes from it; `stream_job_async` yields each "
        "source's result as it finishes",
        python=_names("stream_job_async"),
        limits="Bounded by the time budget and the record limit; robots.txt and allowed hosts "
        "still apply; adaptive per-host pacing",
        instead_of="a crawler that restarts from scratch or keeps every page in memory",
        docs="docs/crawling.md#unbounded-crawls",
    ),
    Capability(
        task="Click, type or follow links before extracting, or let a model find those steps",
        use="`[[recipe.steps]]` page steps with a browser render; `rastro new --explore JOB "
        "-o NEW` (or `explore_job_file`) asks a model once and keeps only steps that replayed",
        python=_names("explore_job explore_job_file ExplorationResult"),
        cli=("rastro new --explore",),
        limits="Steps from a fixed vocabulary, bounded in count and time, under the job's "
        "request budget and network guard; exploration is operator-started only",
        instead_of="a hand-written browser script per site",
        docs="docs/page-steps.md",
    ),
    Capability(
        task="Load more records by scrolling",
        use='`[scroll] max_steps = 10` with `render = "browser"`',
        limits="1 to 30 steps; stops when a step adds no record; never enables stealth",
        instead_of="your own infinite-scroll script",
        docs="docs/jobs.md#scroll",
    ),
)
