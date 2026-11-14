# Roadmap and requirement coverage

**0.6.0 (2026-11-12): first public release of `rastro-engine`.** On top of v0.5 it adds one-call typed scraping (`rastro.scrape` with selectors declared on the Pydantic model and verified self-repair), selectors suggested from example values (`rastro.suggest`, `rastro new`), a public Python API for loading, repairing, promoting and applying recipes, a complete MCP server and a full Studio GUI, plus the fixes from an independent adoption evaluation. This roadmap is the planning source; the master spec is reference only. The engine is domain-neutral; the events example runs offline.

v0.1 remains the reference release. It is complete: `0.1.0rc1` is its release candidate, with M1–M4 implemented, the M5 technical evidence recorded, every section 18 acceptance criterion evidenced, and DIFF-001 partially demonstrated. Publication (namespace ownership, legal/trademark review, tag and Trusted Publishing) awaits maintainer approval through the release gates in issue #5; nothing has been published.

## 0.7.0 (2026-11-14): AI-assisted repair with any model, monitoring and alerts

**AI in the everyday flows (ADR 0022):** a provider-agnostic `rastro.model()` (OpenAI-compatible HTTP, any local CLI, a Python callable, `RASTRO_MODEL`) is consulted only when deterministic repair fails. Model candidates pass recipe CI, value checks and the selector policy before a veto-only semantic judge. Offline benchmark: 23/28 repaired with a good model against 12/28 without one, with 0 wrong promotions even under an approve-all judge. MCP agent tools and one operator-trust model (`--allow-remote`, `--allow-egress`, `--allow-env`) complete it.

**Delivered from an operator-notification audit:** open drift that stays open until acknowledged or repaired, exit code 4 (needs attention) and 5 (storage error after the run), `rastro health`, `rastro job ack-drift`, `rastro job forget-source`, an operator-only `--on-failure` hook, WARNING logs per failed source, clock-skew clamping and owner-only dataset files (DRIFT-004, OBS-002, OBS-007, DATA-004, REPAIR-005, SEC-009). See [Monitoring and alerts](operations.md) and [ADR 0023](decisions/0023-open-drift-and-attention-exit-codes.md).

## v0.4 and v0.5 close (2026-11-11)

**Delivered:** `rastro studio`, a loopback-only page over the same services (pick a job, validate, preview, correct one selector, accept, connect) (P-008); `rastro mcp`, a stdio MCP server for Claude Code with job validate/preview/load, dataset reads and recipe listing (AGENT-002, AGENT-003); `rastro recipe export`/`import`, Git-friendly recipe bundles verified offline before storage (HUB-001, first step). Minimal shapes and what was left out are in [ADR 0020](decisions/0020-studio-mcp-hub-minimal.md). Version 0.5.0, not published.

**Not built (waits for a real user):** React/Vite Studio, a hosted Hub registry, recipe signing, MCP write tools (promote, rollback, repair), plus everything frozen at the v0.3 close below.

