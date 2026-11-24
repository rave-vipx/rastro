# Contributing to Rastro

Thanks for helping make web data more reliable. The public surface and its deprecation policy are defined in the [stability policy](docs/stability.md).

## Find a useful change

Read the [open issues](https://github.com/rave-vipx/rastro/issues). For a substantial feature, describe the problem and proposed API in an issue before investing in implementation. Small fixes and documentation improvements can go straight to a pull request.

The README, `SECURITY.md` and the ADRs in `docs/decisions/` define scope. Keep examples and public documentation in English. Use synthetic fixtures that you have permission to distribute.

## Set up

Use Python 3.12+ and uv 0.13.x:

```bash
git clone https://github.com/rave-vipx/rastro.git
cd rastro
uv sync --locked --group docs
uv run rastro demo
```

Create a short-lived branch from `dev` and target `dev` in your pull request.
`dev` is the integration branch: CI runs on every push and PR, and passing checks
are required before merging. Promote reviewed changes to `main` through a separate
PR; documentation deploys only from `main`.

Keep each commit focused, with a descriptive `feat:`, `fix:`, `test:`, or `docs:` message. Commit `uv.lock` whenever dependency resolution changes.

## Verify

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run python examples/offline.py
uv run python examples/adapter.py
uv run python examples/intelligence.py
uv run python examples/event-agenda/read_agenda.py
uv run python -m benchmarks.run --repetitions 2 --warmup 1 --output /tmp/rastro-benchmark.json
uv run --group docs mkdocs build --strict
uv build
uv run twine check dist/*
uv export --locked --all-extras --no-dev --no-emit-project --format requirements-txt --output-file /tmp/rastro-runtime.txt
uv run pip-audit --require-hashes -r /tmp/rastro-runtime.txt
```

The audit requires network access. Tests do not: pytest blocks Python sockets and native curl transfers by default. Event-loop construction briefly allows its internal wake-up socket before any task runs. Unit, integration, parser-contract, property, and boundary tests run together. Keep the branch-aware coverage floor at 90% or higher. Add a regression test for each behavior bug. Prefer observable behavior to implementation assertions.

CI tests Python 3.12, 3.13, and 3.14 on Linux, plus Python 3.12 on macOS and Windows. It separately installs the built wheel in a clean environment and runs the public demo.

For comparison changes, regenerate the full report using the [benchmark methodology](docs/comparison.md). Never gate CI on local latency. The public `examples/catalogue.py` demonstration requires network access and is opt-in; its default regression test uses synthetic HTML.

## Design expectations

- Domain contracts import neither Scrapling nor persistence or model clients.
- Infrastructure returns data through small ports; keep orchestration in the application layer.
- Recipes are declarative. Never execute user-supplied Python from recipe files.
- Ambiguous and invalid records need explicit outcomes. Default values are not source evidence.
- Logs and validation diagnostics must not contain raw input, credential-bearing URLs, or user-defined validator messages.
- New acquisition must include destination validation, redirect/DNS tests, timeouts, and source-policy enforcement before being exposed publicly.
- Layers: `domain/` (Pydantic and the standard library only) ← `application/` → `ports/` ← `adapters/`; `cli`, `mcp`, `studio` and the Python API call `application`. Engine types stay in `adapters/`.
- All network access goes through the acquisition boundary; never add a second fetch path. `import rastro` stays light: optional extras load lazily.
- Write the failing test first; every bug gets a regression test. Tests are offline with synthetic fixtures on `.example` domains.
- Modules stay under 250 lines (hard limit 400) and functions around 40 lines; split instead of growing.
- Public names, flags and persisted formats change only through the [stability policy](docs/stability.md).
- A new capability is registered in `src/rastro/capabilities_*.py`, new issue codes are documented in `docs/issues.md`, and the generated files are refreshed: `rastro capabilities --output llms > llms.txt`, `rastro capabilities --output markdown > docs/capabilities.md`, `rastro config --output markdown > docs/configuration.md`.
- Never open or edit `benchmarks/recovery/` (frozen corpus, ADR 0011).

## Submit a pull request

Explain the user-visible change, link the issue or ADR, and report the exact checks you ran. Include security and performance implications and any limitations. Update API docs and `CHANGELOG.md` in the same change. The PR template provides these prompts.

Maintainers review scope, API clarity, correctness, and evidence. Required CI checks must pass before merging. No reviewer response-time guarantee is offered at this stage.

By contributing, you agree that your contributions are licensed under Apache 2.0. Participation follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## What never goes in the repository

Rastro is a public repository. Keep these out of commits, pull requests and issues; the
maintainers keep them locally under `local/` (ignored by Git):

- Planning documents: roadmaps, requirement lists, action plans, design briefs, mockups and their sample data (tests use synthetic fixtures under `tests/`).
- Process records: agent work logs, review transcripts, release evidence registers, PR body
  drafts (`.pr-*-body.md`) and local run outputs.
- Anything about private projects that use Rastro: names, sites, customers, findings. Describe
  a problem in general terms with a synthetic reproduction instead.
- Credentials, tokens, `.env` files, cookies, captured pages and personal data.

Published documentation explains how to use and operate Rastro (guides, reference, ADRs,
benchmarks with reproducible commands). If a change needs planning context, summarize the
decision in an ADR; do not link to local files.
