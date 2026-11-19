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
        use='`render = "browser"` or `render = "stealth"` on `[acquisition]` or a source; '
        "agent tools, bundles, registry recipes and model output need `--allow-untrusted`",
        limits="`browser` extra; one browser context per source; images and media blocked; "
        "stealth is never implied",
        instead_of="driving Playwright or Selenium yourself",
        docs="docs/acquisition.md#javascript-pages",
    ),
    Capability(
        task="See how every engine feature maps to Rastro",
        use="`docs/engines.md` and `rastro.engine_coverage.SCRAPLING_FEATURES` map each "
        "Scrapling selector, HTTP, browser, spider, template and MCP feature to its Rastro "
        "surface or the reason it is not exposed",
        limits="Engine features reach jobs only through Rastro's strategy ports and policy gates, "
        "never as raw passthrough options",
        instead_of="calling Scrapling fetchers, sessions or browser tools directly",
        docs="docs/engines.md#scrapling-capability-map",
    ),
)
