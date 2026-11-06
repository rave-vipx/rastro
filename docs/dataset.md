# Dataset records and updates

The dataset is a separate SQLite file containing approved extracted fields and
provenance. Recipe memory continues to store extraction instructions. This API
implements DATA-001/004/005 and SYNC-002/003/005/006; CLI/job integration is separate.

```python
from datetime import UTC, datetime
from rastro.adapters.sqlite_dataset import SQLiteDatasetStore
from rastro.domain.dataset import DatasetRecordKey, ObservedRecord
from rastro.domain.results import Evidence

at = datetime(2026, 11, 6, tzinfo=UTC)
source = "https://example.org/catalog"
fields = {"sku": "A-42", "name": "Notebook"}
observation = ObservedRecord(
    key=DatasetRecordKey.from_fields("catalog", fields, ("sku",)),
    source_url=source,
    observed_at=at,
    fields=fields,
    provenance={
        name: Evidence(
            source_url=source, observed_at=at, method="css", locator=f".{name}", recipe_version=1
        )
        for name in fields
    },
)
store = SQLiteDatasetStore("catalog.sqlite")
report = store.apply_snapshot("catalog", source, [observation], complete=True, observed_at=at)
assert report.counts["created"] == 1
report = store.apply_snapshot("catalog", source, [observation], complete=True, observed_at=at)
assert report.counts["unchanged"] == 1
```

Supply fields and evidence from a validated extraction result, after approval.
The synthetic example only demonstrates the interface. Evidence must cover exactly
the approved fields, use the same source and have an aware timestamp no later than
the observation. `source_url` is the canonical evidence URL (without query,
fragment or userinfo). Every observation in a snapshot uses its `observed_at`.
`DatasetStorageError` reports invalid store input, I/O failures, foreign schemas,
limits and out-of-order source attempts without including payloads or paths.

`DatasetRecordKey.from_fields(dataset, fields, identity_fields)` constructs an
ordered identity. Strings normalize Unicode, whitespace and case; aware datetime
values and aware ISO datetime text normalize to UTC. Finite numbers and booleans
retain their types. Identity fields must exist and cannot be null or blank. Keep
the identity field order stable for a dataset. Include a start time to keep two
performances distinct; exclude it when a stable external identifier should survive
rescheduling.

`SnapshotReport.keys` maps each `ChangeKind` to its tuple of keys; `counts` includes
all five kinds. Supplied valid observations report `created`, `updated` or
`unchanged`. Absent earlier origins report `missing` only with `complete=True`,
and `uncertain` with `complete=False`. An empty complete snapshot is an explicit
successful assertion by the caller, never a substitute for an extraction failure.

```python
store.mark_source_unavailable("catalog", source, at, "access_restriction")
status = store.source_status("catalog")[0]
assert status.stale
assert store.records("catalog")[0].last_observed_at == at
with open("catalog.jsonl", "w", encoding="utf-8") as stream:
    store.export_jsonl("catalog", stream)
```

Failure reasons must be short, printable operator diagnostics, never raw source
content or credentials. `source_status()` returns a tuple ordered by URL with
`source_url`, `last_success`, `last_attempt`, `stale` and `reason`. Staleness reflects
the last attempt, not elapsed wall-clock time. Missing records are retained;
there is no deletion or retention operation in v0.2.

`records()` returns a tuple of `StoredRecord` objects. Each keeps `key`, `fields`,
`provenance`, `source_url`, `observed_at`, `first_observed_at`, `last_observed_at`,
`origins` and `uncertain_match`. Origins are keyed by source URL and preserve their
own fields, provenance and first/last times. The top-level fields come from the
newest origin (URL breaks equal-time ties). JSONL lines round-trip through
`StoredRecord.model_validate_json(line)` and include these same fields.

Exact keys from different sources share one record. Distinct keys with nearby
start times and matching normalized labels retain separate records and symmetric
uncertain-match markers naming both keys and origins. Configure the three field
names with `near_match_fields=("name", "place", "begins_at")` and the tolerance
with `near_match_tolerance=timedelta(minutes=10)`. Defaults are `title`, `venue`,
`start_time` and 30 minutes; missing or invalid comparison fields do not imply a
match. Markers persist as historical warnings and never silently merge records.

SQLite consumers can query the approved JSON fields directly:

```sql
SELECT json_extract(payload, '$.fields.sku') AS sku,
       json_extract(payload, '$.fields.name') AS name,
       json_extract(payload, '$.last_observed_at') AS last_observed_at
FROM dataset_records
WHERE dataset = 'catalog';
```

Use a trusted private directory and a regular filesystem path; URI paths, parent
traversal and symlink components are refused. Resolve job-relative paths and their
containment before constructing the adapter. The default `Limits()` allows 1,000
retained records and 2 MB of serialized record payloads per dataset, plus bounded
input depth/nodes. Limits include origins and markers. A limit failure rolls back
the snapshot. Matching scans retained origins; this is a small local store, not a
large-dataset throughput guarantee. See [ADR 0017](decisions/0017-dataset-store.md).
