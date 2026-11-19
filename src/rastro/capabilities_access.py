"""Capability catalog entries."""

from __future__ import annotations

from rastro.capabilities_extract import Capability

ACCESS_CAPABILITIES: tuple[Capability, ...] = (
    Capability(
        task="Find candidate sources for a goal",
        use='`rastro new --goal "TEXT" --search searxng|model --approve HOST`',
        cli=("rastro new --goal TEXT",),
        limits="You approve every host; nothing is fetched outside approved hosts",
        instead_of="your own search-and-crawl discovery",
        docs="docs/jobs.md#start-from-a-goal",
    ),
    Capability(
        task="Scrape JavaScript-rendered pages",
        use='`render = "browser"` on `[acquisition]` or a source; `render = "stealth"` only '
        "with the operator grant `--allow-stealth`",
        limits="`browser` extra; one browser context per source; images and media blocked; "
        "stealth is never implied",
        instead_of="driving Playwright or Selenium yourself",
        docs="docs/acquisition.md#javascript-pages",
    ),
)
