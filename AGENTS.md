# Working on Rastro

Planning documents are local and never committed: product needs, action plans, the roadmap, the master specification, design briefs and mockups, work logs and release evidence registers live in the maintainer's checkout under `local/` (ignored by Git; see "What never goes in the repository" in `CONTRIBUTING.md`). Workers receive the slice they need in their dispatch spec. Current planning source: `local/plans/action-plan-1.0.md` and `local/plans/product-needs.md` (N21–N40 for 1.0.0, capability first per ADR 0036). ADR 0032, ADR 0033 and ADR 0036 win where they repeal a product ban.

Work is coordinated in a single context: one coordinator slices the work, workers implement slices in their own worktrees, and a reviewer checks each cut. A cross-model review per cut is required for changes to persistence, security, network/egress or CI; small docs-only changes need no external review.

- Deliver one usable vertical slice at a time, with tests and documentation.
- Keep domain code independent of adapters. Use Scrapling through the parser or acquisition boundary.
- Preserve explicit failure outcomes and field provenance. Invalid or missing data cannot become successful empty output.
- Keep default tests offline. Fixtures are synthetic; never commit captured credentials or private content.
- Treat recipes as data. Core installation and tests must remain usable without AI or browsers.
- Run the checks in `CONTRIBUTING.md` before committing. Report actual results and known limitations.
- For a new milestone or architectural boundary, update the local roadmap and add an ADR under `docs/decisions/` when a tradeoff needs explanation.
- Each meaningful change identifies the relevant requirement IDs (master spec IDs or product needs N1–N40).
- Never commit planning documents, agent transcripts, PR body drafts, local evidence, credentials or private project details (see `CONTRIBUTING.md`).
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

If a skill conflicts with this file, this file wins. If this file conflicts with the local product needs on what to build next, the product needs win. If either conflicts with `SECURITY.md` on operator grants, SSRF, or recipes-as-data, `SECURITY.md` wins.
