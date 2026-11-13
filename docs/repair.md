# Recipe CI and controlled repair

**Requirements:** REPAIR-001–006, REC-005/006, SCR-002, QUAL-005/006.

Load a successful job with both `recipe_memory = "recipes.sqlite"` and a separate
`[dataset]` first. `job load` retains the last successful sanitized Mock Lab sample
and, for CSS, hashed element fingerprints in the **dataset** file. Failed and partial
runs never replace that evidence. The recipe-memory schema is unchanged.

CSS fingerprints cover the first record's container and its observed fields, so an absent
optional field does not prevent retention. Both use hashes rather than raw page content.
Existing snapshots without the optional `record` fingerprint still load under relocation
schema version 1; they support field relocation but cannot propose a changed container.
The next successful load captures the container without a migration or backfill.
If the document exceeds relocation's structural budget
or uses unsupported selectors, the historical fixture is still retained. The successful
load keeps its exit code and reports `relocation_retention_skipped` in that source's
`issues` array (DATA-001, SCR-002). If the sample cannot be retained at all (for example,
the sanitizer refuses it or the evidence write fails), the rows are already committed: the
load keeps its exit code, reports `evidence_retention_failed` for that source and keeps the
previous evidence. No issue array is emitted when retention succeeds.

```toml
[repair]
auto_promote = true
min_records = 4
min_historical_fixtures = 1
min_completeness = 1.0
min_structural_validity = 1.0
min_semantic_confidence = 1.0
# Optional additional Mock Lab bundle directories, relative to this job:
fixtures = []
```

All fields are optional. Without `[repair]`, automatic promotion is disabled; valid
candidates remain quarantined. Without `[[consistency]]`, semantic confidence is
reported as unassessed and the semantic gate is inapplicable. With declared rules,
only `consistent` passes that gate. An `inconsistent` candidate is rejected.

Each configured fixture must match exactly one job source, using its query-free evidence
URL and regional context. Unmatched or ambiguous bundles raise a configuration error;
they are never silently omitted. Sources that differ only by query parameters cannot be
distinguished by these sanitized fixtures.

## Commands

```sh
rastro job load job.toml --drift
rastro recipe repair job.toml --source 1
rastro recipe promote job.toml --source 1 --version 2 --reason "Reviewed CI report"
rastro recipe promote job.toml --source 1 --version 2 --override-gates --reason "Checked by hand"
rastro recipe apply job.toml --source 1
rastro recipe compare job.toml --source 1 --from-version 1 --to-version 2
rastro recipe show job.toml --source 1 --version 2
rastro recipe rollback job.toml --source 1 --version 1 --reason "Restore previous recipe"
```

`--source` is one-based; repair defaults to all sources. `--report new-report.json`
selects a new report filename within the job directory. Otherwise each repair run
creates `repair-report-<run-id>.json`, with mode 0600, and prints the same JSON.
Existing report files and symlinks are refused. File inputs stay within the job
directory unless `--allow-absolute-paths` is explicitly supplied. Local inputs and
fixture files reject symlinks. HTTP evidence uses the existing robots, destination,
redirect, pacing, byte and access-restriction policies; relocation never fetches.
The job's base directory is resolved first, permitting OS aliases such as macOS `/tmp`;
the job file itself and paths within that base still refuse symlinks. Reports and the
offline demo use UTF-8 independently of the locale.

`recipe apply` writes the serving recipe of one source from `recipe_memory` back into the
job file and prints `{"schema_version": 1, "path", "source_index", "recipe_version",
"changed"}`. In a TOML file only that source's recipe tables (`[sources.recipe]`, its
`fields` and `fallbacks`) are rewritten, in a canonical layout; every other line, comment
included, stays as it was. Comments inside the recipe tables are lost. JSON files are
rewritten with two-space indentation. The result is reparsed and must equal the old job
except for that recipe, or nothing is written. The file is replaced atomically and
symlinks are refused. When several sources share the job-level `recipe`, apply adds a
`[sources.recipe]` table to the repaired source only; the other sources keep the shared
recipe. A recipe written inline (`recipe = { ... }`) or split by other tables is refused
with exit code 2; update it by hand. When the file already holds the serving recipe, `changed` is `false`
and the file is not touched.

