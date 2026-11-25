# ADR 0038: keep Rastro's pinned HTTP transport and add impersonation and HTTP/3 to it

Status: accepted, 2026-11-19. Builds on ADR 0002, ADR 0030 and ADR 0036.

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
   (default), `chrome`, `firefox`, `safari` or `edge`. The transport passes the newest
   matching curl_cffi target and the browser's default headers. Impersonation changes the
   TLS and HTTP/2 fingerprint and those headers (the browser's own `User-Agent` replaces
   Rastro's), never the job's authentication, cookies or `Accept`.

3. **Add HTTP/3 there.** A job sets `[acquisition.http] http_version` to `1.1` (default), `2`
   or `3`. `2` asks for HTTP/2 over TLS only (no cleartext upgrade). `3` tries QUIC on the
   same pinned address and falls back to TCP when the server does not answer it. With
   impersonation on and no version set, the browser's own protocol (HTTP/2) is used; a
   version the job names wins.

4. **Guards are applied last.** Pinning (`RESOLVE`), the proxy switch, disabled redirects,
   allowed protocols and the size cap are passed as curl options that curl_cffi applies
   after impersonation, so no fingerprint can override them. Each request still uses a fresh
   connection, so an `Alt-Svc` answer cannot steer a later request elsewhere, and the
   destination check after the transfer is unchanged.

5. **Missing support falls back, visibly.** HTTP/3 cannot pass a proxy's CONNECT tunnel,
   so through a proxy `3` becomes `2`. If the installed curl_cffi lacks a browser target or
   was built without HTTP/3, the request goes out on the plain profile (or HTTP/2). Each
   fallback logs one warning per process: `impersonation_unavailable` with the family, or
   `http3_unavailable` with the reason (`proxy` or `not_built`).

6. **Both are capabilities.** Impersonation requests `impersonation` and HTTP/3 requests
   `http3` (ADR 0037): on for operator jobs, granted per origin for untrusted input. The
   capability gate refuses them before any client exists.

7. **Older transports keep working.** The acquirer passes `impersonate` and
   `http_version` to the transport port only when a job sets them.

## Alternatives considered

- **Adopt the engine's HTTP session.** Rejected: no address pinning hook, so the
  private-network guard and destination check would have to be rebuilt around it or lost.
- **Add pinning to the engine upstream first.** Possible later; it would not change Rastro's
  public settings, because jobs name capabilities, not engine options.

## Consequences

- Every request keeps the same security properties whatever fingerprint or protocol it
  uses; the transport tests cover pinning, redirect checks and the destination check with
  impersonation on and with HTTP/3 on.
- The protocol version actually negotiated is not yet carried into the run report; the
  fallback warnings are the record until it is.
- Rastro tracks curl_cffi's impersonation targets itself and maps the browser families
  above to current targets in one place.
- The default stays HTTP/1.1 without impersonation, so existing jobs behave as before.
