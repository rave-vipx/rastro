# ADR 0020: minimal Studio, MCP and Hub on the same services

- **Status:** accepted for 0.5.0 (not published).
- **Requirements:** P-008, AGENT-002, AGENT-003, HUB-001 (first step), spec sections 08 and 09.

## Problem and options

The specification describes Studio as FastAPI with a React, TypeScript, Vite and Tailwind
front end, MCP once a concrete consumer exists, and a Hub registry. Building that stack
adds a Node toolchain, a front-end build and a hosted service before any user has asked
for them.

## Decision

- **Studio** is a loopback-only Starlette app behind the optional `studio` extra: one
  static HTML page with vanilla JavaScript, no build step. It covers the proven path
  (pick a job, validate, preview samples, correct one selector, accept, copy connect
  commands) and calls the same application services as the CLI. Mutations need a
  per-run token and a local Host/Origin; writes are atomic, confined to the root and
  guarded by the file's previous hash.
- **MCP** is a stdio server behind the optional `mcp` extra. The concrete consumer is
  Claude Code. Tools wrap job validate, preview and load, dataset reads and recipe
  listing; paths stay inside a workspace root and no new network capability is added.
- **Hub** starts as Git-friendly recipe bundles (`rastro recipe export` / `import`).
  Import verifies hashes, format version and an offline replay against the bundled
  sample before storing the recipe as a candidate. There is no hosted registry.

## Consequences

Core installation stays free of web and agent dependencies. A richer front end, a hosted
registry, recipe signing and MCP write tools (promote, rollback, repair) wait for a user
who needs them. Hashes detect corruption, not a malicious author; imported recipes stay
candidates unless the user activates them.
