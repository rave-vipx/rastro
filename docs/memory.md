# Recipe memory

M3 adds optional, local SQLite memory. A successful extraction can be repeated
without supplying its recipe, including after restarting Python. Extracted records
are validated again on every run; memory stores extraction instructions, not data.

## Try a restart

From the checkout, run this twice:

```bash
uv run python examples/memory.py --database rastro-recipes.db
uv run python examples/memory.py --database rastro-recipes.db
```

Both commands process synthetic HTML offline. The first prints
`"recipe_reused": false`; the second prints `true`. Both return the same records.
SQLite is in Python's standard library; this requires no additional dependencies.

## Remember and reuse

```python
from pydantic import BaseModel
from rastro import Rastro, Recipe, RecipeContext, Source, SQLiteRecipeRepository


class Product(BaseModel):
    name: str


source = Source(url="https://example.com/products", locale="en-US")
context = RecipeContext(template="product-v1", region="us", schema_revision="1")
html = '<script type="application/ld+json">{"name":"Notebook"}</script>'

repository = SQLiteRecipeRepository("rastro-recipes.db")
engine = Rastro(repository=repository)
first = engine.extract_html(
    html,
    source=source,
    schema=Product,
    context=context,
    recipe=Recipe(strategy="jsonld", fields={"name": "/name"}),
)
second = engine.extract_html(html, source=source, schema=Product, context=context)
assert second.recipe_reused
```

Only `success` with validated records is remembered. Missing, invalid, ambiguous,
partial, restricted or failed results do not activate a recipe or update its last
success. A failed new version leaves previously successful versions available.
By default there is no inference or automatic discovery when memory is empty:
`RecipeNotFoundError` reports an exact-context miss. M4 adds explicit opt-in
[asynchronous recipe learning](intelligence.md) through `discover_recipes=True` or
`model=`. Learned recipes carry `require_evidence=True`; required-field observations
are enforced on every reuse, including after restart.

`Rastro.web(policy, repository=repository)` supports the same `context` and optional
`recipe` arguments through `await extract(...)` and `await extract_many(...)`.
Acquisition still fetches the source and enforces all M2 policies on every call.
Lookup happens before fetching; without discovery enabled, a missing recipe fails
without a network request. Missing pinned versions always fail before acquisition.

## Identity and validation

Reuse requires every component of `RecipeKey` to match:

| Component | Meaning |
| --- | --- |
| Source fingerprint | SHA-256 of the normalized, requested URL, including query and excluding fragment. Distinct paths and queries remain isolated. The raw URL is not stored. |
| Schema fingerprint | SHA-256 of canonical Pydantic JSON Schema. The JSON Schema document itself is not stored. |
| Locale, languages, timezone and currency | Exact values from `Source`; language order is significant. No implicit regional or currency equivalence. |
| Network egress | Exact `Source.egress` profile name (`direct` or an administrator-declared proxy profile). Only the name is stored, never the proxy URL or credentials. Browser geolocation is a separate, currently unavailable dimension. |
| Template | Caller-managed template revision; default `page`. Changing a site's template should change this identifier. No DOM fingerprinting is claimed. |
| Region | Caller-managed deployment/data-context tag; default `default`. It does not select network egress or bypass access restrictions. |
| Schema revision | Caller-managed validator revision; default `1`. Change it when custom Python validation behavior changes. |

Context identifiers accept 1–128 ASCII letters, digits, `.`, `_`, `:`, and `-`,
starting with a letter or digit. They must contain no credentials. A template tag
does not allow reuse across different URLs. Redirects retain the requested URL's
memory identity; evidence still identifies the final document URL.

A JSON Schema fingerprint does **not** identify arbitrary Python validator code.
Changing `schema_revision` isolates otherwise identical schemas. Always supply the
actual Pydantic class at extraction time: its field constraints, custom validators
and evidence checks run again. Memory retains that validation-contract reference,
not executable validators or JSON Schema defaults/examples that might hold secrets.

Keys with empty languages, no currency, and direct egress retain the pre-regional
canonical JSON exactly, so existing SQLite rows remain reusable. Non-default
regional context never falls back to that legacy key. This changes no table schema
and stores no proxy configuration, source URL, extracted value, or validator input.

