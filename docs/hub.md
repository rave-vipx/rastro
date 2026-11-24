# Recipe hub: share recipes through Git

A recipe is data, so it can travel in a plain Git repository. A bundle lets one
person publish a working recipe and another application use it without the author's
help. Export and import (`rastro recipe export`/`import`) work
on local directories, and a registry adds package metadata, reproducible trust
indicators and `publish`, `search` and `install`. A registry is a local directory
or any HTTPS URL that serves the same layout. Rastro never runs `git`; it makes
network requests for a registry only when you pass `--registry-url`, under the same
network safeguards as a scrape.

## The shared hub (`rastro hub`)

The hub is a registry shared between users: a static Git repository with the registry
layout below, served raw over HTTPS. Rastro points at the public hub by default
(`https://raw.githubusercontent.com/rave-vipx/rastro-hub/main/`, the constant
`rastro.application.registry_url.DEFAULT_HUB_URL`); `--registry-url URL` points at a
self-hosted hub with the same layout and `--registry DIR` at a local checkout. There are no
accounts, no Rastro-operated server and no telemetry.

### Find a recipe for a URL

```console
$ rastro hub search https://listing.example/cards/page-2
{"schema_version": 1, "candidates": [{"id": "synthetic-listing", "version": "1.0.0", "state": "candidate", "card": {...}, "health": {...}, ...}]}
```

Search matches the URL's domain (`host[:port]`, case-insensitive; the path is ignored, a
subdomain is a different domain) against each package's `source_patterns` and offers the newest
version of at most five packages. An offer is only a `candidate`: search never writes to a
recipe store. The SDK equivalent is `rastro.application.hub_search.hub_candidates(url)`, and
`install_candidate(offer, repository, parser_for, at=...)` installs the offered version.

`rastro hub install ID[@VERSION] --store recipes.sqlite` runs exactly the gates of
`rastro recipe install` (digests, selector policy, offline replay, index match, compatibility)
and stores a `candidate`. Nothing serves until `rastro recipe promote` passes the candidate gate
on your own page. A failed gate leaves the store unchanged.

### Publish in one step

```console
$ rastro hub publish job.toml --source 1 --sample synthetic-page.html --registry ./rastro-hub \
    --id listing-cards --version 1.0.0 --author "Your Name" --license Apache-2.0 \
    --describe title="The card's headline" --describe price_text="Price as printed"
```

`publish` takes the job's serving recipe and writes, into the local checkout, the verified
bundle, the sample you chose, and `card.json` with a description of every top-level field and
the recipe's engine needs (representation, render mode and capabilities such as page actions or
stealth). Every field must be described, and nothing else; descriptions are one line of 1-500
printable characters. All gates of `export` and `publish` below apply, including the secret
and personal-data scan, which also covers `card.json`. The sample must be synthetic or yours to
redistribute: Rastro never copies a page on its own. Publish never uses the network; it prints
the `git` commands to commit, push and open a pull request against the hub.

### Cards and health

Two optional files sit beside the registry layout. Both are untrusted, size-bounded and
validated strictly; an invalid one stops the search with `RegistryError` rather than being
ignored.

| File | Written by | Content |
| --- | --- | --- |
| `recipes/<id>/<version>/card.json` | `rastro hub publish` | `format` (`1`), `fields` (`name`, `description`), `engine_needs` (`representation`, `render`, `capabilities`). At most 64 KiB. |
| `health.json` | the hub's CI | `format` (`1`) and `packages`: `id`, `version`, `last_verified` (date), `rastro_version`, `passed`. Read-only in Rastro. |

The engine needs in a card are informative: an install grants nothing, and a recipe that
needs a capability is still refused unless the operator granted it for registry input. Health
says when the hub last re-ran a package's replay and with which Rastro version; your install
recomputes trust itself.

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
    --id teatro-central-agenda --version 1.0.0 --author "Rastro Contributors" \
    --license Apache-2.0 --public
