# ADR 0008: explicit proxy egress without rotation or fallback

- **Status:** accepted for v0.1; supersedes the direct-only egress limit of ADR 0006.
- **Requirements:** GEO-001–003, GEO-005, SEC-001–005, SEC-008, SEC-011.

## Context

GEO-001 requires explicit network egress configuration. ADR 0006 deliberately
accepted only `direct` because a non-direct selector without a secure transport
would imply behavior Rastro did not provide. ADR 0002 pins every connection to a
locally validated address with libcurl `RESOLVE` and disables ambient proxies.

An outbound proxy changes both properties: the proxy, not Rastro, resolves the
target name and opens the final connection. Proxy credentials add a secret that
must not leak into recipes, results, logs or memory keys. Proxy pools and rotation
are common ways to evade access restrictions, which GEO-005 forbids.

## Decision

A trusted administrator declares up to 16 **named egress profiles** in
`AcquisitionPolicy.egress_profiles`. Each profile is exactly one `http://` or
`https://` proxy origin (host and optional port), plus optional
`username_env`/`password_env` references to environment variables (SEC-011).
SOCKS is not supported: it would add DNS-mode choices (`socks5` vs `socks5h`) and
untested paths for little v0.1 value. A `Source` selects `direct` or one profile by
name. There is no list form, pool, rotation, health check or automatic fallback.

Before any DNS lookup, the acquirer resolves the name and reads credentials from
the environment. An unknown name (`unsupported_egress`) or a missing, empty or
control-character credential (`egress_credentials_unavailable`) is an explicit
permanent failure. Credentials live only in a short-lived `ProxyRoute` whose repr
hides them; the transport port receives it only when a profile is selected, so
direct calls and existing transports are unchanged.

The target is validated exactly as before on every hop: allowlist, scheme/port,
robots, host pacing, local DNS resolution with full public-address checks, per-hop
redirect validation and HTTPS downgrade rejection. Only then is the request handed
to curl_cffi with `CURLOPT_PROXY`, an explicit `PROXYTYPE`, `HTTPPROXYTUNNEL`
(including for `http://` targets, so the proxy is a byte relay), credential no-reuse,
and verified TLS for both target and HTTPS proxy. Session-level `trust_env=False`
still ignores environment proxies. Body, time and request budgets are unchanged.

Host denial (401/403/451) and 429 cooldown state is per client and per target host,
independent of egress. A restriction observed through one profile therefore also
blocks later direct or other-profile requests in that client: changing network
origin is never a recovery strategy.

`AcquisitionResult.egress` and `Metrics.egress` record the profile **name** only.
`RecipeKey` stores the name, so recipes and regional context are isolated per
egress. Direct keys with default regional values keep the pre-regional canonical
JSON from ADR 0006, so existing SQLite rows remain reusable.

## DNS pinning tradeoff

With a proxy, local `RESOLVE` pinning cannot bind the final connection: the CONNECT
request names the host and the proxy resolves it. The peer address curl reports is
the proxy, so the post-transfer destination check is skipped. Rastro still rejects
any target whose local resolution is non-public before contacting the proxy, which
blocks obvious SSRF attempts, but a proxy with a different resolver (or a rebinding
answer) could connect to an address Rastro never saw. The proxy is therefore inside
the trust boundary: administrators should choose proxies that enforce their own
egress controls. The proxy endpoint itself is not checked against public-address
rules, because administrators commonly use internal proxies (SEC-002 permits explicit
administrator exceptions).

## Consequences

- GEO-001 network-egress configuration is supported; browser geolocation remains
  inapplicable without a browser adapter and is rejected explicitly on `Source`
  (GEO-002).
- `Source.egress` changes from a `Literal["direct"]` to a validated name string;
  names absent from the policy fail at acquisition, not at model construction.
- Supplied-HTML extraction treats egress only as a label for validation context and
  recipe identity; it opens no connection.
- One new optional transport keyword; no new dependency, port, storage schema or
  credential persistence.

## Verification

Offline tests with fake transports and resolvers cover: no proxy option for direct
egress; the route on every robots/document transfer after local validation;
pre-DNS failures for unknown profiles and unavailable credentials; credentials
absent from results, policy serialization, reprs, logs and the SQLite file; no
fallback after 403/429 and shared host denial; redirect validation under a proxy;
private-target denial before proxy contact; profile/URL/name/reference validation;
egress-isolated recipe memory with legacy direct keys; and real curl_cffi option
construction and safe proxy error categories.
