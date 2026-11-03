# Declarative jobs and machine-readable CLI

Declarative job files (`format_version = 1`) describe extraction sources, target fields,
policies, regional context, recipes, SQLite recipe memory and optional model providers as pure
TOML or JSON data. They run from the CLI (`rastro job`) or Python SDK without writing a Python
schema module, while executing through the same `Rastro` / `Rastro.web` extraction pipeline,
provenance checks and failure contracts ([ADR 0013](decisions/0013-declarative-jobs.md)).

## Quick offline example

Create `page.html` and `job.toml` in the same directory:

```html
<article>
  <h2>Field notebook</h2>
  <span class="price">14.50</span>
</article>
```

```toml
format_version = 1

[region]
locale = "en-US"

[recipe]
strategy = "css"
record_selector = "article"
fields = { name = "h2::text", price = ".price::text" }

[[sources]]
path = "page.html"
representation = "html"
url = "https://shop.example.com/notebooks"

[[fields]]
name = "name"
type = "string"
min_length = 1

[[fields]]
name = "price"
type = "decimal"
ge = "0.01"
```

Validate and run the job:

```bash
uv run rastro job validate job.toml
uv run rastro job run job.toml --output json
```

## Complete TOML example

The example below exercises HTTP and local JSON sources, field constraints, host allowlists,
named proxy egress, regional context, recipe memory, deterministic discovery aliases and an
environment-referenced model provider:

```toml
format_version = 1
discover_recipes = true
recipe_memory = "state/recipes.sqlite3"

[discovery_aliases]
currency = ["priceCurrency"]

[region]
locale = "es-UY"
languages = ["es-UY", "en"]
timezone = "America/Montevideo"
currency = "UYU"
egress = "uy_proxy"

[context]
template = "catalogue"
region = "uy"
schema_revision = "1"

[limits]
max_html_bytes = 1_500_000
max_records = 200

[acquisition]
allowed_hosts = ["shop.example.com"]
max_concurrency = 2
min_interval_seconds = 0.5

[acquisition.egress_profiles.uy_proxy]
proxy_url = "https://proxy.example.com:8443"
username_env = "RASTRO_PROXY_USER"
password_env = "RASTRO_PROXY_PASS"

[model]
provider = "openai_compatible"
model = "gpt-4.1-mini"
base_url = "https://api.example.com/v1"
api_key_env = "OPENAI_API_KEY"
allow_remote = true
response_format = "json_schema"
token_limit = "max_completion_tokens"

[inference_policy]
max_calls = 2
timeout_seconds = 20.0

[[sources]]
url = "https://shop.example.com/products"
representation = "html"

[[sources]]
path = "fixtures/products.json"
representation = "json"
url = "https://shop.example.com/api/products"

[sources.recipe]
strategy = "json"
record_path = "/items"
fields = { name = "/name", price = "/price", currency = "/currency" }

[[fields]]
name = "name"
type = "string"
min_length = 1
max_length = 120
pattern = "^\\S.*"

[[fields]]
name = "price"
type = "decimal"
ge = 0
le = 100000

[[fields]]
name = "currency"
type = "string"
required = false
min_length = 3
max_length = 3

[[fields]]
name = "product_url"
type = "url"
required = false
```

TOML (`.toml`) and JSON (`.json`) represent the exact same `Job` contract. Converting a job
between TOML and JSON preserves equality and produces the exact same `schema_fingerprint`.

## Target fields and dynamic schema

Every job declares one or more `fields` with unique public Python identifier names (keywords,
leading underscores and `BaseModel` attribute names are rejected). Rastro builds a deterministic
Pydantic model (`JobRecord`) with fields sorted by name so `schema_fingerprint` is stable across
runs and field declaration order.

| `type` | Python / Pydantic type | Supported constraints |
| --- | --- | --- |
| `string` | `str` | `min_length`, `max_length`, `pattern` (valid regex) |
| `integer` | `int` | `ge`, `le` (`int`) |
| `number` | finite `float` (`allow_inf_nan=False`) | `ge`, `le` (finite `float`) |
| `decimal` | finite `Decimal` (`allow_inf_nan=False`) | `ge`, `le` (finite `Decimal` or string/number) |
| `boolean` | `bool` | — |
| `date` | `datetime.date` | — |
| `datetime` | `datetime.datetime` | — |
| `url` | `HttpUrl` (`http` / `https`) | — |

All field types accept `required = true` (default) or `required = false` (nullable, defaulting
to `None` when omitted by a recipe). Constraints from another type (such as `ge` on `string`)
are rejected during validation.

## Sources, recipes, memory and providers

