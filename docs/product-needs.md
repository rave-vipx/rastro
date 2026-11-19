# Product needs

Owner request, 2026-11-16, updated the same day: finish the product in this cut, including bounded scroll, a registry others can host, a spec that matches, and opt-in promote and stealth (ADR 0034). This file is the planning source. It overrides conflicting product bans in `RASTRO_MASTER_SPEC.md`, the README comparison table, and ADR 0020. It does **not** override the security model in `SECURITY.md`.

`docs/roadmap.md` stays the history of what already shipped. This file is what to build next.

The job key for plain HTTP, browser, and stealth is `render`, already shipped on `[acquisition]` and on each source (`http`, `browser`, `stealth`). Do not rename it to `engine`.

## Needs N21–N30 (0.10, current cut)

Owner request, 2026-11-19. Planning source for 0.10; design notes and cut order are in
`docs/action-plan-0.10.md`. N1–N12 below shipped in 0.9.0; N13–N20 are in
`docs/action-plan-adoption.md`.

| ID | Need |
| --- | --- |
| N21 | Fixes reach users the same day (0.9.1 patch release, patch path in `docs/releasing.md`) |
| N22 | A bad value costs one field, not a source |
| N23 | Tolerant structured data (JSON-LD and embedded JSON as browsers read them) |
| N24 | Dates as people write them (region timezone, null placeholders) |
| N25 | Discovery that reaches the right pages (sitemap order and filters, link groups, query-aware provenance) |
| N26 | Records that are not flat (list and object fields, array pointers, ItemList) |
| N27 | AI recipes for nested data |
| N28 | A run that explains itself (one `RunReport` for code, Studio and hooks) |
| N29 | Read results from code (`rastro.read_records`) |
| N30 | Predictable operation (per-host rate, crawl estimate, blocked hints, schema migrate) |

## What Rastro is for

Rastro is the scraper a person with little scraping experience can keep running.

1. They paste a URL (or a short list of URLs).
2. They name the fields they want, in plain language.
3. A model looks at the page **once**, proposes a recipe, and Rastro keeps that recipe only if it extracts valid records.
4. Later runs use the recipe and do not call a model. Each run fetches the URL again.
5. When the site changes, Rastro says so, calls the model again, and adapts the recipe only after the same gates as today. If the operator turned auto-promote on, a candidate that passes those gates and the semantic judge becomes active without another click. If they did not, Rastro asks.
6. Other people can publish a recipe (a store, a classifieds site, a news list) and someone else can install it as a candidate from a Git directory or from an HTTPS URL of that directory. What they publish is the recipe. The scrape still runs against the live URL.
7. The person can watch this from Studio, including drift and failures, not from a TOML file and an exit code.

A hand-written Scrapling + Pydantic script can extract one page. Rastro's job is to keep doing that without silent wrong data, and to cover the sites that script cannot: JavaScript, a next page, a bounded scroll, and a bounded crawl of links the user allowed.

## What a run stores

The input is a URL. Rastro fetches that URL and applies the recipe. It stores the records and the recipe. It does not store the page as the next scrape target. The next run fetches the URL again.

Saving HTML by hand is not the product path. A bundle may include a `sample.html` only as optional evidence so another person can replay the recipe offline before promoting it. That file is not what production scrapes. Examples committed in this repository stay synthetic. Scraping Amazon, Mercado Libre, or a news site means putting that URL in the job.

## Needs

