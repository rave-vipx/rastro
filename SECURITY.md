# Security policy

## Report a vulnerability

Please use [GitHub private vulnerability reporting](https://github.com/rave-vipx/rastro/security/advisories/new), or email **admin@rave-app.com** if that channel is unavailable. Include the affected commit or version, a minimal synthetic reproduction, and the impact. Avoid posting credentials or sensitive page contents in a public issue.

Rastro is in beta (0.7.0). Security fixes target `main` and the latest released minor version; no support SLA is offered.

## Current boundary

`extract_html` processes caller-supplied HTML without network access. `Rastro.web`
adds opt-in HTTP acquisition with an exact source allowlist, public-IP checks,
validated DNS pinning, per-hop redirect checks, verified TLS, robots rules, host
pacing, and request/body/deadline budgets. Standard HTTP(S) ports only unless the operator grants more (see the trust model below); HTTP requires
explicit opt-in and HTTPS never redirects to HTTP. Cookies are disabled by default; opt-in sessions keep bounded, exact-host cookies
in memory for one run, while ambient proxies, netrc, browser impersonation and
transport-level redirects/retries remain disabled.

401/403/451 stop further requests to that host in the client. 429 and Retry-After
produce explicit restrictions/deferred results and host cooldowns. Browser stealth mode is an operator opt-in using Scrapling/Patchright browser
fingerprints; Cloudflare solving additionally requires `solve_challenges = true`.
Automatic proxy or identity rotation, third-party CAPTCHA services and credentials
in job files remain forbidden. Robots checks and exact host/public-IP restrictions
remain mandatory; browser modes refuse egress profiles. Source allowlisting
is the caller's permission assertion; it does not confer permission to access a site.
A `Source` URL by itself never authorizes network access.

HTTP mode executes no JavaScript. Opt-in browser modes execute page JavaScript;
recipes cannot supply executable scripts. Browser contexts are fresh per page, with
no downloads, permissions or service workers. Both adapters install DNS pinning,
request routing and Chromium request-stage redirect checks, and check the final URL.
DOM bounds apply after serialization, not to browser process memory. Known challenge
markers are restricted; this heuristic is not exhaustive. See [ADR 0024](docs/decisions/0024-browser-rendering-and-stealth-opt-in.md).
Rastro resolves no remote JSON-LD contexts. See the [HTTP guide](docs/acquisition.md) for exact behavior.
Recipes accept CSS selectors or JSON pointers. They cannot contain executable code. Caller-provided Pydantic models and custom parser adapters are trusted Python code and can execute within the caller's process. Do not load untrusted schema modules.

Input size, candidate count, and JSON-LD block count have configurable limits. These limits are not a CPU-time or process-memory sandbox. Host untrusted workloads in an appropriately isolated process with external resource limits.

Evidence stores locators, not source excerpts. Query strings, fragments, and URL credentials are excluded or rejected. Validation diagnostics omit input values and custom validator messages; completion logs omit URLs and records. Extracted records and URL paths may still contain personal or sensitive data: the caller is responsible for storage, access, retention, and redaction.

Persistence is opt-in. SQLite memory retains declarative recipe definitions, successful-run metadata and context fingerprints. It does not retain raw URLs, HTML, extracted records, or JSON Schema documents/defaults. Keep the database in a trusted private directory; recipe selectors and context identifiers must not contain credentials. Unknown database versions and corruption fail without reset. `delete` and `prune` provide retention controls with SQLite secure deletion, but do not erase external backups or filesystem snapshots. Stop writers before deletion if concurrent operations must not recreate entries. See the [memory guide](docs/memory.md). CLI output is data and should be handled accordingly.

On POSIX systems dataset and recipe-memory files, and their SQLite `-journal`, `-wal` and `-shm` files, are created readable and writable by the owner only (0600), and existing files the user owns are tightened to 0600 when opened. Dataset files hold sanitized page samples for repair (`relocation_samples`), so keep them in a private directory. On Windows the folder's access rules apply. `rastro job forget-source` deletes a removed source's status, drift history and repair samples (SEC-009).

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
- **Network permissions.** Four widenings of the acquisition policy are the operator's alone
  (SEC-001, SEC-002, SEC-003, SEC-004, SEC-011), through a root flag, an SDK keyword or a
  `RASTRO_*` variable; a job file, bundle, MCP argument or model output cannot grant them.
  `--allow-private-network` (`RASTRO_ALLOW_PRIVATE_NETWORK`) permits RFC 1918, ULA, loopback
  and CGNAT destinations of allowed hosts; link-local and cloud metadata addresses,
  unspecified, multicast, reserved and mapped forms stay denied. `--allow-port N`
  (`RASTRO_ALLOWED_PORTS`) adds ports. `--ignore-robots HOST` (`RASTRO_IGNORE_ROBOTS`) stops
  robots.txt fetching and enforcement for an exact host: the operator asserts permission,
  Rastro logs one `robots_overridden` line per source and flags the result, and allowlist,
  pacing and 401/403/451/429 handling still apply. A job's `[[acquisition.auth]]` reads a
  header value from a `RASTRO_*` or allow-listed variable and sends it only to its exact host
  over HTTPS, never to robots.txt, another host or a proxy; the browser engine refuses it.
  See [Operator permissions](docs/acquisition.md#operator-permissions).
- **Gate overrides from agents.** The MCP `recipe_promote(override_gates=true)` can activate
  a quarantined candidate, including one the semantic judge vetoed, and the agent calling
  it reads untrusted pages that can prompt-inject it. It is refused unless the operator
  starts the server with `rastro mcp --allow-override-gates`
  (`build_server(..., allow_override_gates=True)`); otherwise a person runs
  `rastro recipe promote --override-gates --reason TEXT`.
- **Remote models** in `[model]` (`allow_remote = true`, an endpoint from `base_url_env`,
  or any endpoint other than loopback on the local model ports 11434, 1234, 8000 and
  8080) are used only with the operator's `--allow-remote`, `allow_remote=True` or
  `RASTRO_ALLOW_REMOTE=1` (the same opt-in that lets `RASTRO_MODEL` name a remote model),
  and the model's `api_key_env`/`base_url_env` follow the same
  secret-name rule; the refusal never echoes the name. Loopback models on those ports
  (local Ollama, LM Studio) need no opt-in. Studio's Ask AI notice reads whether content
  may leave the machine from the model it built, not from the flag.
- **Commands.** A job file can never make Rastro start a program: a `provider =
  "command"` model in a job is always refused. A command model comes only from the
  operator: `rastro.model(...)` in Python, `--model` on the CLI or the `RASTRO_MODEL`
  environment variable. A model passed in code is trusted.
- **Failure hook.** `--on-failure CMD` and `RASTRO_ON_FAILURE` come only from the operator
  running `rastro job load|run`; a job file cannot set them (unknown keys are rejected).
  The command runs without a shell (`shlex.split`), with a 30 second timeout and with
  stdout and stderr discarded. It receives outcome codes, hosts and drift levels on
  stdin, never records, page content, paths, URL queries or secrets. Its failure is
  reported without echoing the command line, which may hold a webhook token. The hook
  inherits the environment of the `rastro` process. See
  [Monitoring and alerts](docs/operations.md#failure-hook).

The trust flags may come before or after the subcommand and apply to the whole process, so
`rastro mcp` and `rastro studio` pass them to every job they run. See the
[jobs guide](docs/jobs.md#security-boundaries) and
[optional intelligence](docs/intelligence.md#operator-trust-for-models-in-job-files).

Recipes in job files, Hub bundles, MCP tool arguments (`scrape_file`, `recipe_propose`,
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

## Prompt injection

Scraped pages are written by people Rastro does not trust. Some of that text may address
the model or agent that reads it later. [ADR 0035](docs/decisions/0035-prompt-injection-defense.md)
records the design.

| Threat | Where it enters | What Rastro does |
| --- | --- | --- |
| Hidden instructions (`display:none`, `hidden`, `aria-hidden`, off-screen, zero size, comments, `<template>`, `<noscript>`) | Raw HTML and page outlines sent to a repair model or an MCP agent | Removed before any model sees the page. Extraction still runs on the original page. |
| Invisible characters (zero-width, bidi, Unicode tag smuggling) and compatibility forms | Text in prompts | Stripped and NFKC-folded in model contexts. Tag-encoded ASCII is decoded for detection, so it is still flagged in record values. |
| Visible instructions in a field ("ignore previous instructions", a fake `<\|im_start\|>system` turn, a Rastro tool name, a markdown link that leaks data in its query) | Extracted values, success samples, MCP payloads | Kept as data, never altered. Flagged as `instruction_like_content` with the field and rule ids, never the matched text. |
| A model or agent that obeys the page | Repair proposals, semantic verdicts, `recipe_propose` | The model only returns a recipe or a verdict, parsed strictly and gated by recipe CI. Auto-promotion is refused when the candidate's values, or the samples the judge reads, are flagged. The candidate stays quarantined with the reason `instruction_like_content`. |
| An agent tricked into overriding gates | `recipe_promote(override_gates=true)` | Off for MCP clients unless the operator starts the server with `--allow-override-gates`. A person can promote a held candidate with a reason and `--override-gates`. |

Model contexts use a JSON data envelope whose untrusted parts are named `untrusted_*`,
with a system rule that they are data. MCP and CLI outputs carry
`data_trust: untrusted_source_data` and, for record payloads, a `flags` list.

What Rastro cannot do: detection is a conservative pattern list, so new phrasings, other
languages or encodings get through, and an ordinary value can occasionally match. Rastro
cannot make a downstream agent immune. An agent that reads Rastro's output must treat
records as data, keep its own tool permissions narrow and confirm destructive actions.
Attribute values (`alt`, `title`, `data-*`) and `<script>` bodies still reach a repair model
in raw HTML, because selectors need them.

## Dependency maintenance

CI audits the locked core and all optional runtime dependency graphs. Dependabot checks Python dependencies and pinned GitHub Actions. A clean vulnerability scan is evidence about known advisories, not a guarantee of safety. Dependency-license and naming reviews are required before the first public package release.
