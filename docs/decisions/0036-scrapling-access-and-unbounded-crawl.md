# ADR 0036: capability first, using everything the acquisition engine can do

Status: accepted, 2026-11-18. Amends ADR 0020, ADR 0024, ADR 0032 and ADR 0034 wherever they
limit what an operator can scrape.

## Context

Rastro fetches, parses and crawls through engines behind strategy interfaces; Scrapling is the
default engine today and another engine can replace or join it. Scrapling 0.5 already provides proxy
rotation (`ProxyRotator`), Cloudflare Turnstile and interstitial solving (`solve_cloudflare`),
browser impersonation and HTTP/3 in its HTTP sessions, persistent browser sessions, and a crawl
engine without a page cap that pauses and resumes from checkpoints.

Earlier ADRs turned many of these into refusals or into opt-in switches that are off by
default: no proxy rotation, mandatory robots checks, a page cap on every spider, no
impersonation, stealth and challenge solving behind grants. A first real integration hit
several of these limits on ordinary public sites. The goal of the project is to be the best
scraping tool for people who build with AI agents; limits that stop a legitimate job from
running work against that goal.

## Decision

**Capability first.** Whatever the configured engine can do, Rastro lets the operator do, by default, from
the CLI, the Python API and Studio:

1. Proxy rotation over the operator's proxies, also in browser and stealth modes.
2. Stealth rendering and Cloudflare challenge solving whenever the job asks for them.
3. Crawls without a page cap (`max_pages = "unbounded"`), with the engine's checkpoints, resume,
   autothrottle and per-domain concurrency, stopped by the run time budget.
4. Browser impersonation and HTTP/3 through the engine's HTTP sessions (decided in detail by
   a separate ADR).
5. Robots handling becomes an operator setting (`[acquisition] robots = "obey" | "ignore"`),
   default `obey`, like other crawlers. Persistent sessions and logins use the operator's
   secret store or browser profile.

**Untrusted input still cannot act for the operator.** These rules stay because they protect
the operator's own machine, credentials and AI from hostile content, and they never stop a
job the operator wrote from reaching a public site:

- A shared bundle, a registry recipe, an MCP argument or model output can request a capability
  but cannot switch on something the operator has not enabled for that source of input.
- Secrets never live in job files or bundles; job files cannot read arbitrary environment
  variables.
- Private-network targets (loopback, LAN, cloud metadata) need the existing operator grant,
  so a hostile job cannot reach the operator's network.
- Scraped text stays untrusted for any model (ADR 0035), and shared recipes stay data, never
  code.

## Consequences

- Engine capabilities are exposed through Rastro's strategy interfaces, never as raw engine
  options, so jobs and recipes do not depend on one engine.
- Defaults change: the features above are on for jobs the operator writes; the opt-in
  switches remain only as trust boundaries for content that comes from elsewhere. Studio shows
  which capabilities a run used, and every run report lists them.
- The operator is responsible for the target sites' terms and for robots; the docs say so
  where each capability is described.
- `SECURITY.md` and the guides are amended in the same change that implements each capability,
  so the published security model always matches the code.
- Tests cover: a bundle requesting a capability not enabled for bundles does not get it;
  proxies and credentials never appear in reports or evidence; an unbounded crawl resumes
  without refetching stored pages; robots `ignore` is honored only from the operator's job.
