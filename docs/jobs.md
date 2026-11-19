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

## Start from a goal

A job can start from three entry points:

1. **Known sites and fields** from a saved HTML page and example values (`rastro new job.toml --from page.html --example title="Field notebook"`).
2. **Known sites only** (`rastro new --site URL [--site URL ...] -o job.toml`), where your URLs are the host approval.
3. **Only a goal** (`rastro new --goal "TEXT" [--fields a,b] [--search searxng|model] [--approve HOST ...] -o job.toml`).

### 1. Propose candidate sources (`DISC-001`, `DISC-002`)

Without `--approve`, `rastro new --goal` runs in **proposals-only** mode: it queries the search provider or model, prints candidate sources as JSON (`url`, `host`, `title`, `origin = "search" | "model"`), writes no file, and never fetches any candidate host:

```bash
export RASTRO_SEARXNG_URL="http://127.0.0.1:8080"
uv run rastro new --goal "cultural events in Montevideo"
```

- **Search providers (`--search searxng|model`).** `SearXNGSearchProvider` queries a free self-hosted SearXNG JSON endpoint (`RASTRO_SEARXNG_URL`; needs `httpx`, installed by `pip install 'rastro-engine[openai]'`); `localhost` and loopback addresses are local, other endpoints require HTTPS and `--allow-remote` (`SEC-004`). Passing `--search model` asks the configured model (`--model` or `RASTRO_MODEL`) for candidate URLs (`origin = "model"`), parses the response strictly, keeps only public `http(s)` URLs on standard ports, and caps proposals at 10 (`SEC-005`). If neither a search provider nor a model is configured, Rastro raises `ConfigurationError` explaining both options (`P-002`).

### 2. Approve hosts, propose fields and preview real records (`DISC-002`, `DISC-003`)

Discovered hosts never enter an allowlist without explicit approval. Pass `--approve HOST ...` (or `--site URL ...` when you already know the URLs) with `-o job.toml`:

```bash
uv run rastro new --goal "cultural events in Montevideo" \
  --approve agenda.example \
  --fields title,starts_on,url \
  -o job.toml

# Or start directly from known site URLs (your URLs are the approval):
uv run rastro new --site https://agenda.example/events -o job.toml
```

- Only exact approved hosts enter `[acquisition] allowed_hosts`.
- Rastro fetches at most **3 pages per approved host** through `WebAcquirer` (enforcing `robots.txt`, host pacing, and public-IP checks) and never follows links off approved hosts.
- When `--fields` is omitted, Rastro proposes fields from observed repeated structures on the page (`page_outline` and `suggest_fields`), reporting each field's inferred type, selector, and observed example value with zero model calls unless a model fallback is needed.
- Rastro writes `job.toml` and prints a preview of real extracted records. If no valid records can be observed, the command exits non-zero with an explicit outcome (`insufficient_evidence`) and writes no job file.

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

[model]  # runs only with the operator's --allow-remote (see Security boundaries)
provider = "http"
model = "gpt-4.1-mini"
base_url = "https://api.example.com/v1"
api_key_env = "RASTRO_OPENAI_API_KEY"
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
| `date` | `datetime.date` | `localized` (boolean, default `false`) |
| `datetime` | `datetime.datetime` | `localized` (boolean, default `false`) |
| `url` | `AnyHttpUrl` (`http` / `https`, any host, no TLD required) | — |

All field types accept `required = true` (default) or `required = false` (nullable, defaulting
to `None` when omitted by a recipe). Constraints from another type (such as `ge` on `string`)
are rejected during validation.

Extracted text values of every type have surrounding whitespace, including non-breaking
spaces, removed before validation, so `min_length`, `pattern` and number or date parsing see
the real value. A required field whose value is blank is a validation error, never an empty
success. A blank value for an optional field stays an empty string, so `min_length = 1` still
rejects it.

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
| `url` | `"https://example.com/a"`, `"/relative"` with source context | `"ftp://x"`, `"javascript:x"` |
| `string` | any string that meets the length and pattern constraints | numbers that are not strings |

Values that cannot be coerced become `validation_error` issues on the record, never silent
defaults. Normalize decimal commas or unsupported local formats in the recipe or with the Python SDK.

For Spanish source dates, opt in on each `date` or `datetime` field with `localized = true`
(DATA-005, JOB-002). ISO input remains supported. Month names and abbreviations are case
insensitive; weekday prefixes are ignored. For example, `12 al 14 NOV 2026 - 20 H` becomes
November 12 at 20:00 in `[region].timezone`. Ranges and lists use their first day.
Yearless dates use the observation's year in that timezone, advancing to the next year
when the date is more than 30 days before observation. A `date` field accepts
`06 de noviembre`; a `datetime` field rejects it because no time was supplied.
Localized non-ISO input requires timezone and observation context; extraction supplies both.

