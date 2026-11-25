# Changelog

All notable user-facing changes to Rastro are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](docs/stability.md). Versions before 0.6.0 were never published to a package registry.

## [Unreleased]

- The public recipe hub now lives in this repository under `hub/` (three starter recipes, `health.json`, a CC0 license for recipes and `hub/build.py` to replay every package offline). `rastro hub` reads `https://raw.githubusercontent.com/rave-vipx/rastro/main/hub/` by default, and a `Hub` workflow verifies hub-only pull requests.

## [1.0.0] - 2026-11-24

First stable release. The public Python names, the job file format, the run report schema and the CLI exit codes are frozen under the policy in `docs/stability.md`.

### Added

- Run report: every `rastro job run` and `rastro job load` result carries a content-free `report` (`rastro.RunReport`, `rastro.SourceReport`) saying per source why it ended as it did: outcome, reason, pages, accepted and rejected counts by code, fields nulled, what was stored, acquisition, crawl, repair and timings. It also reaches the failure hook payload and Studio, and its JSON Schema is `docs/schemas/run-report.schema.json`. See `docs/run-report.md`.
- List and object fields (`type = "list"` and `"object"`, `rastro.ListField`, `rastro.ObjectField`, `rastro.ScalarField`), `null_values` placeholders for missing values, and regional datetimes (`localized = true`) in job files. See `docs/fields.md`.
- Nested recipes and pointers: a recipe field can be a `NestedMapping` (repeated child elements or a JSON array with one wildcard) and JSON pointers can be relative. CSS, JSON-LD, JSON and embedded JSON recipes extract nested lists and objects with bounded depth and item counts.
- Tolerant page JSON: a BOM, trailing commas and raw control characters no longer fail a source, and JSON-LD `ItemList` wrappers are traversed. Recovered records report `repaired_json`.
- Field isolation: a bad optional value nulls that field and a bad record is rejected alone, each reported as an issue, instead of failing the whole source.
- Spider link lists: `spider.links` accepts a list of up to eight CSS attribute expressions (a comma selector group is refused with `link_group_refused`), `spider.deny_pattern` rejects paths by regular expression before `max_pages`, and sitemap seeds take `seed_order = "document" | "reverse" | "lastmod"` and `allow_pattern`. Page identity keeps the query keys that distinguish pages (`?state=CA`) while evidence still drops secret-like and tracking keys. See `docs/crawling.md`.
- `[acquisition] robots = "obey" | "ignore"` lets your own job skip robots.txt for its hosts; agent tools, bundles, registry recipes and model proposals need the `robots_ignore` grant.
- Engine capability map: `docs/engines.md` and `rastro.engine_coverage` map every Scrapling selector, HTTP, browser, spider, template and MCP feature to its Rastro setting or the reason it is not exposed.
- Capability-first trust model: jobs from your own files use stealth, browser and solver features without a grant; input from MCP tools, bundles, the registry and model proposals starts with none. `--allow-untrusted ORIGIN:NAME` grants one capability to one origin and `--allow-capability NAME` lets your own jobs use `persistent_session`, `real_chrome`, `cdp` or `solver_plugins`. Capability use is listed in the run report.
- `rastro.read_records(job_path)` reads a job's dataset back as validated `ReadRecord` models with per-field evidence and source origins (`rastro.DatasetRead`), and `rastro dataset export` adds `--format csv` with nested values flattened into one column per member or list item. See `docs/reading-results.md`.
- Discovery report and crawl estimate: sitemap and spider runs report how many links were found and kept, `rastro job estimate` and `rastro.estimate_crawl` (`rastro.CrawlEstimate`) give an offline upper bound on requests and minutes before any fetch.
- Repair in the run report: a source's report says whether a repair was promoted, quarantined or rejected, why promotion was refused and whether it needed a model, and Studio's Alerts view shows the latest stored report's repair and drift alerts.
- Synthetic examples: the examples, benchmarks and documentation use invented event-agenda sources on `.example` hosts.
- `examples/event-agenda/read_agenda.py`: an offline example that extracts nested ticket tiers with a job file and no custom adapter, prints the run report summary and reads the records back with `rastro.read_records`.
- `rastro.Capability` and the catalog rows for the new public names, so `rastro capabilities` and `llms.txt` list them.
- Unbounded crawls: `[spider] max_pages = "unbounded"` crawls until the frontier is empty, bounded by `time_budget_seconds`, with adaptive per-host pacing. A versioned JSON checkpoint next to the job is saved every minute and at the stop, and the next `rastro job run` or `rastro job load` resumes from it (`crawl_resumed`, `crawl_checkpoint_discarded`). See `docs/crawling.md` and ADR 0040.
- Acquisition ladder: a refused source climbs from its own level through impersonation, a browser, a stealth browser, challenge solving and operator-installed solver plugins (entry points in the `rastro.solvers` group, grant-only) in `rastro job run`, `rastro job load` and `rastro.scrape`. Recipe memory keeps the lowest working level and re-checks the levels below every ten runs; the run report lists every level tried and a source refused at every level reports `ladder_exhausted`. See `docs/egress.md` and ADR 0039.
- HTTP options: `[acquisition.http] impersonate = "chrome" | "firefox" | "safari" | "edge"` and `http_version = "1.1" | "2" | "3"` on Rastro's DNS-pinned transport. The run report records the HTTP version that answered; an option the installed client or route cannot honour reports `impersonation_unavailable` or `http3_unavailable`. See ADR 0038.
- Per-host pacing: `[acquisition.hosts."<host>"]` with `min_interval_seconds`, `max_concurrency` and `autothrottle`, which also works on HTTP-only installs.
- Egress rotation: `[acquisition] rotate_egress` cycles egress profiles per transfer and retries a failed proxy on the next one (`proxy_rotated`); browser and stealth modes use egress profiles too. A blocked source's run report entry names the kind of refusal and links to a guide in `docs/egress.md`.
- Browser controls under `[acquisition.browser]`: `wait_selector`, `wait_state`, `network_idle`, `disable_resources`, `block_ads`, `blocked_domains`, `pierce_shadow`, `screenshot` evidence, and the grant-only `session_profile`, `real_chrome` and `cdp_url_env`. The job's `[region]` locale and timezone reach the browser. See `docs/browser.md`.
- Page steps: CSS recipes carry `[[recipe.steps]]` (click, type, select, scroll, wait, follow) that replay in the source's browser page under the job's budget and network guard. `rastro new --explore JOB -o NEW`, `rastro.explore_job_file` and `rastro.explore_job` let a model find steps once and keep only the ones that replayed without it. See `docs/page-steps.md`.
- Network recipes: `[acquisition.browser] capture_network` captures the JSON responses a page loads and a JSON recipe's `capture` pattern reads one of them (`capture_empty` when none matched). See `docs/network-recipes.md`.
- Adaptive repair: elements similar to the remembered records are tried as repair candidates before any model, through the same gates; a passing one repairs with no model call (`origin = "adaptive"`). Model prompts carry a condensed, untrusted Markdown view of the page.
- Nested model proposals: models propose list and object fields, the gates check members, drift tracks members, and every proposal path tells the model the source's region timezone.
- Local models and cost: `rastro model detect` finds local model servers, `rastro model test` tries one, and the run report shows the cost per model call.
- Hub: `rastro hub search URL`, `rastro hub install` and `rastro hub publish`, and `rastro.hub_candidates` (`rastro.HubCandidate`) offers registry recipes for a URL's domain. The synthetic registry ships sitemap, products.json and generic crawl starter recipes. See `docs/hub.md`.
- `rastro.stream_job_async` yields each source's result as it finishes.
- Studio guided flow: `/api/wizard/lookup` offers hub recipes for a URL before any fetch, `/api/wizard/page` renders the page in a browser with a highlight box per field match, and `/api/wizard/pick` turns a clicked element into a stable selector (and, with consent, a model-suggested field name). Studio keeps a short-lived development page cache that prunes expired entries.
- Schedules: `[schedule] at = "HH:MM"` runs at a time of day in the job timezone, and `rastro job schedule "every day at 8:00"` reads a schedule in English or Spanish words. `rastro dataset changes` lists records new, removed or changed since a time or an earlier copy, and `rastro dataset export --format xlsx` writes a spreadsheet.
- Notifications: built-in email, Slack, Telegram and webhook channels (`--notify`, `RASTRO_NOTIFY`) on the failure-hook exits, and `rastro notify test CHANNEL`.
- MCP browser tools for agents building a recipe: `browser_snapshot`, `browser_extract`, `browser_network` and `browser_screenshot` open one guarded page per call, replay page steps from the closed vocabulary and close it; they run as untrusted `mcp` input (steps need `--allow-untrusted mcp:page_actions`) and never evaluate JavaScript. See `docs/mcp.md`.
- `rastro shell JOB [--source N]` opens an interactive Python shell on a job source's parsed page.
- Studio for 1.0: a four-step guided create (hub offer first, an estimate in requests and minutes, a review with the page picture and fields outlined, click to fix a field, save, and an optional AI look for records behind a click); **Use this data instead** saves a project that reads the JSON the page loads (`/api/wizard/offers/use`); the Sites view shows a traffic light and a plain sentence per site and a running read with progress, a time-budget bar and the first records; the Hub view lists what others built for a site as cards, tries or installs it as a candidate, and asks before allowing extra permissions for one site; Advanced lists the page steps a recipe replays with delete and "look again" (`/api/steps`, `/api/steps/delete`). Views built in the page redraw when the language or Advanced changes; every view is checked at 360 px and desktop width, light and dark, in English and Spanish.

