# Recipe hub: share recipes through Git

A recipe is data, so it can travel in a plain Git repository. A bundle lets one
person publish a working recipe and another application use it without the author's
help (HUB-001; master spec section 09). Version 0.5 ships this first step: export
and import. There is no registry service, ranking or automatic download.

## Bundle layout

`rastro recipe export` writes five files with deterministic bytes (sorted keys, LF,
two-space indent), so Git diffs show only real changes:

| File | Content |
| --- | --- |
| `recipe.json` | The serving recipe. |
| `schema.json` | `fields` (the job field definitions used to rebuild the schema) and `json_schema` (informational JSON Schema). |
| `sample.html` or `sample.json` | The sample document, copied verbatim. |
| `expected.json` | The records the recipe yields on the sample. |
| `bundle.json` | Format version, source (URL without query), regional context, `last_verified` date and the SHA-256 of every other file. |

## Export

```console
$ rastro recipe export job.toml --source 1 --sample page.html --to ./my-recipe
```

The job must define `recipe_memory` and hold a serving recipe for the source. You
choose `--sample` explicitly: Rastro never copies a page on its own, so you must have
the right to redistribute it (use a synthetic or trimmed page). Export refuses to
write anything unless the recipe extracts the sample cleanly, and refuses source URLs
with a query string or fragment. Commit the directory to Git.

## Import

```console
$ rastro recipe import ./my-recipe --store recipes.sqlite
```

Import treats the bundle as untrusted. It checks, in order: the directory is real
and not a symlink; `format_version` is known; the file list is exactly the expected
names; every file is a regular, size-bounded file whose SHA-256 matches; the recipe
and schema validate; and the recipe, re-run offline on the sample, yields records
equal to `expected.json`. Only then is the recipe stored, always as a `candidate`, following the
[lifecycle](recipe-lifecycle.md). Promote it with `rastro recipe promote`, which runs
the full candidate gate; import never activates a recipe. It gets
the next free version, so it never overwrites a stored version, and the key (source,
schema, region, context) is the one your own job computes for the same source.
Importing a bundle whose recipe equals a version already stored for that key stores
nothing new: the output reports that version with `"reused": true`.

```console
{"schema_version": 1, "state": "candidate", "version": 1, "reused": false}
```

## Adopt a shared recipe

A consumer job needs `recipe_memory`, a `[dataset]` and no recipe of its own for that
source. Import the bundle into the job's store, promote the candidate, then load:

```console
$ rastro recipe import ./my-recipe --store recipes.sqlite
$ rastro recipe promote job.toml --source 1 --version 1 --reason "Reviewed shared recipe"
$ rastro job load job.toml
```

When nothing serves yet for that source, promotion needs no earlier load. It still runs
the candidate gate on your job's own source page: the recipe must extract it cleanly,
with evidence for every required field and no rejected records. Two gate details differ
because there is no history yet: the minimum number of historical fixtures does not
apply (the import already replayed the bundle's expected records), and a recipe with
fallbacks is accepted. A candidate that fails the gate stays a candidate.

Every failure exits with code 2 and a typed error (`BundleIntegrityError`,
`BundleVersionError`, `BundleVerificationError`, all `ConfigurationError`) and leaves
the store unchanged. Hashes detect corruption, not a malicious author: a bundle gives
integrity, not authenticity. Review a candidate before you promote it.

Because anyone can build a bundle whose hashes and replay check out, import applies the
untrusted-recipe selector policy before the replay runs. CSS selectors may not use
`:has()` or sibling combinators (`~`, `+`), chain more than 8 compound selectors, list
more than 8 alternatives or nest pseudo-classes more than 2 deep. These shapes are
quadratic or worse in lxml: a 48 KB bundle using `p ~ p ~ p ~ b` took 105 s of CPU to
replay. A refused selector fails with a `ConfigurationError` that names the rule. The same
policy applies to recipes from MCP `scrape_file`, Studio selector edits and model
proposals. Recipes you write in your own job file are not checked.

## Limits

- Export also writes `.gitattributes` (`* -text`) so Git on Windows keeps the hashed bytes unchanged.
- `--to` follows symlinks in its parent directories, like `cp`; only the final directory and the bundle files themselves refuse symlinks. Export to a directory you own.

Metadata files are capped at 1 MiB and the sample at the job's input limit. The
JSON Schema in `schema.json` is not used for verification; the field definitions are.