```

The job must define `recipe_memory` and hold a serving recipe for the source. You
choose `--sample` explicitly: Rastro never copies a page on its own, so you must have
the right to redistribute it (use a synthetic or trimmed page). Export refuses to
write anything unless the recipe extracts the sample cleanly, and refuses source URLs
with a query string or fragment.

By default, exported bundles have `"visibility": "private"`. Pass `--public`
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

1. **Public visibility:** private bundles are refused with a hint to re-export using `--public`.
2. **Offline verification:** file digests, untrusted-selector policy, and sample replay must pass.
3. **Immutability:** an existing `<id>@<version>` in the registry is never overwritten.
4. **Secret scanning:** every bundle file is scanned with the Mock Lab secret and personal-data detectors (`src/rastro/mocklab/detectors.py`); any hit is refused naming only the file and detector category, never echoing the matched value.

When all checks pass, Rastro writes `<registry>/recipes/<id>/<version>/`, updates
`<registry>/index.json` atomically, and prints the `git` commands you may run to commit
and push (paths and the commit message are shell-quoted, so you can paste them as is).
If writing the package or `index.json` fails, Rastro removes the package directory this
publish created (and nothing else), leaves `index.json` as it was, and exits with a
`RegistryError`, so you can fix the cause and publish again.

### Search and install

```console
$ rastro recipe search teatro --registry ./examples/registry
$ rastro recipe install teatro-central-agenda --registry ./examples/registry --store recipes.sqlite
$ rastro recipe install teatro-central-agenda@1.0.0 --registry ./examples/registry --store recipes.sqlite
```

`search` matches the query against `id` and `source_patterns`. `install` selects the
highest semver version by default (or the pinned `@VERSION`), verifies file hashes,
selector policy, and offline sample replay, recomputes trust indicators, and stores the
recipe as a `candidate`. A package that declares `compatibility` is also checked
against this install: it is refused (`RegistryError`, store unchanged) when the running
`rastro-engine` version is below its `min_rastro`, or when the bundle's source URL does
not match any of its `source_patterns` (a host such as `example.com`, or a
`host/path-prefix` that matches whole path segments; the port must match when given). Imported packages never execute code: only JSON,
HTML, and declarative selector data are read.

### Install from a registry URL

A hosted registry is any `https` URL that serves the same directory: `index.json` and
`recipes/<id>/<version>/` (a static host such as GitHub Pages, or a raw-file URL such as
`raw.githubusercontent.com`; not the HTML web view of a repository). There is no
account system and no Rastro-operated server. Install the `http` extra first
(`pip install 'rastro-engine[http]'`).

```console
$ rastro recipe search teatro --registry-url https://registry.example.org/catalog/
$ rastro recipe install teatro-central-agenda --registry-url https://registry.example.org/catalog/ --store recipes.sqlite
```

`--registry-url` replaces `--registry`; give one of them, not both. The SDK equivalents are
`rastro.application.registry_fetch.search_registry_url` and `install_from_registry_url`.

The URL is input, not a grant:

- It must be `https`, with no user name or password, no query string, no fragment and a path of plain segments (no `..`, no percent-encoding). Anything else is refused before a request.
- Fetching uses the same acquisition stack as a scrape, with only the registry host allowed: public addresses only, DNS pinned for each request, and every redirect re-checked (a redirect to another host, a plain-`http` URL or a private address is refused). Redirects within the registry host are followed.
- Port 443 only. Another port, a private or loopback address, and `robots.txt` overrides need the operator's flags, never anything in the registry: `--allow-port N`, `--allow-private-network`, `--ignore-robots HOST`. `robots.txt` is enforced like for any fetch; a host that disallows everything needs `--ignore-robots`.
- A package is `index.json` plus five files at fixed names (`bundle.json`, `recipe.json`, `schema.json`, `expected.json` and the sample). The index is capped at 1 MiB, metadata files at 1 MiB and the sample at the job's input limit, the same caps as a local bundle; the whole download is bounded. Ids and versions in `index.json` must match the same patterns as a local registry, so no entry can point outside `recipes/`.

The downloaded bytes go to a temporary directory and through exactly the gates of a local
`install`: file digests, the untrusted-selector policy, offline replay, index match and
compatibility. The result is a `candidate`; install never promotes and never reads or changes a
job file or `[repair] auto_promote`. A digest mismatch, a refused address, an oversize file or a
bad path stops the install with `RegistryError` (or a bundle error) and leaves the recipe store
unchanged. Error messages carry a short category such as `destination_denied` or
`body_too_large`, never the URL query or a response body. A missing `index.json` is an error,
not an empty registry.

### Trust indicators

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
$ rastro recipe install teatro-central-agenda --registry ./examples/registry --store recipes.sqlite
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

## Synthetic examples

The [example registry](https://github.com/rave-vipx/rastro/tree/main/examples/registry)
contains `synthetic-listing`, `synthetic-news` and `synthetic-product`, plus the
original synthetic agenda. All are public format-2 bundles with metadata, hashes,
expected records and offline replay samples; none is a third-party capture. The
[README walkthrough](https://github.com/rave-vipx/rastro#share-a-recipe) installs,
promotes and loads the listing twice. The same steps work for `news.json` and
`product.json` with their matching store names. `price_text` deliberately remains
source text; the example makes no numeric or currency inference.

The shared object is the recipe. Samples are explicit replay evidence, not the
next production target. These examples include samples because the current bundle
format and import verifier require them. A URL job always fetches its URL again;
these example jobs explicitly choose a file source so the tutorial works offline.
Keep source URL, regional context and field definitions aligned with the bundle
when adopting it. Installs cannot modify your job's auto-promote policy or grant stealth.

## Host a static registry

Serve the unchanged registry directory over HTTPS: `index.json` and
`recipes/<id>/<version>/` with the exact exported bytes. No account system or
Rastro-operated service is needed. Publishing remains a local write; commit/push
or upload the directory yourself after reviewing it, then point
`--registry-url` at it (see [Install from a registry URL](#install-from-a-registry-url)).
Studio's registry section accepts the same `https://` address; without the `http`
extra it answers `503 registry_url_unavailable_install_http_extra` and local registry
directories keep working.

The URL path keeps the same verification gates as a local registry: HTTPS only,
public-address checks and redirect re-checks, byte limits, digests, selector
policy and offline replay before candidate storage. A URL, bundle or model cannot
grant private-network access or enable stealth. There is no executable recipe
format and no multi-tenant hosted registry.
