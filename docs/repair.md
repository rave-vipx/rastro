# Recipe CI and controlled repair

**Requirements:** REPAIR-001–006, REC-005/006, SCR-002, QUAL-005/006.

Load a successful job with both `recipe_memory = "recipes.sqlite"` and a separate
`[dataset]` first. `job load` retains the last successful sanitized Mock Lab sample
and, for CSS, hashed element fingerprints in the **dataset** file. Failed and partial
runs never replace that evidence. The recipe-memory schema is unchanged.

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

## Commands

```sh
rastro job load job.toml --drift
rastro recipe repair job.toml --source 1
rastro recipe promote job.toml --source 1 --version 2 --reason "Reviewed CI report"
rastro recipe compare job.toml --source 1 --from-version 1 --to-version 2
rastro recipe rollback job.toml --source 1 --version 1 --reason "Restore previous recipe"
```

`--source` is one-based; repair defaults to all sources. `--report new-report.json`
selects a new report filename within the job directory. Otherwise each repair run
creates `repair-report-<run-id>.json`, with mode 0600, and prints the same JSON.
Existing report files and symlinks are refused. File inputs stay within the job
directory unless `--allow-absolute-paths` is explicitly supplied. Local inputs and
fixture files reject symlinks. HTTP evidence uses the existing robots, destination,
redirect, pacing, byte and access-restriction policies; relocation never fetches.

Exit codes: **0** means every selected source promoted a candidate (other proposals
may have been rejected); **3** means a candidate remains quarantined; **1** means
at least one selected source had no promotion and no candidate was quarantined; **2** means a typed usage,
configuration, storage or resource error. Compare and rollback return 0 on success.
Errors are JSON on stderr, as for job commands.

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

Every candidate is evaluated. Ambiguous candidates remain quarantined. If multiple
candidates pass, all are quarantined as competing proposals. A unique passing candidate
may promote only with `auto_promote = true`. Rejected versions become retired; quarantined
versions stay in lifecycle state `candidate`. Neither state serves unpinned extraction.
`recipe promote` re-runs CI on fresh evidence and explicitly enables promotion; it does
not bypass history, semantic or configured threshold gates. A malformed or now-failing
candidate cannot be approved through this command.

`RecipeComparison` has `schema_version = 1`, old/new versions and canonical recipe
digests, changed field names, strategy-change status and an optional `CandidateEvaluation`.
It omits page values and selector text. See the [comparison schema](schemas/recipe-comparison.schema.json).
A repair report contains one comparison per proposed version, generated checks, measured
indicators, explicit no-candidate outcomes and bounded model issue/call counts.

## Load and rollback behavior

For dataset loads with recipe memory, explicit job recipes bootstrap a missing context.
Once that context has a serving recipe, `job load` uses it. Thus promotion and rollback
affect the next load even when the bootstrap recipe remains in the job. `job run` and
the SDK retain their existing explicit-recipe precedence. An explicitly pinned
`recipe_version` retains its existing behavior.

Rollback can target only a version that previously served. It restores the recipe;
it does not rewind or delete dataset observations. Repair and promotion never call
`apply_snapshot`, so failed repairs cannot delete or replace dataset records.
A subsequent successful load updates observed fields through the ordinary dataset path.

## Boundaries

The [offline demo](https://github.com/rave-vipx/rastro#drift-and-repair-offline) covers quarantine, promotion,
rollback and the valid-sale-date trap with no AI or browser. The date-sensitive example
was executed on 2026-11-07; its November event dates eventually become historical.
This is synthetic evidence, not proof of live-site repair quality.

Relocation uses one template record and requires an applicable record selector. Changed
record selectors fall through to declared fallbacks, discovery and optionally the job's
configured model. Models are never enabled implicitly. Last-success replay conservatively
rejects a candidate that changes historical field values, even when that change might be
intentional. Automatic semantic monitoring of ordinary loads, score calibration,
retention pruning and heterogeneous-template relocation remain open work.

Dataset writes, last-success retention, recipe transitions and report writes are separate
transactions. A storage failure can occur after validated data or a promotion has committed;
inspect lifecycle history before retrying after such an error. Trusted private directories
are required; path checks are not a sandbox against concurrent directory replacement.
Retained samples contain approved extracted values and need the dataset's privacy controls.
