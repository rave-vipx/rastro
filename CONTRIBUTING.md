# Contributing to Rastro

Thanks for helping make web data more reliable. Rastro is at the first milestone of v0.1; the public API may change before a stable release.

## Find a useful change

Read the [roadmap](docs/roadmap.md) and [open issues](https://github.com/rave-vipx/rastro/issues). For a substantial feature, describe the problem and proposed API in an issue before investing in implementation. Small fixes and documentation improvements can go straight to a pull request.

The [master specification](RASTRO_MASTER_SPEC.md) defines scope. Keep examples and public documentation in English. Use synthetic fixtures that you have permission to distribute.

## Set up

Use Python 3.12+ and uv 0.13.x:

```bash
git clone https://github.com/rave-vipx/rastro.git
cd rastro
uv sync --locked --group docs
uv run rastro demo
```

Create a short-lived branch. Keep each commit focused, with a descriptive `feat:`, `fix:`, `test:`, or `docs:` message. Commit `uv.lock` whenever dependency resolution changes.

## Verify

```bash
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv run python examples/offline.py
uv run --group docs mkdocs build --strict
uv build
uv run twine check dist/*
uv export --locked --no-dev --no-emit-project --format requirements-txt --output-file /tmp/rastro-runtime.txt
uv run pip-audit --require-hashes -r /tmp/rastro-runtime.txt
```

The audit requires network access. Tests do not: pytest blocks sockets by default. Unit, integration, parser-contract, property, and boundary tests run together. Keep the branch-aware coverage floor at 90% or higher. Add a regression test for each behavior bug. Prefer observable behavior to implementation assertions.

CI tests Python 3.12, 3.13, and 3.14 on Linux, plus Python 3.12 on macOS and Windows. It separately installs the built wheel in a clean environment and runs the public demo.

## Design expectations

- Domain contracts import neither Scrapling nor persistence or model clients.
- Infrastructure returns data through small ports; keep orchestration in the application layer.
- Recipes are declarative. Never execute user-supplied Python from recipe files.
- Ambiguous and invalid records need explicit outcomes. Default values are not source evidence.
- Logs and validation diagnostics must not contain raw input, credential-bearing URLs, or user-defined validator messages.
- New acquisition must include destination validation, redirect/DNS tests, timeouts, and source-policy enforcement before being exposed publicly.

## Submit a pull request

Explain the user-visible change, reference requirement IDs, and report the exact checks you ran. Include security and performance implications and any limitations. Update API docs and `CHANGELOG.md` in the same change. The PR template provides these prompts.

Maintainers review scope, API clarity, correctness, and evidence. Required CI checks must pass before merging. No reviewer response-time guarantee is offered at this stage.

By contributing, you agree that your contributions are licensed under Apache 2.0. Participation follows the [Code of Conduct](CODE_OF_CONDUCT.md).
