# Security policy

## Report a vulnerability

Please use [GitHub private vulnerability reporting](https://github.com/rave-vipx/rastro/security/advisories/new), or email **admin@rave-app.com** if that channel is unavailable. Include the affected commit or version, a minimal synthetic reproduction, and the impact. Avoid posting credentials or sensitive page contents in a public issue.

Rastro is pre-alpha. Security fixes currently target `main`; no stable release or support SLA is offered yet.

## Current boundary

`extract_html` processes caller-supplied HTML without network access. `Rastro.web`
adds opt-in HTTP acquisition with an exact source allowlist, public-IP checks,
validated DNS pinning, per-hop redirect checks, verified TLS, robots rules, host
pacing, and request/body/deadline budgets. Standard HTTP(S) ports only; HTTP requires
explicit opt-in and HTTPS never redirects to HTTP. Cookies, ambient proxies, netrc,
browser impersonation, and transport-level redirects/retries are disabled.

401/403/451 stop further requests to that host in the client. 429 and Retry-After
produce explicit restrictions/deferred results and host cooldowns. There is no
identity rotation, CAPTCHA solving, or source-restriction bypass. Source allowlisting
is the caller's permission assertion; it does not confer permission to access a site.
A `Source` URL by itself never authorizes network access.

Rastro executes no JavaScript, resolves no remote JSON-LD contexts, and exposes no
management server. See the [HTTP guide](docs/acquisition.md) for exact behavior.
Recipes accept CSS selectors or JSON pointers. They cannot contain executable code. Caller-provided Pydantic models and custom parser adapters are trusted Python code and can execute within the caller's process. Do not load untrusted schema modules.

Input size, candidate count, and JSON-LD block count have configurable limits. These limits are not a CPU-time or process-memory sandbox. Host untrusted workloads in an appropriately isolated process with external resource limits.

Evidence stores locators, not source excerpts. Query strings, fragments, and URL credentials are excluded or rejected. Validation diagnostics omit input values and custom validator messages; completion logs omit URLs and records. Extracted records and URL paths may still contain personal or sensitive data: the caller is responsible for storage, access, retention, and redaction.

The `extract_html` service does not persist data. CLI output is data and should be handled accordingly.

## Acquisition limitations

Network controls are tested against synthetic URLs, DNS answers and transports,
including rebinding, redirects and cancellation. They are not a substitute for
an infrastructure egress policy. Custom acquisition ports are trusted and must
provide their own controls. Limits apply per client, not across processes.
The OS resolver and parsing/validation worker threads can finish after cancellation;
acquisition timeouts do not forcibly stop native parsing or trusted Python validators.

The optional HTTP adapter uses curl_cffi, Scrapling's HTTP engine, because Scrapling's
public fetch API does not expose the required session-level pinning options.
[ADR 0002](docs/decisions/0002-policy-aware-acquisition.md) records the evidence and tradeoff.

## Dependency maintenance

CI audits the locked core and optional HTTP runtime dependency graph. Dependabot checks Python dependencies and pinned GitHub Actions. A clean vulnerability scan is evidence about known advisories, not a guarantee of safety. Dependency-license and naming reviews are required before the first public package release.
