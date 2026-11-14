# ADR 0022: open drift, and exit codes for attention and storage errors

- **Status:** accepted for the next release after 0.6.0.
- **Requirements:** DRIFT-003, DRIFT-004, DRIFT-006, REPAIR-005, OBS-007, DATA-001, SEC-009.

## Problem

An elevated or high structural drift on an otherwise successful `rastro job load --drift`
exited 0, and the drifted run immediately became the baseline. The next run reported
`low`, so a scheduler that alerts on exit codes never saw the early warning DRIFT-004
asks for. A storage failure after the first source was committed exited 2 with
`configuration_error` and printed no report, although rows were saved.

## Options

1. **Overload exit 3** (`partial_success`) for drift. Scripts already read 3 as "some
   sources failed"; drift on a fully successful load is a different condition, and
   `rastro recipe repair` already uses 3 for "a candidate remains quarantined".
2. **A new exit code 4, `NEEDS_ATTENTION`**: the command did its work, but an operator
   must act. The same meaning fits `rastro health` and `rastro dataset status`.
3. **Report drift only in JSON.** Leaves cron and CI blind, which is the bug.

For storage: keep exit 2, or add exit 5, `STORAGE_ERROR`, with the per-source report on
stdout.

## Decision

Option 2, plus exit 5. Exit codes are ordered by severity: 5 (storage error, the report
may be incomplete) over 1 (failed) over 3 (partial or inconsistent) over 4 (open drift)
over 0. `JobLoadResult.status` names the outcome (`success`, `degraded`,
`partial_success`, `failed`, `storage_error`) next to the unchanged `job.status`.

An elevated or high drift on a successful snapshot opens a **drift alert** stored in the
dataset file. Drift schema 2 adds two tables:

- `drift_open` keeps one row per dataset and drift context with the drifted run, its
  risk and the run that was the baseline. While it is open, comparisons keep using that
  pinned baseline, so later runs keep reporting the drift.
- `drift_rebase` marks a source whose serving recipe an operator changed. Its next
  successful run becomes the new baseline without opening drift, because a new recipe
  naturally measures differently.

`rastro job ack-drift` (`rastro.acknowledge_job_drift`) clears the alert: the next run
compares with the latest success, so the drifted structure becomes the normal. Promoting
a repair (by hand or automatically) and `rastro recipe rollback` clear it and rebaseline.
Open drift is read and reported on every `job load`, with or without `--drift`.

Schema 1 files migrate forward inside the first drift write transaction; reads never
migrate. Releases up to 0.6.0 refuse a schema 2 file explicitly ("Foreign or newer drift
schema") instead of advancing past an alert.

## Consequences

- A load that exited 0 with silent drift now exits 4 until someone acknowledges or
  repairs. Schedulers must treat 4 as "look at this", not as a crash.
- `rastro dataset status` exits 4 when a source is stale or has open drift; `rastro
  health` exits 4 when any current source needs attention.
- The report keeps `drift[i]` aligned with `sources[i]`, except after a storage error,
  where `drift` covers only the leading sources whose drift was recorded.
- Downgrading to 0.6.0 after a drift write needs the drift tables removed by hand.
