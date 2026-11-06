# ADR 0017: separate dataset identity, origins and non-destructive snapshots

- **Status:** accepted for v0.2.
- **Requirements:** DATA-001/004/005, SYNC-002/003/005 (observation times)/006.

## Context

[ADR 0016](0016-dataset-outcome.md) requires extracted records to survive repeated
runs and source failures. Recipe memory describes extraction instructions; it must
not become the application database. Identity must distinguish performances while
allowing a caller to decide whether rescheduling changes identity.

## Decision

`DatasetStore` is a new synchronous port; `SQLiteDatasetStore` uses a separate
caller-selected file. Callers provide only validated, approved JSON fields and
existing `Evidence` objects. Every field must have evidence from the record's source,
with an aware timestamp no later than the observation. The extraction/application
boundary remains responsible for proving the values were observed in this run and
for approval; the store cannot prove an arbitrary caller's evidence is authentic.

A key is the dataset name plus the ordered values of the declared identity fields.
Strings are trimmed, internal whitespace runs (including NBSP) collapse to one
space, and text is casefolded and NFC-normalized. Aware datetime objects and
aware ISO datetime strings normalize to UTC with microseconds, idempotently across
serialization. Other strings retain text semantics, including naive datetime text.
Finite numeric and boolean identities retain JSON scalar types; null, empty,
missing, compound and non-finite identities are rejected. The serialized key keeps
numeric `1`, text `"1"` and boolean `true` separate. Identity field names/order must
stay stable within a dataset; changing identity is a deliberate new dataset or a
future migration, not an inferred match.

Exact equal keys merge origins. Each origin retains its latest approved fields and
provenance and its first/last observation times. The record's convenience projection
uses the newest observation, with source URL as the deterministic tie-breaker.
Changes compare the incoming origin's previous fields; refreshed evidence alone is
unchanged. A newly discovered origin on an existing key is not a new record.

Different keys are never merged. Configurable label/label/time field names and a
time tolerance identify potential cross-source matches; the defaults are `title`,
`venue`, `start_time`, and 30 minutes. Labels use identity normalization; time values
must be aware ISO strings. The store persists symmetric `uncertain_match` markers
with both keys and both source URLs. Markers are historical warnings, not proof of
equality or a current match guarantee; rescheduling does not erase them.

Only absence from a **complete successful** source snapshot reports `missing`.
Absence from a partial snapshot reports `uncertain`; valid supplied observations
still update. For an existing origin, partial observations merge supplied fields
and provenance over prior values, preserving omitted fields and their original
evidence timestamps. Classification compares that merged result, so a field-shrunk
partial run with unchanged supplied values reports `unchanged`, with stale source
status. Complete observations replace the origin's field set. Neither operation
deletes records or advances absent records' last observation time. Failed attempts update source status only. A complete snapshot
clears source staleness; partial and failed attempts retain the prior last success.
Out-of-order attempts for the same source fail atomically rather than regress data
or health. Full attempt history, retention and deletion remain v0.3.

Schema 1 uses `dataset_records` and `dataset_sources`, with JSON payloads and
composite primary keys. `PRAGMA application_id` (`RSDS`) and `user_version` identify
ownership and version. Initialization is transactional, idempotent and forward-only,
and runs in the same transaction as the first observation. For a new path, the
transaction runs in a private sibling staging directory; after commit and connection
close, an atomic hard link publishes the file without replacing another creator's
result. The losing creator discards staging and retries once against the published
database. This avoids deleting a peer's file while cleaning up a failed first write.
Handled failures and cancellation remove staging artifacts. Abrupt process termination
can leave an unpublished staging directory; it cannot expose an initialized empty
dataset at the requested path. Reads use SQLite `mode=ro`, verify schema markers
without migrating, and never acquire a write lock or create a missing file.
Foreign, recipe-memory and future schemas are refused without modification. No
existing dataset format is replaced; rollback means using the previous application
and keeping this separate file. There is no destructive downgrade.

## Consequences

- Standard-library SQLite, short-lived connections, five-second busy timeout,
  `BEGIN IMMEDIATE` writes, `trusted_schema=OFF`, and `secure_delete=ON` follow
  [ADR 0003](0003-sqlite-recipe-memory.md); recipe memory is unchanged.
- Snapshot state and source status commit together. Threaded writers serialize.
  Use a worker thread from async applications. First publication requires a local
  filesystem supporting hard links; unsupported filesystems fail explicitly.
- The path follows job syntax validation and rejects symlink components. The
  caller resolves job-relative containment. The file and parent directories must
  be trusted/private; the check is not a sandbox against concurrent directory
  replacement. Approved data may be private; exports need the same protection.
- `Limits` bounds snapshot and retained record count and serialized bytes, and
  input JSON depth/nodes. The default local store is capped at 1,000 retained
  records and 2 MB of serialized record payloads per dataset. Exceeding a budget
  fails atomically; the caller may raise the existing bounded `Limits` settings.
- Matching scans retained origins, and snapshots rewrite retained rows. This is
  intentionally a bounded local implementation, with no throughput claim. Indexed
  matching, pagination and larger datasets need a later measured change.

## Verification

Offline integration tests exercise repeated snapshots, rescheduling with and without
time in identity, distinct performances, per-source observation times, partial and
failed runs, uncertain matches, JSONL round trips, concurrent writers, schema
collision/rollback, invalid input and budgets. Unit tests cover Unicode, timezone,
scalar identity and invalid/missing keys. PR 56 regression inputs additionally cover
NBSP/internal whitespace, missing read paths, preserved fields on partial runs,
readers during a reserved write lock, failed first publication, concurrent creators,
custom match field names, symlinked parent directories and mixed change reports.
