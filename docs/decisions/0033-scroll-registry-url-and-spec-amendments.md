# ADR 0033: bounded scroll, a registry URL, and how the spec stays current

- **Status:** accepted 2026-11-16 by the owner ("terminar Rastro"). Extends ADR 0032.
- **Requirements:** N9, N10, N11 in `docs/product-needs.md`. HUB-001–006 stay. REC-002, SEC-001, P-005, P-007 stay.

## Problem

ADR 0020 left three product surfaces for "a real user": infinite-style browser collection, a registry someone else can host, and a master spec that still describes an older product. The owner asked for those in this cut. A from-scratch rewrite of `RASTRO_MASTER_SPEC.md`, a multi-tenant website, and shipping another site's HTML are not required to finish them.

## Decision

### Scroll (N9)

A job may set:

```toml
[scroll]
max_steps = 10       # 1 to 30
settle_ms = 500      # 100 to 3000
stop_when = "no_new_records"
```

- Browser engine only. HTTP pages ignore `[scroll]` and say so in the run outcome.
- Each step scrolls once inside the source's browser context, waits at most `settle_ms`, and extracts again. A step with no new accepted records stops the scroll.
- Steps count toward `[spider].max_pages` and the per-source request budget. The tighter cap wins. Hitting a cap is a partial success and does not delete accepted rows.
- No stealth, no proxy rotation, and no CAPTCHA solving implied by scroll.

### Registry URL (N10)

The Git-directory registry (HUB-002–006) remains the write format. "Hosted" means any HTTPS URL that serves that directory (`index.json` plus `recipes/<id>/<version>/`).

- `rastro recipe search` and `rastro recipe install` accept `--registry-url`. Fetch uses the existing public-address and SSRF checks. Size caps match the bundle limits. The URL must be `https`, with no userinfo and no query secret. Redirects are re-checked.
- Downloaded bytes go through the same gates as a local registry: digests, selector policy, offline replay, candidate only. Install never promotes.
- Studio can search and install from a registry path or URL, and publish into a local registry directory the operator chose. Publish still does not run `git` and does not push.
- There is no account system and no Rastro-operated server. A public catalog is a Git repository or a static host of one. Recipes for a real site are published by a user who exported a sample they may redistribute.

### Spec (N11)

Do not rewrite `RASTRO_MASTER_SPEC.md`. Add one Amendment Log row for ADR 0032 and this ADR. Where a sentence still says crawling is unavailable, Studio cannot start from a URL, or there will be no hosted registry, mark that sentence superseded and point here. Historical requirement IDs stay.

## Consequences

Core install stays free of a hosted dependency. Tests for the URL fetcher use a local fake HTTP server or recorded bytes, not the public internet. Scroll tests use a fake renderer. `SECURITY.md` is unchanged.
