# ADR 0038: keep Rastro's pinned HTTP transport and add impersonation and HTTP/3 to it

Status: proposed, 2026-11-18. Builds on ADR 0002, ADR 0030 and ADR 0036.

## Context

Rastro's HTTP transport is a thin layer over curl_cffi. For every request it resolves the
host, rejects private and reserved addresses, pins the connection to the checked address,
disables redirects (Rastro follows them itself, checking each hop), bounds the body while
it streams, and confirms afterwards that the connection really went to the pinned address.
Today it always sends HTTP/1.1 and never presents a browser fingerprint.

ADR 0036 asks for browser impersonation and HTTP/3, which curl_cffi supports. The default
acquisition engine also ships its own HTTP session class built on curl_cffi, with
impersonation and HTTP/3 already wired. That session resolves names itself and offers no
hook to pin a connection to an address Rastro has checked, so adopting it would lose the
private-network guard and the destination check.

## Decision

1. **Keep Rastro's transport.** HTTP fetching stays in Rastro's own curl_cffi adapter. The
   engine's HTTP session class is not used for acquisition.

2. **Add impersonation there.** A job sets `[acquisition.http] impersonate` to `off`
   (default), `chrome`, `firefox`, `safari` or `edge`. The transport passes the matching
   curl_cffi target, keeps pinning and redirect handling unchanged, and keeps Rastro's own
   header policy: impersonation changes the TLS and HTTP/2 fingerprint and the browser
   default headers, never the job's authentication or cookies.

3. **Add HTTP/3 there.** A job sets `[acquisition.http] http_version` to `1.1` (default), `2`
   or `3`. HTTP/3 uses the same pinned address. When a proxy is in use, or the server does
   not offer HTTP/3, the transport falls back to HTTP/2 and the run report records the
   version actually used.

4. **Both are capabilities.** Impersonation requests `impersonation` and HTTP/3 requests
   `http3` (ADR 0037): on for operator jobs, granted per origin for untrusted input.

## Alternatives considered

- **Adopt the engine's HTTP session.** Rejected: no address pinning hook, so the
  private-network guard and destination check would have to be rebuilt around it or lost.
- **Add pinning to the engine upstream first.** Possible later; it would not change Rastro's
  public settings, because jobs name capabilities, not engine options.

## Consequences

- Every request keeps the same security properties whatever fingerprint or protocol it
  uses; the transport tests cover pinning and the destination check with impersonation on
  and with HTTP/3 on.
- Rastro tracks curl_cffi's impersonation targets itself and maps the browser families
  above to current targets in one place.
- The default stays HTTP/1.1 without impersonation, so existing jobs behave as before.
