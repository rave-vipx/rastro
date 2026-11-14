# Structural drift on dataset loads

`rastro job load job.toml --drift` measures structural evidence, compares it with
the last successful comparable run, and adds `drift` to the JSON load report.
Without `--drift`, the output and extraction behavior remain unchanged. This
implements DRIFT-001–006; it does not repair or activate recipes.

```sh
cp -R examples/uy-dance-agenda/sample /tmp/rastro-drift-sample
uv run rastro job load /tmp/rastro-drift-sample/job.toml --drift
uv run rastro job load /tmp/rastro-drift-sample/job.toml --drift
```

The first run establishes a baseline. The second unchanged run reports score
`0`, risk `low`, verdict `none`. `drift[i]` describes `sources[i]`. Each signal
names its field, indicator, previous/current measurements, score contribution,
and dataset effect. `stale_sources` supplies the affected record keys and last
successful observation time. Failed and partial loads retain earlier observations;
only validated records enter the dataset. Drift warnings never change extraction
outcomes or authorize repairs.

## Open drift

An `elevated` or `high` assessment of a **successful** run opens a drift alert for that
source (DRIFT-004). The load report then has `status = "degraded"`, lists the alert in
`open_drift` and exits **4** (`JobExitCode.NEEDS_ATTENTION`). While the alert is open:

- the baseline stays at the last run before the drift, so every later run keeps
  comparing with it and keeps reporting the drift;
- every `job load` reports it and exits 4, with or without `--drift`;
- `rastro health`, `rastro dataset status`, the MCP `drift_status` tool and Studio's
  health view show it.

It closes in one of two ways:

```sh
rastro job ack-drift job.toml --source 1   # or --all; Python: rastro.acknowledge_job_drift
```

accepts the new structure: the next run compares with the latest success. Promoting a
repaired recipe (`rastro recipe promote`, or `rastro recipe repair` with
`auto_promote`) or `rastro recipe rollback` clears it as well, and the source's next
successful run becomes the new baseline without opening drift, because a changed recipe
measures differently. Low drift, failed and partial runs never open an alert; failures
are already reported as stale sources. See
[ADR 0023](decisions/0023-open-drift-and-attention-exit-codes.md) and
[Monitoring and alerts](operations.md).

## Measurements and classification

A `StructuralSnapshot` identifies a source, extraction run, observation time,
schema, locale, timezone and egress. Each `FieldStructure` records:

- `selector_match_count`: actual matches across the selected recipe's record
  containers, including ambiguous CSS matches; structured paths count present
  fields. Invalid structured documents have unknown counts.
- `fallback_used`: whether the final recipe attempt used an index above zero.
  The selected attempt is measured, including when a remembered fallback runs
  without retrying the primary. A missing attempt history is unknown.
- `subtree_fingerprint`: the content-free CSS structural digest specified in
  [ADR 0018](decisions/0018-drift-signals.md). JSON and JSON-LD have no DOM
  fingerprint; `null` means unknown, never an empty or unchanged subtree.
- `field_coverage`: validated records with field evidence divided by candidate
  count for the selected attempt (zero when no candidates exist).
- `validation_failure_rate`: distinct candidate indexes with a validation issue
  naming this field divided by that attempt's candidate count (zero if none).

Counts and fingerprints come from an offline parser adapter. A scoped capture
hook runs after extraction validation and never stores the input document, field
values, selectors, or attribute values. JSON parsing uses the existing adapters.
Source provenance URLs follow the existing dataset sanitization policy.

For each comparable field, calculate:

| Indicator | Change | Weight |
| --- | --- | --- |
| Selector count | `min(1, abs(current - baseline) / max(1, baseline))` | 0.35 |
| Fallback use | 1 only when changing from false to true | 0.15 |
| Subtree fingerprint | 1 when both measured digests differ | 0.20 |
| Coverage | `max(0, baseline - current)` | 0.20 |
| Validation failure rate | `max(0, current - baseline)` | 0.10 |