| ID | Need | Done when |
| --- | --- | --- |
| N1 | URL-first Studio | Studio accepts a URL, fetches it, and builds a job. The run scrapes that URL. Saving HTML by hand is not required. |
| N2 | JavaScript as a normal path | If the HTTP body has no records, Studio offers browser rendering. Pages of one source reuse one browser context. |
| N3 | Bounded spider | A job can follow links (same host by default) up to a depth and a page cap, concurrently, while extracting with the recipe. |
| N4 | Pagination a non-expert can turn on | Studio can mark a next link and a page cap. The existing bounded pagination (ADR 0026) is the engine. |
| N5 | Notifications a person understands | When a run ends in drift, failure, quarantine, a refused promote, or a cap, Studio shows that in words without opening a log. The existing `--on-failure` hook still runs, and the README shows a copy-paste hook. The job file still cannot set that hook. |
| N6 | Recipes people can share | `examples/registry/` has at least three synthetic public bundles (listing, news card, product card), and the README shows install, promote, load. The published object is the recipe. |
| N7 | Repair without silent corruption | Studio has "Fix this site" and a separate auto-promote switch, default off. On means the existing gates plus the semantic judge. Off means a person promotes with a reason. See ADR 0034. |
| N8 | A README a newcomer can finish | The first screen is the five-minute Studio path, not the specification. No unsupported accuracy claims. The two opt-in switches are explained in one sentence each. |
| N9 | Bounded scroll | A browser source can scroll until no new records or a step cap. Scroll does not enable stealth. See ADR 0033. |
| N10 | A registry someone else can host | Search and install work from a local registry or an HTTPS URL of the same layout. Studio can search, install as a candidate, and publish into a local registry. No accounts and no Rastro-operated server. See ADR 0033. |
| N11 | The spec matches the product | The master spec Amendment Log records ADR 0032, ADR 0033, and ADR 0034. Sentences that still ban crawling, URL-first Studio, a hostable registry, or opt-in stealth are marked superseded. The file is not rewritten. |
| N12 | Opt-in promote and stealth | Studio shows both switches off. Auto-promote writes `[repair] auto_promote` on the operator's job. Stealth grants the Studio process only (`--allow-stealth` / `RASTRO_ALLOW_STEALTH`). A bundle cannot set either. The job key stays `render`. See ADR 0034. |

## Product bans this cut repeals

These were self-imposed. They are no longer requirements:

- "You name every page." Jobs may discover links inside the spider policy below.
- "Discovered-link crawling is not implemented" as a permanent boundary.
- Studio may only start from a saved HTML file.
- Studio must not show progress for a long run.
- A browser context must be thrown away between pages of the **same** source during one pagination, scroll, or spider run. A fresh context per source is still the default across sources.
- "There is no hosted registry" as a permanent boundary. A hostable static registry is in scope. A multi-tenant website is not.
- "Infinite scroll is out of scope" as a permanent boundary. Unbounded scroll stays out.
- "Stealth is forbidden." It is allowed when the operator grants it (ADR 0024, ADR 0034). It is not the default.

Do not rewrite `RASTRO_MASTER_SPEC.md` to match. ADR 0032, ADR 0033, and ADR 0034 are the amendments. Where the master spec disagrees with this file on the rows above, this file wins.

## Security that stays

Do not weaken these to make N1–N12 easier:

- Recipes are data. No flag turns on executable recipe code. Code the operator writes in their own job or SDK is theirs. Code from a bundle, a model, or a registry is refused.
- A model proposes selectors or a judgement. It never writes records. `auto_promote` defaults off. When the operator turns it on, a failed gate still does not promote.
- Invalid or missing data is never a successful empty result.
- SSRF checks, the public-address default, private-network opt-in, extra ports, and ignore-robots stay operator grants. A job file, a bundle, a registry URL, or a model cannot grant them.
- Stealth and challenge-solving stay operator grants. The job may set `render = "stealth"` on `[acquisition]` or on a source. Without the grant the stealth browser does not start. `render = "browser"` and scroll do not imply stealth. No proxy rotation and no external CAPTCHA service. Do not rename `render` to `engine`.
- Untrusted recipes (Hub, registry URL, MCP, Studio edits, model proposals) keep the selector cost policy (`:has()`, sibling combinators, and the depth limits).
- Logs, hooks, and repair reports do not include page bodies, query strings, or secrets.
- Studio stays loopback-only, with the per-run token, origin check, and path containment.
- Spider, pagination, and scroll cannot reset the per-source request budget on every page or step.
- Default tests stay offline. Fixtures are synthetic. Never commit a captured third-party page into this repository. A live run reads a URL. A bundle's sample, if the publisher attached one, is replay evidence, not the next scrape target.
- Do not open, read, or edit `benchmarks/recovery/sealed/`.
- Do not replace Studio with a Node, React, Vite, or Tailwind toolchain. The loopback page is the GUI.

## Opt-in strategy (N12)

See ADR 0034.

- **Auto-promote, default off.** Studio switch or `[repair] auto_promote = true` on the job the operator owns. Gates and the semantic judge still have to pass. Install from a registry does not change this key and does not promote.
- **Stealth, default off.** Studio session switch, `--allow-stealth`, or `RASTRO_ALLOW_STEALTH=1`. The switch does not write the grant into the job. `render = "browser"` and scroll leave it off.
- **Code.** Not an opt-in on a shared recipe. Local job or SDK code only.
- **Accounts.** Not an opt-in server. Sharing is the registry directory or its URL.

## Spider policy (N3)

