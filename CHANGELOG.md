# Changelog

All notable user-facing changes will be recorded here. No public release has been published.

## Unreleased — pre-v0.2

### Changed

- `Recipe` is now a strategy-discriminated union of `CssRecipe` (`record_selector`), `JsonLdRecipe` (optional `item_type`) and `JsonRecipe` (`record_path`), all exported. `Recipe(...)`, `Recipe.model_validate(...)`, `Recipe.model_validate_json(...)` and Pydantic fields typed `Recipe` return the matching variant, so existing recipe files, `rastro validate-recipe` and stored payloads load unchanged. Each variant rejects other strategies' selectors. `null` selectors written by earlier releases mean absent. Fallbacks are typed per representation. Type-checked code should construct the variant classes, because static checkers see only the shared fields on `Recipe(...)` (REC-002, REC-003, INT-002, QUAL-001). See ADR 0009.
- Serialized recipes (`model_dump`, `learned_recipe`, new SQLite payloads) contain only the variant's own fields and no longer include `null` selectors for other strategies (REC-002).
- `StoredRecipe` separates the definition from operational history. `selected_index`, `created_at`, `last_success_at` and `successful_runs` moved to `StoredRecipe.metadata`, a new exported `RecipeMetadata`. `stored.recipe` and `stored.validated_recipe` are unchanged. This prepares REC-005 lifecycle states without changing definitions (REC-001, REC-003).
- `RecipeProposal` is a strategy union with `CssProposal` and `JsonLdProposal` variants derived from the HTML recipe variants. `proposal_schema()` is unchanged apart from the two variant titles (REC-004, AI-002).
- `AcquisitionResult.html` is renamed to `body` because it also carries JSON (INT-002).
- Custom transports and acquisition ports must accept the keywords Rastro now always passes: `HttpTransport.get(..., proxy=None | ProxyRoute)` and `AcquisitionPort.acquire(..., representation="html" | "json")` (INT-002, GEO-005).
- Internal: prepared extraction moved from `Rastro` into `application.service.ExtractionService`, with no behavior change. SQLite schema is unchanged and needs no migration; M3 and 0.1.0 payloads keep loading (REC-001, QUAL-004).

### Added

- Frozen v0.2 recovery benchmark protocol (`docs/recovery-benchmark.md`, ADR 0011) with exact detection, restoration, false-promotion, silent-error, model-call and human-approval metrics, pass criteria for a DIFF-001 recovery claim and anti-tuning rules, plus hashed synthetic development and sealed corpora of epoch sequences (`benchmarks/recovery/`) and an offline integrity test. No executor or repair code, no measured result, and no public SDK change (DIFF-001, REPAIR-001–006, DRIFT-001–005, QUAL-005/006, MOCK-002/003).

## 0.1.0 — release candidate (not yet tagged or published)

First Rastro Engine release candidate: schema-first, verifiable extraction with
optional policy-aware HTTP acquisition, structured JSON responses, explicit egress
profiles, SQLite recipe memory and optional bounded Ollama assistance. A release
date, tag and registry publication await maintainer approval.

### Added

- Held-out DIFF-001 evaluation (`python -m benchmarks.heldout`): five new synthetic sources with 24 structural mutations, measuring source-specific configuration, silent errors when re-running working integrations, scripted-model calls per successful record with SQLite memory, an observed visibility checklist and latency against a schema-generic baseline with a structured-data adapter. Results show partial DIFF-001 evidence and no silent-error or latency advantage (DIFF-001, QUAL-003, OBS-001).
- Multi-model live Ollama harness (`--model` repeatable, `--trials`, `--keep-previous`) with recorded `qwen3:4b` and `llama3.2:3b` runs, a versioned report that keeps earlier runs, and a `--probe` mode that records raw proposals for diagnosis (AI-001–004, REC-001, REC-004).
- Permitted structured JSON responses: a declarative `json` recipe strategy with an RFC 6901 `record_path` and relative field pointers, `Rastro.extract_json`/`extract_json_async`, and opt-in `representation="json"` HTTP acquisition that sends `Accept: application/json` and rejects non-JSON content types as permanent failures (INT-001–004, SEC-001/005).
- Strict JSON budgets (`max_json_bytes`, `max_json_depth`, `max_json_nodes`), duplicate-key rejection, `json` evidence with absolute pointer locators, explicit `invalid_json`/`invalid_record`/`invalid_record_path`/`record_path_not_found` issues, and the public `Representation` type (QUAL-001–004, SEC-008).
- Deterministic JSON recipe discovery by exact field names over the root object and arrays of objects. JSON recipe memory uses representation-isolated keys, and existing HTML keys and legacy payloads are unchanged (INT-002, REC-001–004). Includes the offline `examples/structured_api.py` and ADR 0007.
- Explicit network egress profiles: `AcquisitionPolicy.egress_profiles` declares named HTTP(S) proxies selected per `Source.egress`, with environment-variable credential references, pre-DNS configuration failures, unchanged target validation, no rotation or fallback, egress-isolated recipe memory and `metrics.egress` (GEO-001–003, GEO-005, SEC-001–005, SEC-008, SEC-011). `Source` rejects `geolocation` explicitly.

