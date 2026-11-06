# Working on Rastro

Read `RASTRO_MASTER_SPEC.md` before implementation. The active milestone is **v0.2**;
v0.1 is complete at release candidate `0.1.0rc1` (tag `v0.1.0rc1`, promoted to `main` on 2026-11-03),
not published. `docs/roadmap.md` tracks milestone scope and unfinished requirements.

- Deliver one usable vertical slice at a time, with tests and documentation.
- Keep domain code independent of adapters. Use Scrapling through the parser or acquisition boundary.
- Preserve explicit failure outcomes and field provenance. Invalid or missing data cannot become successful empty output.
- Keep default tests offline. Fixtures are synthetic; never commit captured credentials or private content.
- Treat recipes as data. Core installation and tests must remain usable without AI or browsers.
- Run the checks in `CONTRIBUTING.md` before committing. Report actual results and known limitations.
- For a new milestone or architectural boundary, update `docs/roadmap.md` and add an ADR under `docs/decisions/` when a tradeoff needs explanation.
- Each meaningful change identifies the relevant requirement IDs from the master spec.
- Never open, read, search or summarize `benchmarks/recovery/sealed/`, and never edit the frozen recovery corpus or manifest. The sealed half exists only for the final v0.2 evaluation (ADR 0011).
- Parallel work uses one worktree per slice and independent pull requests against `dev`; do not stack pull requests that will be squash-merged.
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

If a skill conflicts with this file, this file wins.