### Changed

- `rastro init --agent`, `llms.txt` and `docs/capabilities.md` explain how results reach Studio: keep them in a job's dataset (`rastro job load` or `rastro.load_records`) and open `rastro studio --root`, instead of building an ad hoc database.
- Version 1.0.0: `docs/stability.md` lists the frozen public surface (`rastro.__all__`, job format version, run report schema id, CLI exit codes) and the deprecation policy.
- Documentation, ADRs and the repository templates were rewritten for 1.0: one tagline, GitHub private vulnerability reporting for security reports, and invented site names on `.example` hosts.
- `docs/issues.md`: `link_group_refused` now describes the refused comma selector group, `invalid_record` also covers evidence building and storage serialization, and `extraction_limit` also covers a nested list cut at its limit. The `tolerant_jsonld` issue is now `repaired_json`.
- Release process: patch releases can be tagged from a maintenance branch; see `docs/releasing.md`.
- `rastro.scrape` reports the acquisition ladder in the source's acquisition note.
- A recipe with page steps is written with `render = "browser"`, which the steps need.
- Page steps in a spider crawl run on the start page only; linked pages are extracted as they render.
- Studio: the session stealth checkbox is removed; capabilities for shared or AI-written input are per-origin switches under Connections, and a Hub choice to allow a permission for one site applies only to registry input for jobs whose every address is on that site, while Studio installs, previews or runs them (kept in memory for the session). `operator_trust()` accepts `untrusted=` to scope such grants.

### Deprecated

- `--allow-stealth`, `RASTRO_ALLOW_STEALTH` and `allow_stealth=`: your own jobs need no grant. The flag still grants stealth to untrusted origins; prefer `--allow-untrusted ORIGIN:stealth`.
- `allow_stealth=True` in the Python API now emits a `DeprecationWarning`.

### Fixed

- A job file with invalid JSON raises `ConfigurationError` naming the line and column, not a raw decoder exception; the command line still prints the `syntax_error` envelope.
- Lone UTF-16 surrogates in page JSON (a JSON-LD escape such as `\ud83d` without its low half) no longer fail a whole source: JSON-LD, embedded JSON and JSON parsing and discovery replace them with U+FFFD, valid pairs stay their real character, and a record that still cannot be serialized is rejected with the new `invalid_text` issue and counted in `rejected` instead of failing later (#151).
- JSON-LD blocks with raw newlines or other control characters inside strings are retried once with those characters escaped; recovered records retain provenance and report `repaired_json`. Malformed structure, duplicate keys and non-finite numbers remain rejected.
- Unbounded crawls no longer fail on installs without the browser extra: pacing falls back to a standard-library throttle with the same rules.
- Repair candidate checks use the extraction's observation clock, so results no longer depend on the day the tests run.
- The browser acquirer keeps a render's screenshot on the acquisition result, so Studio's guided flow can show the page with its highlight boxes.
- A rate limit (429) stops the acquisition ladder instead of climbing to a heavier level.
- The webhook notification carries hosts and counts, never URL paths.
- A multi-day schedule with a time of day (`every = "2d"`, `at = "08:00"`) waits the full interval instead of running daily.
- `rastro dataset changes --since` lists only records removed after that time.
- `rastro dataset export --format xlsx` drops every character XML 1.0 cannot hold instead of writing a file spreadsheet programs refuse.
- A solver plugin result whose body is not text counts as unsolved instead of failing the source.
- An unknown schedule time zone is a configuration error, not an internal one.
- A captured response whose path is too long for a recipe's capture pattern is no longer offered as a data source, and building a recipe from one raises `ConfigurationError` instead of an internal error.
- A repaired source reports the real repair origin when no model is configured.

### Security

- Prompt-injection and trust boundaries: model proposals, MCP tool input, registry installs and bundle imports run as untrusted input and cannot use stealth, solvers, robots overrides or other grants unless the operator names the origin and capability. Details in `SECURITY.md`.
- Capture patterns from recipes compile and match on the same linear-time regular expression engine as spider path patterns, so a shared recipe cannot cause catastrophic backtracking.
- Browser modes use egress profiles under the same destination checks as HTTP; crawl checkpoints keep full URLs in owner-only files; registry `card.json` and `health.json` are untrusted data; solver plugins run outside Rastro's DNS pinning and are operator-installed and grant-only. Details in `SECURITY.md`.

## [0.9.0] - 2026-11-17

Studio redesign, the agent configuration reference, the agent capability contract and the URL-first product work: bounded spider, scroll, registry URL, plain-language alerts. 0.8.0 was never tagged; its work ships here.

### Added

- Configuration reference: `rastro config [--output json|markdown]`, the generated `docs/configuration.md` and the MCP resource `rastro://configuration` list every job key, Python keyword argument, operator grant, environment variable and CLI flag with type, default, bounds, description and docs link, introspected from the models, signatures and parser. `rastro job template [--all]` prints an annotated job file that validates as printed. `rastro init --agent` snippets gain a configuration map and the rule to check the reference before writing an option; `rastro job validate` names the closest valid key for an unknown one. Tests fail when a field or flag lacks a description or the page is stale.
- Agent capability contract: `rastro capabilities [--output json|markdown|llms]`, `rastro init --agent claude|codex|generic [--format block|skill]`, the MCP resource `rastro://capabilities`, and generated `llms.txt` and `docs/capabilities.md`. MCP server instructions are URL-first and list every capability; tests fail when they go stale.
- Public adapter API: `rastro.fetch` / `rastro.fetch_async` return a `Fetched` result for one URL under an `AcquisitionPolicy` (same acquirer, robots, egress, budgets and byte limit as jobs; refusals are explicit outcomes), and `rastro.load_records(job, records, *, source_url, observed_at=None)` writes caller-built records into the job's dataset with job-load validation, identity, snapshot semantics and provenance (`method = "json"`, `locator = "adapter"`, `recipe_version = 0`). See `examples/adapter.py`.
- `JsonLdRecipe.item_type` accepts several types, e.g. `("Event", "MusicEvent")`.
- `embedded_json` recipe strategy (`EmbeddedJsonRecipe`) reads JSON from one HTML attribute or script, such as `input[name=GXState]::attr(value)` or `script#__NEXT_DATA__::text`, under the JSON limits and the untrusted-recipe selector policy; zero or several matches, invalid JSON or a missing path are explicit failures. Job JSON schemas regenerated.
- Spider sitemap seed: `[spider] seed = "sitemap"` reads each source as a sitemap.xml (or .xml.gz) under the same allowlist, robots, budget and byte caps, parses it without DTDs or entities, follows a sitemap index one level, and fetches its locs in document order within `max_pages`.
- `docs/issues.md` lists every issue code with its meaning and next step; a test keeps it complete.
- Bounded spider follows allowed links from URL sources with depth and page caps, same-host defaults, robots enforcement and a shared request budget.
- Bounded browser scroll stops when no new records appear or the step cap is reached; it does not enable stealth.
- Browser pages of one source reuse one context during a run, then close it; sources keep separate contexts.
- Stealth requests require the operator grant before any renderer starts, including custom renderers.
- `rastro recipe search` and `install --registry-url HTTPS_URL` consume a hostable static registry with SSRF checks, digest verification and candidate-only installation.
- Studio **Next page** setting: pick a recipe, type a next-link selector and a page cap (1–50); Studio writes the existing `pagination` fields (ADR 0026) after the untrusted selector policy, a digest check and a reparse that must show only that change. TOML and JSON jobs.
- Studio alerts and progress: extract, load, repair and promote answer with plain-language alerts for failure, open drift, stale sources, quarantine, a refused promotion and pagination caps; Health adds last success, why a source is stale, open drift, the last run's alerts and the exact `rastro job load ... --on-failure 'python notify.py'` command for the README hook. `GET /api/progress` reports the running operation, its job file and its real outcome; no page or scroll ordinal is invented and no body, query or secret is included. One operation at a time (`operation_in_progress`).
- **Fix this site** (formerly **Propose repair**) passes the configured Studio model to `repair_job_source`; promotion still needs a reason when auto-promote is off.
- Two switches, both off: **Auto-promote** writes only `[repair] auto_promote` on the opened job (digest and reparse checked), and Studio's **Fix this site** promotes only when a model (Studio `--model`, the job's `[model]`, or `[repair] model` with `RASTRO_MODEL`) can act as the semantic judge, otherwise it keeps the candidate and explains why; **Allow stealth for this Studio session** changes the process grant passed to preview, extract, load, repair and promote, is never written to a job, and is locked on when the process was started with `--allow-stealth` or `RASTRO_ALLOW_STEALTH`. `create_app(..., allow_stealth=)` and `rastro studio --allow-stealth` pass it; `preview_job` accepts `allow_stealth` (ADR 0034).
- Studio registry section: search a local registry directory, install a package as a candidate into the open job's recipe memory (never promotes, never edits the job), and publish an exported public bundle into a local registry without Git or push. HTTPS search and install use the same policy-bound fetcher as `--registry-url`, including operator grants for private networks, extra ports and robots overrides.
- Three synthetic public registry bundles under `examples/registry/` (listing, news card, product card) with offline consumer jobs; the README leads with the five-minute Studio path and a copy-paste failure hook.
- Studio job page controls for `[spider]` and `[scroll]`, bounded by the job models and written through the comment-preserving editor; scroll is shown disabled with its reason when the source renders over HTTP, and a run's spider outcome, host and path appear without page content.

