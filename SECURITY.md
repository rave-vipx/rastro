# Security policy

## Report a vulnerability

Please use [GitHub private vulnerability reporting](https://github.com/rave-vipx/rastro/security/advisories/new), or email **admin@rave-app.com** if that channel is unavailable. Include the affected commit or version, a minimal synthetic reproduction, and the impact. Avoid posting credentials or sensitive page contents in a public issue.

Rastro is in beta (0.6.0 release candidate). Security fixes target `main` and the latest released minor version; no support SLA is offered.

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

Persistence is opt-in. SQLite memory retains declarative recipe definitions, successful-run metadata and context fingerprints. It does not retain raw URLs, HTML, extracted records, or JSON Schema documents/defaults. Keep the database in a trusted private directory; recipe selectors and context identifiers must not contain credentials. Unknown database versions and corruption fail without reset. `delete` and `prune` provide retention controls with SQLite secure deletion, but do not erase external backups or filesystem snapshots. Stop writers before deletion if concurrent operations must not recreate entries. See the [memory guide](docs/memory.md). CLI output is data and should be handled accordingly.

## Trust model for jobs, bundles and agents

A job file is configuration, and it may come from someone else: a shared repository, an
agent using the MCP server, a Studio workspace. It cannot make trust decisions on its own:

- **Egress proxies.** A proxy declared in `[acquisition.egress_profiles]` sees every
  request and receives the proxy credentials. Rastro uses it only with operator opt-in:
  `--allow-egress` on `rastro job run|preview|load`, `rastro recipe repair|promote`,
  `rastro mcp` and `rastro studio`; `allow_egress=True` in Python; or
  `RASTRO_ALLOW_EGRESS=1`. Without it the run fails closed before any request. Every
  boolean opt-in in the environment (`RASTRO_ALLOW_REMOTE`, `RASTRO_ALLOW_EGRESS`) accepts
  `1`, `true` or `yes`; anything else is off.
- **Secret environment names.** A job may make Rastro read only environment variables
  named `RASTRO_*`, or names the operator lists in `RASTRO_ALLOWED_ENV` (comma-separated)
  or with `--allow-env NAME` (`allow_env=` in Python). Otherwise a hostile job could name
  `GITHUB_TOKEN` as a proxy password and send it to its own proxy.
- **Remote models** in `[model]` (`allow_remote = true`, an endpoint from `base_url_env`,
  or any endpoint other than loopback on the local model ports 11434, 1234, 8000 and
  8080) are used only with the operator's `--allow-remote`, `allow_remote=True` or
  `RASTRO_ALLOW_REMOTE=1` (the same opt-in that lets `RASTRO_MODEL` name a remote model),
  and the model's `api_key_env`/`base_url_env` follow the same
  secret-name rule; the refusal never echoes the name. Loopback models on those ports
  (local Ollama, LM Studio) need no opt-in.
- **Commands.** A job file can never make Rastro start a program: a `provider =
  "command"` model in a job is always refused. A command model comes only from the
  operator: `rastro.model(...)` in Python, `--model` on the CLI or the `RASTRO_MODEL`
  environment variable. A model passed in code is trusted.

The trust flags may come before or after the subcommand and apply to the whole process, so
`rastro mcp` and `rastro studio` pass them to every job they run. See the
[jobs guide](docs/jobs.md#security-boundaries) and
[optional intelligence](docs/intelligence.md#operator-trust-for-models-in-job-files).

Recipes from Hub bundles, MCP tool arguments (`scrape_file`, `recipe_propose`,
`recipe_try`), Studio edits and every model proposal (scrape, suggest and repair
escalation) are checked before evaluation against a CSS selector cost policy (no `:has()`, no sibling combinators, bounded compounds,
alternatives and nesting), because lxml evaluates those shapes in quadratic or worse time
and the evaluation cannot be interrupted. A bundle's hashes give integrity, not
authenticity; see the [Hub guide](docs/hub.md#import). The MCP `job_preview` tool writes
nothing but is not advertised as read-only, because a job can fetch URLs or call a model.

## Acquisition limitations

Network controls are tested against synthetic URLs, DNS answers and transports,
including rebinding, redirects and cancellation. They are not a substitute for
an infrastructure egress policy. Custom acquisition ports are trusted and must
provide their own controls. Limits apply per client, not across processes.
The OS resolver, parsing/validation and SQLite worker threads can finish after cancellation; a begun memory write may commit after cancellation.
Acquisition timeouts do not forcibly stop native parsing or trusted Python validators.

The optional HTTP adapter uses curl_cffi, Scrapling's HTTP engine, because Scrapling's
public fetch API does not expose the required session-level pinning options.
[ADR 0002](docs/decisions/0002-policy-aware-acquisition.md) records the evidence and tradeoff.

## Optional inference

Opt-in Ollama inference sends the supplied HTML to a configured local service.
The adapter accepts only literal loopback origins, disables redirects and proxy
environment, and exposes no tools. Page/model content cannot change request options
or execute code. Strict declarative proposals must pass source/schema validation;
learned recipes retain required-field evidence checks after restart. Calls, queued
model-phase time, messages, responses, tokens and concurrency are bounded. Models
can still select semantically wrong source fields; prompt boundaries are not a
truth guarantee. Local service routing and server-side compute cancellation remain
outside the client's control. Selector literals may contain source values: keep
learned recipes/results under the same privacy policy as the source. See the
[intelligence guide](docs/intelligence.md) and [ADR 0004](docs/decisions/0004-bounded-recipe-learning.md).

## Dependency maintenance

CI audits the locked core and all optional runtime dependency graphs. Dependabot checks Python dependencies and pinned GitHub Actions. A clean vulnerability scan is evidence about known advisories, not a guarantee of safety. Dependency-license and naming reviews are required before the first public package release.
