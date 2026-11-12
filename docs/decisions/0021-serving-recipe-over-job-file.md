# ADR 0021: a newer serving recipe wins over the job file's recipe

- **Status:** accepted for 0.6.0.
- **Requirements:** REC-006, REPAIR-005, REPAIR-006, QUAL-006, DATA-001.

## Problem

A job with `recipe_memory` carries a recipe in its file and a lifecycle of versions in
memory. Before this decision, `job load` used the serving recipe as soon as one existed,
while `job run`, `run_job` and `run_job_file` always used the file's recipe. After a
promoted repair, runs kept using the stale recipe and reported `partial_success`, and
nothing wrote the repaired recipe back to the file.

## Options

1. **The file always wins.** Simple, but a promoted repair has no effect until someone
   edits the file by hand.
2. **Memory always wins once it has a serving recipe** (the old `job load` rule). A
   hand-edited recipe in the file is silently ignored.
3. **The higher version wins.** Memory is used only when its serving version is higher
   than the file recipe's `version`.

## Decision

Option 3, for every job entry point (`job run`, `job load`, `run_job`, `run_job_file`).
Repairs allocate versions above every stored version, so a promotion takes effect on the
next run. A person who edits the file's recipe raises its version, and the edit takes
effect. `rastro recipe apply` writes the serving recipe into the file; the versions are
then equal and the file's recipe (now identical) is used. Every run and load report
states the recipe origin and version per source, so the choice is never hidden. A
pinned `recipe_version` keeps its behavior.

## Consequences

- Rolling back to a version below the file's recipe version does not change runs until
  `recipe apply` writes the rolled-back recipe into the file. The documentation says so.
- Editing a file recipe without raising its version while memory holds the same version
  with a different definition fails with `RecipeConflictError`, as `job run` already did.
- `recipe apply` rewrites only the recipe tables of a TOML file and verifies the reparsed
  job; layouts it cannot edit safely (inline tables, split tables, a job-level recipe
  shared by several sources) are refused instead of reformatting the whole file.