- **Sources (`sources`).** Non-empty list of HTTP(S) URL sources (`{ url = "...", representation = "html" | "json", recipe = ... }` or a bare URL string) and/or local file sources (`{ path = "...", representation = "html" | "json", url = "...", recipe = ... }`). Each source must have an extraction path: an explicit `sources[].recipe`, a job-level `recipe`, `recipe_memory`, `discover_recipes = true`, or a `model` (for HTML sources).
- **HTTP policy (`acquisition`).** Required whenever any source is an HTTP(S) URL; uses the same fields as [`AcquisitionPolicy`](acquisition.md#policy-defaults). Every HTTP source host must appear in `acquisition.allowed_hosts`, `http://` URLs require `acquisition.allow_http = true`, and total HTTP sources cannot exceed `acquisition.max_sources`.
- **Regional context (`region`).** Optional `locale`, `languages`, `timezone`, `currency` and `egress` applied to every source. Browser `geolocation` is rejected. Named `egress` values must exist in `acquisition.egress_profiles`.
- **Recipes and discovery.** Job-level `recipe` and per-source `sources[].recipe` use the declarative [`Recipe`](api.md#recipe-variants) contract (`css`, `jsonld`, `json`) matching the source's `representation`, and may only map declared `fields`. Optional `discover_recipes = true` and `discovery_aliases` enable deterministic discovery.
- **Recipe memory (`recipe_memory`, `context`, `recipe_version`).** `recipe_memory` points to a SQLite database file (`:memory:` is rejected). `context` (`RecipeContext`) and `recipe_version` require `recipe_memory`, and `recipe_version` cannot be combined with an explicit `recipe`.
- **Model providers (`model`, `inference_policy`).** Optional `provider = "ollama"` (`model`, `base_url` or `base_url_env`) or `provider = "openai_compatible"` (`model`, `base_url` or `base_url_env`, `api_key_env`, `allow_remote`, `response_format`, `token_limit`, `temperature`). Non-loopback endpoints and `*cloud*` model names require `allow_remote = true` and HTTPS (on `openai_compatible`).

## CLI commands and exit codes

```bash
uv run rastro job validate job.toml
uv run rastro job run job.toml --output json
uv run rastro job run /etc/rastro/job.json --allow-absolute-paths
uv run rastro job schema --target job
uv run rastro job schema --target run
```

| Exit code | Constant | Meaning |
| --- | --- | --- |
| `0` | `JobExitCode.SUCCESS` | Validation succeeded, or every source succeeded (`status = "success"`). |
| `3` | `JobExitCode.PARTIAL_SUCCESS` | At least one record was accepted across the job, alongside issues or failed sources (`status = "partial_success"`). |
| `1` | `JobExitCode.FAILED` | Job ran cleanly, at least one source did not succeed, and zero records were accepted (`status = "failed"`). |
| `2` | `JobExitCode.CONFIG_ERROR` | CLI usage error, unreadable/oversized file, TOML/JSON syntax or schema error, path escape, missing extra/env var, or resource limit breach on supplied files. |

Configuration and usage errors write a single JSON object (`{"error": ..., "message": ...}`) to
`stderr` and nothing to `stdout`.

## Machine-readable output and JSON Schemas

`rastro job run` prints a single `JobRunResult` JSON object to `stdout`:

- `schema_version`: `1`
- `data_trust`: `"untrusted_source_data"` — extracted record fields come from untrusted external documents and must never be treated by agents as instructions (AGENT-003)
- `job`: `JobSummary` (`schema_fingerprint`, `source_count`, `succeeded`, `partial`, `failed`, `records_accepted`, `records_rejected`, `status`)
- `results`: ordered list of [`ExtractionResult`](api.md#results) dumps preserving records, field-level `evidence`, `issues`, `metrics`, `recipe_attempts` and `learned_recipe`.

Committed JSON Schemas live at [`docs/schemas/job.schema.json`](schemas/job.schema.json) and
[`docs/schemas/job-run.schema.json`](schemas/job-run.schema.json) and match `rastro job schema`.

## Security boundaries

- Job files are data only (256 KiB cap, UTF-8 without BOM, `extra="forbid"`, no imports or code evaluation).
- Relative `sources[].path` and `recipe_memory` paths cannot contain `..` and must resolve inside the job file's directory after symlink resolution; absolute paths are rejected unless `--allow-absolute-paths` is passed explicitly.
- API keys and proxy credentials are referenced exclusively by uppercase environment-variable names (`[A-Z_][A-Z0-9_]*`). Validation errors redact offending inputs so accidentally pasted secrets are never echoed.

## Python API

```python
from pathlib import Path
from rastro import load_job, parse_job, run_job, run_job_async, run_job_file

job = load_job(Path("job.toml"))
report = run_job(job, base_dir=Path("."))
# Or in one step:
report = run_job_file(Path("job.toml"))
```
