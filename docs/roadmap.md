# Roadmap and requirement coverage

**Active release: v0.1 — Rastro Engine.** The repository delivers M1 offline extraction and M2 optional policy-aware HTTP acquisition. This is a development preview, not a completed v0.1 release.

## v0.1 milestones

| Milestone | Scope | Dependencies | Acceptance |
| --- | --- | --- | --- |
| M1 — Foundation | Public repo, package, typed offline SDK, JSON-LD/CSS recipes, validation, provenance, CLI, CI, docs | None | Offline demo, failure/boundary/property tests, strict quality checks, installable wheel |
| M2 — Acquisition | Pinned curl_cffi transport (Scrapling’s HTTP engine), HTTP source policies, URL/DNS/redirect boundaries, bounded async execution, retries and restriction outcomes | M1 | Permitted source extraction; private destinations denied; rate limits honored; no identity rotation; cancellation and concurrency tests |
| M3 — Memory | SQLite recipe repository, source/template/schema/region identity, versioned deterministic reuse | M2 | Recipes survive restarts; incompatible context cannot reuse them; async workflows do not block on storage |
| M4 — Optional intelligence | Ollama adapter, structured candidate recipes, prompt boundary, execution budgets, validation before activation | M2, M3 | No mandatory model; bounded model use; invalid model output cannot become an active recipe |
| M5 — Release evidence | Competent Scrapling baseline, reproducible results, end-to-end URL example, license/name review, package release | M1–M4 | All v0.1 acceptance criteria demonstrably pass; no unsupported quality/performance claims |

M1 and M2 are implemented with offline regression tests and quality checks. M3–M5 are pending. No release dates are promised.

## Current requirement mapping

| Requirement | Evidence / current boundary |
| --- | --- |
| INT-001 | Pydantic schemas, supplied HTML, explicit URL acquisition, bounded batches, and acquisition policy. Automatic source discovery remains out of scope. |
| INT-002 | Partial: explicit deterministic JSON-LD and CSS recipes. Ordered strategy discovery and fallback pending. |
| INT-003 | Five extraction outcomes plus explicit access restrictions, transient failures, and permanent failures. |
| INT-004, QUAL-001–004 | Field provenance, Pydantic/custom validation, explicit rejection. No source excerpt retained; defaults have no evidence. |
| REC-002 | Declarative serialization with unknown/executable fields rejected. |
| REC-001/003/004 | Pending persistence, lifecycle metadata, schema compatibility and candidate activation workflows. |
| AI-001–004 | Pending M4; the current engine has no inference path. |
| GEO-001–003 | Partial: locale drives Accept-Language, timezone is metadata, egress is local. Locale-aware value interpretation and recipe/cache isolation remain pending. |
| OBS-001 | Partial: job ID, source metadata, acquisition/network/parsing/validation/total durations, request/record/field counts. Inference and durable history pending. |
| OBS-002 | JSON completion logs with correlation IDs; no source content in events. |
| SEC-001/002/003 | Exact host policy, public destination checks, DNS pinning, per-hop validation, robots enforcement, throttling and restriction outcomes. See ADR 0002 and HTTP tests. |
| SEC-004/005/007/008 | Recipe and diagnostic boundaries, no recipe code execution, input/candidate/request/body/deadline budgets. Process sandboxing remains the host’s responsibility. |
| SEC-009/010 | No persistent data or management server in M1. Controls required when those subsystems arrive. |
| DIFF-001 | Pending M5. No comparative performance claims published. |

## M1 progress record

- **Milestone:** usable offline extraction and public repository foundation.
- **Release:** v0.1 development preview (`0.1.0.dev0`).
- **Files:** `src/rastro/`, `tests/`, `examples/`, `docs/`, packaging files, and `.github/` workflows/community files.
- **Verification:** Ruff, strict mypy, offline pytest, executable example, MkDocs strict build, wheel/sdist checks, clean wheel installation, and dependency audit. CI is the reproducible execution record.
- **Limitations:** no fetching, persistence, AI, benchmark comparison, or published package. Outcome contracts and schema restrictions are documented in the API guide.
- **Architecture:** [ADR 0001](decisions/0001-offline-vertical-slice.md).
- **Next milestone:** M2, policy-aware acquisition.

## M2 progress record

- **Milestone:** policy-aware acquisition and bounded asynchronous SDK.
- **Requirements:** INT-001/003/004, partial GEO-001–003, OBS-001/002, SEC-001–005/008.
- **Evidence:** offline SSRF/redirect/rebinding/robots/HTTP-failure tests, transport-option and body-limit contracts, cancellation/concurrency/SDK tests, strict typing, artifacts and runtime audit. CI provides cross-platform validation.
- **Limitations:** public HTML only; no browser rendering, authenticated sessions, egress selection, discovery crawler, persistence, or inference. DNS and validator threads may finish after cancellation.
- **Architecture:** [ADR 0002](decisions/0002-policy-aware-acquisition.md).
- **Next milestone:** M3, SQLite recipe memory and deterministic reuse.

## Later releases

- **v0.2 — Quality:** synthetic mutation fixtures, controlled repair, recipe CI, rollback, and explainable structural drift.
- **v0.3 — Sync:** recurring sources, record identity, incremental synchronization, historical changes, monitoring, and alerts.
- **v0.4 — Studio:** local visual operations built on the same application services.
- **v0.5 — Hub:** safe declarative recipe exchange with reproducible tests and explicit privacy boundaries.

See the [master specification](https://github.com/rave-vipx/rastro/blob/main/RASTRO_MASTER_SPEC.md) for the authoritative requirements and acceptance criteria.