### Changed

- Studio shell: design tokens with system, light and dark themes, an English/Spanish dictionary (English default), and views for Results, Sources, Alerts, Fix, Settings and New scraper; every token pair meets AA contrast.
- Studio Results: records load when a project opens, with search, source/status/date filters, 25/50/100 per page, JSON and HTML export; empty cells say why (not published, source failed, no reason reported), several prices show a summary with the tier count, and links or images from data never load on their own.
- Studio provenance panel: each field's page, observation time, extraction and recipe version; per-tier prices with availability; an *Imported snapshot* line for adapter records; a *Looks like instructions* marker with its explanation for flagged values.
- Studio Sources and Alerts: per-source status in words with last fetch, read/rejected counts, caps, warnings and one next step; alerts newest first, grouped by day, each with a sentence from its kind and issue code and one action; a missing or failing endpoint is shown as an error, never as an empty list.
- Studio Fix a site: **Find a fix** shows each proposal's checks and a before/after of how every field is read; **Apply fix** needs a reason and re-runs every check through `/api/promote`, **Discard** writes nothing, **Also write it to the job file** uses `/api/apply`; the automatic switch moves here and stays off by default; run, health and correction messages now come from the dictionary.
- Studio wizard and Home: New scraper is four steps (URL, fields, preview, name and save) with file names derived from the project name and folded as advanced; Studio opens on Projects with one card per project (health in words, last update, records, sources with problems, **View results**) and explains an empty folder, unrecognized SQLite files and loading.
- Studio Settings, registry, mobile and Spanish: Settings are folded groups showing only real CLI commands and flags; the registry is **Registry (URL)** with local publish; at 40rem and below tables become labelled cards and the provenance panel a bottom sheet; every JS-built message now comes from the English/Spanish dictionary.
- Run metrics record per source `rejected` records and the collection cap reached (`cap`, `cap_value`: pagination, spider, scroll and record limits) for every `rastro job load` and `rastro.load_records`; `rastro job stats` adds `rejected` and `capped_runs`. Dataset files move to schema 4 on their first write (older files still read; their rows report `null`, not 0); Rastro 0.7 refuses a schema 4 file, so keep a copy before writing if you may downgrade. Studio's `/api/sources` reads `read`, `rejected`, `cap` and `capped` from these metrics, so command-line loads show caps and rejections too, and `/api/projects` adds a readable `name` and the raw `dataset`.
- `evidence_retention_failed` carries `field` and `message = "sensitive_data:<category>"` when the sanitizer refuses a value, so the cause is visible without the value.
- `llms.txt` no longer says Rastro does not crawl (the bounded spider exists).
- robots.txt is fetched once per origin for each user operation (run, registry search or install, Studio or SDK URL start, `rastro new --site/--goal`, repair, review and promotion) and cached only in memory for that operation; a registry install no longer fetches it once per file.
- Studio reads the spider, scroll and next-page limits from the job models instead of copies in the page, and shows each link-following control on its own row.
- Studio no longer offers **Override failed gates**: `/api/promote` refuses `override_gates` with `gate_override_refused`. Operators who need it run `rastro recipe promote --override-gates` themselves (ADR 0034).

### Fixed

- `records[].evidence.page_url` keeps only scheme, host, port and path; userinfo, `;params`, query and fragment are no longer stored for later pagination, spider or scroll pages.
- The Studio URL preview expires after 10 minutes and is reused only for the exact URL it was fetched for.
- Studio preserves CRLF line endings when saving pagination and auto-promote settings in Windows TOML jobs. Synthetic registry inputs and rebuilds keep identical bytes across platforms.

### Security

- Prompt-injection defense: hidden page content (hidden elements, comments, templates, invisible characters) is removed before a page reaches a model. Values that look like instructions are flagged `instruction_like_content` in model contexts and MCP record payloads, never altered. Automatic promotion is refused for such candidates, which stay quarantined until an operator promotes them with a reason and `--override-gates`. See `SECURITY.md` and ADR 0035.

### Known limitations

- `embedded_json` is not produced by model proposals, discovery or repair relocation, and Mock Lab bundles do not support it yet.
- Browser-rendered jobs report `sitemap_unsupported`; the sitemap URL is named as the source, not read from robots.txt.
- `fetch` supports plain HTTP only; provenance uses `locator = "adapter"` rather than a new `Evidence.method` (persisted-format change deferred).
- Injection detection is a pattern list; `job_extract` MCP payloads carry no flags yet.
- `rastro init --agent` is POSIX-only: on Windows it fails closed (no race-free directory-relative writes); use `rastro capabilities --output markdown`.

## [0.7.0] - 2026-11-15

Bring your own AI: any model or agent proposes recipes and repairs while Rastro verifies. Operators also hear about drift and stuck state, not only hard failures; see [Monitoring and alerts](docs/operations.md) and ADR 0023. The release also adds JavaScript pages, sessions, pagination, scheduling, PostgreSQL datasets, recipe registries and operator network permissions.

### Added

