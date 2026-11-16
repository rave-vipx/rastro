# Product needs N1–N8

Owner request, 2026-11-16, recorded by the coordinator. This file is the planning source for the next cut. It overrides conflicting product bans in `RASTRO_MASTER_SPEC.md`, the README comparison table, and ADR 0020. It does **not** override the security model in `SECURITY.md`.

`docs/roadmap.md` stays the history of what already shipped. This file is what to build next.

## What Rastro is for

Rastro is the scraper a person with little scraping experience can keep running.

1. They paste a URL (or a short list of URLs).
2. They name the fields they want, in plain language.
3. A model looks at the page **once**, proposes a recipe, and Rastro keeps that recipe only if it extracts valid records.
4. Later runs use the recipe and do not call a model.
5. When the site changes, Rastro says so, calls the model again, and adapts the recipe only after the same gates as today.
6. Other people can publish a recipe (a store, a classifieds site, a news list) and someone else can install it as a candidate.
7. The person can watch this from Studio, not from a TOML file and an exit code.

A hand-written Scrapling + Pydantic script can extract one page. Rastro's job is to keep doing that without silent wrong data, and to cover the sites that script cannot: JavaScript, a next page, and a bounded crawl of links the user allowed.

## Needs

| ID | Need | Done when |
| --- | --- | --- |
| N1 | URL-first Studio | Studio accepts a URL, fetches it, and builds a job. Saving HTML by hand is no longer required. |
| N2 | JavaScript as a normal path | If the HTTP body has no records, Studio offers browser rendering. Stealth stays an operator opt-in. Pages of one source reuse one browser context. |
| N3 | Bounded spider | A job can follow links (same host by default) up to a depth and a page cap, concurrently, while extracting with the recipe. |
| N4 | Pagination a non-expert can turn on | Studio can mark a next link and a page cap. The existing bounded pagination (ADR 0026) is the engine. |
| N5 | Notifications a person understands | Studio shows drift, stale sources, and quarantined recipes in words. It explains the existing `--on-failure` hook. The job file still cannot set that hook. |
| N6 | Recipes people can share | `examples/registry/` has at least three synthetic public bundles, and the README shows install, promote, load. No hosted marketplace. |
| N7 | Repair without silent corruption | Studio has one "Fix this site" action: propose, show old vs new values, promote only through the existing gates. `auto_promote` stays off unless the job already sets it. |
| N8 | A README a newcomer can finish | The first screen is the five-minute Studio path, not the specification. No unsupported accuracy claims. |

## Product bans this cut repeals

These were self-imposed. They are no longer requirements:

- "You name every page." Jobs may discover links inside the spider policy below.
- "Discovered-link crawling is not implemented" as a permanent boundary.
- Studio may only start from a saved HTML file.
- Studio must not show progress for a long run.
- A browser context must be thrown away between pages of the **same** source during one pagination or spider run. A fresh context per source is still the default across sources.

Do not rewrite `RASTRO_MASTER_SPEC.md` to match. ADR 0032 is the amendment. Where the master spec disagrees with this file on the rows above, this file wins.

## Security that stays

Do not weaken these to make N1–N8 easier:

- Recipes are data. No executable recipe code. Imported and model recipes stay candidates until a person promotes them.
- A model proposes selectors or a judgement. It never writes records.
- Invalid or missing data is never a successful empty result.
- SSRF checks, the public-address default, private-network opt-in, extra ports, and ignore-robots stay operator grants. A job file, a bundle, or a model cannot grant them.
- Stealth and challenge-solving stay operator grants (`--allow-stealth`). No proxy rotation and no external CAPTCHA service.
- Untrusted recipes (Hub, MCP, Studio edits, model proposals) keep the selector cost policy (`:has()`, sibling combinators, and the depth limits).
- Logs, hooks, and repair reports do not include page bodies, query strings, or secrets.
- Studio stays loopback-only, with the per-run token, origin check, and path containment.
- Spider and pagination cannot reset the per-source request budget on every page.
- Default tests stay offline. Fixtures are synthetic. Never commit a captured third-party page, credential, or personal data.
- Do not open, read, or edit `benchmarks/recovery/sealed/`.

## Spider policy (N3)

A job may add:

```toml
[spider]
mode = "spider"          # absent or "list" keeps today's named sources
max_depth = 2            # 1 to 4; depth 0 is the start URL only
max_pages = 25           # 1 to 100, counts every fetched document
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

Pagination (N4) stays the ADR 0026 mechanism: a `next` expression plus `max_pages` on the recipe. Spider and pagination may be used together; the tighter cap wins.

## JavaScript (N2)

- HTTP first. Browser only when the job asks (`acquisition.engine = "browser"`) or when Studio offers it because the HTTP extract returned no records.
- Stealth is never implied by "use a browser".
- One browser context per source per run, reused across that source's pages, then closed. No cookie jar shared across hosts.
- Images, media, fonts, and WebSockets stay blocked.

## Studio (N1, N4, N5, N7)

Keep the loopback Starlette app. Do not add a Node or React toolchain in this cut.

- **New scraper:** URL, field names, optional example values, Ask AI if a model was configured, preview, create job.
- **Progress:** a live line for the current source, page ordinal, and outcome. No page body in that line.
- **Next link:** the person clicks an element in the preview or types a selector; Studio writes the existing pagination fields.
- **Fix this site:** calls the existing repair service and requires a reason to promote.
- **Health:** last success, why it is stale, open drift, quarantined recipes, and the exact command for `--on-failure`.

Spider controls appear in Studio only after the `[spider]` schema exists. Until then, do not invent a second crawler in the front end.

## Sharing (N6)

Stay on the Git-directory registry (HUB-002–006). Add three synthetic bundles under `examples/registry/` (a listing, a news card, a product card). Document install, promote, and load in the README. Do not publish Amazon, Mercado Libre, or any other site's HTML. A real site recipe is something a user exports after they have the right to redistribute the sample.

## Who builds what

Two agents, two surfaces, one cut branch each, PRs against `dev`.

| Agent | Owns | Does not touch |
| --- | --- | --- |
| Claude | Spider, shared request budget, same-source browser reuse, job schema, acquisition tests, ADR 0033 if the spider design needs one | `src/rastro/studio/**`, `README.md`, `examples/registry/**` |
| Codex | Studio URL fetch, progress, next-link editor, Fix and Health copy, README five-minute path, three synthetic registry bundles | `src/rastro/acquisition*`, parser internals, `SECURITY.md` |

Claude reviews Codex's PR. Codex reviews Claude's PR. Network and security changes need that cross-review (see `AGENTS.md`).

## Acceptance for the cut

- Offline tests cover spider caps, off-host refusal, shared budget, and browser-context reuse with a fake renderer.
- Studio tests cover URL create, a pagination field write, and a repair promote that still refuses a rejected candidate.
- `README.md` leads with Studio. The comparison table no longer says crawling is unavailable; it says crawling is bounded and opt-in.
- No new dependency in the core install. Browser and HTTP stay extras.
- Known limits are written down, including: no infinite scroll, no hosted hub, no automatic promote.
