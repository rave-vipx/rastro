# Action plan 1.0: recipes that survive real sites

Status: **approved direction**, 2026-11-19. Target release: **1.0.0**, the version that
consolidates 0.9 and declares the public API stable. Its needs are listed in
`docs/product-needs.md` as N21–N39; each cut follows the rastro-slice skill.

## Why

Rastro's promise to a person who is not a scraping expert, often building with an AI coding
agent ("vibecoding"):

1. Point Rastro at a URL and name the fields in plain language.
2. Scrapling fetches and parses; a model looks at the page **once** and proposes a recipe.
3. Rastro keeps the recipe only if it extracts valid records, then reuses it on the same
   domain without calling a model.
4. When the site changes or the recipe stops being valid, Rastro says so in words, asks the
   model for a new recipe, and adopts it only through the gates.

0.9 delivers that loop on clean pages. A first real integration (an events agenda across five
public sites, built by agents on Rastro 0.9.0 public API only) showed where it breaks on the
web as it is today. The integration is evidence, not the target: every need below is phrased
for any domain (stores, classifieds, news, agendas, listings).

What it showed, grouped:

- **One bad value can lose a whole source.** A single broken emoji (a lone UTF-16 surrogate in
  JSON) failed storage for every record of the source (fixed on dev in #151, not yet
  released). JSON-LD with raw line breaks inside strings is dropped although browsers accept
  it. A placeholder date (`0000-00-00`) rejects the whole record. Naive datetimes stay naive
  although the job declares a timezone.
- **Discovery stops too early or follows the wrong thing.** A sitemap seed takes `loc`s in
  document order up to the page cap, so on a site that lists oldest first the cap reaches only
  stale pages. Two selector groups in `spider.links` follow HTML instead of URLs. Provenance
  drops the query, so `?page=2` and `?page=3` look like one page.
- **Real records are not flat.** A product has variants, an event has ticket tiers, a listing
  has several images. Rastro fields are scalars relative to one record, so the integrator wrote
  adapters that fetch the same page twice and stored lists as JSON strings. `ItemList`
  JSON-LD is not traversed.
- **The run does not explain itself to code.** A load result does not say why a source was
  partial (cap reached or page failed). Drift, repair candidates and refused promotions are
  spread across `job stats`, `job_health` and alerts. There is no public dataset reader, so
  the integrator shelled out to `rastro dataset export`.
- **Operations are guesswork.** A fix waits for a release that is not on PyPI yet. Politeness
  delays make a 100-page crawl take minutes with no estimate. Some sites refuse cloud IPs and
  the outcome only says `http_403`.
- **Self-imposed limits block legitimate jobs.** Scrapling 0.5, which Rastro already uses,
  ships proxy rotation (`ProxyRotator`), Cloudflare Turnstile and interstitial solving
  (`solve_cloudflare`) and a crawl engine without a page cap that pauses and resumes from
  checkpoints, with autothrottle and per-domain concurrency. Rastro forbids proxy rotation
  (ADR 0034) and caps every crawl (ADR 0032). Rastro should expose what Scrapling solves,
  behind the same operator grants, instead of refusing it.
- **Rastro uses a fraction of Scrapling.** Today it uses Scrapling's parser, element
  relocation for drift and the stealth browser. It does not use adaptive element storage,
  `find_similar`, `find_by_text`, selector generation, network capture of XHR/JSON responses,
  most browser controls (`wait_selector`, `disable_resources`, `block_ads`, `pierce_shadow`,
  page actions, persistent sessions, real Chrome or CDP), Scrapling's HTTP sessions
  (impersonation, HTTP/3), the crawl engine and its templates (sitemap, feed, Shopify,
  site-to-markdown), Markdown conversion, or Scrapling's MCP browser tools. Rastro should be
  the harness that turns all of it into recipes, policy, evidence and Studio, not a wrapper
  around a small part.

## Needs