- Studio new-scraper wizard starts from a URL: it fetches the page, suggests a recipe from examples, and writes a `kind = "url"` job with `allowed_hosts` so later runs scrape that address again. Uploading saved HTML is no longer required; a local page remains available for offline replay.
- Opt-in JavaScript page rendering (`pip install "rastro-engine[browser]"`): `[acquisition] render = "browser"` (or per source) renders with plain Chromium behind the same allowlist, DNS pinning, per-request destination checks, robots rules and budgets; `render = "stealth"` (Scrapling, `solve_challenges` included) only with the operator's `--allow-stealth`, `allow_stealth=True` or `RASTRO_ALLOW_STEALTH`. Render timing in metrics. ADR 0024 records the 2026-11-15 amendment of the earlier browser ban; no automatic proxy rotation and no paid CAPTCHA services.
- Run-local HTTP sessions (`[acquisition] session = true`) and conditional requests for dataset loads with an explicit `unchanged` outcome (`Outcome.UNCHANGED`); validators are bound to an inputs digest that includes the engine version, and paginated sources never send them. Dataset schema 2 adds `dataset_validators`; ADR 0030.
- Bounded recipe pagination: `[recipe.pagination] next` / `max_pages` follows next links within the source host and one request budget; a missing next link or a repeated page completes, an empty later page succeeds but is reported incomplete, and any other early stop is partial (exit 3); ADR 0026.
- Optional PostgreSQL datasets (`pip install "rastro-engine[postgres]"`, `[dataset] backend = "postgres"` with a DSN from an allowed environment variable): transactional snapshots and source health; operational state (locks, checkpoints, metrics) stays local. ADR 0025. Studio and MCP dataset views still assume SQLite.
- `rastro job watch job.toml [--once]` with `[schedule] every` / `jitter` and per-source `every`; a dataset lock (`<dataset>.lock`, `run_in_progress`, stale-lock takeover with pid and token, Windows-safe liveness) prevents overlapping loads, a live lock skips that tick, and an interrupted load resumes from `<dataset>.run.json`.
- Start from a goal or from known sites: `rastro new --goal TEXT` / `--site URL`, `new_job_from_goal`, `new_job_from_sites`; sources are proposed by SearXNG (`SearXNGSearchProvider`) or a model, each host needs explicit approval and at most three pages per approved host are fetched, then fields are proposed from repeated page structures with real-record previews (`propose_fields`, `FieldProposal`).
- Run metrics and cost limits: dataset schema 3 adds `run_metrics` (one row per source per load, newest 10,000 kept, counts and timings only); `rastro job stats [--since] [--by]` reports success rates, p50/p95 timings, freshness and coverage (SQLite datasets). `[model] cost_per_call` / `max_cost`, `--max-model-cost` and `--model-cost-per-call` (SDK `max_model_cost=`, `model_cost_per_call=`) cap model spend for job and operator models; an exhausted budget reports `model_budget_exhausted`, and a cap without a price is refused.
- Recipe Hub phase 2: bundle format 2 metadata (`id`, `version`, `author`, `license`, `visibility`, `compatibility`), computed trust indicators (`replay_verified`, `days_since_verified`, `has_sample`), and a local Git-directory registry with `rastro recipe publish`, `search` and `install` (secret scanning, private bundles refused, engine and source-pattern compatibility checked on install).
- Operator network permissions, never grantable by a job file, bundle, MCP argument or model: `--allow-private-network` (RFC 1918, ULA, loopback, CGNAT; link-local and cloud metadata addresses stay denied), `--allow-port N`, `--ignore-robots HOST` (logged as `robots_overridden`), with `RASTRO_*` and SDK equivalents; and `[[acquisition.auth]]` request headers whose value comes from an allowed environment variable, sent only to that exact host over HTTPS. ADR 0031.
- Bring your own AI: `rastro.model(spec=None, **options)` returns a `ModelPort` from a spec string (`http:<model>@<base_url>`, `ollama:<model>`, `command:<argv>`), a provider name with options, a Python function or a `ModelPort`; with no spec it reads `RASTRO_MODEL` (`RASTRO_ALLOW_REMOTE=1` / `RASTRO_MODEL_LOCAL=1` record consent). Three generic transports, no provider special-cased: OpenAI-compatible HTTP, any local command (argv without a shell, empty-string arguments allowed but not an empty program, prompt on stdin, bounded stdout and deadline, `json_path` for wrapper objects, only `PATH`, `HOME` and listed variables passed, stderr discarded, `allow_remote=True` or `local=True` required) and an in-process callable. `docs/intelligence.md` documents presets for Claude Code, Codex CLI, Gemini CLI, `llm`, Ollama, LM Studio, OpenRouter and any gateway, each with privacy notes; ADR 0022.
- Job files accept `provider = "http"` (`"openai_compatible"` stays accepted). `provider = "command"` parses (`CommandProvider` is exported and `job.schema.json` is updated) but a job file can never start a program: running such a job fails closed, and a command model comes only from the operator (`rastro.model`, `--model`, `RASTRO_MODEL`).
- Model prompts small models can follow: numbered rules with a JSON example, field types, required flags and `Field(description=...)`; pages over the context budget are sent as a bounded, deterministic, untrusted-labelled page digest (`rastro.application.page_digest.digest_page`) instead of failing; repair prompts carry the serving recipe, a safe failure category and up to two clipped sample records from the last retained success, and ask for selectors that keep matching the previous structure (CSS unions allowed). `ModelRequest.response_schema` / `output_schema` let a request ask for a schema other than a recipe proposal.
- `rastro.scrape(..., model=...)` accepts any `ModelPort`, a `rastro.model` spec or `"env"` (`RASTRO_MODEL`): with no selectors and no structured data the model proposes a recipe from the field names, types and descriptions, verified by extraction and the schema and remembered with `memory=`; when relocation finds no candidate that passes, the model proposes repairs (at most two calls by default) gated exactly like relocated ones. `SelfRepair.origin` (`relocation` or `model`) and `model_calls` report it; both are omitted without a model.
- Semantic gate: with a model, every would-be automatic promotion (scrape and job repair, relocated or model candidates) is judged per changed field from its name, type, description and earlier values, with candidate values clipped and labelled untrusted. Verdicts appear in `indicators.results` (`kind = "model_judge"`) and `semantic_confidence`; `mismatch` quarantines (`semantic_mismatch`), `unsure` promotes only when every deterministic gate is at its strictest, and a failed answer quarantines (`semantic_judge_failed`). Without a model the indicator stays unassessed.
- Model repair composes each changed selector as `"<serving>, <proposed>"` so historical samples keep parsing, and sends the serving recipe in the prompt; the plain proposal is gated only if the union fails.
- `repair_job_source(..., model=, allow_remote=)`, `rastro recipe repair --model SPEC [--allow-remote]` and `[repair] model = true` (use `RASTRO_MODEL` when the job has no `[model]` table); `SourceRepair.origin` reports where candidates came from; `job.schema.json` gains the optional `repair.model`.
- `rastro.suggest(..., model=...)` makes `examples` optional; `rastro suggest` and `rastro new` take `--model SPEC`, `--allow-remote` and `--field NAME`, with `--example` optional when `--model` is set. Model recipes must still reproduce every given example and extract `records_min` records; `FieldConfidence.method` gains `model`.
- `ModelRequest.response_schema` (optional) and `ModelRequest.output_schema` let a request ask for a JSON shape other than a recipe proposal; the Ollama and OpenAI-compatible providers constrain decoding with `output_schema`.
- MCP agent in the loop: the client's own model proposes recipes and judges meaning while Rastro verifies and stores, with no API key and no provider-specific code. `repair_context` (failing recipe, field definitions, last success values, current issues and a bounded, untrusted-labelled page digest, the same one model prompts use), `recipe_propose` (strict recipe validation, each changed selector composed with the serving one as model repair does, the same recipe CI as repair, stored as a candidate with equal candidates reused, returned with old and new values side by side; never promotes, even with `[repair] auto_promote = true`, because the calling agent is the semantic judge), `semantic_review` (old and new values per changed field), `suggest_context` and `recipe_try` (author a recipe for a new page without examples). The server instructions describe the workflow and ask the agent to check meaning before `recipe_promote`.
- Studio **Ask AI**: with `rastro studio --model SPEC` or `RASTRO_MODEL`, the wizard asks the model for a recipe from field names alone (kept only if it extracts valid records) and the repair view runs repair with the model (`repair_job_source(..., model=...)`: relocation, then model proposals composed with the serving selectors, the same gates and the semantic judge; promoted automatically only when the job sets `auto_promote` and the judge agrees). `--allow-remote` is required for non-loopback or hosted models, and Studio then says that page content may leave the machine; the model receives a bounded page outline, not the page.
- `benchmarks/ai_repair/`: an offline benchmark of 28 synthetic before/after pages, run with deterministic Rastro, Rastro plus a fake `ModelPort` (scripted oracle: correct, backward-compatible, adversarial, and escalation only after deterministic failure), and a hand-written Scrapling + Pydantic baseline. It reports correct, wrong (silent) and refused outcomes, model calls and Wilson 95% intervals; the oracle is not a quality claim. Recorded local runs: `qwen3:4b` and `llama3.2:3b`.
- `docs/ai.md` ("Bring your own AI") explains how to connect any provider (local, API, subscription CLI, MCP agent), what a model does in each flow, privacy, call budgets and the measured results; `docs/ai-evidence.md` records the benchmark runs. README and overview pages now say Rastro works offline and deterministically and that a connected model proposes while Rastro verifies.
- Open drift: an elevated or high drift on a successful `rastro job load` stays open, keeps the drift baseline at the run before it and is reported on every load until acknowledged. `JobLoadResult` gains `status` (`success`, `degraded`, `partial_success`, `failed`, `storage_error`) and `open_drift`; the job-load JSON Schema is updated.
- New exit codes: `4` (`JobExitCode.NEEDS_ATTENTION`) for a successful load with open drift and for `rastro health` / `rastro dataset status` findings; `5` (`JobExitCode.STORAGE_ERROR`) when dataset storage fails after the job ran.
- `rastro job ack-drift job.toml --source N|--all` and `rastro.acknowledge_job_drift`; promoting a repair and `rastro recipe rollback` clear open drift and rebaseline the source; if that dataset write fails after the recipe changed, the `storage_error` hint names `rastro job ack-drift`.
- `rastro health job.toml [--output table|json]` and `rastro.job_health`: last success and attempt, stale reason, open drift and quarantined candidates per current source; exit 0 healthy, 4 needs attention. Schema in `docs/schemas/health.schema.json` (`rastro job schema --target health`).
- `rastro job forget-source job.toml --url URL` and `rastro.forget_job_source` delete a removed source's status, drift history, open drift and repair samples; only samples of that URL under the job's regions are deleted, so another job sharing the dataset keeps its samples.
- Operator-only failure hook: `--on-failure CMD` on `rastro job load|run` and `RASTRO_ON_FAILURE` run a command without a shell, with a timeout and a content-free JSON summary on stdin; a hook failure never changes the exit code.
- Failed sources store a content-free `detail` (`robots_denied`, `http_403`, `http_429`, `timeout` or the issue code) next to `reason`; the load report shows it as `sources[].reason`. Optional `SourceFailureRecorder` port.
- A `missing_data` source suggests `next_action = "rastro recipe repair"`.
- Each non-successful source logs a `source_failed` WARNING with index, host, outcome and detail, visible without `--verbose`; no paths, queries or content.
- MCP `drift_status` reports `open_drift` and `orphaned_sources`; Studio shows open drift instead of green.

### Changed

