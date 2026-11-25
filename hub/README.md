# Rastro recipe hub

A static registry of shared recipes. Rastro reads it by default
(`https://raw.githubusercontent.com/rave-vipx/rastro/main/hub/`): `rastro hub search URL`
offers the recipes whose source matches the URL's domain, and `rastro hub install` stores one
as a candidate that only serves after it passes your own gates. There is no server, no account
and no telemetry: the hub is this folder, served raw by GitHub.

Recipes are data. A package holds a recipe, a description of every field, and a synthetic
sample the recipe is replayed against offline. Nothing here runs code.

## Layout

```text
hub/
  index.json                         one entry per package (generated)
  health.json                        last offline verification per package (generated)
  recipes/<id>/<version>/
    bundle.json recipe.json schema.json expected.json sample.html|json card.json
```

`index.json` and `health.json` are produced by `hub/build.py`; do not edit them by hand.

## Publish a recipe

1. Make the recipe work in your project, then prepare a synthetic sample page or JSON that
   shows the same structure with invented content (`.example` hosts).
2. From a checkout of this repository:

   ```console
   rastro hub publish job.toml --source 1 --sample synthetic-page.html --registry <checkout>/hub \
       --id my-recipe --version 1.0.0 --author "Your Name" --license CC0-1.0 \
       --describe title="The card's headline"
   ```

3. Run `uv run python hub/build.py` to refresh `index.json` and `health.json`.
4. Open a pull request that only touches `hub/`. Hub-only pull requests go straight to `main`.
   CI replays every package offline and rejects stale summaries, executable files, links and
   anything outside the layout above.

Published versions are immutable: to change a recipe, publish a new version.

## Rules

- Recipes are data: no scripts, no executables, no links.
- A synthetic sample is required. Never commit a captured page, credentials or personal data;
  publishing scans for secret and personal-data patterns.
- Every top-level field has a one-line description in `card.json`.
- You must be free to release the package under CC0 1.0 (see `LICENSE`).
