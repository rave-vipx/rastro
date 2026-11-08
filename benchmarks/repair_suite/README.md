# Repair suite

Measures automatic recipe repair on breakage modeled on three Uruguayan source shapes
(REPAIR-001–005, DIFF-002 repair metric). It is not the recovery benchmark and does not touch
`benchmarks/recovery/`. Pages are synthetic copies of the structures, not captured content.

## Method

For each page × mutation, `measure.py` works in a temp directory through the public CLI entry
point (`rastro.cli.main`):

1. `job load --drift` on the base page; its rows must equal `expected.json`.
2. Apply the mutation to `page.html`, then `job load --drift`.
   * exit 0 and rows equal to expected: `not_broken`; exit 0 with other rows: `silent_wrong`
     (the load succeeded with wrong data, repair is not invoked).
   * exit 3 with an inconsistent report and the original expected rows retained: `held`
     (safe detection without promotion; repair is not invoked by this measurement).
3. Otherwise `recipe repair`, then `job load` if it exited 0. Stored rows must equal
   `expected.json` exactly (same ids, every expected field equal):
   `promoted_correct` / `promoted_wrong`; no promotion gives `quarantined` (a quarantine
   decision), `rejected` (candidates, none promoted) or `no_candidates`.

`model_calls` is 0 by construction: no job configures a model.

## Cases

* `cases/sodre`: 6 venue cards (`.item .cyg--info.espectaculo`).
* `cases/tickantel`: JSON-LD array of 3 `Event`s with `offers.validFrom` (the sale-date trap).
* `cases/zitarrosa`: `main > ul > li.evento`, 5 items.
* Each has `job.toml` (with `[repair]` and `[[consistency]]`), `page.html`, `expected.json`.
* `mutations.py` defines the named transforms: structural (should be repaired) and traps
  (must not be promoted wrongly). A mutation can turn out harmless for a page's selectors;
  that is reported as `not_broken`.

## Run

```sh
UV_PYTHON=/opt/homebrew/bin/python3.12 uv run python benchmarks/repair_suite/measure.py
# one case: --case sodre:field_class_rename
```

It prints one row per case and writes `results.json`; the numbers there are the only claim.

## After B3 (QUAL-005/006, SYNC-006)

Job loads now gate snapshots on the job's declared consistency rules. The suite jobs
also declare `distinct_ratio` on `title` (minimum ratio 0.5 over at least 3 records):
these agendas expect varied titles. This is a user-declared rule, not an engine assumption.
`held` counts as safe and never as `promoted_correct`; it measures detection, not recovery.

| Outcome | Before B3 | After B3 |
| --- | ---: | ---: |
| promoted_correct | 6 | 6 |
| promoted_wrong | 0 | 0 |
| quarantined | 0 | 0 |
| rejected | 3 | 3 |
| no_candidates | 9 | 9 |
| not_broken | 2 | 2 |
| silent_wrong | 5 | 0 |
| held | 0 | 5 |

`results.json` is the after B3 run. Previously silent wrong date/title loads are held;
this does not imply that their recipes were repaired.
