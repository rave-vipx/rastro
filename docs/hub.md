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
$ rastro recipe import ./my-recipe --store recipes.sqlite [--activate]
```

Import treats the bundle as untrusted. It checks, in order: the directory is real
and not a symlink; `format_version` is known; the file list is exactly the expected
names; every file is a regular, size-bounded file whose SHA-256 matches; the recipe
and schema validate; and the recipe, re-run offline on the sample, yields records
equal to `expected.json`. Only then is the recipe stored, as a `candidate`, or as
`active` with `--activate`, following the [lifecycle](recipe-lifecycle.md). It gets
the next free version, so it never overwrites a stored version, and the key (source,
schema, region, context) is the one your own job computes for the same source.

Every failure exits with code 2 and a typed error (`BundleIntegrityError`,
`BundleVersionError`, `BundleVerificationError`, all `ConfigurationError`) and leaves
the store unchanged. Hashes detect corruption, not a malicious author: review a
candidate and keep `--activate` for bundles you trust.

## Limits

Metadata files are capped at 1 MiB and the sample at the job's input limit. The
JSON Schema in `schema.json` is not used for verification; the field definitions are.
