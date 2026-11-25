# ADR 0039: a blocked source climbs an acquisition ladder, ending in operator solver plugins

Status: proposed, 2026-11-23. Builds on ADR 0036, ADR 0037 and ADR 0038.

## Context

A job names one way to fetch its pages: plain HTTP, a browser or a stealth browser, with or
without challenge solving and proxy rotation. When a site refuses that level, the run ends
`blocked` with a hint, and the operator edits the job and runs again. Most refusals are
lifted by the next level up, and ADR 0036 already lets an operator's job use every one of
those levels without a grant. Asking the operator to climb by hand wastes a run per level.

Some sites need something Rastro does not ship, such as a CAPTCHA vendor the engine does not
solve. Operators who have such a tool want to plug it in without forking Rastro.

## Decision

1. **Climb on a block.** When a source is refused (`access_restriction` or a blocked hint),
   the same source is fetched again one level up, in this order: HTTP, HTTP with browser
   impersonation, browser, stealth browser, stealth with challenge solving, proxy rotation
   over the job's egress profiles, solver plugins. The first level that is not refused ends
   the climb. A refusal by robots.txt never climbs, and neither does a failure that is not a
   refusal (a timeout or missing data): a stronger level does not fix those.

2. **The job's own setting is the floor.** The first level is the job's policy exactly as
   written; each level above adds one capability to it and keeps everything else (hosts,
   pacing, robots, the job's own proxy rotation). A level identical to the one below is
   skipped. Structured (JSON) sources skip the browser levels. Proxy rotation is tried only
   when the job declares egress profiles and the operator passed `--allow-egress`.

3. **Every level passes the capability gate.** Before a level is tried, the capabilities it
   adds are checked for the job's origin (ADR 0037). For the operator's jobs every level up to
   proxy rotation is on by default. For agent tools, bundles, registry recipes and model output,
   a level is used only when the operator granted its capability to that origin. A refused
   level is recorded as `not_granted` and skipped; a level that cannot be built (a missing
   extra) is recorded as `failed` and skipped.

4. **The lowest working level is remembered.** Recipe memory stores, per recipe version, the
   lowest level that last returned the page (`start_level`) and the number of runs started
   there since (`level_runs`). The next run starts there and climbs only on a new block. Every
   ten runs a run starts at the floor again, so a lifted block is noticed and the cheaper
   level is remembered. A job without recipe memory always starts at its floor. A new recipe
   version (a promoted repair) starts at the floor once.

5. **Every attempt is reported.** The source's acquisition note lists each level tried and how
   it ended (`ok`, `blocked`, `failed`, `not_granted`) and the level the run started at. A
   source still refused at the top also gets the issue `ladder_exhausted`. Notes hold level
   names only: no URLs, proxy addresses or credentials.

6. **Solver plugins are operator-installed code, never data.** A plugin is a Python entry point
   in the `rastro.solvers` group of a package the operator installed. Its object is a factory:
   given the job's acquisition policy, it returns an acquisition port. There is no job key,
   recipe field, bundle file or agent argument that names, loads or configures a plugin; the
   ladder uses every installed plugin, in entry point name order, and only when the
   `solver_plugins` capability is granted. That capability is grant-only even for the operator
   (`--allow-capability solver_plugins`), because it runs third-party code on each blocked
   source. Entry points are listed by name to decide whether the level exists; a plugin is
   imported only when the level is reached and granted.

7. **What Rastro still checks around a plugin.** A plugin makes its own requests, so Rastro's
   DNS pinning and private-network guard do not reach inside it. Rastro never hands a plugin a
   source outside the job's allowed hosts, discards a result whose final URL leaves those hosts
   or uses plain HTTP without `allow_http`, discards a body over the byte limit, and counts a
   plugin that raises as unsolved. A plugin that fails to load is skipped and logged by name.

## Consequences

- A blocked run costs a few more requests to the same site before it ends, at most one per
  level. Pacing and the request budget of each level still apply.
- The recipe store moves to schema 3: two columns on `recipes`, added by a forward-only
  migration in one transaction. A file written by 0.9.0 (schema 2) is migrated on its first
  write; read-only commands ask for a writing command first, as for every schema change. An
  older Rastro refuses a schema 3 file instead of misreading it.
- `rastro.scrape` climbs the same ladder for a URL target, without memory.
- Installing a solver plugin is a trust decision equal to installing any Python package; the
  grant makes using it a second, explicit one.
