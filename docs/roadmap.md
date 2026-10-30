# Roadmap and requirement coverage

**Active release: v0.1 — Rastro Engine.** The repository delivers M1 offline extraction and M2 optional policy-aware HTTP acquisition, M3 SQLite recipe memory, and M4 bounded optional intelligence. This is a development preview, not a completed v0.1 release.

## v0.1 milestones

| Milestone | Scope | Dependencies | Acceptance |
| --- | --- | --- | --- |
| M1 — Foundation | Public repo, package, typed offline SDK, JSON-LD/CSS recipes, validation, provenance, CLI, CI, docs | None | Offline demo, failure/boundary/property tests, strict quality checks, installable wheel |
| M2 — Acquisition | Pinned curl_cffi transport (Scrapling’s HTTP engine), HTTP source policies, URL/DNS/redirect boundaries, bounded async execution, retries and restriction outcomes | M1 | Permitted source extraction; private destinations denied; rate limits honored; no identity rotation; cancellation and concurrency tests |
| M3 — Memory | SQLite recipe repository, source/template/schema/region identity, versioned deterministic reuse | M2 | Recipes survive restarts; incompatible context cannot reuse them; async workflows do not block on storage |
| M4 — Optional intelligence | Ollama adapter, structured candidate recipes, prompt boundary, execution budgets, validation before activation | M2, M3 | No mandatory model; bounded model use; invalid model output cannot become an active recipe |
| M5 — Release evidence | Competent Scrapling baseline, reproducible results, end-to-end URL example, license/name review, package release | M1–M4 | All v0.1 acceptance criteria demonstrably pass; no unsupported quality/performance claims |

M1–M4 are implemented with offline regression tests and quality checks. M5 is in progress: the first comparison and live HTTP/reuse evidence are available. One local live-model run is recorded. Broader live-model evidence, demonstrated differentiation, unfinished requirements and publication gates remain open. No release dates are promised.

## Current requirement mapping

| Requirement | Evidence / current boundary |
| --- | --- |
| INT-001 | Pydantic schemas, supplied HTML, explicit URL acquisition, bounded batches, and acquisition policy. Automatic source discovery remains out of scope. |
| INT-002 | Partial: verified memory → bounded exact-name JSON-LD → simple DOM microdata → optional model. Explicit fallback recipes remain supported. Acquisition of separate permitted structured APIs remains pending. |
| INT-003 | Five extraction outcomes plus explicit access restrictions, transient failures, and permanent failures. |
| INT-004, QUAL-001–004 | Field provenance, Pydantic/custom validation, explicit rejection. No source excerpt retained; defaults have no evidence. |
| REC-002 | Declarative serialization with unknown/executable fields rejected. |
| REC-001/003 | SQLite versions keyed by source/template/schema/region; mappings/fallback definitions, validation-contract references, creation/last-success timestamps and successful-run counts. Advanced lifecycle/health remains v0.2. |
| REC-004 | Model candidates require strict declarative structure, source observations, required-field evidence and full schema success before activation. Evidence policy survives restart; partial/failed candidates never activate. |
| AI-001–004 | Optional local Ollama adapter, structured proposals, capability boundary and bounded calls/corrections/deadline/context/response/concurrency. Offline contract tests cover these guarantees. One local `qwen3:4b` run verified learning, activation, cross-process reuse and explicit failures ([live evidence](live-ollama-evidence.md)); broader model quality is unmeasured. |
| GEO-001–003 | Partial: explicit ordered languages with descending Accept-Language weights and locale fallback; locale/languages/timezone/currency/direct-egress reach validators through Pydantic's supported `model_validate` context path; unsupported egress fails before DNS; recipe memory isolates the ordered regional context while retaining default legacy keys. Custom model constructors follow Pydantic semantics and must handle context themselves. Browser geolocation remains inapplicable until a browser adapter exists, and non-direct egress is unsupported. See [ADR 0006](decisions/0006-explicit-regional-context.md). |
| OBS-001 | Partial: job ID, source metadata, acquisition/network/parsing/validation/total durations, request/record/field counts. Inference call counts and model-phase duration are available; durable history remains pending. |
| OBS-002 | JSON completion logs with correlation IDs; no source content in events. |
| SEC-001/002/003 | Exact host policy, public destination checks, DNS pinning, per-hop validation, robots enforcement, throttling and restriction outcomes. See ADR 0002 and HTTP tests. |
| SEC-004/005/007/008 | Recipe and diagnostic boundaries, no recipe code execution, input/candidate/request/body/deadline budgets. Process sandboxing remains the host’s responsibility. |
| SEC-009/010 | Scoped deletion and last-success retention for recipe memory; no management server. External backups and OS access controls remain the host’s responsibility. |
| DIFF-001 | Partial M5 evidence: independent baseline and reproducible corpus/results. Equal quality and shared semantic failure; meaningful differentiation is not yet demonstrated. See the [comparison](comparison.md). |

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

## M3 progress record

