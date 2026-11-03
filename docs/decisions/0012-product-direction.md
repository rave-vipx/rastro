# ADR 0012: provider strategy, declarative jobs, agent interface and Scrapling parity

- **Status:** accepted on 2026-10-30 by the project owner after the 0.1.0 release candidate.
- **Requirements:** AI-005, AI-007, JOB-001–003, DISC-001/002, AGENT-001–003, SCR-001–003 (master specification 1.1).

## Context

The 0.1.0 release candidate meets the section 18 acceptance criteria, but four product
expectations were not covered by the plan:

1. **Model choice.** Only a loopback Ollama adapter existed. Adopters want their own
   runtimes or hosted APIs (for example an OpenAI/Codex API key) without forking Rastro.
2. **Non-programmers and declarative configuration.** Using Rastro required Python. Studio
   (v0.4) had no shared contract to edit, and "research by terms" was out of scope.
3. **AI agents as users.** Nothing was designed for agents to drive Rastro reliably.
4. **Scrapling.** Rastro used Scrapling's parser but neither exposed its other capabilities
   nor planned to use its adaptive element relocation, which is directly relevant to repair.

## Decision

- **Provider strategy (AI-005/007, pulled forward from v0.2).** `ModelPort` remains the
  single strategy interface. A first-party OpenAI-compatible adapter covers OpenAI and other
  compatible servers. A documented contract lets adopters implement any other provider.
  Non-loopback providers need explicit opt-in and environment-referenced keys, and every
  provider keeps the same budgets and validation. See ADR 0010.
- **Declarative jobs (JOB-001/002 now, JOB-003 in v0.4).** A job file (TOML or JSON)
  describes sources, target fields, policy, region and optional recipes. It runs from the
  CLI without Python. The SDK, CLI, agent interface and Studio share this one validated,
  schema-described format through the same application services (P-008). This is the
  contract a GUI edits; the GUI itself stays in v0.4.
- **Term-based discovery (DISC-001/002, v0.3).** A search-provider strategy may propose
  URLs, but hosts enter an allowlist only after explicit approval. Discovery is optional
  and never crawls beyond approved hosts. It belongs with recurring sources in v0.3, not
  in pre-v0.2 hardening.
- **Agent interface (AGENT-001 now, AGENT-002 in v0.2).** JSON in and out with published
  JSON Schemas comes first, because an MCP server is only as reliable as the contracts
  beneath it. The MCP adapter follows in v0.2. Returned content is labelled untrusted data
  with provenance, and agent tools cannot widen policies.
- **Scrapling parity (SCR-001/002/003).** Rastro wraps rather than hides Scrapling.
  Adaptive relocation becomes a deterministic repair-candidate source in v0.2. Evasion
  features stay disabled, consistent with SEC-003 and GEO-005.

## Consequences

- Pre-v0.2 hardening grows from refactors to also include provider strategy, job files and
  machine-readable output. The [roadmap](../roadmap.md) tracks each slice.
- Remote providers create a data-egress path for page excerpts. Opt-in, documentation and
  key handling are the mitigations; Rastro still needs no paid service.
- One job format must serve programmers, agents and a future GUI. Its schema is public API
  from its first release and needs careful versioning.
- Term-based discovery introduces search-provider terms of service and approval UX; these
  are deferred until v0.3 so that they are designed together with recurring sources.
