"""Engine capability map for Scrapling.

Most of this module is one data table, ``SCRAPLING_FEATURES``; it stays in one file so every
engine feature and its Rastro surface can be reviewed together.
"""

from __future__ import annotations

from rastro.domain.engine_map import EngineArea, EngineFeature, Surface
from rastro.engine_inspect import inspect_scrapling_features

__all__ = ["SCRAPLING_FEATURES", "inspect_scrapling_features", "render_engines_markdown"]


def _row(
    area: EngineArea, name: str, surface: Surface, where: str = "", docs: str = "", reason: str = ""
) -> EngineFeature:
    return EngineFeature(
        engine="scrapling",
        area=area,
        name=name,
        surface=surface,
        where=where,
        docs=docs,
        reason=reason,
    )


def _int(area: EngineArea, prefix: str, names: str) -> tuple[EngineFeature, ...]:
    return tuple(_row(area, f"{prefix}.{n}", "internal") for n in names.split())


def _plan(area: EngineArea, prefix: str, names: str) -> tuple[EngineFeature, ...]:
    return tuple(_row(area, f"{prefix}.{n}", "planned") for n in names.split())


def _no(area: EngineArea, prefix: str, *items: tuple[str, str]) -> tuple[EngineFeature, ...]:
    return tuple(_row(area, f"{prefix}.{n}", "not_exposed", reason=r) for n, r in items)


def _exp(
    area: EngineArea, prefix: str, surface: Surface, *items: tuple[str, str, str]
) -> tuple[EngineFeature, ...]:
    return tuple(_row(area, f"{prefix}.{n}", surface, w, d) for n, w, d in items)


_ACQ_DEFAULTS = "docs/acquisition.md#policy-defaults"
_ACQ_EGRESS = "docs/acquisition.md#network-egress"
_ACQ_PERMS = "docs/acquisition.md#operator-permissions"
_ACQ_REGION = "docs/acquisition.md#metrics-and-regional-behavior"
_JOBS_EXAMPLE = "docs/jobs.md#complete-toml-example"
_NO_SESSIONS = (
    "Rastro keeps no browser between agent calls: each call opens one guarded page and closes it."
)
_BROWSER = "docs/browser.md"
_NETWORK = "docs/network-recipes.md"
_EGRESS_ROTATION = "docs/egress.md#rotation"
_EGRESS_BROWSER = "docs/egress.md#browser-and-stealth-modes"
_PER_HOST = "docs/crawling.md#per-host-pacing"
_GRANT_ONLY = "docs/browser.md#grant-only-controls"
_PROFILES = "docs/browser.md#persistent-sessions"
_OWN_TRANSPORT = "Rastro keeps its own DNS-pinned transport and asks it for this itself (ADR 0038)."


def _browser_controls(prefix: str) -> tuple[EngineFeature, ...]:
    """The ``[acquisition.browser]`` controls both Playwright entry points share."""
    wait = _BROWSER + "#waiting-for-the-page"
    load = _BROWSER + "#what-the-page-may-load"
    shared = _exp(
        "browser",
        prefix,
        "job_key",
        ("blocked_domains", "[acquisition.browser] blocked_domains", load),
        ("disable_resources", "[acquisition.browser] disable_resources", load),
        ("network_idle", "[acquisition.browser] network_idle", wait),
        ("pierce_shadow", "[acquisition.browser] pierce_shadow", _NETWORK + "#shadow-dom"),
        ("wait_selector", "[acquisition.browser] wait_selector", wait),
        ("wait_selector_state", "[acquisition.browser] wait_state", wait),
    )
    fixed_wait = (
        "wait",
        "A fixed delay is replaced by wait_selector, network_idle and the wait page step.",
    )
    extra = (
        _exp("browser", prefix, "job_key", ("block_ads", "[acquisition.browser] block_ads", load))
        if prefix == "PlaywrightSession"
        else ()
    )
    return (*shared, *extra, *_no("browser", prefix, fixed_wait))


