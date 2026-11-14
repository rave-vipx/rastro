# Stability policy

From 0.6.0, Rastro follows [Semantic Versioning](https://semver.org/). While the major version
is 0, a minor release (0.7.0) may change the public surface below, but only through the
deprecation policy; a patch release (0.6.1) only fixes bugs and never breaks it.

## What is public

These are covered by the policy. Code, scripts and files that depend only on them keep working
across patch releases, and get at least one minor release of warnings before a change.

- **Python names in `rastro.__all__`**, imported from the top-level package
  (`from rastro import ...` or `rastro.<name>`), including their documented parameters, return
  types and result fields. The optional model providers `rastro.OllamaModel` and
  `rastro.OpenAICompatibleModel` are public too, although they are left out of `__all__` so a
  star import never needs an extra.
- **CLI commands and flags** of the `rastro` executable, and its exit codes (`0` success,
  `1` failed, `2` configuration or usage error, `3` partial success, `4` needs attention,
  `5` storage error after the run).
- **CLI JSON output.** Objects that carry `schema_version` keep their meaning for that version;
  a breaking change bumps `schema_version`. New fields may be added at any time, so parse
  leniently and ignore unknown keys. The error object on stderr keeps the keys `error`,
  `message` and `location`, and documented `error` codes keep their meaning.
- **Published JSON Schemas** in [`docs/schemas/`](https://github.com/rave-vipx/rastro/tree/main/docs/schemas)
  for jobs, run results and previews (also printed by `rastro job schema`).
- **Job files** with `format_version = 1`. A job that validates today keeps validating with the
  same meaning; a breaking change introduces `format_version = 2` and keeps reading version 1
  for at least one minor release.
- **Recipe bundles** with `format_version = 1` (`rastro recipe export` and `import`).
- **SQLite stores** (recipe memory and datasets). Newer releases open files written by older
  releases and migrate them forward in a transaction. Downgrading is not supported: an older
  release refuses a file with a newer schema version instead of guessing.
- **MCP tool names and arguments** served by `rastro mcp`, with the same JSON results as the
  equivalent CLI commands.

## Deprecation policy

1. A public name, flag, field or format is first marked deprecated in a minor release. It keeps
   working and emits a `DeprecationWarning` (Python) or a warning on stderr (CLI), and the
   changelog lists it under "Deprecated" with the replacement.
2. It may be removed no earlier than the next minor release. The removal is listed under
   "Removed" or "Changed" with migration guidance.
3. Security fixes are the only exception: if keeping old behavior would be unsafe, it may change
   in a patch release, and the changelog says so.

## What is not public

These may change in any release without notice:

- `rastro.adapters.*`, `rastro.application.*`, `rastro.domain.*`, `rastro.ports.*` and
  `rastro.mocklab.*` when imported by module path. Use the names re-exported from `rastro`.
- `rastro.cli_*`, `rastro.mcp_tools`, `rastro.mcp_lifecycle` and `rastro.mcp_authoring` modules. Use the `rastro` command or the public API.
- `rastro.studio.*` internals, including its HTTP routes and page assets. Studio's behavior in the
  browser is documented, its internal API is not.
- Log messages and their structure (`--verbose`), the wording of human-readable messages, and
  the text of error `message` fields. Match on `error` codes, not messages.
- Repository files outside the package: benchmarks, examples and test fixtures.
- Exact selectors produced by suggestion or repair, and timing figures. Both may improve between
  releases; results are always verified against your schema.

## Supported Python versions

Rastro supports CPython 3.12, 3.13 and 3.14. Dropping a Python version happens only in a minor
release and is announced in the changelog.
