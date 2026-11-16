# Monitoring and alerts

**Requirements:** DRIFT-004, OBS-002, OBS-007, DATA-004, REPAIR-005, SEC-009, SCHED-001, SCHED-002, SCHED-003, SCHED-004, SCHED-006, SCHED-007, SYNC-001.

Rastro runs from cron, CI or any scheduler, or with its built-in foreground watcher
(`rastro job watch`, SCHED-002). Everything an operator needs to alert on is in the exit
code; the JSON on stdout says why.

## Scheduler

Recurring runs use the **same job file** (SCHED-007); add an optional `[schedule]` block
and optional per-source `every` overrides (SCHED-001, SCHED-006, SYNC-001):

```toml
[schedule]
every = "6h"    # units: s, m, h, d; min 1m, max 30d
jitter = "5m"   # optional; bounded <= every / 4

[[sources]]
url = "https://fast.example/events"
every = "1h"    # optional per-source override
```

Run in the foreground or trigger one tick from cron or CI:

```sh
rastro job watch job.toml --drift
rastro job watch job.toml --once --on-failure 'notify-send "Rastro needs attention"'
```

- Each tick checks `last_attempt` in `[dataset]` (never attempted = due), runs one load
  restricted to the due sources, and sleeps until the next due time plus deterministic
  jitter seeded from the job path and source URL hash.
- `--once` runs a single tick and exits with that load's exit code (`0` when no source is
  due), so cron or CI (`0 * * * * rastro job watch /path/to/job.toml --once`) can trigger
  due sources without a separate config file (see also [Daily dataset and agenda](scheduling.md)).
- `SIGINT` and `SIGTERM` finish the current source, release the lock, and exit `0`. They
  also wake the wait for the next due time at once.
- **Overlap protection (SCHED-003):** every `rastro job load` and each `watch` tick creates
  an owner-only lock `<dataset file>.lock` (`O_CREAT|O_EXCL`, mode `0600`) holding the PID,
  a random start token and the start time. If another live process holds the lock,
  `job load` and `watch --once` do no work, print `{"error": "run_in_progress", ...}` on
  stderr, and exit `4`. A running `watch` instead logs a `WARNING` (`watch_tick_skipped`),
  skips that tick and retries after at least 30 seconds. A stale lock (dead PID or older
  than `operation_timeout_seconds × sources + 1h`) is replaced with a `WARNING` log: the
  starter moves the stale file aside, checks it is still the file it judged stale, and
  creates the new lock exclusively, so two starters cannot both take over. A holder only
  deletes the lock while it still holds its own PID and token, never a successor's. PID
  liveness uses `OpenProcess` on Windows and signal `0` elsewhere.
- **Checkpoint and recovery (SCHED-004):** during a load Rastro atomically writes
  `<dataset file>.run.json` (`0600`) with the completed sources of the current run, each
  keyed by its position in the job file and its URL, so two sources with one URL in
  different regions stay distinct. If a load is interrupted, the next load of the same job
  resumes with the remaining sources first and then clears the checkpoint;
  already-completed sources keep their records and status. One load uses one acquisition
  client for all its sources, so host pacing (`min_interval_seconds`) and run-local
  sessions carry over from one source to the next.

## Exit codes

| Code | Meaning | Typical action |
| --- | --- | --- |
| `0` | Success, nothing needs attention. | None. |
| `1` | The job ran and no source succeeded. | Check `sources[].reason` and `rastro health`. |
| `2` | Configuration or usage error; the job did not run. | Fix the job file or command line. |
| `3` | Partial success, or a source failed a consistency rule. | Repair the failing source. |
| `4` | Needs attention: open drift after a successful load; stale sources, open drift or quarantined candidates in `rastro health` and `rastro dataset status`. | Review, repair, or `rastro job ack-drift`. |
| `5` | Dataset storage failed after the job ran. The report lists what was committed. | Check disk space and file permissions, then rerun. |

See [ADR 0023](decisions/0023-open-drift-and-attention-exit-codes.md) for why 4 and 5
are separate codes.

## One health check

```sh
rastro health job.toml            # table; exit 0 healthy, 4 needs attention
rastro health job.toml --output json
```

For each current source of the job it shows the last success and last attempt, whether
it is stale and why (`reason` is the outcome, `detail` a content-free code such as
`robots_denied`, `http_403`, `http_429` or `timeout`), open drift, and candidate recipe
versions waiting for review. Sources still in the dataset but no longer in the job are
listed under `orphaned_sources` and never affect health. The output follows
[`docs/schemas/health.schema.json`](schemas/health.schema.json)
(`rastro job schema --target health`). It reads only and creates no file.

## Open drift

An elevated or high drift on a successful load stays open: the load reports `status =
"degraded"`, lists `open_drift` and exits 4 on every run, with or without `--drift`,
until one of these happens:

- `rastro job ack-drift job.toml --source 1` (or `--all`) accepts the new structure. The
  next run compares with the latest success.
- A repaired recipe is promoted, or `rastro recipe rollback` changes the serving recipe.
  The next successful run becomes the new baseline. If the dataset write fails after
  the recipe changed, the command exits 2 with `storage_error` and a hint: the recipe
  change stays, so fix the dataset file and run `rastro job ack-drift`.

The MCP `drift_status` tool and Studio's health view show open drift too.

## Removed or renamed sources

Only the job's current sources count as stale. When a source was removed from the job or
its URL changed, its old state stays in the dataset until you forget it:

```sh
rastro job forget-source job.toml --url https://old.example/list
```