- Opt-in live Ollama harness and recorded `qwen3:4b` evidence: cross-process learning and zero-inference reuse, plus labelled live/controlled failure scenarios (AI-001–004, REC-004).
- Explicit ordered language preferences with descending Accept-Language weights, currency, timezone and direct-egress context through Pydantic's supported `model_validate` path; early rejection of invalid headers and unsupported egress (GEO-001/002).
- Regional recipe isolation by language order, currency and egress with unchanged default canonical keys for existing SQLite memory (GEO-003, REC-001).

- Reproducible offline comparison against an independent Scrapling + Pydantic baseline: synthetic corpus, multiset quality scoring, raw timing samples and source hashes (DIFF-001, QUAL-003).
- Public demo-site end-to-end example with persistent recipe reuse and offline regression coverage; recorded live results, runtime license inventory and explicit v0.1 release gates (OBS-001, REC-001–003, specification sections 17–18).

- Opt-in deterministic recipe discovery, asynchronous supplied-HTML extraction and optional local Ollama assistance through `ModelPort`.
- Structured model proposals, bounded calls/corrections/deadline/context/response/concurrency, and source/schema validation before activation (AI-001–004, REC-004, INT-002).
- Learned recipes retain required-field evidence checks across restarts; M3 payloads remain reusable without false version conflicts.
- Inference metrics, attempt origins, an inspectable learned recipe, offline example, adapter/attack/budget tests and a separate `ollama` dependency extra.

- Optional SQLite recipe memory with exact source/template/schema/region identity, immutable versions, success metadata, retention and deletion.
- Deterministic reuse across restarts in offline and asynchronous SDKs, with worker-thread storage and explicit persistence failures.
- Bounded explicit fallback recipes; only the alternative validated successfully becomes reusable.
- Recipe version/reuse/attempt metadata, storage timings, a repeated-extraction example and persistence/security/concurrency tests.

- Optional policy-aware HTTP SDK with exact host allowlists, DNS pinning, robots rules, redirect checks, bounded retries and host pacing.
- Asynchronous single-source and ordered batch extraction, cancellation, request/body budgets, and explicit network outcomes.
- Acquisition metrics, regional Accept-Language, HTTP example, and offline transport/security regression tests.

- Offline HTML extraction with explicit JSON-LD and CSS recipes through Scrapling.
- Typed Pydantic results, field provenance, safe diagnostics, resource limits, and processing timings.
- CLI demo, environment diagnostics, and declarative recipe validation.
- Offline regression and property tests, strict typing, package checks, and documentation.
- Public contribution and security policies and an incremental v0.1 roadmap.
- Release evidence records built wheel/sdist contents and a bundled native notice review for `lxml` and `curl-cffi` on macOS arm64 (spec section 18 release gates).

### Fixed

- JSON-LD and JSON decoding now reject numbers that overflow or underflow a finite float (for example `1e999`, `-1e999`, `1e-999`) and integers over 4,300 digits. These are explicit invalid outcomes instead of `inf` or `0.0` records that report success (QUAL-004, SEC-005).
- `missing_evidence` issues now name each field that lacks evidence, for all strategies (QUAL-003, INT-004).
- The Ollama adapter disables thinking. Capable models no longer spend the output budget and deadline on hidden reasoning (AI-003).
- The decoding schema now requires `record_selector` for CSS proposals and forbids it for JSON-LD, matching recipe validation (AI-002, REC-004).

### Known limitations

- DIFF-001 is partially demonstrated: configuration ties an adapted Scrapling + Pydantic baseline, visibility and memory-backed inference use improve, but accuracy and silent-error detection are equal and latency is higher. See `docs/comparison.md`.
- Live Ollama evidence covers two small local models on synthetic inputs; both fail safely on a page with distractors. Broader model quality remains unmeasured.
- HTTP supports public standard-port HTML and JSON sources with direct or explicit HTTP(S) proxy egress; no JavaScript rendering, browser geolocation, authentication, SOCKS, or discovery crawling. Proxied connections cannot use the local DNS pin.
- Recipe memory requires explicit template/region/validator revisions and stores successful-run metadata, not a durable job history (v0.3). Lifecycle, repair and drift belong to v0.2.
- Pydantic models with a custom `__init__` retain Pydantic's constructor semantics and must handle validation context themselves; Rastro does not bypass user constructors.
- Schema aliases, API endpoint discovery, JSON embedded in HTML, automatic stored-recipe repair, and cross-document synchronization are not supported. Discovery is limited to exact JSON-LD names, simple microdata and exact JSON field names.
