# v0.2 product-task comparison (DIFF-002)

On this offline task both integrations produce the correct database, retain it
when extraction breaks, and restore the changed price after a scripted manual
repair. The baseline has lower elapsed times in this run and fewer site-specific
lines under the harness rule: 78 vs 124; under a common JSON layout, 78 vs 81;
including one-time helpers, 136 vs 137. Silent errors tie only for the shared
scripted fix; network requests, model calls, external service spend and manual
selector-edit counts tie. This does not establish general superiority
for either integration or satisfy the sealed DIFF-001 evaluation.

## Reproduce and inspect

```sh
UV_PYTHON=/opt/homebrew/bin/python3.12 uv run python -m benchmarks.product_task.measure \
  --output /tmp/rastro-product-task.json
UV_PYTHON=/opt/homebrew/bin/python3.12 uv run pytest tests/unit/test_product_task.py --no-cov -q
```

[Raw report](https://github.com/rave-vipx/rastro/blob/dev/benchmarks/product_task/results.json),
[harness and counting rules](https://github.com/rave-vipx/rastro/blob/dev/benchmarks/product_task/README.md),
[baseline](https://github.com/rave-vipx/rastro/tree/dev/benchmarks/product_task/baseline),
[Rastro job](https://github.com/rave-vipx/rastro/blob/dev/benchmarks/product_task/rastro/job.json),
and [application query](https://github.com/rave-vipx/rastro/blob/dev/examples/uy-dance-agenda/queries/upcoming.sql).
Use the files in the same checkout to review unmerged work. Temporary databases
are removed after measurement; the benchmark README explains how to keep one.
Rastro's independently usable load/export/query path is in [Dataset](dataset.md).

## Method and environment

Recorded on 2026-11-08, Python 3.12.16, macOS 26.6.2 arm64, SQLite 3.54.1,
Scrapling 0.5, Pydantic 2.14.2, lxml 6.1.3, Rastro 0.1.0 (the report predates the 0.2.0rc1 version bump; no measured code changed). The report records
Git parent/dirty status and SHA-256 hashes of the measured code, job, source
mappings, fixtures, hand-written expected table and lockfile. Host load and CPU
power state were not controlled.

The task loads the same synthetic SODRE and Tickantel sample bytes into SQLite,
adds synthetic Costa, repeats all three, loads the broken Costa bytes, then
repairs and reloads Costa. Identity is `performance_id`; six rows become seven,
with a shared ID upserted in source order. Near-matching distinct IDs stay distinct.
Only the scorer uses the expected table. Both paths have explicit selectors,
Pydantic validation and provenance, and preserve existing rows on source failure.
The baseline implementation, fixtures and scoring/counting rules from E1 are
unchanged. Neither path fetches live content or uses a model.

Each phase runs once, in fixed order, without warmup; the baseline runs first.
Phase timing includes local reading, parsing, validation and persistence, and
excludes scoring. Rastro additionally invokes the real `job validate` and `job
preview` before initial and third-source loads; all loads use `job load` with
recipe memory and retained repair evidence. Its measured work includes CLI JSON
serialization, job parsing, and switching the preview job to memory-enabled load.
Imports and fixture copying are outside timing. These are actual workflows with
different work, not isolated extraction-speed measurements. Repair timing includes
reading/applying the scripted fix, reload and verification/scoring on both sides.
Human diagnosis/editing time is excluded, separately recorded as unknown.

Both repairs are prewritten manual fix replays. The baseline edits `.dance` to
`.performance`. Rastro first tries `recipe repair`: **`no_candidates`**, not an
automatic recovery. A generic staging helper stores a candidate with the single
selector edit `.dance, .performance`; `recipe promote` re-runs current and retained
historical fixture CI before activation, then `job load` consumes it. Keeping both
classes permits replay of the historical page. The historical fixture and all
three generated regression checks (four in total) pass. No semantic rules were
configured: semantic confidence is null / `not_assessed`, not proof of semantic
correctness. Candidate
staging uses existing repository/context interfaces; no new runtime feature was
added. The failure-to-propose attempt is included in Rastro's repair time.

In a one-off review probe (a throwaway script run by the independent reviewer, not part of
the repository or the harness, so not reproducible from this checkout), the baseline's own `.performance` fix is rejected by
Rastro's historical-fixture CI (`recipe promote` fails `historical_fixture_0`).
A schema-valid wrong title mapping (`.dance, .performance` with
`title: .venue::text`) is accepted by the baseline as 1 silent error (stores title
"Sala Costa") and rejected by Rastro's promote, so the repair times compare
different acceptance bars.

## Recorded numbers

Times below are milliseconds; full precision seconds are retained in the report.

| Phase | Baseline ms | Rastro ms | Rows, both | Silent errors, both | Stale rows, both |
| --- | ---: | ---: | ---: | ---: | ---: |
| Initial two | 3.162 | 59.156 | 6 | 0 | 0 |
| Add third | 1.235 | 23.114 | 7 | 0 | 0 |
| Repeat | 2.258 | 55.265 | 7 | 0 | 0 |
| Broken | 0.098 | 7.770 | 7 | 0 | 1 |
| Repaired load | 0.608 | 16.004 | 7 | 0 | 0 |

| Metric | Scrapling + Pydantic | Rastro |
| --- | ---: | ---: |
| Initial machine minutes | 0.00005270 | 0.00098594 |
| Site-specific code/config lines | 78 (16 schema + 62 sources) | 124 (job, schema + sources) |
| One-time integration helper lines | 58 | 13 (manual candidate staging) |
| Shared reused code lines | 143 extractor + 75 contracts | 10,765 (whole runtime) |
| Silent accepted errors, all phases | 0 | 0 |
| Missing stored rows, all phases | 0 | 0 |
| Wrong retained rows at broken phase | 1 | 1 |
| Scripted repair including verification, ms | 0.810 | 35.811 |
| Manual changed config entries / repair-file lines | 1 / 3 | 1 / 3 |
| Initial / recurring network requests | 0 / 0 | 0 / 0 |
| Initial / recurring model calls | 0 / 0 | 0 / 0 |
| Initial / recurring external service spend | 0 / 0 | 0 / 0 |
| Human minutes / development-agent minutes and tokens | Unknown / uninstrumented | Unknown / uninstrumented |

The baseline has lower measured elapsed times and fewer site-specific lines
under the harness rule; the line gap depends on counting and formatting choices.
Rastro needs fewer one-time helper lines because it reuses the installed dataset
and recipe lifecycle implementation; that runtime is separately counted, not
hidden as zero code. Whole-module counts include unused runtime helpers and are
not a normalized measure of functionality. The report excludes fixture HTML,
tests, oracle and measurement orchestration from integration LOC. It counts every
nonblank non-comment physical line, including imports, docstrings and punctuation;
JSON formatting and declarative versus Python representation affect the totals.
Repair-file counts are additional to initial configuration counts.

One-off review probes (throwaway scripts, not in the repository) show the sensitivity of the line
counts (baseline vs Rastro). Only rules A and B are derivable from `results.json`; C and D are
not reproduced by the harness:

| Rule | Baseline lines | Rastro lines |
| --- | ---: | ---: |
| A: harness rule, as reported | 78 | 124 |
| B: include one-time integration helpers | 136 | 137 |
| C: drop punctuation-only lines | 64 | 87 |
| D: use the same compact JSON layout for both JSON files | 78 | 81 |

Rule B includes `baseline/__init__.py` (58 lines) and `rastro/loader.py`
(13 lines). About 43 of Rastro's lines are the nine-field `fields` block laid
out one key per line. Rastro's `dataset.identity` and `path` are counted as
site-specific while the baseline's equivalent sits in the separately counted
loader. These are alternative counts, not changes to the reported harness rule.

Both failures report `missing_data`; their one wrong retained price is classified
as stale data, not a silently accepted new error. Successful phases have no wrong
rows against the shared oracle. The shared scripted phases do not measure detection of an
arbitrary valid-but-wrong value, so zero silent errors is not an error-detection
advantage. Network connection/DNS attempts are blocked and counted; Rastro's native
curl transfers are also blocked. A network attempt aborts measurement. No model
client is configured. Local compute/electricity and agent development costs are
unpriced, not free.

## Human steps and limits

For both paths a person inspects the sample sources, declares the schema and
identity, writes source mappings, validates the extracted values and queries the
database, then adds a third mapping. On failure they inspect the changed HTML,
apply a selector fix, reload and verify the price and row count. Rastro adds
validate/preview and candidate/promotion-report review; the baseline implements
its own SQLite loader. The report lists these steps separately for each path.
They are not a timed human trial or measured usability score.

This is **one task on offline synthetic inputs**, with **scripted repair and no
live sources**. Wall times are machine dependent; single samples, fixed order,
shared import state and additional CLI work prohibit portable latency claims or
median/p95 conclusions. Rastro's declarative datetime field does not enforce
Pydantic `AwareDatetime`'s timezone requirement, and its text fields do not have
the baseline model's whitespace stripping; all supplied timestamps have offsets
and supplied strings are already trimmed. The schema contracts therefore match
these inputs, not every possible input. The benchmark does not test adversarial
input, long-running acquisition, fuzzy reconciliation, pruning or live repair.

The class rename and price change were authored in E1, independently of Mock Lab;
they are not a generated cut-2 mutation. Both integrations use those exact bytes;
Rastro uses the cut-2 lifecycle for repair evaluation. The scripted union selector
is supplied by the maintainer, not inferred by Rastro. No gain in diagnosis time,
human maintenance effort, safety or live-source accuracy is claimed. DIFF-001's
sealed corpus has **not been opened or run here** and remains a separate final
evaluation under [ADR 0011](decisions/0011-recovery-benchmark.md).
