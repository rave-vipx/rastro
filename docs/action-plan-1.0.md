# Action plan 1.0: recipes that survive real sites

Status: **approved direction**, 2026-11-19. Target release: **1.0.0**, the version that
consolidates 0.9 and declares the public API stable. Its needs are listed in
`docs/product-needs.md` as N21–N32; each cut follows the rastro-slice skill.

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

### N30 operations

- Rate limits are per host and shared by pagination, spider and detail fetches of one run.
- The estimate uses the caps and the configured rate; it never fetches.
- Release: one release, 1.0.0, carries every need; later fixes ship as 1.0.x.

## Cuts and order

| Cut | Needs | Why this order | Review |
| --- | --- | --- | --- |
| 1 | N21 (release pipeline) | Trusted publishing works and the surrogate fix is on dev; 1.0.0 ships it with the rest | release checklist |
| 2 | N22, N23, N24 | Stops silent data loss on messy pages; small, independent | cross-model (persistence) |
| 3 | N25 | Unblocks sites whose sitemaps or links misled the crawl | cross-model (network) |
| 4 | N28, N29 | The contract other cuts report into; lets integrators stop shelling out | cross-model (public API) |
| 5 | N26, N27 | Largest change; builds on the run contract and robust coercion | cross-model (persistence, public API) |
| 6 | N30, N31, N32 | Operations and access, on the stable run contract | cross-model (network, security) |

After cut 6, release 1.0.0 (public API stable from here: `rastro.__all__`, job format, `RunReport` schema) with the capability manifest (`rastro capabilities`), `llms.txt`
and the configuration reference regenerated, and the integration example rebuilt to show that
its custom adapters are no longer needed.

## Out of scope

- Anything specific to one integration (event visibility, price policy, deduplication across
  sites). Those belong to the integrating project.
- Access methods Scrapling does not provide (third-party CAPTCHA services, account logins).
- Hosted multi-tenant services and executable shared recipes.
