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

```console
$ RASTRO_PROXY_USER=me RASTRO_PROXY_PASS=secret rastro job run job.toml --allow-egress
```

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