SCRAPLING_FEATURES: tuple[EngineFeature, ...] = (
    # Selector & SelectorsGeneration (parser & adaptive)
    *_exp(
        "parser",
        "Selector",
        "recipe_strategy",
        ("css", '[recipe] strategy = "css"', "docs/api.md#recipe-variants"),
    ),
    *_exp(
        "adaptive",
        "Selector",
        "recipe_strategy",
        ("relocate", "rastro recipe repair", "docs/repair.md"),
    ),
    *_int(
        "parser",
        "Selector",
        "attrib below_elements body children extract extract_first find find_all find_ancestor "
        "get get_all_text getall has_class html_content iterancestors json next parent path "
        "prettify previous re re_first siblings tag text urljoin xpath",
    ),
    *_exp(
        "adaptive",
        "Selector",
        "recipe_strategy",
        ("find_by_regex", "rastro recipe repair (adaptive candidates)", "docs/repair.md"),
        ("find_by_text", "rastro recipe repair (adaptive candidates)", "docs/repair.md"),
        ("find_similar", "rastro recipe repair (adaptive candidates)", "docs/repair.md"),
        ("generate_css_selector", "rastro recipe repair (adaptive candidates)", "docs/repair.md"),
    ),
    *_no(
        "adaptive",
        "Selector",
        ("generate_full_css_selector", "Recipes keep short selectors that survive layout changes."),
        ("generate_full_xpath_selector", "Recipes are CSS; XPath selectors are not stored."),
        ("generate_xpath_selector", "Recipes are CSS; XPath selectors are not stored."),
    ),
    *_no(
        "adaptive",
        "Selector",
        ("retrieve", "Rastro persists relocation fingerprints in its own recipe memory."),
        ("save", "Rastro persists relocation fingerprints in its own recipe memory."),
    ),
    # RequestsSession (http & proxy)
    *_exp(
        "http",
        "RequestsSession",
        "job_key",
        ("headers", "[[acquisition.auth]]", _ACQ_PERMS),
        ("max_redirects", "[acquisition] max_redirects", _ACQ_DEFAULTS),
        ("retries", "[acquisition] max_retries", _ACQ_DEFAULTS),
        ("retry_delay", "[acquisition] backoff_seconds", _ACQ_DEFAULTS),
        ("timeout", "[acquisition] request_timeout_seconds", _ACQ_DEFAULTS),
    ),
    *_exp(
        "proxy",
        "RequestsSession",
        "job_key",
        ("proxies", "[acquisition.egress_profiles]", _ACQ_EGRESS),
        ("proxy", "[acquisition.egress_profiles.<name>] proxy_url", _ACQ_EGRESS),
        ("proxy_auth", "[acquisition.egress_profiles.<name>] username_env", _ACQ_EGRESS),
    ),
    *_exp(
        "proxy",
        "RequestsSession",
        "job_key",
        ("proxy_rotator", "[acquisition] rotate_egress", _EGRESS_ROTATION),
    ),
    *_no(
        "http",
        "RequestsSession",
        ("http3", _OWN_TRANSPORT + ' Set [acquisition.http] http_version = "3".'),
        ("impersonate", _OWN_TRANSPORT + " Set [acquisition.http] impersonate."),
    ),
    *_int("http", "RequestsSession", "follow_redirects selector_config stealthy_headers"),
    *_no(
        "http",
        "RequestsSession",
        ("cert", "Client TLS certificate paths are not accepted from job files."),
        ("verify", "TLS verification is mandatory and cannot be disabled."),
    ),
    # PlaywrightSession & PlaywrightFetchParams (browser & proxy)
    *_exp(
        "browser",
        "PlaywrightSession",
        "job_key",
        ("cookies", "[acquisition] session", "docs/acquisition.md#sessions"),
        ("extra_headers", "[region] languages", _ACQ_REGION),
        ("max_pages", "[acquisition] max_concurrency", _ACQ_DEFAULTS),
        ("timeout", "[acquisition] request_timeout_seconds", _ACQ_DEFAULTS),
    ),
    *_exp(
        "browser",
        "PlaywrightFetchParams",
        "job_key",
        ("extra_headers", "[region] languages", _ACQ_REGION),
        ("timeout", "[acquisition] request_timeout_seconds", _ACQ_DEFAULTS),
    ),
    *_browser_controls("PlaywrightSession"),
    *_browser_controls("PlaywrightFetchParams"),
    *_exp(
        "browser",
        "PlaywrightSession",
        "job_key",
        ("locale", "[region] locale", _BROWSER + "#region"),
        ("timezone_id", "[region] timezone", _BROWSER + "#region"),
        ("record_requests", "[acquisition.browser] capture_network", _NETWORK),
    ),
    *_exp(
        "browser",
        "PlaywrightSession",
        "grant",
        ("cdp_url", "[acquisition.browser] cdp_url_env", _BROWSER + "#grant-only-controls"),
        ("real_chrome", "[acquisition.browser] real_chrome", _BROWSER + "#grant-only-controls"),
        (
            "user_data_dir",
            "[acquisition.browser] session_profile",
            _BROWSER + "#persistent-sessions",
        ),
    ),
    *_int("browser", "PlaywrightSession", "max_recorded_requests"),
    *_exp(
        "proxy",
        "PlaywrightSession",
        "job_key",
        ("proxy", "[acquisition.egress_profiles.<name>] proxy_url", _EGRESS_BROWSER),
        ("proxy_rotator", "[acquisition] rotate_egress", _EGRESS_BROWSER),
    ),
    *_exp(
        "proxy",
        "PlaywrightFetchParams",
        "job_key",
        ("proxy", "[acquisition.egress_profiles.<name>] proxy_url", _EGRESS_BROWSER),
    ),
    *_int(
        "browser",
        "PlaywrightSession",
        "additional_args extra_flags google_search headless load_dom page_setup retries "
        "retry_delay selector_config useragent",
    ),
    *_int("browser", "PlaywrightFetchParams", "google_search load_dom page_setup selector_config"),
    *_no(
        "browser",
        "PlaywrightSession",
        ("dns_over_https", "Browser DNS goes through Rastro's pinned host resolver rules."),
        (
            "executable_path",
            "Arbitrary host binary paths are not accepted from jobs or bundles.",
        ),
        ("init_script", "Recipes are data; arbitrary JavaScript injection is never executed."),
        ("page_action", "Recipes use declarative page steps instead of Python callables."),
    ),
    *_no(
        "browser",
        "PlaywrightFetchParams",
        ("page_action", "Recipes use declarative page steps instead of Python callables."),
    ),
    # Spider (crawl)
    *_exp(
        "crawl",
        "Spider",
        "job_key",
        ("allowed_domains", "[acquisition] allowed_hosts", _ACQ_DEFAULTS),
        ("concurrent_requests", "[acquisition] max_concurrency", _ACQ_DEFAULTS),
        ("download_delay", "[acquisition] min_interval_seconds", _ACQ_DEFAULTS),
        ("max_blocked_retries", "[acquisition] max_retries", _ACQ_DEFAULTS),
        ("name", "name", _JOBS_EXAMPLE),
        ("start_urls", "[[sources]] url", _JOBS_EXAMPLE),
    ),
    *_exp("crawl", "Spider", "grant", ("robots_txt_obey", "--ignore-robots HOST", _ACQ_PERMS)),
    *_exp(
        "crawl",
        "Spider",
        "job_key",
        ("autothrottle_enabled", '[acquisition.hosts."<host>"] autothrottle', _PER_HOST),
        (
            "concurrent_requests_per_domain",
            '[acquisition.hosts."<host>"] max_concurrency',
            _PER_HOST,
        ),
    ),
    *_int(
        "crawl",
        "Spider",
        "autothrottle_block_backoff autothrottle_max_delay autothrottle_start_delay "
        "autothrottle_target_concurrency development_cache_dir development_mode",
    ),
    *_int(
        "crawl",
        "Spider",
        "fp_include_headers fp_include_kwargs fp_keep_fragments log_file logging_date_format "
        "logging_format logging_level",
    ),
    # Templates (crawl & convert)
    *_exp(
        "crawl",
        "templates",
        "recipe_strategy",
        (
            "crawler",
            "examples/registry/recipes/generic-crawl",
            "docs/crawling.md",
        ),
        (
            "shopify",
            "examples/registry/recipes/shopify-products",
            "docs/crawling.md",
        ),
        (
            "sitemap",
            "examples/registry/recipes/sitemap-crawl",
            "docs/crawling.md",
        ),
    ),
    *_no(
        "crawl",
        "templates",
        (
            "feed",
            "Rastro spiders start from HTML pages or XML sitemaps rather than syndication feeds.",
        ),
    ),
    *_no(
        "convert",
        "templates",
        (
            "site_to_markdown",
            "Rastro extracts typed records with provenance rather than full-site Markdown dumps.",
        ),
    ),
    # MCP server tools (mcp)
    *_exp(
        "mcp",
        "mcp",
        "python_api",
        (
            "make_request",
            "rastro.fetch",
            "docs/api.md#adapters-fetch-and-load-your-own-records",
        ),
    ),
    *_exp(
        "mcp",
        "mcp",
        "mcp_tool",
        ("browser_fetch", "browser_snapshot", "docs/mcp.md#browser-tools"),
        ("browser_fetch_once", "browser_snapshot", "docs/mcp.md#browser-tools"),
        ("browser_actions", "browser_* steps", "docs/mcp.md#browser-tools"),
        ("browser_extract", "browser_extract", "docs/mcp.md#browser-tools"),
        ("browser_network_requests", "browser_network", "docs/mcp.md#browser-tools"),
        ("browser_network_request", "browser_network", "docs/mcp.md#browser-tools"),
        ("browser_screenshot", "browser_screenshot", "docs/mcp.md#browser-tools"),
    ),
    *_no(
        "mcp",
        "mcp",
        ("browser_evaluate", "Arbitrary JavaScript evaluation is never exposed to agents."),
        ("browser_open", _NO_SESSIONS),
        ("close_session", _NO_SESSIONS),
        ("list_sessions", _NO_SESSIONS),
        (
            "browser_load_state",
            "Filesystem storage state paths are not exposed to MCP clients.",
        ),
        (
            "browser_save_state",
            "Filesystem storage state paths are not exposed to MCP clients.",
        ),
        ("open_request_session", "HTTP fetches go through Rastro's pinned policy acquirer."),
        ("session_make_request", "HTTP fetches go through Rastro's pinned policy acquirer."),
    ),
)


def render_engines_markdown() -> str:
    """Render ``docs/engines.md`` from ``SCRAPLING_FEATURES``."""
    from rastro.engine_docs import render_markdown

    return render_markdown(SCRAPLING_FEATURES)
