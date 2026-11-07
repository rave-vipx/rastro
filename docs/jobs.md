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
| `string` | `str` | `min_length`, `max_length`, `pattern` (regex supported by Pydantic's Rust engine: no look-around or backreferences) |
| `integer` | `int` | `ge`, `le` (`int`) |
| `number` | finite `float` (`allow_inf_nan=False`) | `ge`, `le` (finite `float`) |
| `decimal` | finite `Decimal` (`allow_inf_nan=False`) | `ge`, `le` (finite `Decimal` or string/number) |
| `boolean` | `bool` | — |
| `date` | `datetime.date` | — |
| `datetime` | `datetime.datetime` | — |
| `url` | `AnyHttpUrl` (`http` / `https`, any host, no TLD required) | — |

All field types accept `required = true` (default) or `required = false` (nullable, defaulting
to `None` when omitted by a recipe). Constraints from another type (such as `ge` on `string`)
are rejected during validation.

### Strict job definitions, lax extracted values

The job file itself is parsed strictly. Booleans must be TOML/JSON booleans, integers must be
integers, and `number` bounds must be numbers: `allow_http = "yes"`, `max_redirects = "3"`,
`max_redirects = 3.0`, `required = "true"` and `ge = "1"` on a `number` field are all rejected.
This applies to every job section, including `acquisition`, `limits`, `inference_policy` and
`model`. `decimal` bounds additionally accept decimal strings such as `ge = "0.01"` to keep
exact values.

Extracted values are different. Scraped text always arrives as strings, so the generated
`JobRecord` model validates records in Pydantic's default (lax) mode:

| `type` | Accepted extracted input (examples) | Rejected (examples) |
| --- | --- | --- |
| `integer` | `12`, `"12"`, `12.0` | `"12.5"`, `"twelve"`, `12.5` |
| `number` / `decimal` | `14.5`, `"14.50"`, `3` | `"14,50"`, `"NaN"`, `"inf"` |
| `boolean` | `true`, `"true"`, `"yes"`, `"on"`, `"1"`, `1` (and their false counterparts) | `"maybe"`, `2` |
| `date` | `"2026-11-03"` | `"03/11/2026"` |
| `datetime` | `"2026-11-03T10:00:00Z"` | `"yesterday"` |
| `url` | `"https://example.com/a"` | `"/relative"`, `"ftp://x"` |
| `string` | any string that meets the length and pattern constraints | numbers that are not strings |

Values that cannot be coerced become `validation_error` issues on the record, never silent
defaults. Normalize locale-specific formats (decimal commas, local date orders) in the recipe or
with the Python SDK.

## Sources, recipes, memory and providers

- **Sources (`sources`).** Non-empty list of HTTP(S) URL sources (`{ url = "...", representation = "html" | "json", recipe = ... }` or a bare URL string) and/or local file sources (`{ path = "...", representation = "html" | "json", url = "...", recipe = ... }`). Each source must have an extraction path: an explicit `sources[].recipe`, a job-level `recipe`, `recipe_memory`, `discover_recipes = true`, or a `model` (for HTML sources).
- **HTTP policy (`acquisition`).** Required whenever any source is an HTTP(S) URL; uses the same fields as [`AcquisitionPolicy`](acquisition.md#policy-defaults). Every HTTP source host must appear in `acquisition.allowed_hosts`, `http://` URLs require `acquisition.allow_http = true`, and total HTTP sources cannot exceed `acquisition.max_sources`.
- **Regional context (`region`).** Optional `locale`, `languages`, `timezone`, `currency` and `egress` applied to every source. Browser `geolocation` is rejected. Named `egress` values must exist in `acquisition.egress_profiles`.
- **Recipes and discovery.** Job-level `recipe` and per-source `sources[].recipe` use the declarative [`Recipe`](api.md#recipe-variants) contract (`css`, `jsonld`, `json`) matching the source's `representation`, and may only map declared `fields`. Optional `discover_recipes = true` and `discovery_aliases` enable deterministic discovery.
- **Recipe memory (`recipe_memory`, `context`, `recipe_version`).** `recipe_memory` points to a SQLite database file (`:memory:` is rejected). `context` (`RecipeContext`) and `recipe_version` require `recipe_memory`, and `recipe_version` cannot be combined with an explicit `recipe`.
- **Model providers (`model`, `inference_policy`).** Optional `provider = "ollama"` (`model`, `base_url` or `base_url_env`) or `provider = "openai_compatible"` (`model`, `base_url` or `base_url_env`, `api_key_env`, `allow_remote`, `response_format`, `token_limit`, `temperature`). Non-loopback endpoints and `*cloud*` model names require `allow_remote = true` and HTTPS (on `openai_compatible`). Because an endpoint read from `base_url_env` is only known at run time, `openai_compatible` with `base_url_env` also requires `allow_remote = true`. Ollama endpoints, including `base_url_env` values, must be loopback, which the adapter checks at run time.

## Dataset block and preview

An optional `[dataset]` block declares where approved records would be stored and how a record
is identified (DATA-002). It is additive: `format_version` stays `1`, and a job without the block
validates, runs and prints exactly as before.

```toml
[dataset]
name = "uy_dance_agenda"                    # [a-z][a-z0-9_]{0,63}
path = "agenda.sqlite"                      # relative to the job file; same rules as other job paths
identity = ["title", "venue", "starts_at"]  # declared field names, 1 to 8
approve = ["title", "venue", "city", "starts_at", "ticket_url", "price"]  # optional
match_tolerance_minutes = 90                # optional
```

- `identity` and `approve` must name declared `fields`; `identity` must be a subset of `approve`.
  `approve` defaults to every declared field. Unknown keys are rejected.
- `path` is never `:memory:`, `~`, a `file:` URI or a parent traversal, and absolute paths need
  `--allow-absolute-paths`. Containment (symlinks included) is checked by every `rastro job`
  command and `load_job`. The dataset must be a different file from `recipe_memory`, also through
  symlinks and hard links.
- The block only declares the dataset. `rastro job validate` and `rastro job run` ignore it;
  loading records into the file is not part of this command.

`rastro job preview job.toml [--output table|json]` runs the same pipeline as `job run` (same
acquisition policy, live or file sources) and shows, per source and per record, each field's
value, method and locator provenance, validation outcome and `loaded` flag (`true` when the
record was accepted, the field is in `approve` and the source actually supplied it; an optional
field the recipe did not observe shows `validation = "not_observed"` and is never loaded). Rejected records appear with their reason
codes; the pipeline never keeps rejected values, so none are shown. It writes nothing: no dataset
file and no recipe memory, so a job that sets `recipe_memory` is refused (exit code `2`) until a
read-only recipe lookup exists. It requires a `[dataset]` block (exit code `2` otherwise) and shares the exit codes below.
Table cells escape control characters. JSON output carries `data_trust = "untrusted_source_data"`
and follows [`docs/schemas/job-preview.schema.json`](schemas/job-preview.schema.json)
(`rastro job schema --target preview`).

## CLI commands and exit codes

```bash
uv run rastro job validate job.toml
uv run rastro job run job.toml --output json
uv run rastro job run /etc/rastro/job.json --allow-absolute-paths
uv run rastro job preview job.toml --output table
uv run rastro job schema --target job
uv run rastro job schema --target run
uv run rastro job schema --target preview
```

| Exit code | Constant | Meaning |
| --- | --- | --- |
| `0` | `JobExitCode.SUCCESS` | `validate` or `schema` succeeded, or every source of `run` or `preview` succeeded (`status = "success"`). |
| `1` | `JobExitCode.FAILED` | The job ran, no source succeeded or partially succeeded, and zero records were accepted (`status = "failed"`). |
| `2` | `JobExitCode.CONFIG_ERROR` | The job did not run: usage error, missing or unreadable file, TOML/JSON syntax error, schema validation error, path escape, missing extra or environment variable, or resource limit on supplied files. |
| `3` | `JobExitCode.PARTIAL_SUCCESS` | At least one source succeeded or partially succeeded, but not all of them succeeded (`status = "partial_success"`). |

### Error objects

Every exit code `2` from `rastro job` writes exactly one JSON object on one line to `stderr`
and nothing to `stdout`. Usage errors (unknown subcommand, missing file argument, invalid
option) use the same format:

```json
{"error": "validation_error", "message": "base_url_env requires allow_remote = true", "location": "model.openai_compatible"}
```

- `error`: a stable code from the table below.
- `message`: short, safe text. It never contains values from the job file, environment-variable
  contents, file contents or page text. Validation messages describe the first error and count
  the rest.
- `location`: dotted path of the first invalid field, for example
  `fields.0.string.pattern` (union members appear by tag), or `null` when no field applies.

| `error` | Meaning |
| --- | --- |
| `usage_error` | Invalid command line for `rastro job` (argparse message). |
| `syntax_error` | The file is not valid UTF-8, TOML or JSON. The message gives the line and column when known. |
| `invalid_job` | The file parses but breaks a format rule: byte-order mark, duplicate JSON keys, non-finite numbers, unquoted TOML dates. |
| `validation_error` | The job does not match the `Job` schema or its cross-field rules. |
| `schema_error` | The target fields could not be compiled into a record model. |
| `configuration_error` | Missing file or extension, path containment, absolute paths without the flag, missing environment variable or optional extra. |
| `resource_limit` | Oversized job file or local source. |
| `file_error` | The operating system refused to read a file. |
| `internal_error` | Unexpected failure. Run with `--verbose` for a traceback on `stderr`. |

Commands other than `rastro job` keep their existing output.

## Machine-readable output and JSON Schemas

`rastro job run` prints a single `JobRunResult` JSON object to `stdout`:

- `schema_version`: `1`
- `data_trust`: `"untrusted_source_data"` — extracted record fields come from untrusted external documents and must never be treated by agents as instructions (AGENT-003)
- `job`: `JobSummary` (`schema_fingerprint`, `source_count`, `succeeded`, `partial`, `failed`, `records_accepted`, `records_rejected`, `status`)
- `results`: ordered list of [`ExtractionResult`](api.md#results) dumps preserving records, field-level `evidence`, `issues`, `metrics`, `recipe_attempts` and `learned_recipe`.

Committed JSON Schemas live at [`docs/schemas/job.schema.json`](schemas/job.schema.json) and
[`docs/schemas/job-run.schema.json`](schemas/job-run.schema.json) and
[`docs/schemas/job-preview.schema.json`](schemas/job-preview.schema.json) and match `rastro job schema`.

## Security boundaries

- Job files are data only (256 KiB cap, UTF-8 without BOM, `extra="forbid"`, no imports or code evaluation).
- Relative `sources[].path` and `recipe_memory` paths cannot contain `..` and must resolve inside the job file's directory after symlink resolution; absolute paths are rejected unless `--allow-absolute-paths` is passed explicitly.
- API keys, model endpoints and proxy credentials are referenced only by uppercase environment-variable names (`[A-Z_][A-Z0-9_]{0,127}`) in every `*_env` field. Lowercase, token-shaped values are rejected, and validation errors never echo offending inputs.
- `rastro job validate` prints the normalized job with every `*_env` value replaced by `"<redacted>"`.

## Python API

```python
from pathlib import Path
from rastro import load_job, parse_job, run_job, run_job_async, run_job_file

job = load_job(Path("job.toml"))
report = run_job(job, base_dir=Path("."))
# Or in one step:
report = run_job_file(Path("job.toml"))
```

## Loading a dataset

Use `rastro job load job.toml` to write approved observed fields, then
`rastro dataset status agenda.sqlite` or
`rastro dataset export agenda.sqlite --format jsonl` to consume them.
The load report schema is available with `rastro job schema --target load`.
`[dataset].match_fields` optionally names the two labels and aware timestamp used
for uncertain cross-source comparisons; it defaults to `title`, `venue`, `start_time`.
See [dataset usage and the executed offline sequence](dataset.md#load-inspect-and-export-a-job).
