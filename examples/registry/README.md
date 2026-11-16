# Synthetic recipe registry (N6)

This is a usable Git-directory registry, distributed under Apache-2.0. Every page
and expected record is synthetic; the `.example` hosts are placeholders, not live
scrape targets. The original `uy-sodre-agenda` also uses synthetic HTML; its real
source URL is metadata, not evidence of coverage of that website.

| Package | Fields | Offline consumer |
| --- | --- | --- |
| `synthetic-listing@1.0.0` | title, location | `listing.json` |
| `synthetic-news@1.0.0` | title, section | `news.json` |
| `synthetic-product@1.0.0` | title, price_text | `product.json` |

Each package under `recipes/<id>/<version>/` has a format-2 public manifest with
version, author, license, source patterns, minimum engine version and verification
date. `sample.html`, `expected.json`, field schema and the declarative recipe pass
normal offline export/publish gates. The sample is replay evidence; production
URL jobs fetch their URL again. Current imports require a replay sample, although
saving HTML is not part of the normal URL scraping workflow.

From the checkout, copy this directory so the walkthrough keeps local state out
of the source tree:

```sh
scratch=$(mktemp -d)
cp -R examples/registry "$scratch/registry"
rastro recipe search synthetic --registry "$scratch/registry"
rastro recipe install synthetic-listing@1.0.0 --registry "$scratch/registry" \
  --store "$scratch/registry/listing-recipes.sqlite"
rastro recipe promote "$scratch/registry/listing.json" --source 1 --version 1 \
  --reason "Reviewed synthetic listing replay"
rastro job load "$scratch/registry/listing.json"
rastro job load "$scratch/registry/listing.json"
rastro dataset export "$scratch/registry/listing-data.sqlite" --format jsonl
```

The first load creates two records; the second reports two unchanged records.
Install stays a candidate and repeat installs reuse the stored recipe. Promote
checks the actual job source and requires evidence. Use the version reported by
install if your recipe store already contains versions. For news/product, replace
`listing` in the package, job, memory and dataset paths with `news`/`product`.

To rebuild in a **new** directory, using `export_bundle` and `publish_to_registry`:

```sh
uv run python examples/registry/build.py /tmp/new-rastro-registry
```

The date is fixed at 2026-11-16 for reproducibility (the original agenda retains
its prior verification date); it is not a claim of verification against live sites.
The sorted canonical index and bundle bytes are checked in the integration test.
Do not edit hashed bundle files by hand; regenerate a new version for publication.
`.gitattributes` preserves bytes on Windows.

To share your own serving recipe, explicitly choose a sample you may redistribute:

```sh
rastro recipe export job.toml --source 1 --sample sample.html --to ./my-bundle \
  --id my-listing --version 1.0.0 --author "Example Publisher" \
  --license Apache-2.0 --public
rastro recipe publish ./my-bundle --registry ./my-registry
```

The job needs recipe memory with a serving recipe. Publish verifies replay and
scans for secrets before writing; it prints Git commands and never runs them.
See [Hub](../../docs/hub.md) for limits and the **pending** `--registry-url` path.
No accounts, executable shared recipes or operator grants travel in a package.
