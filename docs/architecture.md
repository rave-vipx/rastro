# Architecture

Rastro starts as a modular monolith. The first vertical slice is:

```text
Python SDK / CLI
       │
       ▼
application.Rastro
       │
       ├── domain: source, recipe, outcomes, evidence, limits
       │
       └── ports.DocumentParser ◄── adapters.ScraplingParser
```

The domain imports Pydantic and the standard library. The application validates configuration, enforces budgets, orchestrates parsing and Pydantic validation, accounts for candidates, and emits safe execution events. Scrapling-specific types remain inside the adapter.

The SDK and CLI call the same service. Public imports live in `rastro.__init__`; internal module layout can evolve without forcing users to depend on implementation details.

## Implemented boundaries

- `domain/models.py`: explicit, serializable contracts and configuration/resource errors.
- `ports/parser.py`: candidate and parsed-document contracts independent of Scrapling.
- `adapters/scrapling.py`: deterministic JSON-LD discovery and CSS selection without fetching.
- `application/engine.py`: validation, evidence, status classification, and metrics.
- `cli.py`: offline demo, package diagnostics, and recipe validation.

There are no empty modules for later releases. Add acquisition, SQLite, and inference ports when their milestones implement a real caller and adapter.

## Decisions and references

The [offline first-slice ADR](decisions/0001-offline-vertical-slice.md) explains why the first usable milestone stops before network acquisition.

Implementation references:

- [Scrapling selection documentation](https://scrapling.readthedocs.io/en/latest/parsing/selection.html).
- [Pydantic model validation](https://docs.pydantic.dev/latest/concepts/models/).
- [uv's GitHub Actions integration](https://docs.astral.sh/uv/guides/integration/github/).
- [RFC 6901: JSON Pointer](https://www.rfc-editor.org/rfc/rfc6901).

These references guide adapter behavior and build tooling; the master specification remains the project's scope authority.
