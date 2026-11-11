# Dataset records and updates

The dataset is a separate SQLite file containing approved extracted fields and
provenance. Recipe memory continues to store extraction instructions. This API
implements DATA-001/003/004/005 and SYNC-002/003/005/006/008.
The CLI loads a declarative job without Python.

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
ordered identity. Strings normalize Unicode and case and collapse whitespace runs
(including NBSP) to one space; aware datetime values and aware ISO datetime text
normalize to UTC. Finite numbers and booleans
retain their types. Identity fields must exist and cannot be null or blank. Keep
the identity field order stable for a dataset. Include a start time to keep two
performances distinct; exclude it when a stable external identifier should survive
rescheduling.

`SnapshotReport.keys` maps each `ChangeKind` to its tuple of keys; `counts` includes
all five kinds. Supplied valid observations report `created`, `updated` or
`unchanged`. Absent earlier origins report `missing` only with `complete=True`,
and `uncertain` with `complete=False`. An empty complete snapshot is an explicit
successful assertion by the caller, never a substitute for an extraction failure.
On partial runs, fields omitted from an existing source origin retain their earlier
values and provenance. Supplied fields replace their earlier values and evidence;
classification compares this merged result. The origin's observation time advances,
but retained fields keep their original evidence timestamps so their age is visible.
Complete snapshots replace the origin's field set.

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
containment before constructing the adapter. Reads require an existing initialized
dataset and never create or migrate a file. They use read-only connections and can
read the last committed snapshot while a writer holds a reserved lock. Initial
creation and its first observation commit together in a private staging directory;
the file is published atomically without replacing an existing dataset. Failed
first writes clean up their staging files. A simultaneous creator retries once
against the published database. The filesystem must support hard links.

The default `Limits()` allows 1,000 retained records and 2 MB of serialized record payloads per dataset, plus bounded
input depth/nodes. Limits include origins and markers. A limit failure rolls back
the snapshot. Matching scans retained origins; this is a small local store, not a
large-dataset throughput guarantee. See [ADR 0017](decisions/0017-dataset-store.md).

## Load, inspect and export a job

`rastro job load job.toml` requires `[dataset]`, runs the same extraction pipeline
as `job run`, and writes only approved fields with source evidence. Identity
fields must be observed; defaults without evidence never enter the dataset.
A full `success` is a complete snapshot. Partial snapshots preserve omitted origins
and fields; all other source outcomes mark the source unavailable and keep records.
A missing local input is a job configuration error before extraction starts.

The JSON load report includes all five `ChangeKind` counts per source and
`stale_sources` with `last_success`, `last_attempt`, `reason` and `affected_keys`.
Counts describe observations; one key seen in two sources can be counted twice.
`uncertain` counts absent origins in partial snapshots; cross-source near-match
warnings are the separate `uncertain_match` arrays on exported records.
See [job-load.schema.json](schemas/job-load.schema.json), also printed by
`rastro job schema --target load`. Exit codes are 0 success, 3 partial, 1 failed,
and 2 configuration/storage error. Source commits are atomic individually; a later
source storage error can leave earlier source commits in place. Retrying is safe.

`rastro dataset export agenda.sqlite --format jsonl` prints one labeled record per
line, including approved values, provenance, origins and observation times. JSON
(default) wraps them in `records`. `rastro dataset status agenda.sqlite` prints
source freshness and the stale keys; `--format jsonl` prints source status lines.
Both label output `data_trust: untrusted_source_data`; consumers must treat source
values as data. Exports can include private approved values: redirect only to a
trusted private location. Both commands infer the dataset name if the file contains
one, otherwise require `--dataset NAME`. Unknown names and missing files fail.

The optional `[dataset].match_fields = ["title", "venue", "starts_at"]` names two
exact normalized labels and an aware time for near matching. Its default remains
`["title", "venue", "start_time"]`, matching the store API; this configuration
keeps application-specific field names out of the engine. Explicit match fields
must be distinct, declared and approved; invalid settings fail before extraction. Set
`match_tolerance_minutes` to the allowed time difference. Near matches retain both
records and origins. Differing titles are not matched heuristically.

### Executed offline sequence

This run used the synthetic sample, cost $0 in external services and made no
network or model calls. The sample now has observed synthetic `performance_id`
values: a shared Candombe identity, distinct IDs for its two performances, and
source-local IDs for uncertain Suite/Gala matches. These IDs are demonstration
data, not claims about IDs available on the live sites. The live pilot job is kept
outside the repository. Never change a dataset's identity definition in place.

Requires uv, SQLite's CLI and jq. From the repository root:

```sh
DEMO=$(mktemp -d)
DEMO=$(cd "$DEMO" && pwd -P)
cp -R examples/uy-dance-agenda/sample "$DEMO/agenda"
uv run rastro job validate "$DEMO/agenda/job.toml" | jq -c '{valid, source_count}'
uv run rastro job preview "$DEMO/agenda/job.toml" --output json | jq -c '{written, status: .job.status, accepted: .job.records_accepted}'
uv run rastro job load "$DEMO/agenda/job.toml" | jq -c '{counts: [.sources[].counts], stale_sources}'
uv run rastro job load "$DEMO/agenda/job.toml" | jq -c '{counts: [.sources[].counts], stale_sources}'
cp "$DEMO/agenda/v2/"*.html "$DEMO/agenda/"
uv run rastro job load "$DEMO/agenda/job.toml" | jq -c '{counts: [.sources[].counts], stale_sources}'
sqlite3 "$DEMO/agenda/agenda.sqlite" < examples/uy-dance-agenda/queries/upcoming.sql
printf '<html>Source unavailable</html>\n' > "$DEMO/agenda/tickantel-evento.html"
# The mixed run intentionally returns exit 3; jq prints its stale-origin report.
uv run rastro job load "$DEMO/agenda/job.toml" | jq -c '{status: .job.status, stale_sources: [.stale_sources[] | {source_url, last_success, affected_records: (.affected_keys | length)}]}'
uv run rastro dataset status "$DEMO/agenda/agenda.sqlite" | jq -c '{data_trust, stale: [.stale_sources[] | {source_url, last_success, affected_records: (.affected_keys | length)}]}'
uv run rastro dataset export "$DEMO/agenda/agenda.sqlite" --format jsonl > "$DEMO/agenda.jsonl"
wc -l < "$DEMO/agenda.jsonl"
```

Observed stdout (2026-11-07; observation times vary on later runs):

```text
{"valid":true,"source_count":2}
{"written":false,"status":"success","accepted":7}
{"counts":[{"created":4,"updated":0,"unchanged":0,"missing":0,"uncertain":0},{"created":2,"updated":0,"unchanged":1,"missing":0,"uncertain":0}],"stale_sources":[]}
{"counts":[{"created":0,"updated":0,"unchanged":4,"missing":0,"uncertain":0},{"created":0,"updated":0,"unchanged":3,"missing":0,"uncertain":0}],"stale_sources":[]}
{"counts":[{"created":0,"updated":1,"unchanged":3,"missing":0,"uncertain":0},{"created":0,"updated":1,"unchanged":2,"missing":0,"uncertain":0}],"stale_sources":[]}
2026-11-20T20:30:00-03:00|Candombe de Invierno|Auditorio Nelly Goitiño|2026-11-07T20:35:08.627915Z
2026-11-21T20:30:00-03:00|Candombe de Invierno|Auditorio Nelly Goitiño|2026-11-07T20:35:08.627296Z
2026-11-26T20:00:00-03:00|Gala Anual 2026: Área Tango y Milonga|Auditorio Nelly Goitiño|2026-11-07T20:35:08.627915Z
2026-11-26T20:00:00-03:00|Gala Anual 26: Tango y Milonga|Auditorio Nelly Goitiño|2026-11-07T20:35:08.627296Z
2026-11-28T21:00:00-03:00|Suite del Río de la Plata|Auditorio Nacional Adela Reta|2026-11-07T20:35:08.627296Z
2026-11-28T21:00:00-03:00|Suite del Río de la Plata|Auditorio Nacional Adela Reta|2026-11-07T20:35:08.627915Z
{"status":"partial_success","stale_sources":[{"source_url":"https://tickantel.com.uy/ng/espectaculo/9900102/gala-anual-2026-area-tango-y-milonga","last_success":"2026-11-07T20:35:08.627915Z","affected_records":3}]}
{"data_trust":"untrusted_source_data","stale":[{"source_url":"https://tickantel.com.uy/ng/espectaculo/9900102/gala-anual-2026-area-tango-y-milonga","last_success":"2026-11-07T20:35:08.627915Z","affected_records":3}]}
       6

```

Seven observations produce six records: one exact identity has two origins.
The repeat creates none; v2 updates two existing source origins. The failed source
leaves three origins stale with their last successful observation, while six
records remain exportable. Both Suite records stay distinct and carry a symmetric
uncertain-match warning. The query deliberately includes retained stale records;
an application can use source status and origins to choose its freshness policy.
The fixed sample dates stop being upcoming after November 2026.