A `date` field also accepts an ISO datetime and keeps its calendar day in `[region].timezone`
when `localized = true`. A list of times (`26 NOV 2026 - 19 H y 21.30 H`) uses the first time.

URL fields resolve relative links against the source evidence URL before validating them
as HTTP(S). The Python model also supports this through `context={"source_url": "..."}`.
Without that context, relative URLs fail validation. Resolution does not fetch the link.

## Sources, recipes, memory and providers

- **Sources (`sources`).** Non-empty list of HTTP(S) URL sources (`{ url = "...", representation = "html" | "json", recipe = ... }` or a bare URL string) and/or local file sources (`{ path = "...", representation = "html" | "json", url = "...", recipe = ... }`). Each source must have an extraction path: an explicit `sources[].recipe`, a job-level `recipe`, `recipe_memory`, `discover_recipes = true`, or a `model` (for HTML sources).
- **HTTP policy (`acquisition`).** Required whenever any source is an HTTP(S) URL; uses the same fields as [`AcquisitionPolicy`](acquisition.md#policy-defaults). Every HTTP source host must appear in `acquisition.allowed_hosts`, `http://` URLs require `acquisition.allow_http = true`, and total HTTP sources cannot exceed `acquisition.max_sources`.
- **Regional context (`region`).** Optional `locale`, `languages`, `timezone`, `currency` and `egress` applied to every source. Browser `geolocation` is rejected. Named `egress` values must exist in `acquisition.egress_profiles`.
- **Recipes and discovery.** Job-level `recipe` and per-source `sources[].recipe` use the declarative [`Recipe`](api.md#recipe-variants) contract (`css`, `jsonld`, `json`) matching the source's `representation`, and may only map declared `fields`. Optional `discover_recipes = true` and `discovery_aliases` enable deterministic discovery.
- **Recipe memory (`recipe_memory`, `context`, `recipe_version`).** `recipe_memory` points to a SQLite database file (`:memory:` is rejected). `context` (`RecipeContext`) and `recipe_version` require `recipe_memory`, and `recipe_version` cannot be combined with an explicit `recipe`. If you edit a job recipe without raising its `version`, a higher version stored in `recipe_memory` (for example a promoted repair) is used instead; each run reports which one was used in `recipe_origin`. Raise `version` to the number named in the error, or above the stored versions, so your edit is used.
- **Model providers (`model`, `inference_policy`).** Optional `provider = "ollama"` (`model`, `base_url` or `base_url_env`), `provider = "command"` (`argv` list, `allow_remote` or `local`, `timeout`, `output`, `json_path`, `env`; never run by a shell) or `provider = "http"` (alias `"openai_compatible"`; `model`, `base_url` or `base_url_env`, `api_key_env`, `allow_remote`, `response_format`, `token_limit`, `temperature`). Non-loopback endpoints and `*cloud*` model names require `allow_remote = true` and HTTPS (on `openai_compatible`). Because an endpoint read from `base_url_env` is only known at run time, `openai_compatible` with `base_url_env` also requires `allow_remote = true`. Ollama endpoints, including `base_url_env` values, must be loopback, which the adapter checks at run time. These keys declare what the job needs; the operator still has to consent at run time, as described under [Security boundaries](#security-boundaries) and in [operator trust](intelligence.md#operator-trust-for-models-in-job-files).

### CSS field scope: `document:` and blank text

A `css` recipe evaluates each field expression inside its record. Prefix an expression with
`document:` to evaluate it against the whole page instead, for a value printed once outside
the records, such as a venue name in a footer:

```toml
[sources.recipe.fields]
title = "h4.card-title::text"
venue = "document:footer#mainFooter strong.mt-4::text"
```

Every record receives the same value, and the uniqueness rules are unchanged: no match leaves
the field missing (`missing_evidence` when the field is required) and more than one match is
`ambiguous_match`. The evidence locator keeps the full expression, prefix included. The prefix
is CSS-only (`jsonld` and `json` mappings are unchanged), still requires `::text` or
`::attr(name)`, and is not supported by relocation repair, which skips such recipes
(`relocation_retention_skipped`). It cannot write constants: the value must exist on the page.

### One record per value: `expand:`

A card that lists several dates (or schedules) is ambiguous by default. Prefix exactly one
field of a `css` recipe with `expand:` to turn the card into one record per matched value:

```toml
[sources.recipe.fields]
title = "h2::text"
starts_at = "expand:time::text"
```

Each record copies the card's other fields and goes through normal validation (for example
`localized` dates). The evidence locator ends in `[value N]`, the index of that value. Include
the expanded field in the dataset `identity` so each date is its own record. No match leaves
the field missing (rejected when required), never an empty success. Expanded records count
against `max_records` (`ResourceLimitError`). At most one field per recipe may expand, and it
cannot be combined with `document:`. The drift probe measures it. Relocation repair does not support it: `job load` skips saving
repair evidence (`relocation_retention_skipped`) and `rastro recipe repair` with a snapshot
raises `ConfigurationError`.

Whitespace-only text nodes are never matches, so `h6::text` over
`<h6>MIE 11 NOV <span>19:00</span></h6>` reads `MIE 11 NOV` instead of reporting the blank text
after the span as a second match. When a card lists several dates in sibling nodes, a selector
such as `time.cyg--dia:first-of-type::text` picks the first.

## Dataset block and preview

An optional `[dataset]` block declares where approved records would be stored and how a record
is identified (DATA-002). It is additive: `format_version` stays `1`, and a job without the block
validates, runs and prints exactly as before.

```toml
[dataset]
name = "uy_dance_agenda"                    # [a-z][a-z0-9_]{0,63}
path = "agenda.sqlite"                      # relative to the job file; same rules as other job paths
identity = ["title", "venue", "starts_on"]  # declared field names, 1 to 8
approve = ["title", "venue", "city", "starts_on", "starts_at", "ticket_url", "price"]  # optional
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
file and no recipe memory. For a job that sets `recipe_memory`, the store is opened read-only
and never created or upgraded: each source uses the stored serving recipe when its version is
higher than the job file's recipe, otherwise the job file's recipe. If the store file does not
exist yet, the job file's recipe is used. A store written by an older release must be upgraded
first by any writing command, such as `rastro job load`. It requires a `[dataset]` block (exit code `2` otherwise) and shares the exit codes below.
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
uv run rastro job template --all > job.toml   # every option, commented
uv run rastro job load job.toml --drift --on-failure "notify-script"
uv run rastro job ack-drift job.toml --source 1
uv run rastro job forget-source job.toml --url https://old.example/list
uv run rastro health job.toml --output json
```

| Exit code | Constant | Meaning |
| --- | --- | --- |
| `0` | `JobExitCode.SUCCESS` | `validate` or `schema` succeeded, or every source of `run` or `preview` succeeded (`status = "success"`). |
| `1` | `JobExitCode.FAILED` | The job ran, no source succeeded or partially succeeded, and zero records were accepted (`status = "failed"`). |
| `2` | `JobExitCode.CONFIG_ERROR` | The job did not run: usage error, missing or unreadable file, TOML/JSON syntax error, schema validation error, path escape, missing extra or environment variable, or resource limit on supplied files. |
| `3` | `JobExitCode.PARTIAL_SUCCESS` | At least one source succeeded or partially succeeded, but not all of them succeeded (`status = "partial_success"`). |
| `4` | `JobExitCode.NEEDS_ATTENTION` | The command succeeded but an operator must act: `load` finished with open drift (`status = "degraded"`); `rastro health` or `rastro dataset status` found a stale source, open drift or a quarantined candidate. |
| `5` | `JobExitCode.STORAGE_ERROR` | `load` ran but a dataset write or read failed. The report is still printed: committed sources keep their counts, the rest carry a `storage_error` issue (`status = "storage_error"`). |

Every key, flag and default is listed in the [Configuration reference](configuration.md)
(`rastro config`). An unknown key fails validation with a `hint` naming the closest valid key.

Severity order: 5, 1, 3, 4, 0. See [Monitoring and alerts](operations.md) for open drift,
`rastro health`, the `--on-failure` hook and `RASTRO_ON_FAILURE`, and
[ADR 0023](decisions/0023-open-drift-and-attention-exit-codes.md) for the reasoning.

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
- `hint` (optional): a suggested next step, such as the command to run.

| `error` | Meaning |
| --- | --- |
| `usage_error` | Invalid command line for `rastro job` (argparse message). |
| `syntax_error` | The file is not valid UTF-8, TOML or JSON. The message gives the line and column when known. |
| `invalid_job` | The file parses but breaks a format rule: byte-order mark, duplicate JSON keys, non-finite numbers, unquoted TOML dates. |
| `validation_error` | The job does not match the `Job` schema or its cross-field rules. |
| `schema_error` | The target fields could not be compiled into a record model. |
| `configuration_error` | Missing file or extension, path containment, absolute paths without the flag, missing environment variable or optional extra. |
| `resource_limit` | Oversized job file or local source. |
| `storage_error` | A dataset file could not be read or written (foreign or newer schema, symlinked path, SQLite failure) before a report existed. |
| `file_error` | The operating system refused to read a file. |
| `internal_error` | Unexpected failure. Run with `--verbose` for a traceback on `stderr`. |

Other commands use the same envelope. Their input errors use the code `invalid_input`
with a specific `message` (for example `Dataset file not found: agenda.sqlite` from
`rastro dataset export`), and a missing optional dependency uses `missing_extra`:

```json
{"error": "missing_extra", "message": "This command needs the optional 'studio' extra", "location": null, "hint": "pip install 'rastro-engine[studio]'"}
```

## Machine-readable output and JSON Schemas

`rastro job run` prints a single `JobRunResult` JSON object to `stdout`:

- `schema_version`: `1`
- `data_trust`: `"untrusted_source_data"` — extracted record fields come from untrusted external documents and must never be treated by agents as instructions (AGENT-003)
- `job`: `JobSummary` (`schema_fingerprint`, `source_count`, `succeeded`, `partial`, `failed`, `records_accepted`, `records_rejected`, `status`)
- `results`: ordered list of [`ExtractionResult`](api.md#results) dumps preserving records, field-level `evidence`, `issues`, `metrics`, `recipe_attempts` and `learned_recipe`.
- `recipes`: one entry per source (`source_index`, `recipe_origin`, `recipe_version`) saying whether the source used the job file's recipe (`"job"`) or the serving recipe in `recipe_memory` (`"memory"`). With `recipe_memory`, a stored serving recipe whose version is higher than the job file's recipe is used; see [load and rollback behavior](repair.md#load-and-rollback-behavior). `rastro job load` reports the same `recipe_origin` and `recipe_version` per source.

Committed JSON Schemas live at [`docs/schemas/job.schema.json`](schemas/job.schema.json) and
[`docs/schemas/job-run.schema.json`](schemas/job-run.schema.json) and
[`docs/schemas/job-preview.schema.json`](schemas/job-preview.schema.json) and match `rastro job schema`.

## Security boundaries

- Job files are data (256 KiB cap, UTF-8 without BOM, `extra="forbid"`, no imports or code evaluation), and data cannot grant trust. A `[model]` may be declared, but Rastro uses a non-loopback endpoint, an endpoint outside the local model ports 11434, 1234, 8000 and 8080, `base_url_env` or `allow_remote = true` only with the operator's `--allow-remote` (SDK `allow_remote=True`, env `RASTRO_ALLOW_REMOTE=1`). A `provider = "command"` model is always refused: a job file can never make Rastro start a program, and a command model comes only from the operator (`rastro.model`, `--model`, `RASTRO_MODEL`). Otherwise the run fails closed before any request, naming the flag.
- A job-declared egress proxy is used only with `--allow-egress` (SDK `allow_egress=True`, env `RASTRO_ALLOW_EGRESS=1`). The trust flags may come before or after the subcommand.
- `api_key_env`, `base_url_env` and proxy `username_env`/`password_env` names must start with `RASTRO_` unless the operator allows them (`--allow-env NAME`, SDK `allow_env=[...]`, or `RASTRO_ALLOWED_ENV="A,B"`), so a job cannot pick an arbitrary process secret and send it to an endpoint; the refusal does not echo the name.
- Relative `sources[].path` and `recipe_memory` paths cannot contain `..` and must resolve inside the job file's directory after symlink resolution; absolute paths are rejected unless `--allow-absolute-paths` is passed explicitly.
- API keys, model endpoints and proxy credentials are referenced only by uppercase environment-variable names (`[A-Z_][A-Z0-9_]{0,127}`) in every `*_env` field. Lowercase, token-shaped values are rejected, and validation errors never echo offending inputs.
- `rastro job validate` prints the normalized job with every `*_env` value replaced by `"<redacted>"`.

### Operator opt-in

A job file is configuration: remote models, egress proxies and secret environment names
need operator opt-in. The job file cannot grant it to itself.

- A job that declares `[acquisition.egress_profiles]` runs only with `--allow-egress`
  (`rastro job run|preview|load`, `rastro recipe repair|promote`, `rastro mcp`,
  `rastro studio`), `allow_egress=True` on `run_job`, `run_job_async`, `run_job_file`,
  `load_job_file`, `preview_job`, `repair_job_source` and `promote_job_recipe`, or
  `RASTRO_ALLOW_EGRESS=1`. Without it the run fails with a `ConfigurationError` at
  `acquisition.egress_profiles` before any request. Jobs without egress profiles need no
  opt-in.
- Proxy credential references (`username_env`, `password_env`) must be named `RASTRO_*`
  (`RASTRO_[A-Z0-9_]{1,64}`) or appear in the operator allow-list: `RASTRO_ALLOWED_ENV`
  (comma-separated) or `--allow-env NAME` (repeatable; `allow_env=` in Python). The error
  names the job key, never the variable.
- A job-declared remote model (`[model]` with `allow_remote = true` or `base_url_env`) runs
  only when the operator sets `RASTRO_ALLOW_REMOTE=1`; its `api_key_env` and `base_url_env`
  must be named `RASTRO_*` or listed in `RASTRO_ALLOWED_ENV`. Otherwise the run fails at
  `model` / `model.api_key_env` before any request. Loopback models need no opt-in.

- Private-network destinations, extra ports and a robots.txt override are likewise the
  operator's: `--allow-private-network`, `--allow-port N` and `--ignore-robots HOST` (SDK
  `allow_private_network=`, `allow_ports=`, `ignore_robots=`; env `RASTRO_ALLOW_PRIVATE_NETWORK`,
  `RASTRO_ALLOWED_PORTS`, `RASTRO_IGNORE_ROBOTS`). A job file that sets any of them is
  rejected as an unknown key. See [Operator permissions](acquisition.md#operator-permissions).

```console
$ RASTRO_PROXY_USER=me RASTRO_PROXY_PASS=secret rastro job run job.toml --allow-egress
```

### Auth header from the environment

`[[acquisition.auth]]` declares a request header whose value comes from an environment
variable. The job names the variable; the secret is never in the file.

```toml
[acquisition]
allowed_hosts = ["api.example.com"]

[[acquisition.auth]]
host = "api.example.com"       # must be in allowed_hosts
header = "Authorization"       # default
value_env = "RASTRO_API_TOKEN" # RASTRO_* or --allow-env NAME
scheme = "Bearer"              # optional; sent as "Bearer <value>"
```

- `header` must be an HTTP token and cannot be `Host`, `Cookie`, `Set-Cookie`,
  `Content-Length`, `Transfer-Encoding`, `Connection`, `Proxy-Authorization` (or other
  hop-by-hop headers) or a header Rastro sets itself (`User-Agent`, `Accept`,
  `Accept-Language`, `If-None-Match`, `If-Modified-Since`). Each host/header pair appears once.
- A missing, empty or malformed value (control characters, non-ASCII, oversized) is a
  `ConfigurationError` before any request; the error never echoes the variable name or value.
- The header goes only to that exact host, over HTTPS; the source must be `https`, except a
  loopback source with `--allow-private-network`. It is not sent on a redirect to another
  host, to robots.txt, or to a proxy in any way but the end-to-end request.
- HTTP engine only: with `render = "browser"` or `"stealth"` (or `solve_challenges`), or a
  per-source `render`, the job is refused with `auth headers are HTTP-engine only`.
- Reprs, logs, errors, dataset rows, run metrics and conditional-load digests carry only
  the variable name, never the value. Hub bundles hold recipes and fields but no
  acquisition block, so they cannot carry it.

## Python API

```python
from pathlib import Path
from rastro import load_job, load_job_file, parse_job, run_job, run_job_async, run_job_file

job = load_job(Path("job.toml"))
report = run_job(job, base_dir=Path("."))
# Or in one step:
report = run_job_file(Path("job.toml"))
# Run and load approved fields into the job's [dataset], as `rastro job load` does:
loaded = load_job_file(Path("job.toml"), drift=True)
```

Repairing, promoting and writing a recipe back are covered in
[recipe repair](repair.md#python-api).

## Loading a dataset

Use `rastro job load job.toml` to write approved observed fields, then
`rastro dataset status agenda.sqlite` or
`rastro dataset export agenda.sqlite --format jsonl` to consume them.
The load report schema is available with `rastro job schema --target load`.
`[dataset].match_fields` optionally names the two labels and aware timestamp used
for uncertain cross-source comparisons. Without it, records are matched on `identity`
only and no uncertain match is flagged. A misspelled name is reported with
`"location": "dataset.match_fields"`.
See [dataset usage and the executed offline sequence](dataset.md#load-inspect-and-export-a-job).

## Pagination

For URL sources, a recipe can declare a next-page link (SYNC-002, SEC-008;
[ADR 0026](decisions/0026-bounded-recipe-pagination.md)):

```toml
[recipe.pagination]
next = "a[rel=next]::attr(href)"
max_pages = 5
```

The block belongs to the effective recipe, including `[sources.recipe.pagination]`
when a source has its own recipe. `max_pages` includes the first page, defaults to
5 and accepts integers from 1 through 50. CSS next expressions follow the selector
cost policy. With `json` or `jsonld`, `next` is an absolute JSON pointer, for example
`"/links/next"`; JSON-LD pointers are evaluated on the document's JSON-LD objects.
The TOML writer preserves this block when replacing a recipe.

Rastro resolves relative links against the acquired page URL. Every page uses the
same acquisition port, permission allowlist, robots rules, pacing, DNS checks and
egress policy. One `max_requests` budget covers the whole source, including robots,
redirects, retries and address fallbacks when using `WebAcquirer`. Redirects cannot
escape the exact source hostname or downgrade HTTPS. There are no JavaScript clicks,
URL templates, guessed page numbers or cross-host traversal; scrolling a rendered page
is a separate, bounded [`[scroll]`](#scroll) table.

The source result combines records in page order and reports `pagination_stop`:
`no_next`, `repeat`, `empty_page`, `max_pages`, `off_host`, `https_downgrade`,
`budget`, `page_failure`, `invalid_next`, or `record_limit`. A normal end of the list
is a complete snapshot (`complete=True`, exit 0): `no_next` (no next link) and `repeat`
(the next link points at a page already read). `empty_page` (an HTTP 200 page after
the first yielding no records) is also `success` with exit 0, but loads with
`complete=False`: drift is measured on page one only, so a later page that drifted
to zero records must not mark its earlier records missing. Every other stop preserves earlier records as
`partial_success`, so dataset loading uses `complete=False` (exit 3) and cannot mark
unseen records missing. A failed first page retains its original failure outcome.
`limits.max_records` also bounds the combined result.

The next link's host is compared with the source host case-insensitively and
ignoring one trailing dot, so `https://AGENDA.test./two` is the same host as
`agenda.test`; any other host is refused as `off_host`. A fallback recipe cannot
declare `pagination`: only the primary recipe does, and a fallback with the block
is rejected as a configuration error.

With a job dataset, repeated identities across pages keep the first occurrence.
`pagination_duplicates_conflicting` counts later occurrences whose field values
differ; zero is omitted from the source result. Duplicate identities within one
page still fail dataset storage, just as without pagination. Without a dataset,
the extraction result retains every record because no record identity is declared.
Each paginated record has a 1-based `page_index`. Field provenance keeps the logical
source in `source_url`; `page_url` records a different contributing page and keeps only
scheme, host, port and path (userinfo, `;params`, query and fragment are dropped). Drift measurement and repair evidence remain focused on
page 1. Repair candidates keep the declared pagination block and strategy.
Supplied-file extraction does not follow links.

## Scroll

A browser source can scroll to load more records
([ADR 0033](decisions/0033-scroll-registry-url-and-spec-amendments.md)):

```toml
[acquisition]
allowed_hosts = ["example.com"]
render = "browser"

[scroll]
max_steps = 10          # 1 to 30
settle_ms = 500         # 100 to 3000
stop_when = "no_new_records"
```

| Key | Default | Meaning |
| --- | --- | --- |
| `max_steps` | `10` | Scroll steps per page, from 1 through 30. |
| `settle_ms` | `500` | The longest a step waits after scrolling before it reads the page, from 100 through 3000. |
| `stop_when` | `"no_new_records"` | The only value: a step with no new accepted record ends the scroll. |

The table is optional and applies to the whole job; unknown keys and out-of-range values
are rejected. It needs `render = "browser"`: with `render = "http"` it is ignored and the
source result carries the issue `scroll_ignored_http` (still a success); with
`render = "stealth"` it is ignored the same way and the issue is `scroll_unsupported_stealth`
(Rastro does not drive scroll in a stealth page). Scroll never
turns on stealth or challenge solving, and a job file cannot grant either. Steps spend the
per-source `max_requests` budget, shared with pagination; hitting `max_steps` or the budget
is `partial_success` (`scroll_max_steps`, `scroll_budget`) and keeps every accepted record.
See [Scroll](acquisition.md#scroll) for the full behavior and
[Browser contexts](acquisition.md#browser-contexts) for how a source's context is reused.
## Spider

A job can follow links from the pages it names
([ADR 0032](decisions/0032-vibe-coder-spider-and-studio.md)). The start URLs are the job's
`[[sources]]`; nothing else starts a crawl, and a model or bundle cannot supply a bare
domain.

```toml
[spider]
mode = "spider"                  # absent or "list" keeps today's named sources
links = "a.item::attr(href)"     # or link_field = "url": one declared field holds the link
max_depth = 2                    # 1 to 4; depth 0 is the start URL
max_pages = 25                   # 1 to 100 documents per start source
same_host = true                 # default; false still needs the host in allowed_hosts
allow = ["/item"]                # optional path prefixes, whole segments
deny = ["/login", "/cart"]
```

| Key | Meaning |
| --- | --- |
| `mode` | `"list"` (default) reads each source once; `"spider"` follows links. |
| `seed` | `"page"` (default): the start URL is a page. `"sitemap"`: it is a sitemap whose locs are the links ([Start from a sitemap](#start-from-a-sitemap)). |
| `seed_order` | `"document"` (default), `"reverse"` or `"lastmod"` (newest first, entries without a valid `<lastmod>` last in document order). Only for `seed = "sitemap"`; applied before `max_pages`. |
| `links` | A CSS expression ending in `::attr(name)`; every match is a link. It follows the selector cost policy of any job recipe. HTML sources only. |
| `link_field` | The name of a declared field whose value in each accepted record is a link. Works for JSON too. Exactly one of `links` or `link_field` is required in spider mode, unless `seed = "sitemap"` (then at most one). |
| `max_depth` | Link hops from the start URL, 1 to 4 (default 2). |
| `max_pages` | Fetched documents per start source, 1 to 100 (default 25). Pagination pages count. |
| `same_host` | Default `true`. With `false`, a link is followed only when its host is in `[acquisition].allowed_hosts`. |
| `allow`, `deny` | Path prefixes matched on whole segments: `/item` covers `/item` and `/item/1`, never `/items`. `deny` wins; with `allow`, a path outside it is skipped. The start URL is always fetched. |
| `allow_pattern` | Optional linear-time regular expression (no look-around or backreferences, max 512 chars) matched against each link's normalized path; non-matching links are skipped as `path_denied` before `max_pages`. `deny` still wins. |

The table grants nothing and rejects unknown keys, so a job file cannot add
`ignore_robots`, private-network or stealth permissions here. They stay operator flags
([Operator opt-in](#operator-opt-in)).

Rules:

- Links resolve against the acquired page. Fragments are dropped, hosts are lowercased,
  the query is kept for the fetch but never logged or reported, and a URL is fetched
  once. `javascript:`, `mailto:`, userinfo, encoded slashes and dot segments are refused.
- Every page uses the normal acquisition port: allowlist, robots, pacing, DNS checks,
  egress and the concurrency cap. Robots are enforced per page unless the operator passed
  `--ignore-robots` for that host.
- Pages of one start source share **one** request budget (`max_requests`, robots and
  redirects included). With pagination, the tighter of the two caps wins and
  neither resets per page. Pagination inside a spider page costs from the same budget.
  A recipe with `pagination` makes the crawl sequential so the caps are deterministic.
- With `render = "browser"` (or `"stealth"`), a start source's pages share **one** browser
  context, closed when the source finishes, and the crawl fetches one page at a time
  (HTTP spiders keep `max_concurrency`). With `[scroll]`, every scroll step also spends
  `max_pages` and `max_requests`: the tighter cap wins, and a page reached after the caps
  are spent is not scrolled and is `partial` (`scroll_budget`). Records already collected
  are kept.
- A spider needs an explicit recipe or a stored one (`recipe`, `[sources.recipe]` or
  `recipe_memory`); a learned recipe is not reused across pages yet and reports
  `spider_needs_recipe`.

The source result combines records from all pages in discovery order (identity dedupe
as for pagination, with `page_index` up to 100) and adds `spider`: every discovered URL as
`{ordinal, depth, host, path, outcome, records}` and no query. Outcomes: `fetched`,
`no_records` (a links-only page), `partial`, `failed`, `robots_denied`, `off_host`,
`https_downgrade`, `path_denied`, `port_not_allowed` (a port only the operator can grant;
it never takes a page slot), `invalid_url`, `depth_limit`, `page_limit`, `budget`.
Reported paths drop `;params` and repeated slashes, and `allow`/`deny` match the same way,
so `//login` and `/login;x=1` are denied by `deny = ["/login"]`.
At most 600 pages are listed; `unreported` counts the rest.

`pagination_stop` ends with `spider_complete` (success, a complete snapshot),
`spider_depth_limit`, `spider_page_limit`, `spider_budget` or `spider_page_failed`.
Hitting a cap, a robots denial or a failed page is `partial_success` with the records
already accepted (exit 3); it never deletes dataset rows. Skipped links that match a
filter (`off_host`, `path_denied`, ...) are reported but do not make the run partial.
If no page yields a record the result is `missing_data`, not an empty success.

### Start from a sitemap

When a site's listing or API is closed (for example `robots_denied`) but its
`robots.txt` advertises a `sitemap.xml` of public detail pages, seed the spider from the
sitemap instead of parsing it yourself:

```toml
[[sources]]
url = "https://dance.example/sitemap.xml"   # the sitemap is the start URL

[recipe]
strategy = "jsonld"
item_type = "Event"
fields = { name = "/name", start = "/startDate" }

[spider]
mode = "spider"
seed = "sitemap"          # default "page": the start URL is an HTML page
seed_order = "lastmod"    # "document" (default), "reverse", or "lastmod" (newest first)
max_depth = 1             # detail pages only; 2+ needs links or link_field
max_pages = 21            # the sitemap counts: 1 sitemap + 20 detail pages
allow = ["/evento"]
allow_pattern = "/evento/2026-"  # optional regex on the normalized path, before max_pages
```

- The sitemap is fetched like any page: allowlist, robots, pacing, DNS checks, egress,
  the shared `max_requests` budget and the HTML byte cap. It needs `render = "http"`;
  a browser-rendered source reports `sitemap_unsupported`.
- Accepted media types are `application/xml`, `text/xml` and gzip (`.xml.gz`, inflated
  under the same byte cap). Anything else is `unsupported_content_type`.
- The XML is parsed without a DTD: any `DOCTYPE` (and so any entity or external
  resource) is refused as `sitemap_invalid`, as are malformed XML and roots other than
  `<urlset>` and `<sitemapindex>`. Element names match without their namespace. More
  than 50,000 URLs, or a gzip that inflates past the cap, is `sitemap_too_large`.
- A `<sitemapindex>` is followed one level: its child sitemaps are depth 0, spend
  `max_pages` and the budget, and pass the host rules (not `allow`/`deny`/`allow_pattern`,
  which are for pages). A child that is itself an index is `sitemap_nested_index`.
- Every `<url><loc>` becomes a depth-1 link and passes the normal link rules: same host
  by default, https downgrade refused, `allow`/`deny`/`allow_pattern`, fetched once.
- Ordering and filtering apply **before `max_pages`** and the budget:
  - `seed_order = "document"` (default) keeps the sitemap's own order.
  - `seed_order = "reverse"` walks the sitemap from last entry to first (and child
    sitemaps of a `<sitemapindex>` in reverse too).
  - `seed_order = "lastmod"` sorts entries with a valid W3C Datetime / ISO 8601
    `<lastmod>` newest-first in UTC; equal timestamps and entries without a valid
    `<lastmod>` keep their document order, with undated entries last. Child sitemaps of a
    `<sitemapindex>` follow the same order, and `<url>` entries from all fetched child
    sitemaps are sorted together.
  - Filtered locs are reported as `path_denied` without spending a page slot; excess
    matching locs past `max_pages` or the budget are reported as `page_limit` or `budget`
    (`partial_success`).
- The sitemap yields no records itself (`no_records` in the report). A sitemap with no
  locs is `missing_data` with `sitemap_empty`; a refused or invalid sitemap is a failed
  start page, never an empty success. Without `links` or `link_field` the detail pages
  are leaves; with one of them, links on detail pages are followed up to `max_depth`.

## Your own PostgreSQL

Install `pip install 'rastro-engine[postgres]'` and put your connection string in
`RASTRO_PG_DSN` in the process environment. Never put the connection string in a job.
The variable name must be `RASTRO_*` or allowed by the operator with `--allow-env`.

```toml
[dataset]
name = "events"
backend = "postgres"
dsn_env = "RASTRO_PG_DSN"
schema = "rastro" # optional
identity = ["title", "venue"]
```

The DSN must specify a host (a loopback IP, `localhost`, a Unix socket directory,
or a remote host); libpq service references are not supported. A host outside
loopback requires the operator's `--allow-remote` flag, `allow_remote=True` in
`load_job_file`, or `RASTRO_ALLOW_REMOTE=1`. Configure TLS in the DSN for a remote
server. Rastro does not print the DSN in errors or object representations.

`rastro job load job.toml` fills the database. Each source snapshot commits in one
transaction, preserving identity, provenance and records from unavailable sources.
A failed transaction rolls back and reports `storage_error` (exit 5). One connection
is reused for the load and closed afterward. Schema migrations run transactionally,
only forward; a newer schema version is refused without resetting any data.

Only records and source status live in PostgreSQL. Drift and relocation state stay
in `rastro-state.sqlite` beside the job; recipe memory stays at its configured local
path. Do not set `dataset.path` with this backend. Existing SQLite jobs still require
`path` and default to `backend = "sqlite"`. There is no automatic transfer of existing
SQLite records. Re-running the job populates the configured PostgreSQL dataset.
`health`, repair history and `job forget-source` use the configured backend.
Forgetting a source preserves its records and provenance. Local operational cleanup
and PostgreSQL status cleanup are separate transactions; retry if either fails.

The path-only `rastro dataset` CLI continues to address SQLite files. Query PostgreSQL
with your application or SQL client, or use `PostgresDatasetStore.export_jsonl` in
Python and call `close()` afterward. Its JSONL is identical to SQLite for identical
observations. The application tables, under the chosen schema, are:

| Table | Columns | Primary key |
| --- | --- | --- |
| `rastro_schema` | `version INTEGER` (currently 1) | `version` |
| `dataset_records` | `dataset TEXT`, `key TEXT` (canonical identity JSON), `payload TEXT` (StoredRecord JSON), `identity_hash TEXT` (SHA-256 of key) | `(dataset, identity_hash)` |
| `dataset_sources` | `dataset TEXT`, `source TEXT` (canonical evidence URL), `payload TEXT` (SourceStatus JSON), `identity_hash TEXT` (SHA-256 of source) | `(dataset, identity_hash)` |

Record JSON includes `fields`, `provenance`, `origins`, `first_observed_at`,
`last_observed_at`, and `uncertain_match`. Status JSON includes `last_success`,
`last_attempt`, `stale`, `reason`, and optional `detail`. Text JSON preserves export
bytes; cast it when querying:

```sql
SELECT payload::jsonb -> 'fields' AS fields,
       payload::jsonb ->> 'last_observed_at' AS last_observed
FROM rastro.dataset_records
WHERE dataset = 'events'
ORDER BY key;
```

Use a dedicated schema owned by the database role Rastro connects as; that role needs
permission to create the schema and tables. Writers serialize within that schema to
preserve atomic identity updates. Reads do not create missing schemas.

Unset `PGSERVICE` and `PGHOSTADDR` when using this adapter; use explicit DSN options
instead, so libpq environment defaults cannot override the checked destination.
The architecture and cleanup tradeoff are recorded in
[ADR 0025](decisions/0025-postgres-datasets.md).

This backend is supported by job loading and the job-aware CLI health, maintenance,
and repair flows. Studio dataset views and MCP `drift_status` support SQLite only and refuse a
PostgreSQL job with an explicit error rather than showing empty data;
use SQL for PostgreSQL dataset queries in this release.