A job may add:

```toml
[spider]
mode = "spider"          # absent or "list" keeps today's named sources
max_depth = 2            # 1 to 4; depth 0 is the start URL only
max_pages = 25           # 1 to 100, counts every fetched document, including scroll steps
same_host = true         # default; false still requires allowed_hosts
allow = ["/item/"]       # optional path prefixes, whole segments
deny = ["/login", "/cart"]
```

Rules:

- Start URLs are the job's `[[sources]]`. The spider does not accept a bare domain from a model.
- Same-host is the default. Off-host links are dropped unless that host is already in `allowed_hosts`.
- `robots.txt` still applies unless the operator already passed `--ignore-robots` for that host.
- Per-host pacing and the existing concurrency cap stay. Pages of one run share one request budget.
- Every discovered URL is recorded as an attempted page with outcome, not swallowed.
- Hitting `max_pages`, `max_depth`, or the budget finishes as a partial success with the records already accepted. It does not delete dataset rows.
- Link extraction is declarative (CSS or the recipe's own record links), not an arbitrary callback.

Pagination (N4) stays the ADR 0026 mechanism: a `next` expression plus `max_pages` on the recipe. Spider, pagination, and scroll may be used together; the tighter cap wins.

## Scroll (N9)

See ADR 0033. Studio shows the step cap only after the `[scroll]` schema exists. Until then, do not invent a second scroller in the front end.

## JavaScript (N2)

- HTTP first. Browser only when `render = "browser"` on `[acquisition]` or on that source, or when Studio offers it because the HTTP extract returned no records. Studio writes `render`, not a new key.
- Stealth is `render = "stealth"`, off until the operator grants it (N12).
- One browser context per source per run, reused across that source's pages and scroll steps, then closed. No cookie jar shared across hosts. This replaces the per-page context in ADR 0024 for pages of the same source. It does not rename `render`.
- Images, media, fonts, and WebSockets stay blocked.

## Studio (N1, N4, N5, N7, N10, N12)

Keep the loopback Starlette app.

- **New scraper:** URL, field names, optional example values, Ask AI if a model was configured, preview, create job. The preview fetches the URL. It does not ask the person to upload the page.
- **Progress:** a live line for the current source, page or scroll ordinal, and outcome. No page body in that line.
- **Next link:** the person clicks an element in the preview or types a selector; Studio writes the existing pagination fields.
- **Fix this site:** calls the existing repair service and requires a reason to promote when auto-promote is off.
- **Auto-promote:** switch, default off, writes `[repair] auto_promote` only. Copy says the gates still apply.
- **Stealth:** session switch, default off. It grants the process. It does not persist a grant in the job. `render = "stealth"` without that grant shows a refusal, not a plain browser fallback that pretends stealth ran.
- **Health:** last success, why it is stale, open drift, quarantined recipes, a refused auto-promote, the exact command for `--on-failure`, and the same alert when the run just finished in one of those states.
- **Registry:** search and install from a directory or HTTPS URL; publish into a local registry directory. Install stays a candidate. Publish the recipe. Do not require a saved page to run the job afterwards.

## Sharing (N6, N10)

Stay on the Git-directory registry. Add three synthetic bundles under `examples/registry/`. Document local install and `--registry-url` in the README. A shared package is the recipe. Scraping a real site is a URL in the job. Do not commit that site's HTML into this repository.

## Review

Network, persistence, security and CI changes need a cross-model review before merge (see `AGENTS.md`).

## Acceptance for the cut

- Offline tests cover spider caps, off-host refusal, shared budget, browser-context reuse, scroll stop-on-no-new-records, a registry URL that fails SSRF or a bad digest without writing a recipe, install leaving `auto_promote` untouched, and stealth requested without a grant not launching the stealth browser.
- Studio tests cover URL create, a pagination field write, a repair promote that still refuses a rejected candidate, an install that stays a candidate, and both switches rendering off. The create flow fetches a URL. It does not require an uploaded page.
- `README.md` leads with Studio and a URL. The comparison table says crawling, pagination, and scroll are bounded and opt-in, and that stealth and auto-promote are off until the operator turns them on.
- The master spec Amendment Log cites ADR 0032, ADR 0033, and ADR 0034. No historical section is deleted.
- No new dependency in the core install. Browser and HTTP stay extras. No Node toolchain.
- Known limits are written down: no unbounded crawl, no Rastro-operated accounts server, no executable shared recipes, no stealth implied by a browser or a scroll.
