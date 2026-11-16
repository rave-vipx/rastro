# Product needs N1–N11

Owner request, 2026-11-16, updated the same day: finish the product in this cut, including what the first draft had postponed (bounded scroll, a registry others can host, and a spec that matches). This file is the planning source. It overrides conflicting product bans in `RASTRO_MASTER_SPEC.md`, the README comparison table, and ADR 0020. It does **not** override the security model in `SECURITY.md`.

`docs/roadmap.md` stays the history of what already shipped. This file is what to build next.

## What Rastro is for

Rastro is the scraper a person with little scraping experience can keep running.

1. They paste a URL (or a short list of URLs).
2. They name the fields they want, in plain language.
3. A model looks at the page **once**, proposes a recipe, and Rastro keeps that recipe only if it extracts valid records.
4. Later runs use the recipe and do not call a model.
5. When the site changes, Rastro says so, calls the model again, and adapts the recipe only after the same gates as today.
6. Other people can publish a recipe (a store, a classifieds site, a news list) and someone else can install it as a candidate from a Git directory or from an HTTPS URL of that directory.
7. The person can watch this from Studio, including drift and failures, not from a TOML file and an exit code.

A hand-written Scrapling + Pydantic script can extract one page. Rastro's job is to keep doing that without silent wrong data, and to cover the sites that script cannot: JavaScript, a next page, a bounded scroll, and a bounded crawl of links the user allowed.

## Needs

| ID | Need | Done when |
| --- | --- | --- |
| N1 | URL-first Studio | Studio accepts a URL, fetches it, and builds a job. Saving HTML by hand is no longer required. |
| N2 | JavaScript as a normal path | If the HTTP body has no records, Studio offers browser rendering. Stealth stays an operator opt-in. Pages of one source reuse one browser context. |
| N3 | Bounded spider | A job can follow links (same host by default) up to a depth and a page cap, concurrently, while extracting with the recipe. |
| N4 | Pagination a non-expert can turn on | Studio can mark a next link and a page cap. The existing bounded pagination (ADR 0026) is the engine. |
| N5 | Notifications a person understands | When a run ends in drift, failure, quarantine, or a cap, Studio shows that in words without opening a log. The existing `--on-failure` hook still runs, and the README shows a copy-paste hook. The job file still cannot set that hook. |
| N6 | Recipes people can share | `examples/registry/` has at least three synthetic public bundles (listing, news card, product card), and the README shows install, promote, load. |
| N7 | Repair without silent corruption | Studio has one "Fix this site" action: propose, show old vs new values, promote only through the existing gates. `auto_promote` stays off unless the job already sets it. |
| N8 | A README a newcomer can finish | The first screen is the five-minute Studio path, not the specification. No unsupported accuracy claims. |
| N9 | Bounded scroll | A browser source can scroll until no new records or a step cap. Scroll is not stealth. See ADR 0033. |
| N10 | A registry someone else can host | Search and install work from a local registry or an HTTPS URL of the same layout. Studio can search, install as a candidate, and publish into a local registry. No accounts and no Rastro-operated server. See ADR 0033. |
| N11 | The spec matches the product | The master spec Amendment Log records ADR 0032 and ADR 0033. Sentences that still ban crawling, URL-first Studio, or a hostable registry are marked superseded. The file is not rewritten. |

## Product bans this cut repeals

These were self-imposed. They are no longer requirements:

- "You name every page." Jobs may discover links inside the spider policy below.
- "Discovered-link crawling is not implemented" as a permanent boundary.
- Studio may only start from a saved HTML file.
- Studio must not show progress for a long run.
- A browser context must be thrown away between pages of the **same** source during one pagination, scroll, or spider run. A fresh context per source is still the default across sources.
- "There is no hosted registry" as a permanent boundary. A hostable static registry is in scope. A multi-tenant website is not.
- "Infinite scroll is out of scope" as a permanent boundary. Unbounded scroll stays out.

Do not rewrite `RASTRO_MASTER_SPEC.md` to match. ADR 0032 and ADR 0033 are the amendments. Where the master spec disagrees with this file on the rows above, this file wins.

## Security that stays

Do not weaken these to make N1–N11 easier:

