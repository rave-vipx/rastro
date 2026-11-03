# Architecture

Rastro starts as a modular monolith. The implemented flow is:

```text
Python SDK / CLI
       │
       ▼
application.Rastro (facade)
       │
       ▼
application.ExtractionService
       │
       ├── domain: source, recipe variants, outcomes, evidence, limits
       │
       ├── ports.RecipeRepository ◄── adapters.SQLiteRecipeRepository
       │
       ├── ports.DocumentParser ◄── adapters.ScraplingParser
       │
       ├── application.RecipeLearner
       │        ├── ports.RecipeDiscovery ◄── adapters.ScraplingDiscovery
       │        └── ports.ModelPort ◄── adapters.OllamaModel (optional)
       │
       └── ports.AcquisitionPort ◄── application.WebAcquirer
                                          ├── URL/DNS/robots/host policy
                                          ├── ports.Resolver ◄── SystemResolver
                                          └── ports.HttpTransport ◄── CurlTransport
```

The domain imports Pydantic and the standard library. The application validates configuration, enforces budgets, orchestrates parsing and Pydantic validation, accounts for candidates, and emits safe execution events. Scrapling-specific types remain inside the adapter.

The SDK and CLI call the same service. Public imports live in `rastro.__init__`; internal module layout can evolve without forcing users to depend on implementation details.

## Implemented boundaries

- `domain/sources.py`, `domain/recipes.py`, `domain/results.py`, `domain/models.py`: source origins, limits, schema fingerprints and domain errors; the `Recipe` strategy union (`CssRecipe`, `JsonLdRecipe`, `JsonRecipe`); outcomes, diagnostics, field provenance, metrics and extraction results; `domain/models.py` re-exports the domain contract surface.
- `ports/parser.py`: candidate and parsed-document contracts independent of Scrapling.
- `adapters/scrapling.py`, `adapters/structured.py`: deterministic HTML (JSON-LD, CSS) and strict JSON parsing/discovery without fetching.
- `application/engine.py`: the public `Rastro` facade and default adapter wiring.
- `application/service.py`, `application/batch.py`, `application/outcomes.py`: prepared single-source and bounded-batch extraction for supplied and acquired documents, parser selection by representation, learner or memory delegation, metric stamping and failure result construction.
- `application/extraction.py`, `application/field_aliases.py`: deterministic parsing, validation, field evidence and discovery alias resolution.
- `application/memory.py`: context lookup, explicit fallback attempts and success-only persistence.
- `domain/memory.py`, `ports/recipes.py`: identity, stored definitions with separate operational metadata, errors and repository contract.
- `adapters/sqlite.py`: transactional versioned persistence, migrations, retention and deletion.
- `domain/acquisition.py`, `ports/acquisition.py`: policies, pinned destination and transport contracts.
- `application/acquisition.py`, `application/http_policy.py`, `application/destinations.py`, `application/robots.py`: `WebAcquirer` orchestration, HTTP header/redirect/status/encoding/Retry-After/egress rules, destination pinning, robots preflight, retries and budgets.
- `adapters/dns.py`, `adapters/http.py`: asynchronous system DNS and optional pinned curl_cffi transfers.
- `cli.py`: offline demo, package diagnostics, and recipe validation.

- `application/intelligence.py`, `application/proposals.py`: deterministic-first cold-context learning, prompt envelopes, strict proposal decoding, bounded inference retries and activation after validation.
- `domain/intelligence.py`, `ports/model.py`, `ports/discovery.py`: optional inference policies, proposal/request contracts and discovery/model boundaries.
- `adapters/discovery.py`, `adapters/ollama.py`, `adapters/openai_compatible.py`: bounded Scrapling metadata discovery and optional HTTPX model providers.

## Decisions and references

The [offline first-slice ADR](decisions/0001-offline-vertical-slice.md) explains why the first usable milestone stops before network acquisition.

The [HTTP transport ADR](decisions/0002-policy-aware-acquisition.md) records the Scrapling API limitation that requires using its underlying HTTP engine through the transport port.

The [recipe memory ADR](decisions/0003-sqlite-recipe-memory.md) records identity, validation, versioning, privacy and asynchronous storage decisions.

The [optional intelligence ADR](decisions/0004-bounded-recipe-learning.md) records the model boundary, budgets, required-field evidence persistence and optional transport choice.

The [recipe model ADR](decisions/0009-recipe-model.md) records the strategy union, the definition/metadata split and legacy payload loading.

Implementation references:

- [Scrapling selection documentation](https://scrapling.readthedocs.io/en/latest/parsing/selection.html).
- [Pydantic model validation](https://docs.pydantic.dev/latest/concepts/models/).
- [uv's GitHub Actions integration](https://docs.astral.sh/uv/guides/integration/github/).
- [RFC 6901: JSON Pointer](https://www.rfc-editor.org/rfc/rfc6901).

These references guide adapter behavior and build tooling; the master specification remains the project's scope authority.
