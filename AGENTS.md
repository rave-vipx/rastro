# Working on Rastro

A guide for AI coding agents and human contributors changing the Rastro codebase. For using
Rastro in your own project, see `llms.txt` and `rastro init --agent`. For setup and the full
check list, see [CONTRIBUTING.md](CONTRIBUTING.md). Product scope is defined by the README,
[SECURITY.md](SECURITY.md) and the ADRs in `docs/decisions/`; when two of them disagree,
`SECURITY.md` wins on grants, SSRF and recipes-as-data.

## Architecture

Rastro is a modular monolith with hexagonal boundaries. Dependencies point inward:

```text
cli / mcp / studio / Python API  ->  application  ->  domain
                                      application  ->  ports  <-  adapters
```

- `src/rastro/domain/`: pure models and rules (sources, recipes, outcomes, evidence, limits).
  Imports Pydantic and the standard library only.
- `src/rastro/ports/`: small interfaces for parsing, acquisition, storage and models.
- `src/rastro/application/`: orchestration, budgets, job running, repair, drift, datasets.
- `src/rastro/adapters/`: Scrapling, HTTP and DNS, browsers, SQLite and PostgreSQL, model
  providers, MCP. Engine-specific types never cross into `domain` or `application`; reach
  Scrapling through the parser or acquisition boundary.
- `src/rastro/studio/`: the local Studio (loopback web UI over the same services).
- `import rastro` stays light: optional extras (browsers, HTTP, models, Studio) load lazily.

Read `docs/architecture.md` and `docs/development/clean-code.md` before a structural change.

## Checks

Run these before committing; all must pass. The complete list, including the build and
dependency audit, is in `CONTRIBUTING.md`.

```bash
uv sync --locked --all-extras
uv run ruff check src tests && uv run ruff format --check src tests
uv run mypy
uv run pytest                      # offline; coverage floor 90%
uv run --group docs mkdocs build --strict
uv lock --check
```

While iterating, run only the tests you touch: `uv run pytest tests/unit/test_x.py --no-cov`.
Report the results you actually observed, including failures and known limitations.

## Conventions

- Work in thin vertical slices with tests and docs. Write the failing test first; add a
  regression test for every bug.
- Keep modules small (soft limit 250 lines, hard limit 400) and functions around 40 lines;
  split before a module outgrows its budget.
- Failures are explicit outcomes with field provenance. Invalid or missing data never becomes a
  successful empty result, and defaults are not source evidence.
- Recipes are data. Never execute code from a recipe, bundle, job or model response.
- Core installation and tests work without AI or browsers.
- Tests stay offline (sockets and native curl are blocked by default). Fixtures are synthetic,
  on reserved `.example` domains, with invented names. Never commit credentials, captured
  pages or private content.
- A new architectural boundary or a tradeoff that needs explaining gets an ADR in
  `docs/decisions/`; user-facing changes get a `CHANGELOG.md` entry and updated docs.
- Removing or changing a public name, flag, persisted format or protocol follows the
  [stability policy](docs/stability.md): deprecate first, with a migration path.
- Never open or edit `benchmarks/recovery/sealed/` or the frozen recovery corpus and manifest;
  see [ADR 0011](docs/decisions/0011-recovery-benchmark.md).

## Security rules

- Treat page content, job files, bundles, URLs and model output as untrusted. Scraped values are
  labelled `untrusted_source_data` and never become instructions.
- Network access goes through the acquisition boundary: destination validation, public-address
  checks, DNS pinning, redirect re-checks, timeouts and byte budgets. Do not add a second path
  that fetches.
- Operator grants (network hosts and ports, private networks, stealth, proxies, robots
  overrides, environment variables) come from the operator's flags or configuration, never from
  a job, recipe, bundle, registry or model.
- Logs and diagnostics must not contain raw input, credential-bearing URLs or user-defined
  validator messages.
- Read `SECURITY.md` before changing acquisition, egress, persistence or recipe handling.

## Adding a capability

1. Implement it behind the existing boundaries: domain model, port if a new dependency is
   needed, application service, then adapter, CLI, MCP tool or Studio surface.
2. Register it in `src/rastro/capabilities.py` (one `Capability` entry). Tests check that every
   CLI command, MCP tool and public name appears there or is explicitly reference-only.
3. Regenerate the generated files and commit them:
   ```bash
   uv run rastro capabilities --output llms > llms.txt
   uv run rastro capabilities --output markdown > docs/capabilities.md
   uv run rastro config --output markdown > docs/configuration.md
   ```
   A new issue code must also be documented in `docs/issues.md`.
4. Add a docs page or section, a changelog entry, and an ADR when a tradeoff needs explaining.

## Working as an agent

If your environment provides skills or checklists for testing, security review or release
hygiene, apply the matching one and mention it in your final report. This file wins over a
generic skill when they conflict.