Exit codes: **0** means every selected source promoted a candidate (other proposals
may have been rejected); **3** means a candidate remains quarantined; **1** means
at least one selected source had no promotion and no candidate was quarantined; **2** means a typed usage,
configuration, storage or resource error. Compare and rollback return 0 on success.
Errors are JSON on stderr, as for job commands.

## Python API

The same steps are available from Python. Source indexes are one-based, as on the
command line; configuration problems raise `ConfigurationError`.

```python
import rastro

loaded = rastro.load_job_file("job.toml")  # JobLoadResult
report = rastro.repair_job_source("job.toml", 1)  # RepairReport
report.sources[0].promoted_version  # None unless a version became active
rastro.promote_job_recipe("job.toml", 1, 2, reason="Reviewed CI report")
rastro.promote_job_recipe("job.toml", 1, 2, reason="Checked by hand", override_gates=True)
rastro.apply_job_recipe("job.toml", 1)  # writes the serving recipe back
```

`repair_job_source` accepts `min_records`, `min_completeness` and `auto_promote` to
override the job's `[repair]` values for one call. `RepairReport.exit_code` follows the
command's exit codes.

## What CI measures

`evaluate_candidate` in `application/recipe_ci.py` evaluates each proposal without
writing recipes or dataset records:

1. Current evidence is extracted through the parser and the job's schema validators.
   Empty, partial, ambiguous or rejected records cannot pass.
2. The last successful sample and configured historical Mock Lab bundles are replayed
   with the candidate. Validated field values and proven field sets must be preserved;
   changed selectors and version numbers are allowed. Missing historical coverage
   quarantines; a regression rejects. Additional bundles bind to the source and schema.
3. Generated regression checks assert nonempty validated records, required-field
   provenance and zero rejected current records. They are declarative checks in the
   report, never executable recipe code.
4. Declared consistency rules use dataset history read **before** any candidate
   snapshot could be applied. Completeness, structural validity and semantic
   confidence remain separate, measured indicators, compared with configured minima.
   Completeness counts every required field, and each optional field at three quarters
   of the rate the stored success samples had a value for it: an optional `stock`
   present in all sample cards needs a value in at least 75% of the current cards, so
   one card in five without it does not block a repair, but losing it in half the cards
   does. An optional field the
   samples never filled is not counted. Required fields must be filled in every record.
   Relocated record containers may omit children (an absent optional element, matched
   by tag and class) but not add, reorder or reclass them, and must keep at least half
   of them. Relocated field selectors use tag and class steps, never positions, and never
   target an element another field already reads, so a card without an optional element
   leaves that field empty instead of reading its neighbour.

Every candidate is evaluated, by the same evaluator for `recipe repair` and
`recipe promote`, so both reach the same verdict on the same page and samples. Hard failures
come first: a candidate whose current records fail validation, a historical replay or a
declared consistency rule is **rejected**, and the report's `blocking` list names each
failing record (`record_index`, `field`, issue `code`, never values). Rejection retires the
version with a lifecycle event (`Recipe CI: reject`), from either command. Ambiguous
candidates that otherwise validate remain quarantined (`ambiguous_evidence`). A proposal equal
to a stored candidate reuses that version, and one equal to a retired version that is rejected
again reuses it too, so repeated repair runs do not allocate new versions. If multiple
candidates pass, all are quarantined as competing proposals. A unique passing candidate
may promote only with `auto_promote = true`. Rejected versions become retired; quarantined
versions stay in lifecycle state `candidate`. Neither state serves unpinned extraction.
`recipe promote` re-runs CI on fresh evidence and explicitly enables promotion; it does
not bypass history, semantic or configured threshold gates. A malformed or now-failing
candidate cannot be approved through this command. `--override-gates` lets a person
activate a candidate that CI quarantined (for example, too few current records); it
requires an explicit `--reason`, which the lifecycle history records as
`gates overridden: <reason>`. The override bypasses quarantine thresholds only, never a
hard validation failure: a rejected candidate (failed validation, regression or an
inconsistent semantic check) is retired, and with the flag the command exits 2 with an error
that says so and lists the blocking records. `recipe promote` is the human review that
ambiguity asks for: it has no relocation evidence, so `ambiguous_evidence` is not re-checked.
Proposals identical to the active mapping after evidence/fallback normalization are
discarded before version allocation. If none remain, repair reports `no_candidates`.