- The dataset SQLite schema is now 3 (validators and run metrics); 0.7 files from earlier builds migrate on first write and older Rastro versions refuse them.
- The `CI required` gate includes the PostgreSQL dataset contract job.
- Recipe proposal prompts include `field_hints` (each field's type and description, clipped) so models map fields by meaning; docs/intelligence.md lists what is sent.
- With a configured model, job repair asks it only when no deterministic candidate would pass (escalation, not competition), so a model candidate can no longer quarantine a passing relocation as `competing_candidates`; an invalid model selector rejects that proposal (`invalid_model_candidate`) instead of aborting the repair.
- Internal: `RepairCandidateGenerator` no longer takes `model` or `model_enabled`; model proposals live in `application/model_repair`.
- `rastro dataset status` exits 4 when a source is stale or has open drift, and lists `open_drift`. Scripts that treated any non-zero exit as a crash should treat 4 as "needs attention".
- `rastro job load` exits 4 instead of 0 while drift is open. Run `rastro job ack-drift` after reviewing, or promote a repair.
- A dataset storage failure after the job ran, including an unreadable existing dataset when `[[consistency]]` rules read its history, prints the per-source report, runs `--on-failure` and exits 5 instead of printing nothing and exiting 2. Error objects for dataset storage failures use the new code `storage_error` instead of `configuration_error`.
- Load reports, MCP `drift_status` and Studio consider only the job's current sources; a removed or renamed source no longer stays stale forever.
- Drift storage moves to schema 2 (adds `drift_open`, `drift_rebase`). Schema 1 files upgrade on the first drift write; 0.6.0 refuses a schema 2 file explicitly.
- A clock that goes backwards no longer fails every run with "Out-of-order": source status, records, drift runs and repair samples clamp to the last recorded time.

### Fixed

- Model repair candidates (scrape, job repair, Studio Ask AI) and MCP `recipe_propose` candidates must pass the relocation value checks (`value_type_changed`, `value_shape_changed`, `value_matches_other_field`) before the semantic judge; a failure rejects the candidate and feeds the retry. The judge still reviews every would-be promotion, relocated ones included, and can only veto; without a model, repair makes no calls. On the AI repair benchmark a swapped recipe with an approve-all judge now promotes 0/28 wrong (6/28 before); the correct oracle still recovers 23/28 with 44 model calls.
- The semantic judge's response schema keeps `$defs` at its root, so schema-enforcing providers such as Ollama accept it; before, every judge call failed and every automatic promotion with a model was quarantined as `semantic_judge_failed`.

### Security

- The 0.6.0 `[model]` trust check now covers every model surface, so a job file can no longer exfiltrate secrets or start programs through `[model]`: a job-declared model uses a non-loopback endpoint, an endpoint outside the local model ports (11434, 1234, 8000, 8080), `base_url_env` or `allow_remote = true` only with operator consent (`--allow-remote` on any command including `rastro mcp` and `rastro studio`, SDK `allow_remote=True` on `run_job`, `run_job_async`, `run_job_file`, `load_job_file`, `repair_job_source(s)`, or `RASTRO_ALLOW_REMOTE=1`); a `provider = "command"` model is always refused, because a command model comes only from operator configuration (`rastro.model`, `--model`, `RASTRO_MODEL`); `api_key_env` and `base_url_env` must match `RASTRO_*` unless allowed with `--allow-env NAME`, `allow_env=[...]` or `RASTRO_ALLOWED_ENV`, and the refusal never echoes the name. Runs fail closed before any request, naming the flag. **Migration:** 0.5 jobs with a remote model need the operator flag, and keys such as `OPENAI_API_KEY` need `--allow-env OPENAI_API_KEY` or a `RASTRO_` name (security audit H1).
- The trust flags `--allow-remote`, `--allow-egress` and `--allow-env NAME` may appear before or after the subcommand and apply process-wide, so `rastro mcp` and `rastro studio` pass them to every job they run.
- MCP `recipe_propose` and `recipe_try` and every model proposal (scrape, suggest and repair escalation) follow the CSS selector cost policy that 0.6.0 applies to job files, Hub bundles, `scrape_file` and Studio edits.
- One consent vocabulary: `RASTRO_ALLOW_REMOTE` is the only remote opt-in, for `RASTRO_MODEL` as for job-declared models (the pre-release `RASTRO_MODEL_ALLOW_REMOTE` is removed), and every boolean opt-in (`RASTRO_ALLOW_REMOTE`, `RASTRO_ALLOW_EGRESS`, `RASTRO_MODEL_LOCAL`) accepts `1`, `true` or `yes`; before, `RASTRO_ALLOW_EGRESS` accepted only `1`.
- MCP `recipe_promote(override_gates=true)` is refused unless the operator starts the server with `rastro mcp --allow-override-gates` (`build_server`/`serve(..., allow_override_gates=True)`): page content can prompt-inject the client agent, which could otherwise activate a quarantined candidate, including one the semantic judge vetoed. The error tells the user to run `rastro recipe promote --override-gates` themselves; the CLI is unchanged. `docs/mcp.md` documents what `recipe_apply` writes.
- Studio Ask AI reports the model's real remoteness: with `RASTRO_MODEL` naming a hosted endpoint and `RASTRO_ALLOW_REMOTE=1` but no `--allow-remote`, `/api/ai` said `remote: false` and the terminal said page content "stays on loopback models". The built-in transports now expose `remote` (`rastro.model_factory.is_remote`), and Studio uses it; the flag only speaks for a custom `ModelPort`.
- Dataset and recipe-memory files and their SQLite sidecars are created 0600 on POSIX, and existing files the user owns are tightened when opened; datasets hold sanitized page samples.
- `.gitignore` keeps local agent, editor and tool state (coding-agent and IDE folders, `.mcp.json`, `*.local.json`) and credential files (`.envrc`, `.netrc`, `.pypirc`, private keys) out of the repository and the sdist; none were ever committed.

## [0.6.0] - 2026-11-12

First public release on PyPI as `rastro-engine`. Versions before 0.6.0 were never published.

### Added

- `Issue.message` (optional) gives Pydantic's built-in error type and message for `validation_error` issues, never the input value or custom validator text; it is omitted from serialized output when absent, and the job-run and job-load JSON Schemas gain the field.
- Tag-triggered release workflow: builds the sdist and wheel, checks them with `twine check --strict`, verifies the tag matches the package version, smoke-tests the installed wheel, publishes to PyPI through Trusted Publishing with attestations and creates the GitHub release from this changelog (Release Evidence).
- CI `package` job installs the built wheel with and without the `http`, `mcp` and `studio` extras and runs the CLI smoke checks (Release Evidence).
- `docs/stability.md` defines the public surface and the deprecation policy; `docs/ai-agents.md` and a root `llms.txt` describe how AI agents use Rastro.
- Studio is a complete local GUI: a new-scraper wizard (pick a saved page, give example values, review the suggested recipe with per-field confidence, create the job file), Extract (writes nothing) and Load into dataset with recipe origin and version per source, health with stale status and optional drift, repair proposals with an old-versus-new selector diff and gate results, promotion with a recorded reason (overriding failed gates warns and records it), writing the serving recipe back to the job file, and a paginated dataset browser with stale markers and JSON or HTML download. JSON job files are listed next to TOML ones. Every new action is a token-protected, exact-origin POST confined to the Studio folder; file edits carry the file digest. The app is split into per-area routers and the page into dependency-free ES modules, with light and dark themes.
- `rastro.scrape(target, schema, ...)` and `rastro.scrape_async` extract HTML text, a file or a URL into validated Pydantic models in one call; `rastro.css("...")` declares a field's selector on the model, `__records__` or `records=` selects records, and `ExtractionResult.items` lists the validated instances.
- `scrape(..., memory="recipes.db")` remembers the working recipe and a sanitized success sample; when the remembered recipe stops matching, `repair="propose"` (default) stores gated offline repair candidates and `repair="auto"` promotes one that passes every gate and returns its result. `ExtractionResult.repair` describes what happened; it is omitted from serialized output when no repair ran, and `job-run.schema.json` gains the optional field. Before promotion, relocated fields must keep their retained values' type and shape and must not take another field's values (`value_shape_changed`, `value_type_changed`, `value_matches_other_field`); an identical stored candidate is reused instead of adding a version.
- `docs/quickstart.md`, `examples/scrape.py` and `examples/self_repair.py`.
- `rastro.suggest(html, schema, *, examples, url=None)` builds a verified CSS recipe from a saved page and one or two example values per field, deterministically and without AI; it returns `Suggestion` (`recipe`, `preview`, per-field `confidence`) or raises `SuggestionError` naming the examples it could not reproduce.
- `rastro suggest page.html --example FIELD=VALUE ...` prints the suggested recipe as JSON or a preview table; `rastro new job.toml --from page.html --example ...` writes a complete job file with conservatively typed fields, never overwriting an existing file.
- A `css` recipe field prefixed `expand:` turns a card with several values into one record per value, with per-value provenance and `max_records` enforced; relocation repair refuses it.
- `rastro recipe prune --store FILE --older-than DAYS [--dry-run]` deletes old recipe versions but never a serving one; `prune` always keeps the newest version of each key so version numbers are never reused.
- A JSON-LD recipe field may list 1 to 4 JSON pointers; the first that resolves wins and the locator records it; a JSON null falls through to the next pointer.
- Public Python API for datasets and repair: `rastro.load_job_file`, `rastro.repair_job_source`, `rastro.promote_job_recipe` and `rastro.apply_job_recipe`, with the `RepairReport`, `SourceRepair` and `RecipeSelection` result types.
- `rastro recipe apply job.toml --source N` writes the serving recipe from `recipe_memory` back into the job file; TOML text outside that recipe's tables is kept, the write is atomic and refuses symlinks.
- `rastro recipe promote --override-gates --reason "..."` activates a quarantined candidate; the lifecycle history records `gates overridden: <reason>`. Rejected candidates are never activated.
- Job run and load reports state per source whether the job file's recipe or the serving recipe in memory was used (`recipes` / `recipe_origin`, `recipe_version`); the published job-run and job-load JSON Schemas are updated.
- `rastro job preview`, Studio preview and the MCP `job_preview` tool work for jobs with `recipe_memory`: the store is opened read-only (never created or upgraded) and a stored serving recipe newer than the job file's recipe is used.
- `SQLiteRecipeRepository(path, read_only=True)` reads an existing store without creating, migrating or writing it.
- `rastro recipe promote` adopts an imported candidate on a store with nothing serving, so export, import, promote and `job load` work on a fresh store; the candidate gate still runs on the job's own page.
- The MCP server covers the whole job lifecycle: `job_extract` (like `rastro job run`, no dataset write), `job_load` (with `drift`), `drift_status`, `recipe_history`, `recipe_repair` (proposes; promotes only when the job sets `[repair] auto_promote = true`), `recipe_promote` (requires a reason), `recipe_apply`, `suggest_recipe`, `job_new` (never overwrites) and `scrape_file` (one-off extraction from a workspace file, no network). Tools declare bounded JSON Schema inputs and read-only/destructive/idempotent hints, outputs are bounded with `truncated`, and the server sends workflow `instructions`.
- `rastro new --recipe-memory FILE`, MCP `job_new(recipe_memory=...)` and the Studio wizard's "Recipe memory file" add `recipe_memory` to the new job, so a job created without the command line can be repaired; MCP `job_new` accepts `identity` as a field name or a list.
- `rastro new --allow-partial` writes a job even when the recipe preview is only a partial success; `Suggestion.status` reports the preview outcome.
- `rastro recipe show job.toml --source N [--version V]` prints a stored recipe version as JSON, read-only; `recipe compare` reports `changed_settings` (such as `record_selector`); `rastro recipe --help` describes every subcommand.
- Rejected repair candidates report `blocking` issues: record index, field and issue code, never values.

### Changed

- The MCP server reports Rastro's package version as its `serverInfo.version` instead of the MCP library's.
- Package classifier is `Development Status :: 4 - Beta`; README rewritten for external users.
- MCP `recipe_repair` declares `destructiveHint: true`, because a job with `[repair] auto_promote = true` lets it change the serving recipe; `dataset_records` output is bounded like the other tools, and every MCP output also caps objects at 100 keys with `truncated`.
- With `recipe_memory`, `job run`, `job load`, `run_job` and `run_job_file` use the serving recipe in memory when its version is higher than the job file's recipe version, so a promoted repair applies to the next run. Previously `job run` kept the stale file recipe and `job load` used memory whenever it had a serving recipe; see ADR 0021.
- Repair completeness counts optional fields only as often as the stored success samples filled them, so an optional field that is legitimately empty in some records no longer quarantines a correct repair.
- Code that imported `rastro.cli_dataset.load_job_file`, `rastro.cli_repair_context` or `rastro.cli_repair_storage` should use `rastro.load_job_file` and the other public functions; those internal modules moved under `rastro.application`.
- `rastro recipe import` reuses a stored version whose recipe equals the bundle's, and its JSON output gains `"reused": true|false`.
- Studio lists only job files (`.toml` or `.json` with a top-level `format_version`); repair reports and other files are no longer shown.
- MCP `job_run` is renamed `job_load`, matching `rastro job load` (it writes the dataset); `job_run` stays as a deprecated alias until 0.7.0. Use `job_extract` for the `rastro job run` behaviour. MCP tool errors, including argument validation errors, are always `{"error", "message", "location", "hint"}`; the `mcp` extra now requires `mcp>=1.10`.
- `rastro studio` and `rastro mcp` report a missing optional extra as `{"error": "missing_extra", "message", "location": null, "hint": "pip install 'rastro-engine[<extra>]'"}`.
- A job `[dataset]` without `match_fields` now matches records on its `identity` fields only and flags no uncertain matches; previously it assumed event fields named `title`, `venue` and `start_time`. To keep near matching, declare `match_fields` explicitly. Errors in `match_fields`, `identity` and `approve` now report their TOML key as `location`.
- `rastro new` refuses (exit 2, listing the preview issues) when the suggested recipe's preview is not a full success; pass `--allow-partial` to write it anyway.
- `rastro.scrape("page.html", ...)` raises `ConfigurationError` asking for `pathlib.Path(...)` instead of parsing the filename as HTML.
- `rastro recipe import` reuses only an equal candidate or serving version; an equal retired version gets a new candidate version.
- The recipe-version conflict error names the next free version to set in the job file (ADR 0021).
- `RepairPolicy` is no longer exported from `rastro`; repair thresholds stay in the job's `[repair]` table. The `suggest` test seam is now the private `_suggester` keyword.
- `recipe repair` and `recipe promote` share one evaluator: hard validation, replay or consistency failures reject before ambiguity is considered, and a rejection from either command retires the version with a `Recipe CI: reject` lifecycle event. `--override-gates` bypasses quarantine thresholds only; on a rejected candidate it exits 2 with an error that says so.
- `recipe repair` reuses a stored candidate (or a still-rejected retired version) with the same definition instead of allocating a new version each run.
- `recipe apply` on a job whose sources share the job-level recipe adds a `[sources.recipe]` table for the repaired source only, instead of refusing.
- `recipe promote` retires a rejected candidate only when the current page is usable; on a page where neither the candidate nor the serving recipe finds records (consent wall, maintenance page) the candidate stays a candidate and the report notes `page_not_usable`. Promotion reports note `candidate_retired` when a version was retired.
- Repair reuse prefers the newest equal candidate over a retired twin and marks reused comparisons with `"reused": true`.

### Fixed

- `Issue.message` is limited to Pydantic error types whose message is fixed or quotes schema constraints; types that can echo input, such as `union_tag_invalid` (the discriminator value), `timezone_offset` and parser `error` details, carry no message.
- `rastro.suggest` tells a value the selector found but the schema type rejects (for example "59.90 EUR" for a `Decimal` field) apart from a value that was not reproduced: `SuggestionError` names only the failing field, its type and the page text's length and shape, and suggests a string field or a `BeforeValidator`; partial results note such fields in `confidence`. The comment written by `rastro new` says `rastro job run` previews without writing and, with a dataset, points to `rastro job load`.
- `rastro job load --drift` reports a total extraction failure (for example a record selector that no longer matches) on a source that succeeded before as `risk: high`, `score: 1.0` with an `extraction_failed` signal on field `*`, instead of `low`/`0.0` when the earlier success was loaded without `--drift`; `job-load.schema.json` gains the indicator.
- Self-repair (`rastro.scrape` and `rastro recipe repair`) relocates a renamed record container when some records lack an optional child, such as one card without a stock label: rows may omit children of the most common row shape (matched by tag and class, keeping at least half), while extra children, other classes or other paths still count as different containers. Relocated field selectors use tag+class steps instead of positions and never target an element another field reads, so a thinned card cannot hand a field its neighbour's value. Optional fields now need three quarters of the fill rate the success sample showed instead of the same rate.
- `rastro.scrape` strips text in `rastro.css` fields like jobs do (NBSP included); a whitespace-only value in a required field is a validation issue instead of an empty string.
- `rastro.scrape` raises `ConfigurationError` when `schema` is not a Pydantic model class.
- `rastro job load` names a dataset folder that does not exist (`location: "dataset.path"`) instead of `Dataset operation failed`; the Studio wizard stores the dataset path relative to the job file and refuses a missing folder or a path outside the job's folder.
- The MCP tools reject an absolute path such as `/etc/passwd` as an absolute path instead of reporting a symlink.
- Job field values are stripped of surrounding whitespace (NBSP included) before validation; a blank required value is a validation error instead of a successful `""`.
- `rastro job load` no longer exits 2 after writing rows when a field holds a relative URL: the retained repair fixture now replays exactly. A repair-evidence retention failure after the rows are committed is reported as `evidence_retention_failed` on that source, with the load's exit code unchanged.
- Every CLI error on `stderr` now has `error`, `message` and `location`, plus an optional `hint`. Commands other than `rastro job` used to print only `{"error": "invalid_input", "hint": ...}`. `rastro dataset export` and `status` on a missing file name the missing path. `rastro studio` and `rastro mcp` without their extra print `missing_extra` with the `pip install` command in `hint` (it used to be in `message` for `mcp`).
- `rastro demo` prints its price as an exact decimal string (`"12.5"`), like every other JSON output.
- The HTTP `User-Agent` reports the installed package version instead of a fixed `0.1` (transparent contact metadata, ADR 0002).
- File targets of `rastro.scrape` are opened non-blocking, so a FIFO is refused instead of hanging.
- Job runs, loads and previews refuse a symlinked `recipe_memory` path; run and load open a current-schema store read-only while choosing recipes, and a pinned `recipe_version` is reported only when the store holds it.
- `rastro recipe apply` refuses a symlink in any directory between the working directory (or, for a job outside it, the job's own directory) and the job file, and atomic writes sync the parent directory on POSIX.

### Security

- A job file can no longer send a model key or page content to an endpoint its author chose: a job-declared remote model needs the operator's `RASTRO_ALLOW_REMOTE=1`, and `[model]` `api_key_env`/`base_url_env` must be `RASTRO_*` or listed in `RASTRO_ALLOWED_ENV` (security audit H1).
- A job file is configuration: a proxy it declares in `[acquisition.egress_profiles]` is used only with operator opt-in (`--allow-egress` on `rastro job run|preview|load`, `rastro recipe repair|promote`, `rastro mcp` and `rastro studio`; `allow_egress=True` in Python; or `RASTRO_ALLOW_EGRESS=1`), and its `username_env`/`password_env` must be `RASTRO_*` names or listed by the operator (`RASTRO_ALLOWED_ENV`, `--allow-env NAME`, `allow_env=`). Before, a hostile job could send any environment variable, such as `GITHUB_TOKEN`, to its own proxy as `Proxy-Authorization`. Jobs without egress profiles are unchanged. Proxy credential references must be uppercase, as documented. Migration: rename proxy credential variables to `RASTRO_*` or allow-list them, and pass `--allow-egress` to jobs you trust.
- Recipes in job files, Hub bundles (checked before the replay), MCP `scrape_file`, Studio selector edits and model proposals refuse CSS selectors with `:has()`, sibling combinators (`~`, `+`), more than 8 chained compounds, more than 8 alternatives or pseudo-class nesting deeper than 2. A 48 KB bundle using `p ~ p ~ p ~ b` took 105 s of CPU to import. Hub bundles give integrity, not authenticity.
- Counting the elements a selector matches is linear: 200,000 sibling matches took minutes and now take under a second.
- The MCP `job_preview` tool is no longer annotated `readOnlyHint`: it writes nothing, but a job can fetch URLs or call a model.
- The source distribution no longer ships `benchmarks/recovery/sealed/` (ADR 0011).
- `rastro new`, MCP `job_new` and Studio create resolve the dataset and `recipe_memory` paths against the job folder and refuse either one when it is the job file, the page or the other (`./data.sqlite` and `data.sqlite` count as one file).
- Studio refuses a `POST` without an `Origin` header even with a valid token; GET requests are unchanged.
- Studio requires the session token on every `/api` request, reads included (job and page lists, connect commands); the page and its static files stay public. Saving a selector and writing a serving recipe compare the expected digest with the bytes being replaced inside the write step, so a file changed after the check fails with `file_changed` instead of being overwritten. Dataset pages and downloads cut field and identity values longer than 2,000 characters and mark the record `truncated`.

## 0.5.0 - 2026-11-11

Not published. Studio, the MCP server and recipe bundles; see [ADR 0020](docs/decisions/0020-studio-mcp-hub-minimal.md).

### Added

- `rastro mcp` serves job validate, preview and run, dataset records and recipe listing to agents over stdio MCP, behind the optional `mcp` extra and a workspace root.
- `rastro recipe export` and `rastro recipe import` share recipes as bundles (recipe, schema, sample, expected records, hashes) verified offline before storage (see `docs/hub.md`).
- `rastro studio` (optional `studio` extra) serves a loopback-only page to validate, preview, correct a selector, accept and copy connect commands for a job; see `docs/studio.md`.

## 0.3.0 - 2026-11-11

Not published.

### Added

- Localized date and datetime job fields (`localized = true`) parse Spanish listing text using the job region's timezone; a datetime without a time fails explicitly. Relative links resolve against the source URL.
- CSS field expressions may read one page-level node with the `document:` prefix (exactly one match, with provenance).
- `rastro dataset export --format html`, plus `--sort-field`, `--upcoming` and `--timezone`; daily scheduling examples in `docs/scheduling.md`.
- Automatic repair relocates a renamed or moved record container from the last successful snapshot, proposes an "old, new" selector union, and quarantines heterogeneous matches; measured on `benchmarks/repair_suite`.
- The events example adds an offline three-venue job (`examples/event-agenda/sample/v03/`).

### Removed

- Live-site pilot material (the live job, pilot reports and the agenda publication) moved out of the repository.

### Fixed

- Job loads gate approved observations on declared consistency rules before storage; inconsistent sources retain stale rows, report rule results and a repair action, and exit 3.
- Acquisition falls back across validated DNS addresses on connection failure only, within the same timeout and request budget, and never through a proxy.
- Blank text nodes no longer count as field matches; uniqueness is decided on elements.

## 0.2.0rc1 - 2026-11-08

Not published.

### Added

- Measured opt-in dataset drift with named field evidence, deterministic risk and retained-table effects (#62; ADR 0018).
- Offline repair candidates from Scrapling relocation, fallbacks and discovery, plus explicitly enabled bounded models; recipe CI replays sanitized last-success and Mock Lab fixtures, checks schema, provenance and generic consistency rules, and promotes only a unique candidate meeting configured thresholds.
- `rastro recipe repair|promote|compare|rollback`, machine-readable comparisons and per-run repair reports; failed/quarantined candidates keep the serving recipe and dataset records.
- Optional job `[[consistency]]` and `[repair]` fields, published schemas, and latest-success relocation/sample tables in the dataset file with an independent version row (ADR 0019). Existing jobs without these fields keep their validation output.
- Separate SQLite dataset with approved fields, provenance, origins, stable identity, update/missing/uncertain outcomes and failure retention (#56; ADR 0017).
- Declarative `[dataset]` configuration and read-only `rastro job preview` with observed values, provenance and approval decisions (#55).
- Events agenda example with synthetic offline v1/v2 samples (#59).
- `rastro job load`, per-source change counts and stale keys with last-success times, plus labeled `rastro dataset status` and JSON/JSONL export; shipped upcoming-events SQL and the executed README path (#61). The sample now includes synthetic stable performance IDs for repeat/reschedule proof; live jobs retain their original schema. Optional `dataset.match_fields` configures generic near-match field names.
- Recipe lifecycle states (`candidate`, `active`, `degraded`, `retired`), the `RecipeLifecycle` port (`save_candidate`, `transition`, `rollback`, `history`, `next_version`), and machine-readable lifecycle history. Candidates do not displace the serving version; rollback restores a previously served version (#46; ADR 0015).
- Mock Lab structural mutations: seeded, reproducible HTML and JSON operators for every mutation category (reordering, class changes, wrapping, missing fields, duplicates, empty containers, changed labels, pagination), each reporting machine-readable changes or an explicit no-target reason, composed by a versioned `MutationPlan` whose outputs carry the synthetic label. `lxml` and `cssselect`, already installed with Scrapling, are now declared dependencies (#48; ADR 0014).
- Synthetic failure scenarios with explicit outcomes and versioned labels (#40).
- Instant Mock Lab fixture bundles: labeled, sanitized, reproducible and replayable offline (#45; ADR 0014).
- Top-level Mock Lab fixture exports and support for extracted opaque product values while scrubbing structural attributes and URL paths.

### Changed

- With recipe memory configured, `job load` uses the lifecycle's serving version after bootstrap, so promotion/rollback controls the next load; `job run` and SDK explicit-recipe precedence are unchanged. Dataset files gain additive `relocation_*` tables; no recipe-memory migration. The dance example's consistency rules now live in its job files instead of `consistency.toml`.
- SQLite recipe memory migrates to schema 2 on first open through a forward-only transaction. Existing 0.1.0 payloads keep loading: the highest version per key remains active and older versions become retired rollback targets. Version 0.1.0 cannot read migrated files; back up before upgrading. `RecipeRepository` remains unchanged from 0.1.0 (#46; ADR 0015).

## [0.1.0rc1] - 2026-11-03

Release candidate tagged on `main`; not published to a package registry. First Rastro Engine release: schema-first, verifiable extraction with optional policy-aware HTTP acquisition, structured JSON responses, explicit egress profiles, SQLite recipe memory and optional bounded Ollama assistance.

### Added

- Declarative job files (`format_version = 1`) in TOML or JSON with closed field types, deterministic dynamic `JobRecord` schemas and fingerprints, path containment and environment-only credential references, non-widening HTTP host allowlists, `rastro job validate|run|schema` with distinct exit codes (`0`/`1`/`2`/`3`) and `untrusted_source_data` labelling, and committed JSON Schemas under `docs/schemas/`. See ADR 0013.
- Job files are parsed strictly (no string-to-bool or string-to-number coercion, including inside `acquisition`, `limits` and `inference_policy`), `string` patterns are checked with Pydantic's regex engine, `*_env` references must be uppercase, `openai_compatible` `base_url_env` requires `allow_remote = true`, `rastro job validate` redacts `*_env` values, and every `rastro job` configuration or usage error is one JSON object `{"error", "message", "location"}` on stderr with exit code `2`.
- Deterministic discovery also matches exact declared Pydantic field aliases (`Field(alias=...)`, string `validation_alias` and `AliasChoices`) and an opt-in typed `discovery_aliases` per-field map for JSON-LD, microdata and JSON responses; recipes still map the model field name, so provenance shows the matched source key, and an aliased schema no longer raises `ConfigurationError`.
- Frozen v0.2 recovery benchmark protocol (`docs/recovery-benchmark.md`, ADR 0011) with exact detection, restoration, false-promotion, silent-error, model-call and human-approval metrics, pass criteria for a recovery claim and anti-tuning rules, plus hashed synthetic development and sealed corpora of epoch sequences (`benchmarks/recovery/`) and an offline integrity test. No executor or repair code, no measured result, and no public SDK change.
- `OpenAICompatibleModel`, a Chat Completions `ModelPort` strategy for OpenAI, OpenRouter, vLLM, LM Studio, llama.cpp server and Ollama's `/v1`, behind a new `openai` extra. It sends `json_schema` structured outputs (or explicit `response_format="json_object"`), never tools; loopback endpoints work without opt-in while any other host requires `allow_remote=True` and HTTPS; API keys are referenced only through `api_key_env`; `InferencePolicy` deadline, context, response-byte, token and concurrency budgets are enforced in the adapter and failures map to `ModelError` categories. See ADR 0010.
- `rastro.OllamaModel` and `rastro.OpenAICompatibleModel` lazy public exports; core `import rastro` still does not import HTTPX, and a missing extra raises an `ImportError` naming it.
- `OpenAICompatibleModel(token_limit="max_completion_tokens", temperature=None)` for OpenAI reasoning models that reject `max_tokens` and sampling parameters.
- "Choosing or writing a model provider" documentation with provider examples and the custom `ModelPort` contract.
- Held-out evaluation (`python -m benchmarks.heldout`): five new synthetic sources with 24 structural mutations, measuring source-specific configuration, silent errors when re-running working integrations, scripted-model calls per successful record with SQLite memory, an observed visibility checklist and latency against a schema-generic baseline with a structured-data adapter. Results show partial evidence and no silent-error or latency advantage.
- Multi-model live Ollama harness (`--model` repeatable, `--trials`, `--keep-previous`) with recorded `qwen3:4b` and `llama3.2:3b` runs, a versioned report that keeps earlier runs, and a `--probe` mode that records raw proposals for diagnosis.
- Permitted structured JSON responses: a declarative `json` recipe strategy with an RFC 6901 `record_path` and relative field pointers, `Rastro.extract_json`/`extract_json_async`, and opt-in `representation="json"` HTTP acquisition that sends `Accept: application/json` and rejects non-JSON content types as permanent failures.
- Strict JSON budgets (`max_json_bytes`, `max_json_depth`, `max_json_nodes`), duplicate-key rejection, `json` evidence with absolute pointer locators, explicit `invalid_json`/`invalid_record`/`invalid_record_path`/`record_path_not_found` issues, and the public `Representation` type.
- Deterministic JSON recipe discovery by exact field names over the root object and arrays of objects. JSON recipe memory uses representation-isolated keys, and existing HTML keys and legacy payloads are unchanged. Includes the offline `examples/structured_api.py` and ADR 0007.
- Explicit network egress profiles: `AcquisitionPolicy.egress_profiles` declares named HTTP(S) proxies selected per `Source.egress`, with environment-variable credential references, pre-DNS configuration failures, unchanged target validation, no rotation or fallback, egress-isolated recipe memory and `metrics.egress`. `Source` rejects `geolocation` explicitly.
- Opt-in live Ollama harness and recorded `qwen3:4b` evidence: cross-process learning and zero-inference reuse, plus labelled live/controlled failure scenarios.
- Explicit ordered language preferences with descending Accept-Language weights, currency, timezone and direct-egress context through Pydantic's supported `model_validate` path; early rejection of invalid headers and unsupported egress.
- Regional recipe isolation by language order, currency and egress with unchanged default canonical keys for existing SQLite memory.
- Reproducible offline comparison against an independent Scrapling + Pydantic baseline: synthetic corpus, multiset quality scoring, raw timing samples and source hashes.
- Public demo-site end-to-end example with persistent recipe reuse and offline regression coverage; recorded live results, runtime license inventory and explicit v0.1 release gates (specification sections 17–18).
- Opt-in deterministic recipe discovery, asynchronous supplied-HTML extraction and optional local Ollama assistance through `ModelPort`.
- Structured model proposals, bounded calls/corrections/deadline/context/response/concurrency, and source/schema validation before activation.
- Learned recipes retain required-field evidence checks across restarts; M3 payloads remain reusable without false version conflicts.
- Inference metrics, attempt origins, an inspectable learned recipe, offline example, adapter/attack/budget tests and a separate `ollama` dependency extra.
- Optional SQLite recipe memory with exact source/template/schema/region identity, immutable versions, success metadata, retention and deletion.
- Deterministic reuse across restarts in offline and asynchronous SDKs, with worker-thread storage and explicit persistence failures.
- Bounded explicit fallback recipes; only the alternative validated successfully becomes reusable.
- Recipe version/reuse/attempt metadata, storage timings, a repeated-extraction example and persistence/security/concurrency tests.
- Optional policy-aware HTTP SDK with exact host allowlists, DNS pinning, robots rules, redirect checks, bounded retries and host pacing.
- Asynchronous single-source and ordered batch extraction, cancellation, request/body budgets, and explicit network outcomes.
- Acquisition metrics, regional Accept-Language, HTTP example, and offline transport/security regression tests.
- Offline HTML extraction with explicit JSON-LD and CSS recipes through Scrapling.
- Typed Pydantic results, field provenance, safe diagnostics, resource limits, and processing timings.
- CLI demo, environment diagnostics, and declarative recipe validation.
- Offline regression and property tests, strict typing, package checks, and documentation.
- Public contribution and security policies.
- Release evidence records built wheel/sdist contents and a bundled native notice review for `lxml` and `curl-cffi` on macOS arm64 (spec section 18 release gates).

### Changed

- `Recipe` is now a strategy-discriminated union of `CssRecipe` (`record_selector`), `JsonLdRecipe` (optional `item_type`) and `JsonRecipe` (`record_path`), all exported. `Recipe(...)`, `Recipe.model_validate(...)`, `Recipe.model_validate_json(...)` and Pydantic fields typed `Recipe` return the matching variant, so existing recipe files, `rastro validate-recipe` and stored payloads load unchanged. Each variant rejects other strategies' selectors. `null` selectors written by earlier releases mean absent. Fallbacks are typed per representation. Type-checked code should construct the variant classes, because static checkers see only the shared fields on `Recipe(...)`. See ADR 0009.
- Serialized recipes (`model_dump`, `learned_recipe`, new SQLite payloads) contain only the variant's own fields and no longer include `null` selectors for other strategies.
- `StoredRecipe` separates the definition from operational history. `selected_index`, `created_at`, `last_success_at` and `successful_runs` moved to `StoredRecipe.metadata`, a new exported `RecipeMetadata`. `stored.recipe` and `stored.validated_recipe` are unchanged. This prepares lifecycle states without changing definitions.
- `RecipeProposal` is a strategy union with `CssProposal` and `JsonLdProposal` variants derived from the HTML recipe variants. `proposal_schema()` is unchanged apart from the two variant titles.
- `AcquisitionResult.html` is renamed to `body` because it also carries JSON.
- Custom transports and acquisition ports must accept the keywords Rastro now always passes: `HttpTransport.get(..., proxy=None | ProxyRoute)` and `AcquisitionPort.acquire(..., representation="html" | "json")`.
- Internal: prepared extraction moved from `Rastro` into `application.service.ExtractionService`, with no behavior change. SQLite schema is unchanged and needs no migration; M3 and 0.1.0 payloads keep loading.
- Internal: `schema_fingerprint` caches canonical JSON Schema digests per model class (invalidating on `model_rebuild`) and `extract_document` computes `source.evidence_url` and validates shared `Evidence` metadata once per extraction, cutting unprofiled p50 latency on `static` (0.326 → 0.172 ms), `metadata` (0.251 → 0.118 ms) and `large` (12.033 → 9.135 ms; same-run gap over baseline 3.530 → 1.822 ms) with byte-identical serialized results, issues and provenance.
- The README, getting-started and acquisition guides now construct `CssRecipe`, `JsonLdRecipe` and `JsonRecipe` directly instead of `Recipe(strategy=...)`, so every documented example is the typed form.
- Internal: split `domain.models` (`sources`, `recipes`, `results` with `domain.models` re-exporting all contracts) and extracted cohesive `application` helpers (`http_policy`, `proposals`, `outcomes`, `batch`) from `acquisition`, `intelligence`, `service` and `engine` so every module stays within the 250-line budget with unchanged public imports, SQLite payloads, schema fingerprints and serialized results.

### Fixed

- JSON-LD and JSON decoding now reject numbers that overflow or underflow a finite float (for example `1e999`, `-1e999`, `1e-999`) and integers over 4,300 digits. These are explicit invalid outcomes instead of `inf` or `0.0` records that report success.
- `missing_evidence` issues now name each field that lacks evidence, for all strategies.
- The Ollama adapter disables thinking. Capable models no longer spend the output budget and deadline on hidden reasoning.
- The decoding schema now requires `record_selector` for CSS proposals and forbids it for JSON-LD, matching recipe validation.

[Unreleased]: https://github.com/rave-vipx/rastro/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/rave-vipx/rastro/compare/v0.9.0...v1.0.0
[0.9.0]: https://github.com/rave-vipx/rastro/compare/v0.7.0...v0.9.0
[0.7.0]: https://github.com/rave-vipx/rastro/releases/tag/v0.7.0
[0.6.0]: https://pypi.org/project/rastro-engine/0.6.0/
[0.1.0rc1]: https://github.com/rave-vipx/rastro/releases/tag/v0.1.0rc1
