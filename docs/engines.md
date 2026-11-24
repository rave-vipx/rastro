# Acquisition and parsing engines

<!-- Generated from src/rastro/engine_coverage.py; edit SCRAPLING_FEATURES instead. -->

Rastro fetches, renders, parses, relocates and crawls through engines behind strategy
interfaces (`rastro.ports`). Scrapling is the default engine today; another engine can
implement the same ports without changing jobs, recipes, Studio or the public Python API.

## Scrapling capability map

Every public feature of Scrapling is mapped below to the Rastro surface where users
meet it, marked `internal` when Rastro drives it behind a port, `planned` while its
surface is landing, or `not_exposed` with the security or architecture reason.
A continuous-integration test inspects the installed Scrapling package and fails if an
upgrade adds an unmapped feature.

### Parser and selectors

| Engine feature | Surface | Rastro location or reason | Docs |
| --- | --- | --- | --- |
| `Selector.css` | `recipe_strategy` | `[recipe] strategy = "css"` | [api.md#recipe-variants](api.md#recipe-variants) |
| `Selector.attrib` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.below_elements` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.body` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.children` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.extract` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.extract_first` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.find` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.find_all` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.find_ancestor` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.get` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.get_all_text` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.getall` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.has_class` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.html_content` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.iterancestors` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.json` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.next` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.parent` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.path` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.prettify` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.previous` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.re` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.re_first` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.siblings` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.tag` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.text` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.urljoin` | `internal` | Used internally behind Rastro ports. | - |
| `Selector.xpath` | `internal` | Used internally behind Rastro ports. | - |

### Adaptive relocation and selector generation

| Engine feature | Surface | Rastro location or reason | Docs |
| --- | --- | --- | --- |
| `Selector.relocate` | `recipe_strategy` | `rastro recipe repair` | [repair.md](repair.md) |
| `Selector.find_by_regex` | `planned` | Used internally behind Rastro ports. | - |
| `Selector.find_by_text` | `planned` | Used internally behind Rastro ports. | - |
| `Selector.find_similar` | `planned` | Used internally behind Rastro ports. | - |
| `Selector.generate_css_selector` | `planned` | Used internally behind Rastro ports. | - |
| `Selector.generate_full_css_selector` | `planned` | Used internally behind Rastro ports. | - |
| `Selector.generate_full_xpath_selector` | `planned` | Used internally behind Rastro ports. | - |
| `Selector.generate_xpath_selector` | `planned` | Used internally behind Rastro ports. | - |
| `Selector.retrieve` | `not_exposed` | Rastro persists relocation fingerprints in its own recipe memory. | - |
| `Selector.save` | `not_exposed` | Rastro persists relocation fingerprints in its own recipe memory. | - |

### HTTP sessions

| Engine feature | Surface | Rastro location or reason | Docs |
| --- | --- | --- | --- |
| `RequestsSession.headers` | `job_key` | `[[acquisition.auth]]` | [acquisition.md#operator-permissions](acquisition.md#operator-permissions) |
| `RequestsSession.max_redirects` | `job_key` | `[acquisition] max_redirects` | [acquisition.md#policy-defaults](acquisition.md#policy-defaults) |
| `RequestsSession.retries` | `job_key` | `[acquisition] max_retries` | [acquisition.md#policy-defaults](acquisition.md#policy-defaults) |
| `RequestsSession.retry_delay` | `job_key` | `[acquisition] backoff_seconds` | [acquisition.md#policy-defaults](acquisition.md#policy-defaults) |
| `RequestsSession.timeout` | `job_key` | `[acquisition] request_timeout_seconds` | [acquisition.md#policy-defaults](acquisition.md#policy-defaults) |
| `RequestsSession.http3` | `planned` | Used internally behind Rastro ports. | - |
| `RequestsSession.impersonate` | `planned` | Used internally behind Rastro ports. | - |
| `RequestsSession.follow_redirects` | `internal` | Used internally behind Rastro ports. | - |
| `RequestsSession.selector_config` | `internal` | Used internally behind Rastro ports. | - |
| `RequestsSession.stealthy_headers` | `internal` | Used internally behind Rastro ports. | - |
| `RequestsSession.cert` | `not_exposed` | Client TLS certificate paths are not accepted from job files. | - |
| `RequestsSession.verify` | `not_exposed` | TLS verification is mandatory and cannot be disabled. | - |

### Browser sessions and controls

| Engine feature | Surface | Rastro location or reason | Docs |
| --- | --- | --- | --- |
| `PlaywrightSession.cookies` | `job_key` | `[acquisition] session` | [acquisition.md#sessions](acquisition.md#sessions) |
| `PlaywrightSession.extra_headers` | `job_key` | `[region] languages` | [acquisition.md#metrics-and-regional-behavior](acquisition.md#metrics-and-regional-behavior) |
| `PlaywrightSession.max_pages` | `job_key` | `[acquisition] max_concurrency` | [acquisition.md#policy-defaults](acquisition.md#policy-defaults) |
| `PlaywrightSession.timeout` | `job_key` | `[acquisition] request_timeout_seconds` | [acquisition.md#policy-defaults](acquisition.md#policy-defaults) |
| `PlaywrightFetchParams.extra_headers` | `job_key` | `[region] languages` | [acquisition.md#metrics-and-regional-behavior](acquisition.md#metrics-and-regional-behavior) |
| `PlaywrightFetchParams.timeout` | `job_key` | `[acquisition] request_timeout_seconds` | [acquisition.md#policy-defaults](acquisition.md#policy-defaults) |
| `PlaywrightSession.block_ads` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.blocked_domains` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.cdp_url` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.disable_resources` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.locale` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.max_recorded_requests` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.network_idle` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.pierce_shadow` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.real_chrome` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.record_requests` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.timezone_id` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.user_data_dir` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.wait` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.wait_selector` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.wait_selector_state` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightFetchParams.blocked_domains` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightFetchParams.disable_resources` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightFetchParams.network_idle` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightFetchParams.pierce_shadow` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightFetchParams.wait` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightFetchParams.wait_selector` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightFetchParams.wait_selector_state` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.additional_args` | `internal` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.extra_flags` | `internal` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.google_search` | `internal` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.headless` | `internal` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.load_dom` | `internal` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.page_setup` | `internal` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.retries` | `internal` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.retry_delay` | `internal` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.selector_config` | `internal` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.useragent` | `internal` | Used internally behind Rastro ports. | - |
| `PlaywrightFetchParams.google_search` | `internal` | Used internally behind Rastro ports. | - |
| `PlaywrightFetchParams.load_dom` | `internal` | Used internally behind Rastro ports. | - |
| `PlaywrightFetchParams.page_setup` | `internal` | Used internally behind Rastro ports. | - |
| `PlaywrightFetchParams.selector_config` | `internal` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.dns_over_https` | `not_exposed` | Browser DNS goes through Rastro's pinned host resolver rules. | - |
| `PlaywrightSession.executable_path` | `not_exposed` | Arbitrary host binary paths are not accepted from jobs or bundles. | - |
| `PlaywrightSession.init_script` | `not_exposed` | Recipes are data; arbitrary JavaScript injection is never executed. | - |
| `PlaywrightSession.page_action` | `not_exposed` | Recipes use declarative page steps instead of Python callables. | - |
| `PlaywrightFetchParams.page_action` | `not_exposed` | Recipes use declarative page steps instead of Python callables. | - |

### Proxy egress and rotation

| Engine feature | Surface | Rastro location or reason | Docs |
| --- | --- | --- | --- |
| `RequestsSession.proxies` | `job_key` | `[acquisition.egress_profiles]` | [acquisition.md#network-egress](acquisition.md#network-egress) |
| `RequestsSession.proxy` | `job_key` | `[acquisition.egress_profiles.<name>] proxy_url` | [acquisition.md#network-egress](acquisition.md#network-egress) |
| `RequestsSession.proxy_auth` | `job_key` | `[acquisition.egress_profiles.<name>] username_env` | [acquisition.md#network-egress](acquisition.md#network-egress) |
| `RequestsSession.proxy_rotator` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.proxy` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightSession.proxy_rotator` | `planned` | Used internally behind Rastro ports. | - |
| `PlaywrightFetchParams.proxy` | `planned` | Used internally behind Rastro ports. | - |

### Spider and crawl engine

| Engine feature | Surface | Rastro location or reason | Docs |
| --- | --- | --- | --- |
| `Spider.allowed_domains` | `job_key` | `[acquisition] allowed_hosts` | [acquisition.md#policy-defaults](acquisition.md#policy-defaults) |
| `Spider.concurrent_requests` | `job_key` | `[acquisition] max_concurrency` | [acquisition.md#policy-defaults](acquisition.md#policy-defaults) |
| `Spider.download_delay` | `job_key` | `[acquisition] min_interval_seconds` | [acquisition.md#policy-defaults](acquisition.md#policy-defaults) |
| `Spider.max_blocked_retries` | `job_key` | `[acquisition] max_retries` | [acquisition.md#policy-defaults](acquisition.md#policy-defaults) |
| `Spider.name` | `job_key` | `name` | [jobs.md#complete-toml-example](jobs.md#complete-toml-example) |
| `Spider.start_urls` | `job_key` | `[[sources]] url` | [jobs.md#complete-toml-example](jobs.md#complete-toml-example) |
| `Spider.robots_txt_obey` | `grant` | `--ignore-robots HOST` | [acquisition.md#operator-permissions](acquisition.md#operator-permissions) |
| `Spider.autothrottle_block_backoff` | `planned` | Used internally behind Rastro ports. | - |
| `Spider.autothrottle_enabled` | `planned` | Used internally behind Rastro ports. | - |
| `Spider.autothrottle_max_delay` | `planned` | Used internally behind Rastro ports. | - |
| `Spider.autothrottle_start_delay` | `planned` | Used internally behind Rastro ports. | - |
| `Spider.autothrottle_target_concurrency` | `planned` | Used internally behind Rastro ports. | - |
| `Spider.concurrent_requests_per_domain` | `planned` | Used internally behind Rastro ports. | - |
| `Spider.development_cache_dir` | `planned` | Used internally behind Rastro ports. | - |
| `Spider.development_mode` | `planned` | Used internally behind Rastro ports. | - |
| `Spider.fp_include_headers` | `internal` | Used internally behind Rastro ports. | - |
| `Spider.fp_include_kwargs` | `internal` | Used internally behind Rastro ports. | - |
| `Spider.fp_keep_fragments` | `internal` | Used internally behind Rastro ports. | - |
| `Spider.log_file` | `internal` | Used internally behind Rastro ports. | - |
| `Spider.logging_date_format` | `internal` | Used internally behind Rastro ports. | - |
| `Spider.logging_format` | `internal` | Used internally behind Rastro ports. | - |
| `Spider.logging_level` | `internal` | Used internally behind Rastro ports. | - |
| `templates.crawler` | `recipe_strategy` | `examples/registry/recipes/generic-crawl` | [crawling.md](crawling.md) |
| `templates.shopify` | `recipe_strategy` | `examples/registry/recipes/shopify-products` | [crawling.md](crawling.md) |
| `templates.sitemap` | `recipe_strategy` | `examples/registry/recipes/sitemap-crawl` | [crawling.md](crawling.md) |
| `templates.feed` | `not_exposed` | Rastro spiders start from HTML pages or XML sitemaps rather than syndication feeds. | - |

### Content conversion

| Engine feature | Surface | Rastro location or reason | Docs |
| --- | --- | --- | --- |
| `templates.site_to_markdown` | `not_exposed` | Rastro extracts typed records with provenance rather than full-site Markdown dumps. | - |

### Agent browser tools (MCP)

| Engine feature | Surface | Rastro location or reason | Docs |
| --- | --- | --- | --- |
| `mcp.make_request` | `python_api` | `rastro.fetch` | [api.md#adapters-fetch-and-load-your-own-records](api.md#adapters-fetch-and-load-your-own-records) |
| `mcp.browser_fetch` | `mcp_tool` | `browser_snapshot` | [mcp.md#browser-tools](mcp.md#browser-tools) |
| `mcp.browser_fetch_once` | `mcp_tool` | `browser_snapshot` | [mcp.md#browser-tools](mcp.md#browser-tools) |
| `mcp.browser_actions` | `mcp_tool` | `browser_* steps` | [mcp.md#browser-tools](mcp.md#browser-tools) |
| `mcp.browser_extract` | `mcp_tool` | `browser_extract` | [mcp.md#browser-tools](mcp.md#browser-tools) |
| `mcp.browser_network_requests` | `mcp_tool` | `browser_network` | [mcp.md#browser-tools](mcp.md#browser-tools) |
| `mcp.browser_network_request` | `mcp_tool` | `browser_network` | [mcp.md#browser-tools](mcp.md#browser-tools) |
| `mcp.browser_screenshot` | `mcp_tool` | `browser_screenshot` | [mcp.md#browser-tools](mcp.md#browser-tools) |
| `mcp.browser_evaluate` | `not_exposed` | Arbitrary JavaScript evaluation is never exposed to agents. | - |
| `mcp.browser_open` | `not_exposed` | Rastro keeps no browser between agent calls: each call opens one guarded page and closes it. | - |
| `mcp.close_session` | `not_exposed` | Rastro keeps no browser between agent calls: each call opens one guarded page and closes it. | - |
| `mcp.list_sessions` | `not_exposed` | Rastro keeps no browser between agent calls: each call opens one guarded page and closes it. | - |
| `mcp.browser_load_state` | `not_exposed` | Filesystem storage state paths are not exposed to MCP clients. | - |
| `mcp.browser_save_state` | `not_exposed` | Filesystem storage state paths are not exposed to MCP clients. | - |
| `mcp.open_request_session` | `not_exposed` | HTTP fetches go through Rastro's pinned policy acquirer. | - |
| `mcp.session_make_request` | `not_exposed` | HTTP fetches go through Rastro's pinned policy acquirer. | - |
