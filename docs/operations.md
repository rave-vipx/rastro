# Monitoring and alerts

**Requirements:** DRIFT-004, OBS-002, OBS-007, DATA-004, REPAIR-005, SEC-009.

Rastro runs from cron, CI or any scheduler (SCHED-002). Everything an operator needs to
alert on is in the exit code; the JSON on stdout says why.

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
  The next successful run becomes the new baseline.

The MCP `drift_status` tool and Studio's health view show open drift too.

## Removed or renamed sources

Only the job's current sources count as stale. When a source was removed from the job or
its URL changed, its old state stays in the dataset until you forget it:

```sh
rastro job forget-source job.toml --url https://old.example/list
```

This deletes that origin's status, drift history and open drift, plus repair samples of
every source no longer in the job (they are stored by digest, so they cannot be matched
by URL). Stored records keep their values and provenance. A current source is refused.

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
