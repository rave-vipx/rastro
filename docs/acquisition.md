# Acquire permitted web sources

HTTP acquisition is optional. From the checkout, install with `uv sync --extra http`,
or install the local package with `python -m pip install '.[http]'`. No PyPI release
has been published yet. The core offline package has no HTTP or browser dependency.
Development dependencies include the HTTP extra so all tests run by default.

## Extract a URL

Supply a schema, explicit recipe, and the exact hosts you have permission to access:

```python
from pydantic import BaseModel
from rastro import AcquisitionPolicy, CssRecipe, Rastro, Source


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
        recipe=CssRecipe(record_selector="article", fields={"title": "h1::text"}),
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
| `request_timeout_seconds` | `15` | Deadline covering DNS resolution and all address attempts for one request. |
| `operation_timeout_seconds` | `60` | Deadline covering acquisition, including queues, robots, redirects, and backoff. |
| `max_requests` | `30` | Attempt budget per source, including robots, redirects, retries, address fallback, and failed DNS attempts. |
| `max_redirects` | `5` | Maximum document redirects and maximum redirects per robots lookup. |
| `max_retries` | `2` | Additional attempts per hop for network/DNS/timeouts and selected transient HTTP statuses. |
| `backoff_seconds` | `1` | Initial exponential retry delay. |
| `max_retry_delay_seconds` | `30` | Cap on local backoff; fallback cooldown for 429 with missing/invalid Retry-After. Never shortens a valid server delay. |
| `egress_profiles` | `{}` | Up to 16 named, administrator-declared HTTP(S) proxies. See [network egress](#network-egress). |

`Limits.max_html_bytes` (`max_json_bytes` for JSON requests) bounds decoded response bytes during transfer and UTF-8
input bytes before extraction. Redirect bodies obey the same limit. Robots bodies
have a separate 512,000-byte cap. Oversized bodies produce an explicit failure;
partial bodies are never passed off as successful documents.

## JSON responses

To extract from a permitted JSON API, pass `representation="json"` with a `json`
recipe, or omit the recipe and enable discovery:

```python
from rastro import JsonRecipe

