# ADR 0037: input origin decides which capabilities a job may use

Status: accepted, 2026-11-19. Builds on ADR 0035 and ADR 0036; refines ADR 0031 and ADR 0034.

## Context

ADR 0036 makes Rastro capability first: stealth rendering, challenge solving, proxy
rotation, impersonation, HTTP/3, unbounded crawls and robots handling are available to the
operator by default. It also keeps one boundary: content that comes from somewhere else
must not switch those capabilities on by itself.

Jobs and recipes reach Rastro through several doors. An operator writes a job file and
runs it from the command line, or calls the Python API. An AI agent calls Rastro's MCP
tools with arguments it chose, possibly after reading a hostile page. A recipe can be
installed from a registry or imported from a shared bundle. A model can propose a recipe
during authoring or repair. Earlier releases handled this with one switch per feature
(`--allow-stealth`), which mixed two questions: what the feature is, and who asked for it.

A job file cannot be trusted to declare its own origin: a hostile bundle would simply
claim to be the operator's.

## Decision

1. **Origin is decided by the entry point, never by the job.** There are five origins:

   | Entry point | Origin |
   | --- | --- |
   | `rastro job run`, `job load`, `job preview`; Studio; the Python API | `operator` |
   | `rastro mcp` tools | `mcp` |
   | `rastro recipe install` from a registry (CLI or Studio) | `registry` |
   | `rastro recipe import` of a bundle | `bundle` |
   | Recipes proposed by a model | `model` |

   The origin is set once, where input enters, and carried through the run in a context
   variable that defaults to `operator`. The MCP server sets `mcp` around every tool call; a
   registry install sets `registry` and keeps it while it imports the package's bundle.
   Inside an untrusted block nothing can switch back to `operator`, and nothing read from a
   job, recipe, bundle or page can change the origin.

2. **Capabilities are a closed vocabulary.** A job requests capabilities through the
   settings it uses (for example, `render = "stealth"` requests `stealth`, a page step
   requests `page_actions`). The vocabulary is: `stealth`, `solve_challenges`,
   `proxy_rotation`, `impersonation`, `http3`, `unbounded_crawl`, `robots_ignore`,
   `page_actions`, `network_capture`, `screenshots`, `persistent_session`, `real_chrome`,
   `cdp`, `solver_plugins`.

3. **Operator jobs get every capability except the grant-only ones.** `persistent_session`,
   `real_chrome`, `cdp` and `solver_plugins` touch the operator's own browser profile,
   browser installation or installed code, so even the operator grants them explicitly
   with `--allow-capability NAME` (or `RASTRO_ALLOW_CAPABILITIES`).

4. **Untrusted origins get only what the operator granted to that origin.** By default
   `mcp`, `registry`, `bundle` and `model` get nothing. The operator grants a capability to
   one origin with `--allow-untrusted ORIGIN:NAME` (or `RASTRO_ALLOW_UNTRUSTED`). A grant
   never names the `operator` origin.

5. **Refusal is explicit and safe to print.** Before any network work, Rastro compares the
   requested capabilities with the grants for the origin. A refused capability stops the
   job with the error `capability_refused`, which names the origin, each refused
   capability and the flag that would grant it. Names outside the vocabulary are refused
   and counted, never echoed. A bundle or registry recipe that requests a capability not
   granted to its origin is not imported; a model proposal that does is rejected as an
   invalid model response. Run reports list the capabilities each source used.

6. **Existing operator grants stay as they are.** Private-network targets, extra ports,
   environment variable names, command models, the failure hook and `--allow-egress` remain
   operator grants that no job can widen. `--allow-stealth` remains as a deprecated alias:
   it changes nothing for operator jobs and grants `stealth` and `solve_challenges` to
   untrusted origins; the CLI prints a `deprecated_flag` warning when it is used.

7. **Robots is an operator setting.** `[acquisition] robots = "ignore"` skips robots.txt
   for the whole job and requests `robots_ignore`, so it is honored for operator jobs and
   refused for every other origin without a grant. It is logged once per run, and checked
   again at each request so a policy built outside a job cannot bypass the gate. The
   per-host `--ignore-robots HOST` grant is unchanged.

## Alternatives considered

- **A trust field in the job file.** Rejected: the content being judged would vouch for
  itself.
- **One trust level for everything that is not the command line.** Rejected: an operator
  who trusts their own registry should not have to trust every MCP argument as well.
- **Keep per-feature switches.** Rejected: the number of switches grows with every engine
  feature, and none of them says which input it is protecting against.

## Consequences

- A capability check is one pure function of (requested capabilities, origin, grants),
  tested without a network or browser.
- Agents calling MCP tools can still run any job that needs no capability, and the
  operator can widen a single origin without widening others.
- Every new engine feature that changes what a job can reach adds one name to the
  vocabulary, and the gate covers it without a new flag.
- Operator jobs that used `render = "stealth"` keep working without the flag; jobs run
  by an MCP client that relied on `rastro mcp --allow-stealth` keep working through the
  alias, and can move to `--allow-untrusted mcp:stealth`.
- Studio's stealth switch is now only the deprecated alias, because Studio runs the
  operator's own jobs.
- The grants have no new Python keyword: SDK callers are the operator, and grant-only
  capabilities come from `RASTRO_ALLOW_CAPABILITIES`.
