# ADR 0001: Deliver a usable offline slice before network acquisition

- Status: Accepted
- Date: 2026-10-24
- Release: v0.1, milestone M1
- Requirements: INT-002/003/004, REC-002, QUAL-001–004, SEC-004/005/007/008

## Context

The repository began with a specification and no code. v0.1 includes acquisition, persistence, and local inference, but the delivery protocol requires one verified vertical slice before several subsystems are implemented. A safe network adapter needs independent destination, redirect, DNS, and policy enforcement.

## Decision

Start with synchronous extraction from caller-supplied HTML. Use Scrapling's parser through a small port, explicit declarative CSS or JSON-LD recipes, Pydantic validation, field provenance, and visible partial/failure outcomes. Export `extract_html` with behavior matching its name. Do not expose the illustrative asynchronous `extract`/`local` API until it actually works.

Keep `Source` URL and regional fields as provenance metadata. Build tooling uses the prescribed uv/Hatchling stack. Package the library as the provisional distribution `rastro-engine` with import name `rastro`; confirm registry naming before release.

## Consequences

The first milestone is useful for saved pages, fixtures, and HTML acquired by an existing application. It is independently testable without network, browsers, or models. It proves the validation/evidence path while keeping acquisition policy work explicit.

This is not a completed v0.1 release. Persistent recipes, async acquisition, regional enforcement, optional Ollama generation, and benchmark evidence remain mandatory release work. JSON-LD semantic expansion and schema aliases are unsupported initially. The synchronous API requires a bounded worker pool in async host applications.

## Alternatives considered

Implementing the complete release at once would enlarge the unverified surface. Creating only directories and abstract interfaces would produce no usable functionality. A direct convenience HTTP request would risk shipping a policy bypass before the security boundary is designed.