- **Milestone:** durable recipe memory and deterministic reuse, v0.1 development preview.
- **Requirements:** REC-001–003, GEO-003, SEC-004/009; additive OBS-001/002 metadata.
- **Files:** memory domain/port, SQLite adapter, shared extraction/memory services, SDK, persistence tests, repeated-extraction example and guides.
- **Evidence:** actual process restart; context/validator revision isolation; fallback activation boundaries; concurrent writes; immutable versions; migrations/corruption; deletion/retention; async offloading, redirects and cancellation. CI also exercises the installed core wheel.
- **Limitations:** explicit identity tags; no JSON Schema/validator code persisted; success metadata only; no discovery, automatic repair, scheduler or inference. A started storage write can finish after cancellation.
- **Architecture:** [ADR 0003](decisions/0003-sqlite-recipe-memory.md).
- **Next milestone:** M4, optional bounded Ollama-assisted recipe generation.

## Later releases

- **v0.2 — Quality:** synthetic mutation fixtures, controlled repair, recipe CI, rollback, and explainable structural drift.
- **v0.3 — Sync:** recurring sources, record identity, incremental synchronization, historical changes, monitoring, and alerts.
- **v0.4 — Studio:** local visual operations built on the same application services.
- **v0.5 — Hub:** safe declarative recipe exchange with reproducible tests and explicit privacy boundaries.

See the [master specification](https://github.com/rave-vipx/rastro/blob/main/RASTRO_MASTER_SPEC.md) for the authoritative requirements and acceptance criteria.


## M4 progress record

- **Milestone:** opt-in cold-context recipe discovery and local Ollama assistance, through narrow discovery/model ports.
- **Requirements:** AI-001–004, REC-004 and partial INT-002; supporting OBS-001/002 and SEC-004/005.
- **Public behavior:** asynchronous supplied HTML or acquired sources; memory and deterministic metadata precede inference. Core extraction/discovery needs no model, browser, or HTTPX.
- **Validation:** strict JSON proposals, independent source/schema execution, required-field evidence retained after restart, success-only activation, immutable concurrent writes and M3 payload compatibility.
- **Budgets:** calls, failed-proposal corrections, queue-inclusive model deadline, complete prompt/schema bytes, response bytes, tokens, model concurrency and deterministic candidate/traversal bounds.
- **Evidence:** offline model/HTTP contract and attack tests, cancellation/budget/concurrency regressions, installed core/optional wheel checks, inference metrics and runnable synthetic example. [ADR 0004](decisions/0004-bounded-recipe-learning.md) records trust and dependency tradeoffs.
- **Known limitations:** live Ollama quality not measured; separate structured API acquisition, stored-recipe repair and tokenizer-specific fit remain outside this slice. Client cancellation does not guarantee server compute termination.
- **Next milestone:** M5, reproducible baseline, live end-to-end evidence, license/name review and package release gates.

## M5 progress record

- **Milestone:** first release-evidence slice, v0.1 development preview; M5 is not complete.
- **Requirements:** partial DIFF-001; evidence for QUAL-003, OBS-001, REC-001–003 and specification sections 17–18.
- **Files:** `benchmarks/`, offline scorer/corpus tests, `examples/catalogue.py`, its synthetic regression tests, [comparison](comparison.md), [release evidence](release-evidence.md) and CI smoke check.
- **Evidence:** independent Scrapling + Pydantic baseline, 14 synthetic cases, 50 timed samples per executor/case, primitive quality counts and source hashes. Two actual public HTTP runs in separate processes verified field provenance and recipe reuse.
- **Results:** equal extraction quality; 1 false success out of 8 announced successes for both. Rastro adds provenance and orchestration with higher measured latency. No accuracy, speed or human-effort advantage is claimed.
- **Distribution:** runtime dependency license inventory and name/docs lifecycle review recorded; no package or protected publisher configured.
- **Architecture:** [ADR 0005](decisions/0005-reproducible-release-evidence.md).
- **Live model:** [one local Ollama run](live-ollama-evidence.md) (`qwen3:4b`, synthetic inputs) found and fixed two adapter defects. Learning, activation and zero-inference reuse passed; a harder page failed safely.
- **Remaining work:** broader live-model quality evidence, held-out comparison and measurable differentiation, incomplete INT-002/GEO-001 scope, native dependency notice review, namespace ownership and release approval. Rendered snapshots and large pages do not establish browser or pagination behavior.

## Regional-context vertical slice

- **Release:** v0.1 development preview; GEO-001 remains partial.
- **Requirements:** GEO-001–003 and REC-001 compatibility.
- **Public behavior:** ordered language preferences use descending HTTP weights;
  explicit currency/timezone and direct egress reach validators through Pydantic's
  supported `model_validate` context path. Unsupported egress is rejected before DNS;
  no proxy or false geolocation is introduced.
- **Evidence:** offline contract, 20-language acquisition, CRLF, redirect, discovery,
  validator-context/custom-constructor, memory-isolation, model-currency/reuse and
  real legacy-row SQLite tests.
- **Limitations:** no browser adapter/geolocation and no non-direct egress adapter.
  Separate structured API acquisition (INT-002) and DIFF-001 evidence remain open.
- **Architecture:** [ADR 0006](decisions/0006-explicit-regional-context.md).
