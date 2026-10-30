# Changelog

All notable user-facing changes will be recorded here. No public release has been published.

## Unreleased — v0.1 foundation

### Added

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

### Fixed

- The Ollama adapter disables thinking. Capable models no longer spend the output budget and deadline on hidden reasoning (AI-003).
- The decoding schema now requires `record_selector` for CSS proposals and forbids it for JSON-LD, matching recipe validation (AI-002, REC-004).

### Known limitations

- Live Ollama evidence covers one local model and synthetic inputs; broader model quality remains unmeasured. The first comparison shows equal extraction quality and a shared silent semantic error; no comparative advantage is claimed. Recipe memory requires explicit template/region/validator revisions; it stores successful-run metadata, not a durable job history.
- HTTP supports public standard-port HTML sources with direct egress; no JavaScript/browser geolocation, authentication, proxies, or discovery crawling.
- Pydantic models with a custom `__init__` retain Pydantic's constructor semantics and must handle validation context themselves; Rastro does not bypass user constructors.
- Schema aliases, alternate structured API discovery, automatic stored-recipe repair, and cross-document synchronization are not supported. Discovery is limited to exact JSON-LD names and simple microdata.
