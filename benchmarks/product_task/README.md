# Offline product-task comparison (DIFF-002)

This combines E1's independent Scrapling + Pydantic baseline with E2's real
Rastro validate/preview/load and scripted manual candidate/promotion path. See
[the comparison](../../docs/comparison-v02.md) for both sides' numbers and limits. It follows
[ADR 0005](../../docs/decisions/0005-reproducible-release-evidence.md) and does not
open or change the frozen recovery corpus from ADR 0011.

## Run

From the repository root, with the locked development environment:

```sh
UV_PYTHON=/opt/homebrew/bin/python3.12 uv run python -m benchmarks.product_task.measure \
  --output /tmp/rastro-product-task.json
UV_PYTHON=/opt/homebrew/bin/python3.12 uv run pytest tests/unit/test_product_task.py --no-cov -q
```

The harness creates and removes a fresh temporary SQLite database. To keep a
queryable database, use the same baseline directly (choose a new local path):

```python
from pathlib import Path
import sqlite3
from benchmarks.product_task.baseline import load_source, sources

database = Path("/tmp/baseline-agenda.sqlite")
for source in sources():
    print(source.name, load_source(database, source))
with sqlite3.connect(database) as connection:
    rows = connection.execute(
        """
        SELECT key, json_extract(payload, '$.title'),
               json_extract(payload, '$.starts_at')
        FROM performances
        WHERE julianday(json_extract(payload, '$.starts_at')) >= julianday(?)
        ORDER BY julianday(json_extract(payload, '$.starts_at')), key
    """,
        ("2026-11-01T00:00:00-03:00",),
    ).fetchall()
    print(rows)
```

## Baseline and inputs

The baseline reuses the existing independent `benchmarks.baseline.run` extractor,
which uses Scrapling directly, rejects missing/ambiguous fields, and validates
records with Pydantic. It imports no Rastro runtime services. The task-specific
schema validates nonempty text, timezone-aware dates, HTTP(S) ticket URLs, finite
nonnegative prices and UYU currency. This workload has prices on every record;
price and currency are required by this baseline.

`baseline/sources.json` contains explicit selectors and JSON-LD pointers for the
two existing **synthetic** files under `examples/uy-dance-agenda/sample/` and a
third synthetic source under `fixtures/`. No downloads, browsers or model clients
are used. URLs are provenance, never fetch targets. The baseline cannot see
`fixtures/expected.json`; only the scorer reads that hand-written table.

The SQLite primary key is `performance_id`, matching the sample job's declared
identity. Seven initial observations produce six rows because `candombe-1` is
shared. Distinct IDs stay distinct, including near-matching Gala titles and the
Suite listings with conflicting times. This benchmark does not resolve those
cross-source disagreements or measure fuzzy matching. Source order is fixed:
SODRE, Tickantel, then Costa; the last observation of a shared ID wins. Every row
keeps the last source name, URL and full field mapping as extraction provenance.
A whole source must validate before its transaction upserts anything. Failure,
ambiguous identity and partial extraction preserve existing records; absence is
never interpreted as deletion.

The committed break changes Costa's record class from `.dance` to `.performance`
and its price from 250 to 280. It is authored independently of cut 2's Mock Lab;
it is not a generated Mock Lab mutation. `fixtures/repair.json` changes exactly
one selector. Repair is a **prewritten manual fix replay**, not automatic repair.
Rastro runs on these same bytes, and its automatic candidate generator returns
`no_candidates`; the scripted manual repair goes through candidate CI/promotion.

## Measurement rules

The JSON report retains every phase, source outcome, row count, wall seconds,
machine minutes, environment/dependency versions, Git parent/dirty state and
SHA-256 hashes of the measured sources, mappings, oracle, fixtures and lockfile.
The committed `results.json` is one descriptive run; latency has no CI threshold.

