# Working on Rastro

Read `docs/product-needs.md` before `docs/roadmap.md`. The product needs are the planning source for the current cut: N21–N39 for 1.0.0 (robust extraction, discovery, nested records with AI support, a run report contract, a public dataset reader, operations, and proxy rotation, challenge solving and unbounded crawl through Scrapling per ADR 0036, and Rastro as the full harness of Scrapling), with design notes and cut order in `docs/action-plan-1.0.md`. N1–N20 shipped in 0.9.0. `docs/roadmap.md` is the history of what already shipped. `RASTRO_MASTER_SPEC.md` is reference only; ADR 0032, ADR 0033 and ADR 0036 win where they repeal a product ban. Release **0.7.0** (AI-assisted repair, monitoring and alerts, plus the v0.3–v0.5 completion wave) is delivered on top of 0.6.0, the first public PyPI release.

Work is coordinated in a single context: one coordinator slices the work, workers implement slices in their own worktrees, and a reviewer checks each cut. A cross-model review per cut is required for changes to persistence, security, network/egress or CI; small docs-only changes need no external review.

- Deliver one usable vertical slice at a time, with tests and documentation.
- Keep domain code independent of adapters. Use Scrapling through the parser or acquisition boundary.
- Preserve explicit failure outcomes and field provenance. Invalid or missing data cannot become successful empty output.
- Keep default tests offline. Fixtures are synthetic; never commit captured credentials or private content.
- Treat recipes as data. Core installation and tests must remain usable without AI or browsers.
- Run the checks in `CONTRIBUTING.md` before committing. Report actual results and known limitations.
- For a new milestone or architectural boundary, update `docs/roadmap.md` and add an ADR under `docs/decisions/` when a tradeoff needs explanation.
- Each meaningful change identifies the relevant requirement IDs from the master spec or from `docs/product-needs.md` (N1–N11).
- Never open, read, search or summarize `benchmarks/recovery/sealed/`, and never edit the frozen recovery corpus or manifest. The sealed half exists only for the final v0.2 evaluation (ADR 0011).
- Parallel work uses one worktree and branch per slice; the coordinator merges slice branches into one `cut/<name>` branch and opens a single pull request per cut against dev (see the rastro-slice skill).
- Follow docs/development/clean-code.md; split modules before they exceed its size budget.

## Agent skills

When your agent offers these skills (in Claude Code, Codex, OpenCode and Antigravity they live in the user's skill folder, `~/.agents/skills`), apply the matching one before you start, and list the ones you applied in your final report. Subagents follow the same rule.

| Task | Skill |
| --- | --- |
| Any code change | `incremental-implementation`, `test-driven-development`, `git-workflow-and-versioning`, `ponytail` (level lite or full; never ultra on persistence or security) |
| Untrusted input, secrets, sanitization, network or egress | `security-and-hardening` |
| Domain terms, ADRs, public API or module design | `domain-modeling`, `codebase-design`, `documentation-and-adrs` |
| Removing or changing a public name, a protocol or a persisted format | `deprecation-and-migration` |
| Failing tests or broken builds | `debugging-and-error-recovery` |
| Latency or throughput work | `performance-optimization` |
| Reviewing a PR, or a self-review before opening one | `code-review-and-quality` |
| Coordinating Orca workers, writing specs, merging or promoting | `rastro-slice` (its `references/checklist.md` is the definition of done) |

If a skill conflicts with this file, this file wins. If this file conflicts with `docs/product-needs.md` on what to build next, the product needs win. If either conflicts with `SECURITY.md` on operator grants, SSRF, or recipes-as-data, `SECURITY.md` wins.
