# Acquire permitted web sources

HTTP acquisition is optional. From the checkout, install with `uv sync --extra http`,
or install the local package with `python -m pip install '.[http]'`. No PyPI release
has been published yet. The core offline package has no HTTP or browser dependency.
Development dependencies include the HTTP extra so all tests run by default.

## Extract a URL

Supply a schema, explicit recipe, and the exact hosts you have permission to access:

```python
from pydantic import BaseModel
from rastro import AcquisitionPolicy, Rastro, Recipe, Source


class Article(BaseModel):
    title: str


async def extract_article():
    engine = Rastro.web(AcquisitionPolicy(allowed_hosts=("example.com",)))
    result = await engine.extract(
        source=Source(
            url="https://example.com/article",
            locale="es-UY",
            languages=("es-UY", "es", "en"),
            timezone="America/Montevideo",
            currency="UYU",
            egress="direct",
        ),
        schema=Article,
        recipe=Recipe(strategy="css", record_selector="article", fields={"title": "h1::text"}),
    )
    return result
```

The example illustrates the contract; it does not assert that this URL contains an
article. Inspect `status` and `issues` before consuming records. The runnable
[`examples/web.py`](https://github.com/rave-vipx/rastro/blob/main/examples/web.py)
extracts a product with an explicit recipe and requires `--allow-host`.

`await engine.extract_many(sources, schema=..., recipe=...)` accepts a bounded
sequence of `Source` objects and preserves their order. Each source receives its
own result, including restrictions and failures. Cancellation propagates and
cancels active workers. Use one client per event loop; reuse it for shared host
limits. No asynchronous context manager is needed: each transfer closes its session.

### Windows event loop

curl_cffi requires `add_reader` support. The tested HTTP path uses a selector loop,
which is also selected by `examples/web.py`. For a standalone script:

```python
import asyncio

with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
    result = runner.run(extract_article())
```

This works on the supported Python versions without changing global loop policy.
Windows' default Proactor loop makes curl_cffi start a fallback selector thread and
emit a warning; use the selector configuration above when warnings are errors.

## Policy defaults

| Setting | Default | Meaning |
| --- | --- | --- |
| `allowed_hosts` | Required | Exact ASCII/Punycode hostnames or IPs; no wildcard, scheme, or port. All resolved IPs must be public. |
| `allow_http` | `False` | HTTPS by default; explicit opt-in permits HTTP on port 80. HTTPS redirects never downgrade. |
| `max_concurrency` | `4` | Global active DNS/transfer slots per client; one active request per host. |
| `max_sources` | `100` | Maximum sources per batch; excessive batches raise `ResourceLimitError`. |
| `min_interval_seconds` | `1` | Minimum time after a host request completes before its next request begins. |
| `request_timeout_seconds` | `15` | Deadline covering DNS resolution and one HTTP transfer. |
| `operation_timeout_seconds` | `60` | Deadline covering acquisition, including queues, robots, redirects, and backoff. |
| `max_requests` | `30` | Attempt budget per source, including robots, redirects, retries, and failed DNS attempts. |
| `max_redirects` | `5` | Maximum document redirects and maximum redirects per robots lookup. |
| `max_retries` | `2` | Additional attempts per hop for network/DNS/timeouts and selected transient HTTP statuses. |
| `backoff_seconds` | `1` | Initial exponential retry delay. |
| `max_retry_delay_seconds` | `30` | Cap on local backoff; fallback cooldown for 429 with missing/invalid Retry-After. Never shortens a valid server delay. |
| `egress_profiles` | `{}` | Up to 16 named, administrator-declared HTTP(S) proxies. See [network egress](#network-egress). |

`Limits.max_html_bytes` bounds decoded response bytes during transfer and UTF-8
input bytes before extraction. Redirect bodies obey the same limit. Robots bodies
have a separate 512,000-byte cap. Oversized bodies produce an explicit failure;
partial bodies are never passed off as successful documents.

## Source rules and failures

Rastro identifies itself as `Rastro/0.1`, checks robots.txt for each source origin,
and uses the same protected transport for robots and documents. Rules are reused
only within one operation. A 404/410 robots response allows acquisition. Denials,
HTML instead of robots text, malformed encoding, and server failures fail closed.
Robots redirects must stay within the allowlist. `Crawl-delay` is honored; for
`Request-rate`, Rastro conservatively spaces requests by the full window.

Before parsing, robots directives must have a colon, at most 2,000 nonempty lines,
and at most 2,048 characters per line. A conservative expansion estimate is capped
at 10,000 rules and 4,000,000 characters across agent groups. Excesses produce
`robots_limit`. Nonfinite/negative crawl delays and invalid request rates are rejected
before changing host state. Rate numerator/denominator values must be positive and
at most 16 digits. Time-qualified rates and `Visit-time` scheduling are unsupported
and fail closed. These conservative limits may reject otherwise valid large files.

- **401/403/451:** restriction; no retry. The client stops requesting that host.
- **429:** restriction; no automatic retry. A shared host cooldown honors Retry-After.
- **408/425/500/502/503/504:** bounded retries; Retry-After defers to the caller with
  `retry_after_seconds` and a shared cooldown instead of retrying early.
- **Other failures:** explicit transient or permanent outcomes. Only a complete
  HTTP 200 HTML/XHTML document enters extraction. Unsupported content types,
  invalid declared encoding, and incomplete 206 responses are rejected.

`http_status` is the last received response status, including robots. Issue codes
identify the failing boundary. `source_url` identifies the final attempted document
URL; `requested_url` identifies the original input. Both omit query and fragment.
Redirected records carry evidence from the final document URL.

## Metrics and regional behavior

Metrics add acquisition/network duration, request/retry/redirect counts, received
bytes, and the number of validated observed fields. Acquisition duration includes
waiting and DNS; network duration measures transport calls, including failed calls.
Received bytes count completed HTTP responses, including robots and redirects;
interrupted transfers may be undercounted. Input bytes measure UTF-8 HTML passed
to the parser. Extraction and validation durations remain separate.

Ordered `Source.languages` set Accept-Language with descending quality weights: the
first preference has the implicit weight `1`, then weights decrease by `0.05` down
to `0.05` for the twentieth preference. When languages are empty, a valid `locale`
remains the backward-compatible unweighted header; `und` omits it. Invalid tags fail
before DNS. Locale, languages, timezone, currency, and egress are passed through
Pydantic's `model_validate` context; Rastro does not infer regional rules or currency.
The remote server decides whether to honor Accept-Language.

`Source.egress` selects the network origin; see [network egress](#network-egress).
Regional recipe isolation is documented in [recipe memory](memory.md).

## Network egress

Egress is the **network origin** of the HTTP connection. It is not browser
geolocation: Rastro v0.1 has no browser adapter, so a `geolocation` value on `Source`
is rejected rather than silently ignored, and a proxy never implies a browser-reported
location. Validators receive the egress profile name, never the proxy endpoint.

`egress="direct"` (the default) connects from the current machine through the
pinned transport. A trusted administrator may also declare named outbound proxies
in the policy; each source then selects one by name:

```python
policy = AcquisitionPolicy(
    allowed_hosts=("example.com",),
    egress_profiles={
        "eu-office": {
            "proxy_url": "http://proxy.internal.example:3128",
            "username_env": "RASTRO_EU_PROXY_USER",
            "password_env": "RASTRO_EU_PROXY_PASSWORD",
        },
        "relay": "https://relay.example:8443",  # shorthand: no credentials
    },
)
source = Source(url="https://example.com/article", egress="eu-office")
```

- **One endpoint per profile.** Profile names are 1–64 lowercase letters, digits, `_`
  or `-`, starting with a letter; `direct` is reserved. A proxy URL must be
  `http://` or `https://` host with an optional port from 1 to 65535; empty or zero
  ports, credentials, paths, queries, whitespace and SOCKS schemes are rejected.
  Policy validation errors never include input values, so a rejected inline
  credential is not echoed. The proxy host is not checked against
  `allowed_hosts` or public-address rules: declaring it is the administrator's
  explicit permission (SEC-002), so keep untrusted input out of policy construction.
- **Credentials are environment references only.** `username_env`/`password_env`
  name variables read from the process environment when each source is acquired.
  A password reference requires a username reference. Values never appear in
  recipes, recipe keys, results, metrics, logs or policy serialization.
- **Fail before network.** An unknown profile name returns `permanent_failure` with
  `unsupported_egress`, and a missing, empty or control-character credential
  returns `egress_credentials_unavailable`, both before DNS or any request.
- **No rotation or fallback.** There are no proxy pools, rotation or automatic
  fallback to another egress. A 401/403/429/451 through a proxy remains an
  `access_restriction`; the client's per-host denial and cooldown also apply to
  later sources using a different egress, so changing origin cannot bypass it.
- **Same target checks.** Every hop, including robots and redirects, is allowlisted,
  resolved locally and rejected if any address is non-public **before** it is sent to
  the proxy. The proxy is used as a CONNECT tunnel (also for `http://` targets), TLS
  still verifies the target hostname, and proxy TLS (for `https://` proxies) is
  verified. Robots, pacing, retries, body and time limits are unchanged.
- **DNS tradeoff.** The proxy resolves the target name itself, so the local DNS
  pin cannot bind the final connection and the peer-address check is skipped. A
  proxy (or its resolver) could connect elsewhere; trust the proxy accordingly. See
  [ADR 0008](decisions/0008-explicit-egress.md).

`ExtractionResult.metrics.egress` records the profile name used (`direct` for
direct connections), never the proxy URL. Supplied-HTML extraction opens no
connection but records `Source.egress` the same way, matching the recipe-memory key. Proxy failures surface as the transient
`proxy_failure` category when curl identifies them, otherwise as `network_failure`.
A custom acquisition path that hands the transport a malformed route (non-HTTP(S)
scheme, or a password without a username) gets `invalid_proxy_route`.
Recipe memory isolates recipes by egress name; see [recipe memory](memory.md).

## Security and operational boundaries

Every hop is allowlisted and re-resolved. Mixed public/private DNS answers are
rejected; the connection is pinned to one validated IP while preserving the
hostname for TLS verification. IPv4/IPv6 private, loopback, metadata, reserved,
multicast, mapped, and known transition addresses are rejected. Standard ports
only. Ambient (environment) proxies, cookies, netrc, impersonation, and automatic
redirects or transport retries are disabled; only an explicitly selected egress
profile uses a proxy. There is no identity rotation or restriction bypass.

Caller-provided adapters and schemas are trusted code. Policies apply within one
client, not across processes. System DNS runs in the platform executor; an OS DNS
call may finish after cancellation. Parsing/validation runs in a worker thread and
can also finish after cancellation. Acquisition deadlines do not forcibly stop
trusted validators or native parsers. Use process isolation for hostile workloads.

JavaScript rendering, browser geolocation, authenticated sessions, SOCKS or rotating
proxies and discovered-link crawling are not implemented. Optional [recipe memory](memory.md) and
[recipe learning](intelligence.md) integrate with the async SDK. See the
[transport ADR](decisions/0002-policy-aware-acquisition.md) for the documented use of
curl_cffi, Scrapling's HTTP engine, to expose public DNS-pinning controls.
