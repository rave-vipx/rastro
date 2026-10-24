# ADR 0002: Policy-aware HTTP acquisition through a pinned transport

- Status: Accepted; implemented
- Date: 2026-10-24
- Release: v0.1, milestone M2
- Requirements: INT-001/003/004, GEO-001/002, OBS-001/002, SEC-001–005/008

## Trust boundary and use cases

A trusted caller supplies an exact host allowlist and declarative extraction recipe.
Untrusted URLs, DNS answers, redirects, response headers, robots rules, and bodies
must not authorize private-network access, bypass source restrictions, exhaust
resources, or expose URL tokens in logs.

The service will resolve all returned addresses, reject the destination if any is
non-public, and connect to one validated address while preserving the hostname for
TLS and HTTP. Each redirect and retry gets a new validation. Automatic redirects,
proxies from the environment, cookies, browser impersonation, and transport retries
are disabled. HTTPS is the default; cleartext HTTP requires explicit policy opt-in.
Only standard HTTP(S) ports are supported in this milestone.

## Transport decision and reproducible evidence

Scrapling 0.5's public `FetcherSession` constructor does not accept `curl_options`
or `trust_env`. Its `AsyncFetcher` forwards extra request arguments to
`curl_cffi.AsyncSession.request`, whose signature also lacks session-level curl
options. Modifying `_async_curl_session` would depend on private internals.

Use **curl_cffi, Scrapling's own underlying HTTP transport**, through a narrow
`HttpTransport` port. Its public `AsyncSession(curl_options=..., trust_env=False)`
allows DNS pinning and transfer limits. Scrapling remains the HTML parsing engine.
This changes the planned acquisition facade, not the HTTP or parsing engine; no
custom HTTP implementation or second crawler is introduced. Contract tests inspect
the real transport's request options and bounded callback behavior without network.
Dependency-boundary regression tests cover curl_cffi's actual CFFI callback bridge:
its explicit `CURL_WRITEFUNC_ERROR` sentinel is required to abort a body transfer.
Protego receives preflighted robots text with directive and expansion budgets;
numeric delay validation happens before shared host state changes.
No performance advantage is claimed. Reconsider the facade when Scrapling exposes
these controls publicly.

## Delivery plan

1. Define policy, pinned destination, HTTP response, and acquisition result contracts.
2. Implement URL/DNS validation, robots enforcement, redirect checks, host pacing,
   bounded retries, request/body budgets, and safe failure classification.
3. Add an optional HTTP transport and asynchronous SDK extraction through the same
   validation/evidence service. Limit batch size and worker count.
4. Test SSRF encodings, mixed DNS answers, rebinding, redirects, robots denials,
   403/429/5xx, Retry-After, timeouts, cancellation, concurrency, body limits, logs,
   and transport options offline.
5. Update docs, CI optional-dependency coverage, and the runtime audit; open a draft
   PR after local checks, then require the full CI matrix before review/merge.

## Scope and limitations

This milestone handles public HTTP(S) HTML with explicit recipes. It does not
render JavaScript, provide authenticated sessions or proxies, interpret prices by
locale, persist recipes, crawl discovered links, or add checkpoint recovery.
`Source.locale` supplies Accept-Language when valid; timezone remains metadata and
network egress remains the current machine. Global and host limits apply to one
client/process, not distributed deployments. Source allowlisting is the caller's
permission assertion, not a substitute for obtaining permission.

References: [Scrapling HTTP API](https://scrapling.readthedocs.io/en/latest/fetching/static.html),
[curl_cffi API](https://curl-cffi.readthedocs.io/en/latest/api.html),
[libcurl RESOLVE](https://curl.se/libcurl/c/CURLOPT_RESOLVE.html).
