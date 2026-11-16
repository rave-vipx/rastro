# Recipe hub: share recipes through Git

A recipe is data, so it can travel in a plain Git repository. A bundle lets one
person publish a working recipe and another application use it without the author's
help (HUB-001..HUB-006; master spec section 09). Phase 1 ships local export and
import (`rastro recipe export`/`import`); Phase 2 adds package metadata, reproducible
trust indicators, and a Git-directory community registry (`publish`, `search`,
`install`). Rastro reads and writes local directories; it never runs `git` and never
makes network requests for a registry.

## Bundle layout (format 2)

`rastro recipe export` writes five files with deterministic bytes (sorted keys, LF,
two-space indent), so Git diffs show only real changes:

| File | Content |
| --- | --- |
| `recipe.json` | The serving recipe. |
| `schema.json` | `fields` (the job field definitions used to rebuild the schema) and `json_schema` (informational JSON Schema). |
| `sample.html` or `sample.json` | The sample document, copied verbatim. |
| `expected.json` | The records the recipe yields on the sample. |
| `bundle.json` | Format version (`2`), package metadata (`id`, `version`, `author`, `license`, `visibility`, `compatibility`), source (URL without query), regional context, `last_verified` date and the SHA-256 of every other file. |

Format 1 bundles (without package metadata) remain supported on import. Unknown
`format_version` values fail with `BundleVersionError`.

## Export

```console
$ rastro recipe export job.toml --source 1 --sample page.html --to ./my-recipe \
    --id uy-sodre-agenda --version 1.0.0 --author "Rastro Contributors" \
    --license Apache-2.0 --public
```

The job must define `recipe_memory` and hold a serving recipe for the source. You
choose `--sample` explicitly: Rastro never copies a page on its own, so you must have
the right to redistribute it (use a synthetic or trimmed page). Export refuses to
write anything unless the recipe extracts the sample cleanly, and refuses source URLs
with a query string or fragment.

By default, exported bundles have `"visibility": "private"` (HUB-004). Pass `--public`
together with `--id` (`^[a-z0-9][a-z0-9-]{1,63}$`), `--version` (`MAJOR.MINOR.PATCH`),
`--author` (and optional `--author-url`), and `--license` (SPDX identifier) when
preparing a package for a shared registry.

## Import

```console
$ rastro recipe import ./my-recipe --store recipes.sqlite
```

Import treats the bundle as untrusted. It checks, in order: the directory is real
and not a symlink; `format_version` is known (`1` or `2`); the file list is exactly the expected
names; every file is a regular, size-bounded file whose SHA-256 matches; the recipe
and schema validate; costly selectors are refused; and the recipe, re-run offline on the sample, yields records
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

## Git-directory registry (`publish`, `search`, `install`)

A registry is a plain directory backed by a Git repository that you clone and push
yourself (see `examples/registry/` for a synthetic example):

- `<registry>/index.json` — canonical JSON list of published packages (`id`, `version`, `author`, `license`, `source_patterns`, `last_verified`, `trust`).
- `<registry>/recipes/<id>/<version>/` — the verified bundle files.

### Publish

```console
$ rastro recipe publish ./my-recipe --registry ./registry
```

`publish` enforces four gates before writing to `<registry>`:

1. **Public visibility (HUB-004):** private bundles are refused with a hint to re-export using `--public`.
2. **Offline verification (HUB-003):** file digests, untrusted-selector policy, and sample replay must pass.
3. **Immutability:** an existing `<id>@<version>` in the registry is never overwritten.
4. **Secret scanning (HUB-004):** every bundle file is scanned with the Mock Lab secret and personal-data detectors (`src/rastro/mocklab/detectors.py`); any hit is refused naming only the file and detector category, never echoing the matched value.

When all checks pass, Rastro writes `<registry>/recipes/<id>/<version>/`, updates
`<registry>/index.json` atomically, and prints the `git` commands you may run to commit
and push (paths and the commit message are shell-quoted, so you can paste them as is).
If writing the package or `index.json` fails, Rastro removes the package directory this
publish created (and nothing else), leaves `index.json` as it was, and exits with a
`RegistryError`, so you can fix the cause and publish again.

### Search and install

```console
$ rastro recipe search sodre --registry ./examples/registry
$ rastro recipe install uy-sodre-agenda --registry ./examples/registry --store recipes.sqlite
$ rastro recipe install uy-sodre-agenda@1.0.0 --registry ./examples/registry --store recipes.sqlite
```

`search` matches the query against `id` and `source_patterns`. `install` selects the
highest semver version by default (or the pinned `@VERSION`), verifies file hashes,
selector policy, and offline sample replay, recomputes trust indicators, and stores the
recipe as a `candidate`. A package that declares `compatibility` is also checked
against this install: it is refused (`RegistryError`, store unchanged) when the running
`rastro-engine` version is below its `min_rastro`, or when the bundle's source URL does
not match any of its `source_patterns` (a host such as `example.com`, or a
`host/path-prefix` that matches whole path segments; the port must match when given). Imported packages never execute code (HUB-005): only JSON,
HTML, and declarative selector data are read.

### Trust indicators (HUB-006)

Trust indicators are computed from offline verification, never self-declared:

- `replay_verified`: whether the recipe reproduced `expected.json` on the bundled sample under this Rastro version.
- `days_since_verified`: days elapsed since the bundle's `last_verified` date.
- `has_sample`: whether the bundle includes a non-empty sample document.
- `format`: bundle manifest format version (`1` or `2`).

`index.json` records the trust computed at publish time; `install` recomputes trust at
install time and outputs both `trust` and `published_trust`.

## Adopt a shared recipe

A consumer job needs `recipe_memory`, a `[dataset]` and no recipe of its own for that
source. Import or install the bundle into the job's store, promote the candidate, then load:

```console
$ rastro recipe install uy-sodre-agenda --registry ./examples/registry --store recipes.sqlite
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
`BundleVersionError`, `BundleVerificationError`, `RegistryError`, all `ConfigurationError`) and leaves
the store unchanged. Hashes detect corruption, not a malicious author: a bundle gives
integrity, not authenticity. Review a candidate before you promote it.

Because anyone can build a bundle whose hashes and replay check out, import and install apply the
untrusted-recipe selector policy before the replay runs. CSS selectors may not use
`:has()` or sibling combinators (`~`, `+`), chain more than 8 compound selectors, list
more than 8 alternatives or nest pseudo-classes more than 2 deep. These shapes are
quadratic or worse in lxml: a 48 KB bundle using `p ~ p ~ p ~ b` took 105 s of CPU to
replay. A refused selector fails with a `ConfigurationError` that names the rule. The same
policy applies to recipes from MCP `scrape_file`, Studio selector edits and model
proposals. Recipes you write in your own job file are not checked.

## Limits

- Export and publish write `.gitattributes` (`* -text`) so Git on Windows keeps the hashed bytes unchanged.
- `--to` and `--registry` refuse symlinks on the target directory, `index.json`, `recipes/`, package directories, and bundle files.

Metadata files are capped at 1 MiB and the sample at the job's input limit. The
JSON Schema in `schema.json` is not used for verification; the field definitions are.
