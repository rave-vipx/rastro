# ADR 0025: PostgreSQL datasets with local operational state

- **Status:** accepted for 0.7.0.
- **Requirements:** DATA-001, DATA-003, SYNC-002, SYNC-003, SYNC-006, SEC-011.

## Problem

Users want jobs to populate their own application database. Moving recipe memory,
drift history and relocation evidence would also couple local repair workflows to a
remote service, without helping applications query records.

## Decision

Offer PostgreSQL as an optional dataset adapter behind the existing DatasetStore and
SourceFailureRecorder ports. Only approved records and source status move. Operational
state remains in local SQLite; recipe memory retains its existing path. SQLite stays
the default and core installs and default tests need no PostgreSQL driver or server.

Both adapters share pure snapshot classification, identity and provenance code. A
shared contract suite verifies observable behavior and byte-identical JSONL. PostgreSQL
stores the canonical record JSON as text, allowing SQL JSON casts without changing
serialization order. SHA-256 identity indexes avoid PostgreSQL btree key-size limits
for valid long identities; the original identity remains queryable. Each snapshot takes a schema-scoped transaction advisory lock to
serialize read/modify/write operations, including concurrent initial migration.

Jobs reference an environment variable, never a DSN. The existing operator trust rules
control the variable name and remote-host opt-in. A single lazy connection serves a
load, with one transaction per source and deterministic cleanup. Version 1 migrations
are transactional, idempotent and forward-only; reads never initialize missing schemas.

## Consequences

- Application queries can use the documented tables in the user's database.
- Snapshot writes within a schema serialize; this favors correctness for bounded jobs
  over concurrent write throughput. No pool, ORM or async driver is introduced.
- Local operational cleanup and PostgreSQL source-status cleanup cannot commit atomically
  together. Cleanup is repeatable; an error is explicit and the operator can retry.
- Existing SQLite data is not automatically migrated. Switching backends starts a new
  dataset; retaining historic observations requires a separate future migration tool.
- The path-only dataset CLI remains SQLite-specific; PostgreSQL consumers use SQL or
  the adapter's export method. No existing public protocol or export is removed.