`RecipeComparison` has `schema_version = 1`, old/new versions and canonical recipe
digests, changed field names, changed recipe settings (`changed_settings`, such as
`record_selector`), strategy-change status and an optional `CandidateEvaluation`.
It omits page values and selector text. See the [comparison schema](schemas/recipe-comparison.schema.json).
A repair report contains one comparison per proposed version, generated checks, measured
indicators, explicit no-candidate outcomes and bounded model issue/call counts.

## Load and rollback behavior

With `recipe_memory`, the job file's recipe bootstraps a missing context. After that,
`job run`, `job load`, `run_job` and `run_job_file` use the serving recipe in memory
when its version is **higher** than the job file's recipe `version`; otherwise the job
file's recipe is used. A promoted repair therefore applies to the next run without
editing the job file, and `recipe apply` makes the file match it. Editing a recipe by
hand works as before: give it a version above the stored ones. Each run and load report
says which recipe each source used (`recipe_origin` is `"job"` or `"memory"`, with
`recipe_version`). An explicitly pinned `recipe_version` keeps its existing behavior.
See [ADR 0021](decisions/0021-serving-recipe-over-job-file.md).

Rollback can target only a version that previously served. Because the newer version
wins, rolling back below the job file's recipe version has no effect on runs until you
run `recipe apply` (which writes the rolled-back recipe into the file). It restores the recipe;
it does not rewind or delete dataset observations. Repair and promotion never call
`apply_snapshot`, so failed repairs cannot delete or replace dataset records.
A subsequent successful load updates observed fields through the ordinary dataset path.

## Boundaries

The [offline demo](https://github.com/rave-vipx/rastro#drift-and-repair-offline) covers quarantine, promotion,
rollback and the valid-sale-date trap with no AI or browser. Its observation clock
is pinned to 2026-11-07 so the November sample remains reproducible.
This is synthetic evidence, not proof of live-site repair quality.

Relocation uses one template record. When the record selector finds nothing, Scrapling
can relocate its saved container fingerprint, hashing class tokens individually and
comparing them as sets. The proposed selector combines the old selector with the shared
ancestor path (joined by child combinators), the matched tag and shared classes, and is
accepted only when the recipe's field selectors or relocated field fingerprints find its
fields inside the container. Keeping both selectors allows historical pages to keep
parsing; field relocation then runs inside the new container in the same candidate. The
`record_selector` evidence entry counts container matches (including ties), or all records
selected if the derived selector expands beyond those matches. Homogeneous listing
containers—sharing the same parent path, child-tag sequence and class token set, with no
competing same-class or classless same-tag containers of another shape on the page—pass to
recipe CI; any heterogeneous, extra or unverified single-container match is marked
ambiguous and quarantined when its fields validate, and rejected when they do not. Promotion still requires the
ordinary history, evidence and configured CI gates.

If no container candidate is available, repair tries declared fallbacks, discovery and
optionally the job's configured model. Models are never enabled implicitly. Last-success replay conservatively
rejects a candidate that changes historical field values, even when that change might be
intentional. Automatic semantic monitoring of ordinary loads, score calibration,
retention pruning and heterogeneous-template relocation remain open work.

Dataset writes, last-success retention, recipe transitions and report writes are separate
transactions. A storage failure can occur after validated data or a promotion has committed;
inspect lifecycle history before retrying after such an error. Trusted private directories
are required; path checks are not a sandbox against concurrent directory replacement.
Retained samples contain approved extracted values and need the dataset's privacy controls.
