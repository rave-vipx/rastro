# ADR 0018: measured drift snapshots in the dataset file

- **Status:** accepted for v0.2.
- **Requirements:** DRIFT-001–006, SEC-004/008, SYNC-006/008.

## Context

A validated result contains accepted records and provenance, but cannot reconstruct
selector match counts or the structure of rejected fields. Treating lost records
as proof of structural drift would confuse schema failures, restrictions and
network failures with changes in the page. Recipe memory must remain separate
from the dataset and retain its existing schema.

## Decision

Use an opt-in, execution-scoped capture hook in `extract_document`, supplied with
an offline `StructuralProbe` port. The context propagates to asynchronous tasks
and `to_thread` calls, avoiding new required arguments on every extraction layer.
The default hook does nothing. `ScraplingDriftProbe` measures the same document
and selected recipe after validation; it never fetches. Capture stores only
measurements keyed by extraction run ID; load associates them with the final
outcome and fallback index. Unmeasured quantities remain `None`.

The snapshot vocabulary is explicit: **coverage** is validated field evidence per
candidate, **validation failure rate** counts candidates rejected for that field,
**structural drift** requires measured structural change, and **extraction
failure** without that change remains a separate verdict. A risk score is a
heuristic, not a probability or semantic-confidence score. The fixed formula and
thresholds are documented in [Drift](../drift.md).

### Fingerprint representation, version 1

`FieldStructure.subtree_fingerprint` is a lowercase SHA-256 hex digest, or `None`
for structured data and unavailable measurements. For each CSS field:

1. Parse the field's CSS selectors using cssselect; remove terminal `::text` or
   `::attr(...)` pseudo-elements to identify their owning element. A bare
   pseudo-element refers to the record container itself.
2. For each matched element, collect ancestor tag names in nearest-first order,
   and preorder `(relative_depth, tag_name)` pairs for its subtree. Ignore
   comments, text, attributes and their values.
3. Hash `json.dumps([ancestor_tags, subtree_pairs]).encode()` using Python's
   default JSON separators and ASCII escaping, then sort these element digests.
4. Hash `json.dumps(sorted_element_digests).encode()` the same way for the field.
   No matched elements means the digest of `[]`, a measured empty selection.

The persisted schema version binds this representation to the installed
Scrapling 0.5/lxml 6 parser family; changes to normalization require a new drift
schema version. Input byte, record, field, subtree depth and node limits bound
measurement. Record ordering, text changes and attribute-value changes do not
change a fingerprint. Moving a field beneath a different ancestor tag does.

These are opaque comparison digests, **not** Scrapling adaptive relocation
serialization. R2 can read the last successful snapshot via
`SQLiteDriftMonitor.baseline(dataset, snapshot)` and compare structure; relocation
still needs its own approved last-successful element/sample evidence. A digest
alone cannot recreate element attributes, content or adaptive storage.

### Persistence and migration

The dataset connection helper owns SQLite file creation, ownership checks,
symlink refusal, limits and staged atomic publication. Drift adds a singleton
`drift_schema(id=1, version=1)` row and a `drift_runs` table with dataset/context/run
primary key, observation time, success flag, snapshot JSON and assessment JSON.
An index supports last-successful lookup. The context digest covers canonical
source URL, schema fingerprint, locale, timezone and egress.

Migration and the first drift write commit in one transaction. Existing dataset
rows and its `application_id`/`user_version` are unchanged. Foreign/future drift
schemas are refused. Only successful snapshots become baselines; all attempted
runs are retained. Baseline reads use read-only connections and never migrate.
Failed writes roll back their drift changes and clean up unpublished files.
Recipe memory is neither read nor changed by this adapter.

## Alternatives

Reconstructing structure from provenance is cheaper but cannot supply truthful
measurements on failed fields. Adding telemetry methods to existing parser and
repository protocols would break minimal third-party implementations. Persisting
raw subtrees would improve relocation context at the cost of retaining unapproved
source data. The small optional probe and content-free digests avoid those costs.

## Consequences

- No browser, model or network dependency; existing adapters parse structured data.
  Opt-in measurement reparses the input and adds CPU cost; no throughput claim.
- Defaults preserve the prior load JSON. `--drift` adds an assessment tuple aligned
  with report sources; stale-source entries already name affected record keys.
- Observations compare the selected recipe attempt. A fallback can restore field
  coverage while still producing a fallback warning; its match count is never
  presented as the failed primary selector's count.
- Dataset loading and drift persistence are separate transactions. Monitoring
  failure may follow a committed valid dataset update and is reported explicitly;
  it never rolls back or deletes valid records. Atomic cross-port writes would
  require a larger dataset transaction interface.
- Same-template content changes and class-value changes that retain matches may
  be invisible. Count changes from normal listing growth can warn. These are
  reasons to evaluate repair candidates, not automatic promotion criteria.
- Schema 1 stores run history without automatic retention. Query URL variants
  share the dataset's canonical source identity; separate datasets are needed
  when query variants represent distinct sources.

## Verification

Offline tests mutate the synthetic dance sample through Mock Lab, detect the
renamed field, and verify retained records and stale observation times. Unit and
integration tests cover deterministic scoring, non-structural validation failures,
fallbacks, async capture, structured unknowns, resource limits, opt-in CLI output,
schema coexistence, repeat writes, rejected versions/symlinks and aborted migrations.
