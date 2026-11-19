# ADR 0024: browser rendering and stealth opt-in

- **Status:** accepted, 2026-11-15.
- **Requirements:** SCR-003 amended, SEC-003, SEC-005, SEC-008, OBS-001, GEO-005.

## Problem

Some permitted sources expose their records only after JavaScript executes. HTTP
acquisition cannot extract those records. SCR-003's prohibition
on stealth features is lifted; consent must still come from the operator, never a job file.

## Options

1. Only HTTP: preserves the smallest runtime but cannot read JavaScript pages.
2. Plain Chromium plus a separately consented Scrapling stealth adapter: keeps the
   common case honest and exposes the requested capability behind a separate port.

## Decision

Option 2. `BrowserRenderer` is a new runtime-checkable protocol. Existing acquisition
protocols stay unchanged. Jobs select `http`, `browser`, or `stealth`; HTTP is the
default. Plain mode uses Playwright Chromium with Rastro's User-Agent. Stealth uses
Scrapling's Patchright browser fingerprint behavior. `--allow-stealth`, the Python
`allow_stealth=True` argument, or `RASTRO_ALLOW_STEALTH=1` grants consent. Challenge
solving additionally requires `acquisition.solve_challenges = true`; no external
CAPTCHA service, proxy rotation, or automatic identity rotation is configured.

Both modes reuse HTTP robots checks, host pacing, destination checks, request
budgets, deadlines and restriction outcomes. Browser requests use exact host and
scheme checks. Chromium resolver rules pin every allowed hostname to a validated
public address and disable unlisted DNS resolution. Both adapters install request
routing and Chromium Fetch request-stage interception, including redirect requests,
before navigating, and validate the final URL. Images, media, fonts and WebSockets
are blocked. Downloads, permissions and service workers are disabled; stealth
explicitly restores TLS certificate verification, which Scrapling otherwise disables.
Setup failure closes the page because Scrapling catches setup callback exceptions.

A browser and context are created for each page; cookies are not reused between
pages. Existing per-host locks serialize page rendering. DOM output is bounded by
`max_bytes` and is never truncated into success. `render_seconds` measures the
renderer invocation, including browser startup. Adapter errors and Scrapling logs
are suppressed or converted to safe categories so URLs and page contents do not
escape in diagnostics. The browser extra is optional.

## Consequences and limits

- Browser startup is paid for each page. No pool, session reuse, screenshots,
  arbitrary operator scripts, browser proxy support, or public facade exports are added.
- All listed hosts must resolve publicly before launch, even if a page does not use
  them. Operators should keep allowlists narrow. IPv4/IPv6 fallback is not attempted
  inside a browser run.
- Cloudflare resources must themselves be allowlisted; solving may fail when the
  allowlist or resource blocks prevent required resources. No success is promised.
- Known challenge markers cause an explicit restriction; novel challenge pages may
  evade this heuristic. Content validation remains necessary.
- DOM size is checked after serialization; this is an output bound, not a Chromium
  process memory bound. Browser execution is not a general hostile-code sandbox.
- The plain and stealth APIs expose DNS flags and pre-navigation routing, so neither
  has an intentionally unimplemented DNS-pinning or subresource-filtering gap.
  Runtime browser tests remain opt-in; unit tests alone do not prove Chromium's
  operating-system network isolation.
- Conflicting modes for an identical URL are rejected: `AcquisitionPort` has no source
  index and the public `Source` contract is unchanged.
