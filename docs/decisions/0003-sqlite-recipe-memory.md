# ADR 0003: exact-context SQLite recipe memory

- **Status:** accepted for M3, v0.1 development preview.
- **Requirements:** REC-001–003, GEO-003, SEC-004/009; additive OBS-001/002 evidence.

## Problem

M2 requires a recipe on every call. Repeated acquisition needs durable instructions
without reusing an incompatible regional/schema context or silently activating an
unverified alternative. Storage must not block asynchronous acquisition workers.

## Decision

Introduce the synchronous `RecipeRepository` port and a file-backed SQLite adapter
using Python's standard library. The SDK shares one memory orchestration service
between offline and HTTP extraction, and runs it in worker threads for async calls.
The existing deterministic extraction/validation code moves into a separate function
so fallback attempts use exactly the same evidence and validation boundary.

The key contains the requested URL fingerprint, JSON Schema fingerprint, locale,
timezone, template tag, region tag and custom-validator revision. Full URL queries
affect the hash but are not persisted. An explicit context tag avoids guessing DOM
or regional compatibility. Defaults remain usable for exact-URL reuse.

Persist declarative definitions, the selected successful alternative, creation and
last-success timestamps, and a successful-run counter. The key references the
validation contract; do not serialize Python validators or JSON Schema documents
that may contain private defaults/examples. Every reuse revalidates fresh records
against the supplied model. Custom-validator changes require a new schema revision.

Only a complete successful attempt can be remembered. Definitions are immutable
within a key/version; lookup selects the highest validated version or an explicit
pinned version. Atomic SQL compare-and-upsert operations prevent lost counters,
version collisions and an older completed request superseding a newer version.
Fallback definitions remain available for explicit testing, but reuse executes only
the alternative proven successful. This avoids treating untested alternatives as
active recipes and does not introduce v0.2 repair/lifecycle machinery.

Each operation opens and closes its own connection. Writes use `BEGIN IMMEDIATE`
and a bounded SQLite busy timeout. Migration 0 → 1 creates the table/index and
application/schema markers atomically. Future versions and foreign databases are
rejected. There is no automatic destructive downgrade: reverting M3 leaves its new
database unused; explicit repository deletion/file removal is the rollback cleanup.
No pre-M3 database exists to migrate or destroy.

Use `secure_delete=ON`, explicit scoped deletion and caller-triggered last-success
retention. Do not enable WAL: short transactions and the default rollback journal
keep the local implementation and deletion behavior simple. No distributed/network
filesystem support or throughput claim is made. Payload and row sizes are bounded.

## Consequences

- No new runtime dependency, background thread, management server or automatic data sharing.
- Callers supply templates/regions and version custom validators deliberately.
- Storage errors are explicit domain exceptions, distinct from an empty lookup.
- A storage write may commit after coroutine cancellation; Python cannot stop the
  worker thread safely. The transaction remains atomic and closes its connection.
- Recipe metadata is private application data. A hash is not encryption/anonymity;
  use filesystem access controls and manage external backups separately.
- Health evidence is successful-run metadata only. Durable attempts, lifecycle
  transitions, repair, cross-URL template matching and inference remain later work.

## Verification

Offline tests cover actual process restart, full identity isolation, same-schema
custom-validator revisions, immutable versions, concurrent creation/writes,
fallback activation boundaries, corruption, unknown schema versions, lock timeout,
deletion/retention, safe diagnostics and no retained source/schema secrets.
Async tests intercept SQLite connections to assert worker-thread execution, cover
ordered batch reuse and redirects, and cancel an extraction during a blocked write.
The build verifies memory using a clean installed core wheel.

## References

- [Python sqlite3 transactions and connection handling](https://docs.python.org/3.12/library/sqlite3.html).
- [SQLite transaction behavior](https://www.sqlite.org/lang_transaction.html).
- [SQLite secure_delete limitations](https://www.sqlite.org/pragma.html#pragma_secure_delete).
