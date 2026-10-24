# Changelog

All notable user-facing changes will be recorded here. No public release has been published.

## Unreleased — v0.1 foundation

### Added

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

### Known limitations

- Ollama and the comparison benchmark remain pending. Recipe memory requires explicit template/region/validator revisions; it stores successful-run metadata, not a durable job history.
- HTTP supports public standard-port HTML sources; no JavaScript, authentication, proxies, or discovery crawling.
- Schema aliases, automatic recipe discovery, and cross-document synchronization are not supported.
