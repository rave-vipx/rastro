# Crawling linked pages

A job can discover pages from its starting URLs with a `[spider]` table:

```toml
[spider]
mode = "spider"
links = ["a.product::attr(href)", "a.next::attr(href)"]
allow = ["/products"]
deny_pattern = '^/products/archive/'
max_depth = 2
max_pages = 25
```

Each expression must read a URL attribute, normally `::attr(href)`; existing
`::attr(name)` expressions such as `::attr(data-href)` remain supported. A single
string is also accepted.
A list holds up to eight expressions of at most 1024 characters each. Expressions
are evaluated in list order, with matches in document order within each expression;
duplicate destinations are followed once. Every expression passes the same selector
cost checks as a recipe.

Do not write `links = "a.product, a.next::attr(href)"`: the attribute suffix does
not apply safely to both selector groups. This produces `link_group_refused` with
a hint to use a list. Migrate it to
`links = ["a.product::attr(href)", "a.next::attr(href)"]`.
Commas inside quoted attribute values are allowed. To discover URLs from extracted
records instead, set `link_field` to a declared field and omit `links`.

## Filter before the page limit

`allow` and `deny` match whole path prefixes. `allow_pattern` and `deny_pattern`
match the normalized, percent-decoded path with a linear-time regular expression;
queries do not participate. Patterns may have up to 512 characters and cannot use
look-around or backreferences. Anchor with `^` and `$` when the whole path must match.
A deny match takes precedence over an allow match.

Filtering happens before spending a page slot, so a rejected link cannot crowd out
an allowed page at `max_pages`. Rejected paths appear as `path_denied` in the crawl
report. The starting page still consumes a page slot. With `seed = "sitemap"`, path
filters apply to discovered pages, not to child sitemap URLs. `seed_order` controls
sitemap entry order; it accepts `document`, `reverse` or `lastmod`.

All followed pages use the existing acquisition policy, destination checks and
request budgets. Link discovery grants no network access by itself.

## Page identity and evidence

Rastro normalizes page URLs in two forms. Both keep scheme, host, port and path,
sort query parameters, and remove URL credentials, path session parameters,
fragments and tracking keys such as `utm_*`, `fbclid`, `gclid` and `mc_eid`.

- The **page identity** recognizes repeated pages in crawling and recipe pagination.
  It keeps every other query key, so `?state=CA` and `?state=NY` are two pages. It
  lives in memory only and is never stored, logged or reported.
- The **stored page URL** (field evidence) also removes secret-like query keys,
  including `token`, `key`, `sig`, `signature`, `session`, `sid`, `auth`, `password`,
  `code` and `state`. Keys are split into words on separators and camelCase, so
  `access_token`, `accessToken`, `X-Amz-Signature` and `jsessionid` are removed while
  `keyword` or `author` are kept.

For example, `https://catalogue.example/products?utm_source=mail&page=2&token=hidden`
is stored as `https://catalogue.example/products?page=2`. Page 3 remains a separate
page. Duplicate query keys and blank values are retained when their keys are allowed.
Requests still use the original URL: normalization changes identity and evidence,
not the address sent to the acquisition adapter.

Field evidence uses `page_url` for the canonical acquired page when it differs from
its query-free source URL. Redirected pages use the final acquired URL. `source_url`
continues to identify the starting source when pages are combined. Logs, hooks and
crawl reports carry no URL queries.

The stored-URL filter is conservative: secret-like key names may be removed even when
their values are ordinary data, so two pages can share one stored page URL while
both are still fetched. It cannot recognize a secret placed under an unrelated key
such as `page`; treat retained page parameters and paths as source data subject to
your storage and retention policy.

## Unbounded crawls

Set `max_pages = "unbounded"` to crawl a site until its frontier is empty. The time budget
bounds the crawl instead of a page count:

```toml
[spider]
mode = "spider"
links = ["a.product::attr(href)", "a.next::attr(href)"]
max_depth = 4
max_pages = "unbounded"
time_budget_seconds = 1800
```

- `time_budget_seconds` (60 to 86400, default 1800) is how long each start URL may crawl.
  When it runs out, the crawl stops after the pages in flight, reports `crawl_time_budget`
  and saves a checkpoint. The next run resumes from it, fetches only the pages still in the
  frontier and reports `crawl_resumed`. A crawl that empties its frontier removes the
  checkpoint, so the run after it starts over.
- The job's `max_requests` does not apply to an unbounded crawl. Robots, allowed hosts,
  path filters, `max_depth`, private-network checks and `min_interval_seconds` all still
  apply. The pace adapts per host: it slows when responses slow down and backs off on
  429, 503 or a `Retry-After` header.
- `[limits] max_records` still caps one run. Pages past it go back to the frontier, so the
  next run picks them up instead of losing their records.
- Checkpoints live in `.rastro/crawl/` next to the job file. They are JSON, private to your
  user, and validated when read; a damaged or edited checkpoint is removed with
  `crawl_checkpoint_discarded` and the crawl starts over. Delete the folder to start fresh.
- An unbounded crawl requests the `unbounded_crawl` capability. Your own jobs have it;
  input from agent tools, bundles, registry recipes or model output needs
  `--allow-untrusted <origin>:unbounded_crawl`.

- While it runs, an unbounded crawl rewrites its checkpoint every minute. A run that dies
  (a crash, a killed process) resumes from the last one; the pages it had fetched go back
  into the frontier, because their records are stored only when a run ends.
- An install without the browser extra still adapts its pace: Rastro uses its own
  throttle with the same rules when the engine's crawl package is not installed.
- `stream_job_async(job)` yields each source's result as it finishes instead of waiting
  for the whole job.

The run report's crawl note gives the pages fetched, the frontier left, whether the run
resumed and the checkpoint state (`saved`, `finished`, `discarded` or `none`). Past 600
rows, the crawl report counts the rest in `unreported` and totals every page by outcome.

## Per-host pacing

`[acquisition.hosts."shop.example"]` overrides the job-wide pacing for one allowed host:

```toml
[acquisition.hosts."shop.example"]
min_interval_seconds = 2    # seconds between requests to this host (0 to 60)
max_concurrency = 2         # transfers in flight to this host; unset: the job-wide value
autothrottle = true         # widen the spacing when the host slows down or answers 429
```

With `autothrottle`, the spacing starts at `min_interval_seconds`, follows the response
time, doubles on a refusal or waits what `Retry-After` asks, and never drops below the
floor or rises past 60 seconds. It works on HTTP-only installs too.

## Starter recipes

The synthetic registry in `examples/registry/` ships three starting points for crawls:
`sitemap-crawl`, `shopify-products` and `generic-crawl`. Install one with `rastro hub
install`, point its job at your site's URL and allowed host, then preview it with
`rastro job run`. They are templates: the selectors are generic and need a real site.