1. Load the first two sources into a new database; expect six rows.
2. Add Costa; expect seven rows.
3. Repeat all three sources; expect seven rows with identical values.
4. Load the broken Costa snapshot with the original mapping; expect
   `missing_data`, seven retained rows and one stale price.
5. Apply the committed selector edit and reload; expect seven current rows,
   including the updated price of 280.

Phase wall time includes local input reading, parsing, validation, SQLite schema
creation/upserts and connection teardown. Imports, source-config loading, scoring,
report construction and human work are outside phase timing. Initial machine
minutes are phase 1 seconds / 60. Repair wall time includes reading/applying the
prewritten fix, the reload **and its verification query/scoring**; it excludes
human diagnosis and editing. Each phase runs once without warmup, in fixed order;
this is a workflow smoke measurement, not a statistically controlled latency study.

`score` counts a row as wrong if any field differs from the expected row, its ID
is unexpected, or it duplicates an expected ID. Each wrong row counts once;
missing IDs are counted separately. Successful phases report wrong stored rows
as `silent_errors`. For the single-source failed phase, no rows were accepted:
the wrong retained price is reported as `stale_records` and
`dataset_wrong_records`, not a silently accepted error. This prevents a failed
load from appearing current merely because the database retained its last data.
An oracle is required to detect syntactically valid wrong values; the baseline
itself does not detect arbitrary semantic corruption.

The site-specific metric counts physical nonblank, non-`#` lines in
`baseline/agenda.py` and `baseline/sources.json`. Docstrings, imports, braces and
JSON punctuation count; formatting therefore affects the result. The report gives
both per-file counts and their sum. The one-time SQLite loader, shared extractor
and shared benchmark contracts are counted separately, so reuse does not hide
setup code. Tests, fixture HTML, oracle, report and measurement harness are
excluded from integration LOC. The repair additionally records changed mapping
entries and physical lines in `repair.json`. Rastro applies the same rules to `rastro/job.json`, with its manual staging
helper and whole shared runtime counted separately; `rastro/repair.json` is the
additional repair configuration.

Successful offline execution requires **zero** network requests: Python socket
connect, connect_ex and DNS resolution are blocked and counted, and an attempt
aborts the run. The parser-only executor has no native HTTP client. Model calls
are zero by construction: no model port/client exists on this path. Initial and
recurring service spend is zero; local compute, electricity and development
agent usage are unpriced, not free. Listed human steps and `human_minutes: null`
keep human effort separate; agent build minutes/tokens were not instrumented.

## Original E1 baseline run

The original E1 cold workflow run on 3.12.16 / macOS-26.6.2-arm64-arm-64bit;
the table below is historical. `results.json` now contains a fresh run of both
sides, documented in [comparison-v02.md](../../docs/comparison-v02.md).

| Phase | Machine milliseconds | Rows | Silent errors | Stale rows |
| --- | ---: | ---: | ---: | ---: |
| initial_two | 3.094 | 6 | 0 | 0 |
| add_third | 0.819 | 7 | 0 | 0 |
| repeat | 1.896 | 7 | 0 | 0 |
| broken | 0.083 | 7 | 0 | 1 |
| repaired | 0.543 | 7 | 0 | 0 |

Site-specific code/config: 78 lines (schema 16, config 62); one-time loader: 58 lines.
Shared extractor/contracts: 143/75 lines (whole reused modules, including unused helpers).
Network requests/model calls: 0/0; scripted repair: 0.727 ms and one mapping edit.
Human time and development-agent cost remain unmeasured.

## E2 integration

The original baseline, counting rules and inputs are unchanged. The comparison
is linked from the documentation navigation. No runtime dependency, public protocol
or persisted-format change is introduced. ADRs 0005 and 0011 still govern this
evidence; no sealed corpus is opened or edited. Human minutes and development
agent usage remain uninstrumented.

For complete checks, exclude `tests/unit/test_recovery_corpus.py`: that existing
module opens the sealed split. Ruff/mypy must also exclude the sealed directory.