- Recipes are data. No executable recipe code. Imported, installed, and model recipes stay candidates until a person promotes them.
- A model proposes selectors or a judgement. It never writes records. `auto_promote` is not turned on by this cut.
- Invalid or missing data is never a successful empty result.
- SSRF checks, the public-address default, private-network opt-in, extra ports, and ignore-robots stay operator grants. A job file, a bundle, a registry URL, or a model cannot grant them.
- Stealth and challenge-solving stay operator grants (`--allow-stealth`). Scroll and browser mode do not imply stealth. No proxy rotation and no external CAPTCHA service.
- Untrusted recipes (Hub, registry URL, MCP, Studio edits, model proposals) keep the selector cost policy (`:has()`, sibling combinators, and the depth limits).
- Logs, hooks, and repair reports do not include page bodies, query strings, or secrets.
- Studio stays loopback-only, with the per-run token, origin check, and path containment.
- Spider, pagination, and scroll cannot reset the per-source request budget on every page or step.
- Default tests stay offline. Fixtures are synthetic. Never commit a captured third-party page, credential, or personal data. Amazon, Mercado Libre, and news sites are recipes a user exports, not samples in this repository.
- Do not open, read, or edit `benchmarks/recovery/sealed/`.
- Do not replace Studio with a Node, React, Vite, or Tailwind toolchain. The loopback page is the GUI.

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

- HTTP first. Browser only when the job asks (`acquisition.engine = "browser"`) or when Studio offers it because the HTTP extract returned no records.
- Stealth is never implied by "use a browser" or by scroll.
- One browser context per source per run, reused across that source's pages and scroll steps, then closed. No cookie jar shared across hosts.
- Images, media, fonts, and WebSockets stay blocked.

## Studio (N1, N4, N5, N7, N10)

Keep the loopback Starlette app.

- **New scraper:** URL, field names, optional example values, Ask AI if a model was configured, preview, create job.
- **Progress:** a live line for the current source, page or scroll ordinal, and outcome. No page body in that line.
- **Next link:** the person clicks an element in the preview or types a selector; Studio writes the existing pagination fields.
- **Fix this site:** calls the existing repair service and requires a reason to promote.
- **Health:** last success, why it is stale, open drift, quarantined recipes, the exact command for `--on-failure`, and the same alert when the run just finished in one of those states.
- **Registry:** search and install from a directory or HTTPS URL; publish into a local registry directory. Install stays a candidate.

## Sharing (N6, N10)

Stay on the Git-directory registry. Add three synthetic bundles under `examples/registry/`. Document local install and `--registry-url` in the README. Do not publish Amazon, Mercado Libre, or any other site's HTML.

## Who builds what

Two agents, two branches cut from `docs/product-needs-n1-n8`, pull requests into that branch.

| Agent | Owns | Does not touch |
| --- | --- | --- |
| Claude | N2, N3, N9. Spider, scroll, shared request budget, same-source browser reuse, job schema, acquisition tests. N10 fetch only: new `src/rastro/application/registry_fetch.py` plus the CLI flag, using existing SSRF checks. | `src/rastro/studio/**`, `README.md`, `RASTRO_MASTER_SPEC.md`, `examples/registry/**` |
| Codex | N1, N4, N5, N6, N7, N8, N10 Studio and docs, N11. URL-first Studio, progress, next-link editor, Fix and Health, registry search/install/publish UI, README, three synthetic bundles, Amendment Log row and superseded sentences. | `src/rastro/acquisition*`, `src/rastro/application/registry_fetch.py`, parser internals, `SECURITY.md` |

Claude reviews Codex's PR. Codex reviews Claude's PR. Network and security changes need that cross-review (see `AGENTS.md`).

## Acceptance for the cut

- Offline tests cover spider caps, off-host refusal, shared budget, browser-context reuse, scroll stop-on-no-new-records, and a registry URL that fails SSRF or a bad digest without writing a recipe.
- Studio tests cover URL create, a pagination field write, a repair promote that still refuses a rejected candidate, and an install that stays a candidate.
- `README.md` leads with Studio. The comparison table says crawling, pagination, and scroll are bounded and opt-in.
- The master spec Amendment Log cites ADR 0032 and ADR 0033. No historical section is deleted.
- No new dependency in the core install. Browser and HTTP stay extras. No Node toolchain.
- Known limits are written down: no unbounded crawl, no Rastro-operated accounts server, no automatic promote, no stealth implied by a browser or a scroll.
