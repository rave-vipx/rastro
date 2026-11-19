# ADR 0036: proxy rotation, challenge solving and unbounded crawl through Scrapling

Status: accepted, 2026-11-19. Amends ADR 0032 (bounded spider) and ADR 0034 (no proxy
rotation). Needs N31 and N32 in `docs/product-needs.md`.

## Context

Rastro fetches and parses through Scrapling. Scrapling 0.5 already provides:

- `ProxyRotator` and per-request proxies for its fetchers and sessions;
- `solve_cloudflare` on its stealth fetcher (Turnstile and interstitial challenges);
- a crawl engine (`scrapling.spiders`) with no page cap, checkpoints that pause and resume a
  crawl, autothrottle, per-domain concurrency and blocked-request retries.

ADR 0034 refused proxy rotation and kept challenge solving behind the stealth grant. ADR 0032
required a page cap on every spider. A first real integration hit both limits on public
sites: a site that refuses cloud IPs, and a sitemap larger than the cap. Rastro's users are
people building with AI agents who expect the tool to do what its engine can already do.

## Decision

Rastro exposes these Scrapling capabilities instead of refusing them, under the same model as
stealth: the operator grants, the job requests, a bundle can never grant.

1. **Proxy rotation** builds on the existing egress profiles (`[acquisition.egress_profiles]`,
   `--allow-egress`): with the additional grant `--allow-proxy-rotation` or
   `RASTRO_ALLOW_PROXY_ROTATION=1`, Rastro rotates requests over the listed profiles with
   Scrapling's `ProxyRotator`, including browser and stealth modes, which today refuse egress
   profiles. Credentials still come only from allowed environment names (`SECURITY.md`) and are
   redacted everywhere. SSRF and allowed-host checks apply to the target URL. `SECURITY.md` is
   amended in the same change.
2. **Challenge solving** keeps `solve_challenges` behind the stealth grant and maps it to
   Scrapling's `solve_cloudflare`. Challenge types Scrapling does not solve stay `blocked`.
3. **Unbounded crawl**: a spider may set `max_pages = "unbounded"` when the operator grants
   `--allow-unbounded-crawl` or `RASTRO_ALLOW_UNBOUNDED_CRAWL=1`. The run uses Scrapling's crawl
   engine with a checkpoint in the job's state folder, resumes on the next run, and stops at
   the run time budget. Allowed hosts and robots still apply.
4. Studio shows each grant as a switch, off by default, with one sentence on what it does.
   Every run report names which grants were used.

## Consequences

- Users can reach sites that rely on Cloudflare challenges or block datacenter IPs, and crawl
  whole sites, without leaving Rastro.
- The operator is responsible for the target site's terms; the docs say so where each switch
  is described. Robots checks stay mandatory (`SECURITY.md`).
- The security model in `SECURITY.md` gains two grants (proxy rotation, unbounded crawl) and
  lets browser modes use egress profiles under them; recipes stay data and cannot raise
  privileges. Tests cover: a bundle requesting a grant does not receive it; proxies never
  appear in reports or evidence; an unbounded crawl without the grant is refused at
  validation; a resumed crawl does not refetch pages already stored.
- No new core dependency: proxy rotation and the crawl engine are in Scrapling's core;
  challenge solving stays in the `browser`/stealth extra.