## Versions and explicit fallbacks

The default lookup chooses the highest stored version for the exact key. Use
`recipe_version=1` with `recipe` omitted to pin a stored version. Versions are
integers from 1 to 2,147,483,647. The definition at a key/version is immutable:
changing mappings, selectors or fallbacks requires incrementing `Recipe.version`.
A collision raises `RecipeConflictError`, including under concurrent writes.

`Recipe.fallbacks` is a tuple of up to three declarative `Recipe` alternatives.
They must use the parent's version and cannot have their own fallbacks. When
explicitly supplied, Rastro tries the primary and then the alternatives in order,
stopping at the first result containing records. Partial results remain visible;
they do not trigger another strategy. Configuration/resource-limit errors also
stop execution. Limits apply to each attempt, with at most four attempts.

Memory preserves the whole definition and the index of the alternative that
actually succeeded (`0` is the primary). Reuse executes **only that validated
alternative**. An unexecuted fallback is never silently promoted. If reuse fails,
the failure is returned; retrying other candidates requires explicitly supplying
the definition again. This is not automatic repair or the v0.2 lifecycle system.

Results include `recipe_version`, `recipe_reused` and ordered `recipe_attempts`
with each attempted index and outcome. Records/issues come from the final attempt;
candidate counts and extraction/validation timings sum all attempts. Storage time
is exposed separately as `metrics.storage_seconds`. HTTP failures before parsing
have no recipe attempts. Completion logs include safe recipe metadata, never its
selectors or the database path.

## Inspect, delete and retain

```python
from datetime import UTC, datetime, timedelta
from rastro import RecipeKey

key = RecipeKey.for_source(source, Product, context)
stored = repository.get(key)  # StoredRecipe or None; optional version=...
# stored.recipe, stored.validated_recipe, stored.created_at,
# stored.last_success_at, stored.successful_runs

repository.delete(key, version=1)  # Delete exactly one version; returns row count.
repository.delete(key)  # Delete all versions for this exact context.
repository.prune(before=datetime.now(UTC) - timedelta(days=30))
```

Here `repository` is your `SQLiteRecipeRepository` instance. `prune` deletes every
version whose last successful execution is strictly older than the aware timestamp.
Cleanup is caller-triggered; there is no scheduler or implicit retention period.
Deleting a version can expose the next-highest remaining version to default lookup;
pin a version when that behavior is unsuitable. Counters measure successful
validations, not a health score or a durable job/failure history.

Deletion uses SQLite `secure_delete=ON` with no application-created backups.
This does not erase external backups, filesystem snapshots or other copies. Stop
writers before bulk deletion if concurrent extractions must not recreate entries.
Use a trusted file in a private directory with appropriate OS access controls.
Hashes prevent retaining literal URLs but are not an anonymity guarantee. Recipes
and context labels may themselves reveal private business structure; never embed
credentials in selectors, mappings or identifiers.

## Storage and asynchronous guarantees

The adapter initializes lazily and closes a connection after every operation.
Migration 0 → 1 creates the schema transactionally and records SQLite application
and schema versions. Unknown versions, foreign databases and corrupt rows fail
explicitly; they are never silently reset. Writes serialize with a bounded lock
timeout (default 5 seconds, configurable above 0 and at most 30 seconds).
Use a local filesystem; this is not a shared network-filesystem database service.

`RecipeStorageError` reports database failures without SQL, payloads or paths.
It is distinct from `RecipeNotFoundError`. A failed write raises even if extraction
itself succeeded, so callers cannot mistake failed persistence for success.

Async SDK methods offload all repository I/O, including initialization, with
`asyncio.to_thread`. Cancellation does not stop a thread already running: a begun
write may finish and commit after the coroutine is cancelled. Operations remain
atomic and close their connections. Direct repository calls are synchronous; use
`await asyncio.to_thread(repository.prune, before=cutoff)` in async applications.
The repository port is trusted infrastructure: direct `save_success` callers must
perform validation themselves. Prefer the SDK for validation before remembering.

See [ADR 0003](decisions/0003-sqlite-recipe-memory.md) for design tradeoffs and scope.