| ID | Need | Done when |
| --- | --- | --- |
| N21 | Fixes reach users the same day | 1.0.0 is published to PyPI through trusted publishing and carries the surrogate fix (#151); `docs/releasing.md` describes a 1.0.x patch path that needs no feature freeze. |
| N22 | A bad value costs one field, not a source | Every stage isolates failures: a value that cannot be decoded or coerced makes that field null with an issue (optional field) or rejects that record (required field), never the source. Audited by a fault-injection test per stage. |
| N23 | Tolerant structured data | JSON-LD and embedded JSON with raw control characters in strings, trailing commas or a BOM are read as browsers do, and flagged `repaired_json`. Strict parsing stays for recipes and config. |
| N24 | Dates as people write them | Naive datetimes take the job or region timezone and are stored aware; per-field `null_values` (e.g. `0000-00-00`, `TBA`) turn placeholders into null with a reason; the AI recipe prompt is told the region timezone. |
| N25 | Discovery that reaches the right pages | Sitemap seeds support sitemap indexes, `order = "document" \| "reverse" \| "lastmod"`, and `allow`/`deny` patterns on `loc` before the cap applies; `spider.links` accepts selector groups or refuses them at validation; provenance keeps the query (tracking parameters stripped, normalized order). |
| N26 | Records that are not flat | Field types `list` and `object` (with typed members) in jobs, `load_records`, storage, export and Studio (shown as nested rows, filterable on members). JSON pointers can use `*` over arrays and `..` to read beside the record; `ItemList`/`@graph` JSON-LD is traversed. |
| N27 | AI recipes for nested data | The recipe proposer and repair can produce list/object fields from a plain-language description ("each ticket type with its price"); drift and the judge compare nested members, not just the top-level count. |
| N28 | A run that explains itself | One `RunReport` model, returned by `run_job`, `load_job_file` and the CLI `--json`, and passed to the notification hook: per source outcome and reason (cap vs failure vs blocked), pages, records, rejected by code, drift, repair candidate, quarantine, promotion refused, timing. Studio and alerts read the same model. |
| N29 | Read results from code | `rastro.read_records(dataset, since=, fields=)` in `rastro.__all__`, with provenance per field and multi-origin records merged; documented as the adoption path instead of shelling out to export. |
| N31 | Access what Scrapling can reach | Proxy rotation over the operator's egress profiles (Scrapling `ProxyRotator`, credentials from allowed environment names, never from the job or a bundle, also in browser and stealth modes) and Cloudflare challenge solving run when the operator grants them, like stealth (ADR 0034, ADR 0036). Outcomes name what was used. Anything Scrapling does not solve (other CAPTCHA vendors, logins) stays a `blocked` outcome with a hint. |
| N32 | Crawls without a page cap | A spider may set `max_pages = "unbounded"` when the operator grants it; the run uses Scrapling's crawl engine with checkpoints (pause and resume across runs), autothrottle and per-domain concurrency; robots, allowed hosts and the time budget still apply; progress and resume state appear in the run report and Studio. |
| N33 | Every Scrapling feature has a place | `docs/scrapling.md` maps each Scrapling public feature to its Rastro surface (job key, grant, recipe strategy, Studio control, MCP tool) or to a written reason for not exposing it. The map is data in the capability manifest; a CI test inspects the installed Scrapling (fetcher and session options, parser methods, spider settings, templates, MCP tools) and fails when a feature is unmapped, so upgrades never leave value unused. |
| N34 | Adaptive first, AI second | Recipe creation and repair try Scrapling before any model: `find_similar` finds the repeated record, `generate_css_selector` proposes stable selectors, adaptive storage and `relocate` re-find moved elements, `find_by_text`/`find_by_regex` anchor fields by their labels. The model is called only when these candidates fail the gates, and it receives a Markdown condensation of the page instead of raw HTML. The run report counts repairs done without a model. |
| N35 | Recipes from the site's own data | In browser mode Rastro records the page's XHR/JSON responses with Scrapling's network capture; Studio offers "this page loads its data from an API" and builds a recipe over that response (same policy, the page stays the job URL). Drift watches the JSON shape, which changes less than markup. |
| N36 | Browser controls as data | Job keys for `wait_selector` (and state), `network_idle`, `disable_resources`, `block_ads`, `blocked_domains`, `pierce_shadow`, locale and timezone from `[region]`, and declarative page actions (click, type, scroll, wait, as data, never code); screenshots as optional evidence; persistent browser sessions, real Chrome and CDP connections under operator grants; fingerprint options under the stealth grant. |
| N37 | Scrapling's HTTP engine behind Rastro's policy | An ADR decides whether Scrapling's `FetcherSession` (browser impersonation, HTTP/3, retries) becomes the HTTP path, keeping DNS pinning, redirect and size checks, robots and host limits; impersonation becomes an operator grant with the `SECURITY.md` amendment. If a guarantee cannot be kept, the ADR records why the current client stays. |
| N38 | Crawl engine and templates | Beyond N32: a development response cache so Studio iterates without refetching, streamed results while a crawl runs, and Scrapling's spider templates (sitemap, feed, Shopify, site-to-markdown, generic crawler) published as starter recipes in the registry. |
| N39 | Scrapling tools for agents | Rastro's MCP server offers Scrapling's browser tools (snapshot, actions, network requests, screenshot, extract) while an agent builds a recipe, under Rastro's network policy and prompt-injection defense (ADR 0035); `rastro shell JOB` opens Scrapling's interactive shell on a job's page. |
| N30 | Predictable operation | Per-host rate and concurrency in `[acquisition]`; a dry-run estimate of requests and minutes before a crawl; `blocked` outcomes carry a hint (cloud IP refused, robots, login wall) and link to an egress guide (proxy, self-hosted runner); `Fetched.text`; a `rastro dataset migrate` path between schema versions; small doc and CLI fixes (`job stats --since 30m`, `dataset.path` default). |

Every need keeps the existing rules: recipes are data, scraped text is untrusted (ADR 0035),
core install works without AI or a browser, failures stay explicit, tests stay offline and
synthetic.

## Design notes

### N22–N24 robustness

- One helper decides "field null with issue" versus "record rejected" from the field's
  `required` flag; every extractor (CSS, XPath, JSON-LD, embedded JSON, AI output) goes through
  it. New issue codes go in `docs/issues.md` (the existing test enforces it).
- Tolerant JSON is a separate decoder used only on page data, mirroring the opt-in
  `repair_text` of #151. Mock Lab keeps a strict case so the difference stays tested.
- Timezone application happens at coercion, so recipes, drift and exports all see aware
  values. Existing datasets are not rewritten; the migrate path (N30) can.

### N25 discovery

- `order = "lastmod"` sorts by `<lastmod>` and falls back to document order when it is
  missing; the order and the filtered count appear in the run report so a capped crawl is
  explainable.
- Query normalization lives in one function shared by provenance, deduplication and the
  spider's seen-set.

### N26–N27 nested records

- Storage keeps one row per record; list/object values are stored as validated JSON with a
  schema, so SQLite and Postgres backends need no child tables. Export emits native JSON;
  CSV flattens with a documented convention.
- Studio shows nested members in an expandable row and allows filtering on a member path.
- The AI proposer gets examples of nested fields; repair gates validate member types, and the
  semantic judge samples members. This is the change that removes most custom adapters.

### N28–N29 run contract

- `RunReport` is a versioned pydantic model in the public API with a JSON schema under
  `docs/schemas/`. The hook receives it on stdin; Studio's alerts are a view of it.
- Backward compatibility: existing result fields stay for one minor version with a
  deprecation note.

### N31–N32 access and unbounded crawl (ADR 0036)

- Grants, not job keys: `--allow-proxy-rotation` / `RASTRO_ALLOW_PROXY_ROTATION`, the existing
  stealth grant for `solve_challenges`, and `--allow-unbounded-crawl` /
  `RASTRO_ALLOW_UNBOUNDED_CRAWL`. Studio shows each as a switch, off by default. A bundle or a
  registry recipe can request them; it can never grant them.
- Proxies are the operator's existing egress profiles (`--allow-egress`); rotation over them
  needs the extra grant, and credentials come only from allowed environment names. They are
  redacted in every report, log and evidence record. The SSRF and allowed-host checks run on the target URL, not
  on the proxy hop.
- Unbounded crawls keep a checkpoint per job under the job's state folder, resume
  automatically on the next run, and stop at the run's time budget, not at a page count.
- Robots checks stay mandatory, as `SECURITY.md` requires; no grant turns them off.

### N33–N39 Rastro as Scrapling's harness

- Principle: Scrapling acquires, parses, relocates and crawls; Rastro decides what is allowed
  (policy, grants, robots, SSRF), turns results into typed records with provenance, keeps and
  repairs recipes, and shows it all in Studio and to agents. A Scrapling capability reaches
  users only through that harness, never as a raw passthrough of untrusted options.
- The coverage map (N33) is the backbone: each later need adds rows to it, and a Scrapling
  upgrade starts by running the coverage test.
- N34 changes the cost profile of the product: most layout changes are repaired by Scrapling's
  adaptive relocation without a model call; the AI is reserved for real redesigns and for
  naming fields from plain language.
- Page actions and network capture are data in the job (steps and URL patterns), consistent
  with "recipes are data"; `page_action` callables and init scripts stay out of jobs and
  bundles.

### N30 operations

- Rate limits are per host and shared by pagination, spider and detail fetches of one run.
- The estimate uses the caps and the configured rate; it never fetches.
- Release: one release, 1.0.0, carries every need; later fixes ship as 1.0.x.

## Cuts and order

| Cut | Needs | Why this order | Review |
| --- | --- | --- | --- |
| 1 | N21 (release pipeline) | Trusted publishing works and the surrogate fix is on dev; 1.0.0 ships it with the rest | release checklist |
| 2 | N22, N23, N24, N33 | Stops silent data loss; the Scrapling coverage map frames every later cut | cross-model (persistence) |
| 3 | N25 | Unblocks sites whose sitemaps or links misled the crawl | cross-model (network) |
| 4 | N28, N29 | The contract other cuts report into; lets integrators stop shelling out | cross-model (public API) |
| 5 | N26, N27, N34 | Nested records and adaptive-first recipe creation and repair, on the run contract | cross-model (persistence, public API) |
| 6 | N30, N31, N32, N38 | Operations, access and the crawl engine with its templates | cross-model (network, security) |
| 7 | N35, N36, N37 | Network-first recipes, browser controls as data, the HTTP engine decision | cross-model (network, security) |
| 8 | N39 | Scrapling tools for agents through MCP and the shell | cross-model (security) |

After cut 8, release 1.0.0 (public API stable from here: `rastro.__all__`, job format, `RunReport` schema) with the capability manifest (`rastro capabilities`), `llms.txt`
and the configuration reference regenerated, and the integration example rebuilt to show that
its custom adapters are no longer needed.

## Out of scope

- Anything specific to one integration (event visibility, price policy, deduplication across
  sites). Those belong to the integrating project.
- Access methods Scrapling does not provide (third-party CAPTCHA services, account logins).
- Hosted multi-tenant services and executable shared recipes.
