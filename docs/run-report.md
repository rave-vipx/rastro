# Run report

Every `rastro job run` and `rastro job load` ends with a **run report**: a short,
content-free account of what happened to each source and why. It answers "did it stop
because of a cap, or because it failed?" without opening the records.

The report holds hosts, URL paths, counts, reason codes and timings. It never holds
records, page bodies, URL queries, file paths or secrets, so it is safe to print, attach to
a failure hook and show in Studio.

## Where it appears

| Where | How |
| --- | --- |
| CLI | the `report` key of the JSON that `rastro job run` and `rastro job load` print |
| Python | `JobRunResult.report` and `JobLoadResult.report` |
| Failure hook | the `report` key of the JSON on the hook's stdin (payload version 2) |

```python
import rastro

result = rastro.run_job_file("job.toml")
for source in result.report.sources:
    print(source.host, source.path, source.outcome, source.reason, source.detail)
```

The report is versioned on its own (`schema_version`). Its JSON Schema is published at
`schemas/run-report.schema.json`.

## Reading a source

Each entry of `sources` has:

- `host` and `path`: where the source is. The query and fragment are never kept.
- `outcome`: the source outcome, as in the rest of the job result.
- `reason`: why work on the source ended. `complete`, `not_modified`, `page_cap`,
  `depth_cap`, `request_budget`, `time_budget`, `stopped`, `failed`, `blocked`,
  `robots_denied`, `invalid_data` or `missing_data`.
- `detail`: for a failure, a short code such as `http_500`, `http_403`, `timeout` or an
  issue code.
- `pages`, `records_accepted`, `records_rejected`, and `rejected_by_code` (how many
  records each validation code rejected).
- `stored`: on a load, how many records were created, updated, unchanged, missing or uncertain.
- `seconds`: time spent acquiring, extracting and validating.

A spider that stops at `max_pages` reports `reason: "page_cap"` with a partial outcome. A
source that answers `500` reports `reason: "failed"` and `detail: "http_500"`. Telling the
two apart is the point of the report.

When a site refused the first fetch, the source's `acquisition` entry lists every level of
the acquisition ladder that was tried in `ladder` (`level` and `outcome`), the level the
run started at in `start_level`, and `http_version` when the transport knows which HTTP
version answered. A source still refused at the top level also carries `ladder_exhausted`.

`totals` sums the sources; `open_drift` counts unacknowledged drift on a load.

## Failure hook payload

`--on-failure` and `RASTRO_ON_FAILURE` now receive payload version 2. It keeps every key of
version 1 (`job`, `command`, `status`, `exit_code`, `sources`) and adds `report`. Existing
hooks keep working unchanged.