**Owner decisions pending:** publish 0.5.0 (tag, PyPI; v0.1 gates in issue #5) or keep it unpublished. Nothing else on this roadmap is open.

## v0.3 close (2026-11-11)

**Delivered:** multi-source loads into a database with Spanish dates and HTML export (#65) and automatic recipe repair with a consistency gate (#66); numbers are in the [README](https://github.com/rave-vipx/rastro#readme). The live use-case test (Uruguay venues) runs outside the repository. Version 0.3.0, not published, no release candidate.

**Frozen until a real user asks (code stays):** model-assisted repair (Ollama, OpenAI-compatible adapters); the sealed recovery corpus and final v0.2 evaluation; Mock Lab and `benchmarks/product_task`; new sources needing robots exceptions or split-date parsing; the 2,230-line master spec as planning source.

| Date | Signal | Action |
| --- | --- | --- |
| 2026-11-18 | Any week with more than 10 PRs and no user-visible change | Cut scope |
| 2026-12-01 | v0.3 not available anywhere installable or viewable | Publish as is or archive |
| 2026-12-15 | No real source breakage recorded (repaired or rejected) | Stop investing in repair |

**Owner decisions pending:** superseded by the v0.4/v0.5 close above.

## v0.1 milestones

| Milestone | Scope | Dependencies | Acceptance |
| --- | --- | --- | --- |
| M1 — Foundation | Public repo, package, typed offline SDK, JSON-LD/CSS recipes, validation, provenance, CLI, CI, docs | None | Offline demo, failure/boundary/property tests, strict quality checks, installable wheel |
| M2 — Acquisition | Pinned curl_cffi transport (Scrapling’s HTTP engine), HTTP source policies, URL/DNS/redirect boundaries, bounded async execution, retries and restriction outcomes | M1 | Permitted source extraction; private destinations denied; rate limits honored; no identity rotation; cancellation and concurrency tests |
| M3 — Memory | SQLite recipe repository, source/template/schema/region identity, versioned deterministic reuse | M2 | Recipes survive restarts; incompatible context cannot reuse them; async workflows do not block on storage |
| M4 — Optional intelligence | Ollama adapter, structured candidate recipes, prompt boundary, execution budgets, validation before activation | M2, M3 | No mandatory model; bounded model use; invalid model output cannot become an active recipe |
| M5 — Release evidence | Competent Scrapling baseline, reproducible results, end-to-end URL example, license/name review, package release | M1–M4 | All v0.1 acceptance criteria demonstrably pass; no unsupported quality/performance claims |

M1–M4 are implemented with offline regression tests and quality checks. M5 technical work is complete: regression and held-out comparisons, live HTTP/reuse evidence, two-model live Ollama evidence, structured JSON responses (INT-002) and explicit egress profiles (GEO-001) are recorded. Remaining gates are human decisions: accepting partial DIFF-001 evidence for v0.1, namespace ownership, legal/trademark review and protected publishing. No release dates are promised.

## Pre-v0.2 hardening

Owner decisions after the 0.1.0 release candidate ([ADR 0012](decisions/0012-product-direction.md), specification 1.1). Slices are independent pull requests against `dev`; none is stacked on another.

| Slice | Requirements | Acceptance |
| --- | --- | --- |
| Recipe model: strategy union, definition/metadata split, internal shims removed, thinner engine ([ADR 0009](decisions/0009-recipe-model.md)) | REC-001–004, preparation for REC-005/006 | Behavior preserved; legacy SQLite rows load; no internal compatibility shims |
| Model provider strategy: OpenAI-compatible adapter and custom-provider contract ([ADR 0010](decisions/0010-model-providers.md)) | AI-001–005, AI-007, SEC-011 | Mock-server contract tests; remote opt-in; keys never serialized |
| Recovery benchmark protocol frozen before repair code ([ADR 0011](decisions/0011-recovery-benchmark.md)) | DIFF-001, section 17, REPAIR-*, DRIFT-* | Committed and hashed development and sealed corpora; pass criteria decided in advance |
| Latency profile | P-010, DIFF-001 | Measured hotspots and ranked hypotheses; no optimization without measurement |
| Field aliases in deterministic discovery | INT-002, DIFF-001 (integration effort) | Pydantic aliases and explicit alias maps resolve differently named source fields |
| Declarative jobs and machine-readable CLI | JOB-001/002, AGENT-001, INT-001 | A TOML/JSON job runs without Python; JSON output; published JSON Schemas |

### Pre-v0.2 status and handoff (2026-11-03)

| Item | State | Next step |
| --- | --- | --- |
| Product direction, specification 1.1 (#21) | Merged | None; the v0.2 order of work below follows it |
| Model provider strategy (#22) | Merged, including `max_completion_tokens`/`temperature=None` for OpenAI reasoning models | Live-test against a hosted provider when a key is available |
| Recovery benchmark protocol (#23) | Merged | None until the v0.2 evaluation |
| Latency profile (#24) | Merged: measured hotspots and ranked hypotheses | Re-run the profile after further optimizations |
| Recipe model refactor (#25) | Merged | None |
| Clean-code and module size guidelines (#28) | Merged; the module splits in #34 bring every module within the ≈250-line soft budget | None |
| Development tool updates (#29) | Merged; Dependabot now targets `dev` | None; superseded Dependabot #26/#27 closed |
| Docs use typed recipe variants (#30) | Merged | None |
| Field aliases in deterministic discovery (#31) | Merged | None |
| Latency wins: fingerprint cache and hoisted provenance (#32) | Merged; small-page gap −57 to −62%, large-page gap −48% | Re-profile as further optimizations land |
| Declarative jobs and machine-readable CLI (#35: JOB-001/002, AGENT-001) | Merged; `rastro job validate`, `rastro job run` and `rastro job schema`, with the job JSON Schemas published in `docs/schemas/` | None |
| Repository hygiene | `dev` promoted to `main` as release candidate `0.1.0rc1` on 2026-11-03 (PR #39, tag `v0.1.0rc1`); nothing published | Close issues #2–#4 (implemented) and satisfy the release gates in issue #5 |

## Re-plan of 2026-11-06 (specification 1.2)

Base for integration: `dev` (`31cbcde`). `staging` (`8f96d41`) is an ancestor of `dev` with no commits of its own; `dev` adds only #51, #52 and #43. CI is green on `main` (`3ab9dd2`, tag `v0.1.0rc1`), `staging` and `dev`. Locations of the delivered work:

| Work | Commit on `dev` | Tests | On `staging` | On `main` |
| --- | --- | --- | --- | --- |
| Declarative jobs and job CLI (JOB-001/002, AGENT-001) | `dd60825` (#35) | `tests/unit/test_jobs.py`, `test_job_fields.py`, `test_job_strict.py`; `tests/integration/test_job_*.py` | Yes | Yes |
| Recipe lifecycle and rollback (REC-005/006, REPAIR-006) | `0636610` (#46), `a66196d` (#49) | `tests/unit/test_lifecycle*.py`, `tests/integration/test_recipe_lifecycle*.py` | Yes | No |
| Mock Lab (MOCK-001–006) | `e9a01fc` (#40), `fda1103` (#45), `eb499bd` (#47), `8f96d41` (#48) | `tests/unit/mocklab/` | Yes | No |

| Status | Work |
| --- | --- |
| **Finished, kept** | v0.1 (reference); pre-v0.2 hardening; Mock Lab (MOCK-001–006); recipe lifecycle (REC-005/006, REPAIR-006); declarative jobs (JOB-001/002, AGENT-001) |
| **Brought forward v0.3 → v0.2** | Record lifecycle changes, identity and deduplication, last observation time, no deletion on failure (SYNC-002/003/005 partial/006) |
| **New in v0.2** | Dataset store separate from recipe memory, preview, approved fields, JSONL export and application query, uncertain matches, observed/inferred/unknown values (DATA-001–005); stale-source report (SYNC-008); zero-external-spend mode (COST-001); field-level drift effect (DRIFT-006); product-task comparison (DIFF-002) |
| **Stays in v0.2** | Drift (DRIFT-001–005), controlled repair (REPAIR-001–005, SCR-002), semantic consistency (QUAL-005/006), sealed-corpus evaluation (DIFF-001, ADR 0011) |
| **Moved v0.2 → v0.4** | MCP adapter (AGENT-002), once a concrete integration will consume it |
| **Reframed v0.3** | Three entry modes and observed field proposals (DISC-003); pagination, JavaScript, sessions, per-domain concurrency and run recovery chosen by pilot obstacles; scheduling with the same job (SCHED-007); paid-service limits (COST-002) |
| **Reframed v0.5** | Hub starts with Git export/import of recipe, schema, redistributable sample, test and last-verified date |

## v0.2 delivery and evaluation

The v0.2 implementation is delivered for release-candidate preparation. Evaluation remains partial, with the following evidence and limits:

1. **Pilot and preview:** [PR #59](https://github.com/rave-vipx/rastro/pull/59) records the dance agenda pilot (report kept outside the repository); [PR #55](https://github.com/rave-vipx/rastro/pull/55) adds the dataset block and preview (DATA-002, JOB-002).
2. **Known sources to a database:** [PR #56](https://github.com/rave-vipx/rastro/pull/56) adds the dataset store and [PR #61](https://github.com/rave-vipx/rastro/pull/61) delivers load, export, the example query and second-run proof (DATA-001–005, SYNC-002/003/005/006/008, COST-001) (SYNC-005 partial).
3. **Drift and repair:** [PR #62](https://github.com/rave-vipx/rastro/pull/62) delivers drift, repair candidates, consistency and recipe CI (DRIFT-001–006, REPAIR-001–006, SCR-002, QUAL-005/006).
4. **Product-task evaluation:** the [v0.2 comparison](comparison-v02.md) records partial DIFF-002 evidence and its limits. DIFF-002 is partial: human effort / minutes to a correct database were not measured (`human_minutes: null`); DIFF-001 sealed run not done: the recovery harness must be built first (see the first v0.3 task below). No sealed result is claimed.

The live pilot did not establish a complete two-source dataset. `0.2.0rc1` is not tagged or promoted; those are owner decisions, and no package has been published.

### v0.3 candidates ranked by pilot evidence

The pilot observations lead this list; items 8–11 are earlier pilot-ranked acquisition items kept below the newer ones; the "Open" line lists operational follow-ups from the v0.2 implementation, not measured pilot rankings.

**First v0.3 task (owner decision, 2026-11-08): build the recovery benchmark harness.** The DIFF-001 sealed run is still open because the runner in the [frozen protocol](recovery-benchmark.md#reproducibility) does not exist yet: `python -m benchmarks.recovery verify|run|verdict`, the five executors (`baseline_static`, `baseline_adapted` with its hashed fallback library, `rastro` through the public SDK, `always_alarm`, `never_alarm`), the `deny_all`/`approve_all` approvers, the Wilson-interval metrics, the C1–C8 verdict and the scenario-independence scan. Build and tune it on the development split only, with different authors for the baseline and the repair code, freeze the executor hashes, then run the sealed split once (ADR 0011). The sealed half has not been opened.

Status at the leftovers cut (2026-11-11):

| # | Candidate | State |
| --- | --- | --- |
| 1 | Spanish, yearless dates and ranges | Done in #65 (localized fields) |
| 2 | Sale vs event date; several schedules per card | Several dates per card: `expand:` field prefix (leftovers cut). Sale vs event date is chosen by the job's selectors |
| 3 | Partial JSON-LD field fallbacks | JSON pointer lists per field (leftovers cut) |
| 4 | Repair validated on a real change | Needs a live breakage; tripwire 2026-12-15 |
| 5 | Retention pruning | `rastro recipe prune` (leftovers cut); dataset evidence already keeps only the latest success (ADR 0019) |
| 6 | Drift calibrated on observed sources | Needs live history; waits for a user running daily loads |
| 7 | Relocation across heterogeneous sources | Done in #66 (heterogeneous matches quarantined) |
| 8 | Relative URL resolution | Done in #65 |
| 9 | IPv6/IPv4 address fallback | Done in #65 |
| 10 | Access-restriction reporting, no evasion | Done (`access_restriction` outcomes, robots refusal) |
| 11 | HTTPS downgrade and certificate diagnostics | Done (`https_downgrade`, `tls_verification_failed`) |

Open, waiting for a real user: HtmlDuplicateElement speed (Mock Lab is frozen); newcomer usability test.

## Current requirement mapping

| Requirement | Evidence / current boundary |
| --- | --- |
| DATA-001–005, SYNC-002/003/005/006/008, COST-001 | Dataset store (#56), observed preview (#55), live obstacle pilot (#59), approved load/export/status and second-run proof (#61). SYNC-005 is limited to first/last observation, not full history. Live completeness remains open. |
| INT-001 | Pydantic schemas, supplied HTML, explicit URL acquisition, bounded batches, and acquisition policy. Automatic source discovery remains out of scope. |
| INT-002 | Verified memory → bounded exact-name JSON-LD → permitted structured JSON responses (supplied or opt-in `representation="json"` acquisition, RFC 6901 `json` recipes, bounded exact-name discovery) → simple DOM microdata → optional model. Explicit fallback recipes remain supported. Rastro does not discover API endpoints or fetch redundant representations; JSON embedded in HTML scripts is out of scope. See [ADR 0007](decisions/0007-structured-responses.md). |
| INT-003 | Five extraction outcomes plus explicit access restrictions, transient failures, and permanent failures. Overflowing JSON numbers are invalid data, never success. |
| INT-004, QUAL-001–004 | Field provenance, Pydantic/custom validation, explicit rejection. No source excerpt retained; defaults have no evidence. |
| REC-002 | Declarative serialization with unknown/executable fields rejected. |
| REC-001/003, REC-005/006, REPAIR-006 | SQLite versions keyed by source/template/schema/region; mappings/fallback definitions, validation-contract references, timestamps and successful-run counts. Lifecycle states, candidate storage, history and rollback use [ADR 0015](decisions/0015-recipe-lifecycle.md); the repair CLI now gates promotion with real CI and controls the next dataset load. Automatic degradation remains open. |
| REC-004 | Model candidates require strict declarative structure, source observations, required-field evidence and full schema success before activation. Evidence policy survives restart; partial/failed candidates never activate. |
| AI-001–004 | Optional local Ollama adapter, structured proposals, capability boundary and bounded calls/corrections/deadline/context/response/concurrency. Offline contract tests cover these guarantees. Live runs with `qwen3:4b` and `llama3.2:3b` (5 trials each) verified learning, activation, zero-inference cross-process reuse and explicit failures; both failed safely on a page with distractors ([live evidence](live-ollama-evidence.md)). Broader model quality is unmeasured. |
| GEO-001–003, GEO-005 | Explicit ordered languages with descending Accept-Language weights and locale fallback; locale/languages/timezone/currency/egress reach validators through Pydantic's supported `model_validate` context path; unsupported egress fails before DNS; recipe memory isolates the ordered regional context while retaining default legacy keys. Custom model constructors follow Pydantic semantics and must handle context themselves. Named administrator-declared HTTP(S) proxy egress profiles with environment credential references, no rotation or fallback, and egress-isolated memory; a proxy resolves target DNS, so local pinning does not bind proxied connections (trusted-proxy boundary). Browser geolocation is rejected explicitly until a browser adapter exists (GEO-002). See [ADR 0006](decisions/0006-explicit-regional-context.md) and [ADR 0008](decisions/0008-explicit-egress.md). |
| OBS-001 | Per-execution job ID, source URL, start/completion times, HTTP status, acquisition method and egress profile, acquisition/network/extraction/validation/inference/total durations, request/retry/redirect counts, bytes received, records and fields validated, and outcome, in results and completion logs. Browser rendering duration is not applicable without a browser adapter. Durable, queryable history belongs to OBS-003/004 (v0.3). |
| OBS-002 | JSON completion logs with correlation IDs; no source content in events. |
| SEC-001/002/003 | Exact host policy, public destination checks, DNS pinning, per-hop validation, robots enforcement, throttling and restriction outcomes. See ADR 0002 and HTTP tests. |
| SEC-004/005/007/008 | Recipe and diagnostic boundaries, no recipe code execution, input/candidate/request/body/deadline budgets. Process sandboxing remains the host’s responsibility. |
| SEC-009/010 | Scoped deletion and last-success retention for recipe memory; no management server. External backups and OS access controls remain the host’s responsibility. |
| DIFF-002 | Partial: one offline synthetic product task with scripted repair; human effort / minutes to a correct database were not measured (`human_minutes: null`); development-agent usage was not instrumented. DIFF-001 sealed run not done. See the [v0.2 comparison](comparison-v02.md). |
| DIFF-001 | Partial: against a competent Scrapling + Pydantic baseline on a held-out synthetic set, Rastro ties an adapted baseline on source-specific configuration (10 vs 10 entries, with 33 fewer adapter lines), exposes 16/16 vs 1/16 visibility items, and reduces scripted-model calls per successful record tenfold with SQLite memory. Accuracy and silent-error detection are equal, Rastro is slower, and maintenance/recovery effort is unmeasured. See the [comparison](comparison.md). |

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

- **v0.3 — from an intent to reusable sources and recipes:** start from known sites and fields, sites only, or only a goal; source and field proposals backed by real records; pagination, JavaScript, sessions, per-domain concurrency and run recovery as the pilot requires; scheduling with the same job; freshness, coverage and cost per source; alerts.
- **v0.4 — Studio (delivered minimal, ADR 0020):** the proven path done visually (describe, review samples, correct a match, accept a recipe, connect the application), starting with the screen pilot users needed most; MCP once a concrete integration consumes it.
- **v0.5 — Hub (step 1 delivered: Git export/import, ADR 0020):** reuse a recipe with its schema, redistributable sample, test and last-verified date, starting with Git export/import; first proof: a third party publishes a recipe and another application uses it without the author's help.

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

- **Milestone:** release evidence for the `0.1.0` release candidate; technical work complete, publication pending approval.
- **Requirements:** partial DIFF-001; evidence for QUAL-003, OBS-001, REC-001–004, AI-001–004 and specification sections 17–18.
- **Files:** `benchmarks/`, offline scorer/corpus tests, `examples/catalogue.py`, its synthetic regression tests, [comparison](comparison.md), [release evidence](release-evidence.md) and CI smoke check.
- **Evidence:** independent Scrapling + Pydantic baseline, 14 synthetic cases, 50 timed samples per executor/case, primitive quality counts and source hashes. Two actual public HTTP runs in separate processes verified field provenance and recipe reuse.
- **Results:** regression corpus: equal extraction quality, 1 false success out of 8 announced successes for both. Held-out set (5 sources, 24 mutations): configuration tie with the adapted baseline, 16/16 vs 1/16 visibility items, tenfold fewer scripted-model calls with memory, equal silent errors (3/24) and higher Rastro latency. No accuracy or speed advantage is claimed.
- **Distribution:** runtime dependency license inventory, wheel/sdist contents and bundled native notices (macOS arm64) and name/docs lifecycle review recorded; no package or protected publisher configured.
- **Architecture:** [ADR 0005](decisions/0005-reproducible-release-evidence.md).
- **Live model:** [live Ollama evidence](live-ollama-evidence.md): the first `qwen3:4b` run found and fixed two adapter defects; a two-model run (`qwen3:4b`, `llama3.2:3b`, 5 trials each) passed learning, activation and zero-inference reuse, and both failed safely on the distractor page.
- **Remaining work (maintainer decisions):** accept or extend the partial DIFF-001 evidence, namespace ownership, legal/trademark review, notices for other platforms' wheels, tag and protected Trusted Publishing. Rendered snapshots and large pages do not establish browser or pagination behavior; maintenance/recovery effort and real-model quality beyond two small models are unmeasured.

## Structured-response vertical slice

- **Release:** v0.1 release candidate.
- **Requirements:** INT-001–004, QUAL-001–004, REC-001–004, SEC-005/008.
- **Public behavior:** `json` recipes with RFC 6901 record/field pointers, `extract_json`/`extract_json_async`, opt-in JSON acquisition with strict content-type and UTF-8 checks, byte/depth/node budgets, overflow rejection, `json#` provenance and representation-isolated memory.
- **Limitations:** no endpoint discovery, no model assistance for JSON, no traversal of arrays inside records, no JSON embedded in HTML.
- **Architecture:** [ADR 0007](decisions/0007-structured-responses.md).

## Explicit-egress vertical slice

- **Release:** v0.1 release candidate.
- **Requirements:** GEO-001–003, GEO-005, SEC-001–005, SEC-008, SEC-011.
- **Public behavior:** named HTTP(S) proxy profiles in `AcquisitionPolicy.egress_profiles`, selected by `Source.egress`; credentials only from environment references and never in results, logs, errors or memory; pre-DNS failures for unknown profiles; denial state shared across egress so origin changes never bypass restrictions.
- **Limitations:** no SOCKS; proxied connections cannot use the local DNS pin; the proxy is inside the trust boundary.
- **Architecture:** [ADR 0008](decisions/0008-explicit-egress.md).

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

### v0.3 B3 — consistency gates the load

Delivered QUAL-005/006 and SYNC-006 on the dataset load path: declared job rules run
against approved observations and pre-write dataset history. An inconsistent source
keeps its old rows, becomes stale with `consistency_failed`, and returns exit 3 with
rule results and `rastro recipe repair`. Consistent or unassessed sources load normally;
no rules means no semantic gate. Held observations are excluded from repair samples.
The offline repair suite records detection separately as `held` (see its README for
the before/after counts and the user-declared title-diversity rule).