This deletes that origin's status, drift history and open drift, plus its repair
samples. Samples are stored by a digest of the full source, so only samples of that exact
URL under one of the job's regions are deleted; samples of other jobs that share the
dataset are kept, and so are samples taken with a query string. Stored records keep their
values and provenance. A current source is refused.

## Failure hook

`--on-failure CMD` on `rastro job load` and `rastro job run`, or the `RASTRO_ON_FAILURE`
environment variable, runs a command when the job exits 1, 3, 4 or 5. `--on-failure ""`
disables the variable for one run. Configuration errors (exit 2) do not run the hook,
since no report exists; alert on exit 2 from your scheduler instead.

- The command is split like a shell would (`shlex`) but runs **without a shell**, with a
  30 second timeout. Use a script for pipes or redirects.
- It receives one JSON object on stdin:

```json
{"schema_version": 1, "event": "rastro_job_failure", "job": "job.toml", "command": "load",
 "status": "degraded", "exit_code": 4,
 "sources": [{"source_index": 1, "host": "shop.example", "outcome": "success",
              "reason": null, "issues": [], "drift_risk": "elevated", "open_drift": true}]}
```

  No records, page content, paths, URL queries or secrets are included.
- If the hook fails, times out or cannot start, Rastro writes
  `{"issue": "on_failure_hook_failed", "message": ...}` to stderr and keeps the job's exit
  code. The command line is never echoed, since it may contain a token.
- A job file cannot set the hook; only the operator who runs the command can.

Webhook example with `curl` (the token comes from your environment, not the job):

```sh
export RASTRO_ON_FAILURE='curl -fsS -m 20 -H "Content-Type: application/json" --data-binary @- https://hooks.example.com/rastro'
rastro job load job.toml --drift
```

## Logs

Each source that does not succeed logs one `source_failed` WARNING line on stderr with
its index, host, outcome and failure detail, even without `--verbose`. Paths, queries and
content are never logged.

## Clock changes

If the system clock goes backwards, Rastro clamps the new observation time to the last
recorded one for that source instead of failing. Observation times never regress; the
load records the run as the newest observation.

## File permissions

On POSIX systems Rastro creates dataset and recipe-memory files, and their SQLite
`-journal`, `-wal` and `-shm` files, readable by the owner only (0600). It tightens
existing files you own when it opens them. Datasets keep sanitized page samples for
repair, so keep them private. On Windows the folder's access rules apply.

## Metrics and cost

Every `rastro job load` with a SQLite `[dataset]` appends one row per source to the dataset
file's `run_metrics` table (schema 3; a schema 1 or 2 file migrates forward on its first
write, and a newer file is refused). A row holds counts and timings only: the source URL
without query, fragment or credentials, host, strategy (which recipe source produced the
result), start and finish, outcome, HTTP status, requests, retries, bytes, remote latency
(`network_seconds`), local work (`processing_seconds`: total minus network minus model
time), `render_seconds` (0 without a browser), model calls, records, records that have every
required field, and fields validated. Rastro keeps the newest 10,000 rows per dataset.
`model_tokens` is stored but stays 0: model adapters do not report tokens yet. PostgreSQL
datasets record no metrics.

```console
$ rastro job stats job.toml --since 7d --by host
```

The JSON report (`schema_version` 1) has one group per source (default), `host`, `strategy`
or `day`: `runs`, `pages` (requests), `records`, `success_rate`, `error_rate`, `retry_rate`,
p50/p95 of `total_seconds`, `network_seconds` and `processing_seconds`, `records_per_minute`,
`model_calls`, `freshness_seconds` (now minus the last success, `null` if never),
`coverage` (records with every required field over records, `null` without records), a
daily `trend` and `observed_interval_seconds` (median gap between successful runs, the
input for adaptive schedules; Rastro reports it and does not change your schedule). Success
means `success` or `unchanged`; `partial_success` is neither success nor error. With no
history the output is `{"schema_version": 1, "groups": [], "reason": "no_runs"}` and the exit
code is 0. `--since` takes `7d` or `12h`.

### Model cost limits

A paid model can be priced and capped in the job (USD), or on the command line:

```toml
[model]
provider = "http"
model = "gpt-4.1-mini"
allow_remote = true
cost_per_call = 0.002
max_cost = 1.0
```

`rastro job run job.toml --max-model-cost 0.5` overrides `max_cost`, and
`--model-cost-per-call 0.002` overrides `cost_per_call`. Both flags work on `job run`,
`job load`, `recipe repair`, `suggest` and `new`; the SDK takes `max_model_cost=` and
`model_cost_per_call=` on `run_job*`, `load_job_file`, `watch_job` and `repair_job_source(s)`.
A load or a watch tick shares one budget and one notice across all its sources.

The cap covers every model Rastro calls, whoever supplied it: the job's `[model]`, an SDK
`model=`, `--model SPEC` and `RASTRO_MODEL`. An operator model is priced by
`--model-cost-per-call`, else by the job's `[model] cost_per_call`. A limit with no known
price is a `ConfigurationError`; it is never ignored.

Before the first call to a remote model, Rastro prints one line to stderr with the operation,
model name, maximum calls, estimated maximum cost and the limit. Once the next call would
exceed the limit, Rastro stops calling the model: that source ends as `insufficient_evidence`
with the issue code `model_budget_exhausted`, and recipes found without the model still apply.
Local models print no notice, and honor a limit if you set one. A custom `ModelPort` counts as
remote only if it has `remote = True`. The limit covers the recipe proposal, repair and
judgement calls in one run; each `job run`, `job load` or `recipe repair` starts at zero.