result = await engine.extract(
    source=Source(url="https://api.example.com/v1/products"),
    schema=Product,
    recipe=JsonRecipe(
        record_path="/data/products",
        fields={"name": "/name", "price": "/offer/price"},
    ),
    representation="json",
)
```

`extract_many(..., representation="json")` applies the same rules to each source.
The request uses the same allowlist, robots rules, DNS pinning, redirect checks,
pacing, retries and budgets as HTML acquisition. Only two things change:

- `Accept: application/json` replaces `Accept: text/html`. HTML requests are
  unchanged, and the header is only changed when the caller asks for JSON.
- A 200 response must declare `application/json` or an `application/*+json` type,
  such as `application/problem+json`. Any other type, including HTML, fails with
  `unsupported_content_type` (`permanent_failure`). The body is never parsed as
  another representation and nothing else is fetched. A declared charset other than
  UTF-8 fails with `invalid_encoding` (RFC 8259).

Strict decoding, the depth/node/record budgets and the outcomes are described in
[structured JSON responses](api.md#structured-json-responses). Budget breaches
report `extraction_limit`. A custom `AcquisitionPort` receives
`representation="json"` only for JSON requests. HTML calls keep the original
signature, so existing ports continue to work.

## Source rules and failures

Rastro identifies itself as `Rastro/<installed version>`, checks robots.txt for each source origin,
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
  HTTP 200 HTML/XHTML document (or JSON document, when requested) enters extraction. Unsupported content types,
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
  recipes, recipe keys, results, metrics, logs or policy serialization. A job file's
  egress profiles additionally need operator opt-in and `RASTRO_*` or allow-listed
  variable names; see [operator opt-in](jobs.md#operator-opt-in).
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
only. The operator can widen the first and the last
([Operator permissions](#operator-permissions)); a job file never can. Ambient (environment) proxies, ambient cookies, netrc, impersonation, and
automatic redirects or transport retries are disabled; only an explicitly selected egress
profile uses a proxy. There is no identity rotation or restriction bypass.

For direct egress, connection failures can fall back sequentially to up to three
validated addresses in DNS order (SEC-001/002/003). All resolved addresses are
validated before any connection, even addresses beyond that three-address cap;
a private address anywhere rejects the entire answer. Each connection keeps the
original URL, Host header and TLS hostname. A refused connection or connect timeout
can advance to the next address; an HTTP response, TLS failure, or later transfer
failure cannot trigger address fallback. Normal HTTP retries still follow
`max_retries` and Retry-After.

Address attempts share the request and operation deadlines. When alternates exist,
each connect phase gets a share of the remaining request time, leaving time for
fallback; a complete transfer can use the remaining request budget. Each attempt
counts toward `max_requests`, observes host pacing and contributes to network time;
address fallback does not increment the separate `retries` counter. Proxy egress
never uses local address fallback, because the proxy resolves the target.

Custom transports receive `PinnedTarget.alternates` (default `()`) alongside the
current `address`. Only raise `AcquisitionError(..., connect_failure=True)` when
connection setup failed before an HTTP exchange; the default is `False`. The curl
adapter requires connection timing and response metadata to prove that condition.
Missing metadata, generic errors and exhausted budgets remain explicit failures.

Caller-provided adapters and schemas are trusted code. Policies apply within one
client, not across processes. System DNS runs in the platform executor; an OS DNS
call may finish after cancellation. Parsing/validation runs in a worker thread and
can also finish after cancellation. Acquisition deadlines do not forcibly stop
trusted validators or native parsers. Use process isolation for hostile workloads.

Browser geolocation, SOCKS or rotating proxies and discovered-link crawling are not
implemented; JavaScript rendering and sessions are opt-in (see below). Optional [recipe memory](memory.md) and
[recipe learning](intelligence.md) integrate with the async SDK. See the
[transport ADR](decisions/0002-policy-aware-acquisition.md) for the documented use of
curl_cffi, Scrapling's HTTP engine, to expose public DNS-pinning controls.

## Operator permissions

Four widenings of the policy come only from the operator running Rastro, exactly like
`--allow-egress`: a root CLI flag (before or after the subcommand, so `rastro mcp` and
`rastro studio` inherit it), an SDK keyword on `run_job_file`, `run_job_async`, `run_job`
and `load_job_file` (or the `operator_trust(...)` scope), or a `RASTRO_*` environment
variable. A job file, a bundle, an MCP argument or a model output can never grant any of
them: the first three are not job fields (unknown keys are rejected). Without a grant,
behavior is unchanged (SEC-001, SEC-002, SEC-003, SEC-004, SEC-011).

| Permission | CLI | Environment | SDK |
| --- | --- | --- | --- |
| Private network | `--allow-private-network` | `RASTRO_ALLOW_PRIVATE_NETWORK=1` | `allow_private_network=True` |
| Extra ports | `--allow-port N` (repeatable) | `RASTRO_ALLOWED_PORTS="8443,8080"` | `allow_ports=[8443]` |
| Ignore robots | `--ignore-robots HOST` (repeatable) | `RASTRO_IGNORE_ROBOTS="a.example,b.example"` | `ignore_robots=["a.example"]` |

**Private network.** Permits RFC 1918, ULA `fc00::/7`, loopback and CGNAT `100.64.0.0/10`
destinations, after DNS pinning. Link-local (`169.254.0.0/16`, `fe80::/10`, the cloud
metadata range), the metadata services inside opened ranges (`fd00:ec2::254`,
`100.100.100.200`), unspecified, multicast, reserved and broadcast addresses, IPv4-mapped and
transition IPv6 forms and scoped `%` addresses stay denied. The host must still be in
`allowed_hosts`; a mixed answer with one denied address is rejected as a whole, and every
redirect hop is checked again.

**Extra ports.** Integers 1 to 65535; anything else is a `ConfigurationError`. A granted
port is accepted for `http` and `https` alike, in addition to 80 and 443.

**Ignore robots.** For each listed host (exact lowercase name, no wildcards), robots.txt is
neither fetched nor enforced, in the HTTP and browser engines. The allowlist, destination
checks, pacing, the 401/403/451 stop and 429/Retry-After cooldowns still apply. Each
overridden source logs one `robots_overridden` INFO line with the host only, and the
result's `metrics.robots_overridden` is `true` (the key is absent otherwise), so the
override can be audited. This is not an access-restriction bypass: by passing the flag the
operator asserts permission to fetch those paths. Rastro does not check that assertion.

**Auth header from the environment.** A job declares `[[acquisition.auth]]` and Rastro
sends one header with a secret read from the environment. This needs no flag because the
name follows the usual rule (`RASTRO_*` or `--allow-env NAME`); see
[the jobs guide](jobs.md#auth-header-from-the-environment). The value goes only to that
exact host over HTTPS (or loopback HTTP when the private network is granted), never to
robots.txt, a redirect to another host, or a proxy, and never appears in a log, error,
repr, result or digest. The browser engine does not support it.

## Sessions

`[acquisition] session = true` enables a bounded, in-memory cookie jar for one job
run (SCHED-005, SEC-005). The default is `false`. In the SDK, the jar belongs to
the `WebAcquirer` client: create a fresh client for each run. Cookies never enter
jobs, recipes, datasets, results, logs or object representations; they are discarded
with the client. There are no login flows or cookie credential fields.

Only the exact host that set a cookie receives it; `Domain` cannot widen its scope.
Path and expiry rules apply, and `Secure` cookies travel only over HTTPS. Cookies
are withheld after a cross-host document redirect and from another host's robots
lookup. Each host holds at most 50 cookies, each bounded to 4,096 UTF-8 bytes
including attributes. Oversized, malformed and excess cookies are dropped; the jar
counts drops without echoing their contents. curl's own cookie handling remains
disabled, and separate `Set-Cookie` response headers are preserved.

## Conditional requests

`rastro job load` automatically revalidates HTTP sources when the job declares a
`[dataset]` (SYNC-004, ALERT-006). After a successful HTTP 200 extraction and approved
load, it stores `ETag` and `Last-Modified` in the dataset database. The next load
sends `If-None-Match` and `If-Modified-Since` when available. Robots lookups are
unconditional. Validators follow only their exact document URL, including across
redirects; URL queries are hashed in validator keys and are never persisted there.

Validators are stored with a digest of the effective extraction inputs: the job, the
schema and the stored serving recipe. A load sends conditional headers only when the
current digest equals the stored one, so editing a recipe, a field or the job always
triggers a normal GET and replaces the validators; a 304 never hides such a change.
Stores without validator support (PostgreSQL) never send conditional requests, and
rendered (`browser`, `stealth`) sources are never conditional.

A solicited HTTP 304 produces `unchanged`, counts as a successful source and skips
extraction. Existing records and their field provenance stay intact, while
`last_success` and `last_attempt` advance. No missing records, failure alert or new
drift assessment is produced; existing open drift remains open. An unsolicited 304
is rejected. A subsequent successful 200 replaces both validators, including
removing validators the server no longer supplies. Failed, partial or inconsistent
loads do not replace validators. Supplied HTML and jobs without a dataset do not
use conditional requests.

Dataset schema 1 upgrades transactionally and idempotently to schema 2 on the first
write (`job load`), adding only `dataset_validators`; records and source status survive.
Owner-only permissions are retained. Unknown newer or foreign schemas are refused
without resetting the database. This is a forward-only migration: use the updated
Rastro version to open upgraded files.

## JavaScript pages

Install the optional browser runtime:

```sh
pip install 'rastro-engine[browser]'
playwright install chromium
# For stealth mode as well:
scrapling install
```

Set `[acquisition] render = "browser"` for a job default, or set `render` on an
individual URL source. An explicit source value overrides the default. Values are
`"http"` (the normal default), `"browser"` (plain Chromium, honest Rastro User-Agent)
and `"stealth"` (Scrapling's browser fingerprints). Browser modes require HTML and
direct egress; they reject egress profiles.

```toml
[acquisition]
allowed_hosts = ["example.com"]
render = "browser"

[[sources]]
url = "https://example.com/events"
render = "stealth"
```

Stealth additionally needs operator consent: `rastro --allow-stealth job run job.toml`,
`run_job_file("job.toml", allow_stealth=True)` (`load_job_file` and the `repair_job_*`
functions take the same keyword), or `RASTRO_ALLOW_STEALTH=1` (also `true`
or `yes`). A job file cannot grant consent. Cloudflare solving is off unless
`[acquisition] solve_challenges = true` and consent is present. Challenge domains must
also be explicitly allowlisted; there is no third-party solver or rotation.

A URL the job does not declare (for example a next page) uses the render mode shared by
the declared sources on the same exact host, else the `[acquisition]` default; a stealth
default therefore needs consent whenever the job renders at all.

Robots, pacing, public DNS checks and request budgets still apply. Robots are checked
again for the final URL when the browser was redirected to another path. A spent
request budget fails as `request_limit` (permanent failure), as in HTTP mode. Images, media,
fonts, WebSockets, downloads, permissions and service workers are blocked in every context
(see [Browser contexts](#browser-contexts)). Navigation uses `request_timeout_seconds`; the entire operation
uses `operation_timeout_seconds`. Oversized rendered HTML fails explicitly.
`AcquisitionResult.render_seconds` includes browser startup and rendering. Known
unsolved challenge pages and HTTP 401/403/429/451 remain restrictions.

See [ADR 0024](decisions/0024-browser-rendering-and-stealth-opt-in.md) for security
boundaries and limitations. Live browser tests require `RASTRO_LIVE_BROWSER=1`.

### Browser contexts

Pages of one source share one browser context for one run (N2,
[ADR 0034](decisions/0034-opt-in-promote-and-stealth.md)). The source's first rendered page
opens the context; its pagination pages and scroll steps reuse it, then it is closed. The
next source starts a fresh context, so two sources never share cookies or storage, and a
page on a different host inside one run renders in its own fresh context, never in the first
host's. This changes only how many contexts a source uses: the `render` key, the
operator's stealth grant, robots, pacing and the request budget are as before. Every
navigation still goes through the destination pin, the network guard and the shared
request budget, and images, media, fonts and WebSockets stay blocked.

A renderer opts in with the separate `BrowserSessions` protocol (`ports/browser.py`):
`session(targets, *, policy)` returns an async context manager whose session has
`render(...)` and `scroll(...)`. `PlaywrightBrowser` and `ScraplingStealth` implement it. A
renderer that only has `render(...)` keeps the old behavior of one fresh context per page.
`rastro.application.browser_sessions.source_session()` is the scope; nested scopes reuse the
outer one, so a crawl can wrap its whole start source in one.

### Scroll

A job can scroll a rendered page to load more records (N9,
[ADR 0033](decisions/0033-scroll-registry-url-and-spec-amendments.md)); see the
[`[scroll]` reference](jobs.md#scroll). Each step scrolls the source's open context once,
waits at most `settle_ms`, and extracts again with the recipe. The first extraction must
find records; a step that finds no new record (by record equality) ends the scroll normally.

Steps spend the same per-source request budget as pagination, and the tighter cap
wins. A scrolled source follows the pagination boundary too: its redirects cannot leave the
source host or downgrade HTTPS. Reaching `max_steps` (`scroll_max_steps`), the request budget (`scroll_budget`), the
record limit (`scroll_record_limit`), a step that rejects new records
(`scroll_records_rejected`) or a failing step (`scroll_step_failed`) ends as
`partial_success` with every accepted record kept and the issue code on the result, so a
dataset load does not mark unseen rows missing. With `render = "http"` the table is
ignored: the result stays successful and carries the issue `scroll_ignored_http`.

Scroll is a browser step, not a stealth feature: it builds no stealth browser, asks for no
grant and never solves challenges. The stealth browser keeps pages of a source in one
context, but Scrapling closes each page after a fetch, so it cannot scroll a live page yet:
its first step ends as `scroll_step_failed`.