Unknown measurements contribute nothing. Each contribution is rounded to six
places. Sum contributions per field, then take the largest field sum, rounded to
six places and capped at 1. Fields are emitted in sorted order. Thresholds default
to `elevated = 0.2` and `high = 0.5`, inclusive; lower scores are `low`.
`DriftThresholds` can configure these in Python and the assessment includes them.
These weights are an explainable heuristic, not a calibrated probability.

A total extraction failure is never low risk. When an available source yields no
field evidence at all (every `field_coverage` is zero and the outcome is neither
success nor an acquisition failure) and the source succeeded before, the assessment
adds one signal with field `*`, indicator `extraction_failed`, baseline `success`,
the current outcome and contribution 1, so the score is 1 and the risk `high`.
"Succeeded before" means a comparable successful drift run or a successful dataset
load of that source, even one run without `--drift`. A source that has never
succeeded has nothing to drift from and gets no such signal.

Any observed count change, new fallback use or changed fingerprint gives verdict
`structural_drift`, independently of whether extraction still succeeds. When
those structural indicators are unchanged or unknown, a non-successful extraction
gives `extraction_failure`. Acquisition failures/restrictions cannot establish
structural drift, even if an older snapshot exists. Otherwise the verdict is
`none`. The risk level and verdict answer different questions: low-scoring drift
can exist, and high validation failure alone does not prove structural drift.

## Storage and Python integration

`SQLiteDriftMonitor(path, *, limits=None, thresholds=None)` writes `drift_schema`,
`drift_runs`, `drift_open` and `drift_rebase` (drift schema 2) in the existing dataset
file, without altering its version or any recipe-memory tables. A schema 1 file from
0.6.0 is upgraded inside the first drift write transaction; reads never migrate, and
0.6.0 refuses a schema 2 file explicitly. `assess(dataset, snapshot)` compares and
persists atomically; `baseline(dataset, snapshot)` reads the baseline (the last
successful comparable observation, or the pinned one while drift is open) without
creating or migrating a file; `open_drift(dataset)` lists open alerts and returns an
empty tuple for a missing file. Failed/partial runs are recorded but never replace that
baseline. Repeated identical run IDs are idempotent and conflicting IDs fail explicitly.
An observation time earlier than the last recorded one (a clock that went backwards) is
clamped to that time instead of failing.

```python
from rastro.adapters.drift_probe import ScraplingDriftProbe
from rastro.adapters.sqlite_drift import SQLiteDriftMonitor
from rastro.application.drift import collect_snapshots

with collect_snapshots(ScraplingDriftProbe()) as snapshots:
    report = run_job(job, base_dir=directory)
loaded = load_results(
    job.dataset,
    [field.name for field in job.fields],
    report,
    dataset_store,
    monitor=SQLiteDriftMonitor(dataset_path),
    snapshots=snapshots,
)
```

`run_job` and `load_results` are the existing application entry points. Capture
propagates into asynchronous tasks and worker threads and resets on exit.
The optional `DriftMonitor` protocol adds no methods to existing public ports.
Without captured measurements, the hook can report coverage and validation
outcomes, but cannot infer selector counts or subtree fingerprints.

## Limits

Changing recipe selectors or normal listing size can also change structural
measurements. Inspect the evidence and recipe version before deciding to repair.
Fingerprints ignore text and attribute values, so they cannot identify semantic
misbinding, prove that two nodes mean the same thing, or restore Scrapling's
adaptive-element state. Semantic rules and repair evaluation remain separate.
Baseline contexts follow dataset source URLs, which omit URL queries/fragments;
use separate datasets for query variants with different meanings.

Dataset writes and drift writes use separate transactions. If drift storage
fails after loading records, the command fails explicitly but already validated
dataset updates remain committed. A retry is safe; monitoring never deletes
records. Per-snapshot limits are enforced; drift run history grows with runs and
has no automatic retention policy in this slice.
