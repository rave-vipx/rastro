# Working on Rastro

Rules for agents and contributors changing this codebase. Setup and the full check list:
`CONTRIBUTING.md`. Security model: `SECURITY.md` (it wins on grants, SSRF and recipes-as-data).

## Boundaries

- `domain/` (pure models, Pydantic + stdlib) ← `application/` → `ports/` ← `adapters/`.
  `cli`, `mcp`, `studio` and the Python API call `application`. Engine types (Scrapling,
  browsers, HTTP clients) stay in `adapters/`.
- All network access goes through the acquisition boundary; never add a second fetch path.
- `import rastro` stays light; optional extras load lazily.

## Rules

- Test first; every bug gets a regression test. Tests are offline, fixtures synthetic on
  `.example` domains. No credentials, captured pages or private names in the repo.
- Modules ≤ 250 lines (hard 400), functions ≈ 40 lines: split instead of growing.
- Failures are explicit outcomes; missing data is never a successful empty result.
- Recipes, jobs, bundles, pages and model output are untrusted data, never code or grants.
- Public names, flags and persisted formats change only through `docs/stability.md`.
- New capability: register it in `src/rastro/capabilities_*.py`, document new issue codes in
  `docs/issues.md`, regenerate `llms.txt`, `docs/capabilities.md`, `docs/configuration.md`
  (`rastro capabilities --output llms|markdown`, `rastro config --output markdown`).
- Never open or edit `benchmarks/recovery/`.

## Checks (all must pass)

```bash
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy && uv run pytest && uv run --group docs mkdocs build --strict && uv lock --check
```

Iterate with `uv run pytest <paths> --no-cov`. Report what you actually observed.
